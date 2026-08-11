"""Password gate for the single-user app.

Reads ``LANGLEARN_PASSWORD`` from the environment on every check (so rotating
the password requires only a restart — it is never written to disk). Sessions
are signed cookies using ``itsdangerous`` (already a Flask transitive
dependency). Sessions auto-expire after a fixed TTL.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from collections import deque
from threading import Lock
from typing import Deque

from flask import Request, Response, current_app, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from werkzeug.security import check_password_hash, generate_password_hash

from .. import config
from ..util import err

log = logging.getLogger("langlearn.auth")

SESSION_COOKIE = "ll_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600

LOGIN_WINDOW_SECONDS = 60
LOGIN_MAX_ATTEMPTS = 5

_login_attempts: dict[str, Deque[float]] = {}
_login_lock = Lock()


def is_auth_enabled() -> bool:
    """Auth is on when LANGLEARN_PASSWORD is set. If unset, the app is wide
    open (preserves the single-user local-dev workflow)."""
    return bool(os.environ.get("LANGLEARN_PASSWORD"))


def _cached_hash() -> str | None:
    """Hash is cached so we don't re-hash on every request. The cache is keyed
    by the raw password so a restart (or env change via SIGHUP-style rotation)
    invalidates it automatically."""
    raw = os.environ.get("LANGLEARN_PASSWORD")
    if not raw:
        return None
    cached = getattr(current_app, "_ll_password_cache", None)
    if cached and cached[0] == raw:
        return cached[1]
    h = generate_password_hash(raw, method="scrypt", salt_length=16)
    current_app._ll_password_cache = (raw, h)  # type: ignore[attr-defined]
    return h


def verify_password(candidate: str) -> bool:
    h = _cached_hash()
    if not h:
        return False
    return check_password_hash(h, candidate)


def _signer() -> TimestampSigner:
    secret_key = current_app.config.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError("SECRET_KEY not configured on Flask app")
    return TimestampSigner(secret_key, salt="langlearn-session")


def _is_private_or_local_host(host: str) -> bool:
    """True when the Host header points at the local network — a private IP,
    loopback, or a single-label hostname. Used to decide whether plain-HTTP
    clients are realistic for the current request."""
    if not host:
        return False
    h = host.split(":", 1)[0].lower()
    if h == "localhost" or h.endswith(".local") or h.endswith(".lan") or "." not in h:
        return True
    try:
        import ipaddress
        ip = ipaddress.ip_address(h)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        )
    except ValueError:
        return False


def _request_is_secure(req: Request | None = None) -> bool:
    """Decide whether the session cookie should carry the Secure flag.

    No environment configuration is required. We look only at signals on the
    current request:

    - The connection is HTTPS (WSGI scheme), or
    - The client sent ``X-Forwarded-Proto: https`` (a reverse proxy in front
      of the app). This is checked without any trust flag because on a
      local-network deployment there is no proxy to spoof, and on a public
      deployment this is the only signal the backend has after TLS
      termination. Spoofing the header only ever *upgrades* the cookie's
      security, so it cannot weaken the app.
    - The Host header is a public domain (not a private IP, not localhost).
      In that case we err on the side of Secure so the cookie is not silently
      dropped by browsers on the public origin.

    Returns False only when we have positive evidence the request is plain
    HTTP from a local network host — which is the only case where the
    browser will accept a non-Secure cookie and we want one to be set."""
    from flask import request as _req
    req = req or _req
    if req.is_secure:
        return True
    if req.headers.get("X-Forwarded-Proto", "").lower() == "https":
        return True
    host = req.headers.get("Host", "")
    if not _is_private_or_local_host(host):
        return True
    return False


def issue_session(response: Response) -> None:
    token = _signer().sign(b"v1").decode("ascii")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=_request_is_secure(),
        samesite="Strict",
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def is_authenticated(req: Request | None = None) -> bool:
    req = req or request
    token = req.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        _signer().unsign(token, max_age=SESSION_TTL_SECONDS)
    except SignatureExpired:
        return False
    except BadSignature:
        return False
    return True


def _client_ip(req: Request | None = None) -> str:
    req = req or request
    fwd = req.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return req.remote_addr or "unknown"


def check_login_rate_limit() -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    if not current_app.config.get("LL_AUTH_RATE_LIMIT", True):
        return True
    ip = _client_ip()
    now = time.monotonic()
    with _login_lock:
        q = _login_attempts.get(ip)
        if q is None:
            q = deque()
            _login_attempts[ip] = q
        cutoff = now - LOGIN_WINDOW_SECONDS
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= LOGIN_MAX_ATTEMPTS:
            return False
        q.append(now)
        return True


def reset_login_rate_limit() -> None:
    ip = _client_ip()
    with _login_lock:
        _login_attempts.pop(ip, None)


def require_auth(view):
    """Decorator: returns 401 JSON when auth is enabled and the request is not
    authenticated. When auth is disabled (no LANGLEARN_PASSWORD), the view is
    served unconditionally — preserves local-dev workflow."""
    from functools import wraps

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_auth_enabled():
            return view(*args, **kwargs)
        if is_authenticated():
            return view(*args, **kwargs)
        resp = jsonify(err("unauthorized", code="unauthorized"))
        resp.status_code = 401
        return resp

    return wrapper


def constant_time_str_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def configure_app(app) -> None:
    """Set Flask SECRET_KEY (used to sign session cookies). A stable, random
    per-installation secret is kept on disk so cookies survive restarts; if no
    secret file exists yet one is generated."""
    secret_path = config.data_dir() / "session.secret"
    key: str = ""
    if secret_path.exists():
        candidate = secret_path.read_text().strip()
        if len(candidate) >= 32:
            key = candidate
    if not key:
        key = os.urandom(32).hex()
        try:
            secret_path.write_text(key)
        except OSError as e:
            log.warning("could not persist session secret: %s", e)
    app.config["SECRET_KEY"] = key
    app.config["SESSION_COOKIE_NAME"] = SESSION_COOKIE
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Strict"
    # SESSION_COOKIE_SECURE is intentionally NOT set here. The cookie is
    # marked Secure per-request by ``issue_session`` based on the actual
    # request (see ``_request_is_secure``).
    app.config["PERMANENT_SESSION_LIFETIME"] = __import__("datetime").timedelta(
        seconds=SESSION_TTL_SECONDS
    )
