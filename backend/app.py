"""Flask app factory + entry point.

Boot via:
    python -m backend.app
or use ./start.sh which sets up the venv.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from . import config
from .db import init_schema
from .services.dictionaries import registry as dict_registry
from .services.dictionaries import suggest as suggest_svc
from .services import settings as settings_svc
from .services import auth_gate
from .services.tts import registry as tts_registry
from .util import err

log = logging.getLogger("langlearn")


def create_app() -> Flask:
    logging.basicConfig(
        level=logging.DEBUG if config.DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = Flask(
        __name__,
        static_folder=str(config.STATIC_DIR),
        static_url_path="/static",
        template_folder=str(config.TEMPLATES_DIR),
    )
    app.config["JSON_AS_ASCII"] = False
    app.config["DEBUG"] = config.DEBUG
    auth_gate.configure_app(app)

    init_schema()
    dict_registry.bootstrap()
    tts_registry.bootstrap()
    suggest_svc.warmup()
    settings_svc.create_default_settings()
    _register_blueprints(app)

    @app.get("/")
    def index():
        if auth_gate.is_auth_enabled() and not auth_gate.is_authenticated():
            return send_from_directory(config.TEMPLATES_DIR, "login.html")
        return send_from_directory(config.TEMPLATES_DIR, "index.html")

    @app.get("/manifest.json")
    def manifest():
        return send_from_directory(config.STATIC_DIR, "manifest.json")

    @app.get("/sw.js")
    def service_worker():
        resp = send_from_directory(config.STATIC_DIR, "sw.js")
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    _register_auth_gate(app)

    @app.errorhandler(404)
    def not_found(_e):
        return jsonify(err("not_found", code="not_found")), 404

    @app.errorhandler(405)
    def method_not_allowed(_e):
        return jsonify(err("method_not_allowed", code="method_not_allowed")), 405

    @app.errorhandler(500)
    def internal_error(e):
        log.exception("unhandled exception: %s", e)
        return jsonify(err("internal_error", code="internal_error")), 500

    return app


def _register_blueprints(app: Flask) -> None:
    from .blueprints.auth import bp as auth_bp
    from .blueprints.settings import bp as settings_bp
    from .blueprints.languages import bp as languages_bp
    from .blueprints.dictionary import bp as dictionary_bp
    from .blueprints.vocab import bp as vocab_bp
    from .blueprints.structures import bp as structures_bp
    from .blueprints.phrases import bp as phrases_bp
    from .blueprints.tts import bp as tts_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(languages_bp)
    app.register_blueprint(dictionary_bp)
    app.register_blueprint(vocab_bp)
    app.register_blueprint(structures_bp)
    app.register_blueprint(phrases_bp)
    app.register_blueprint(tts_bp)


def _register_auth_gate(app: Flask) -> None:
    """Gate every ``/api/*`` route (except the auth blueprint's own endpoints)
    behind a session cookie when ``LANGLEARN_PASSWORD`` is set. Static assets,
    the SPA, and ``/api/auth/*`` are always reachable."""
    _EXEMPT_API = (
        "/api/auth/status",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/whoami",
    )

    @app.before_request
    def _enforce_auth():
        if not auth_gate.is_auth_enabled():
            return None
        path = request.path
        if not path.startswith("/api/"):
            return None
        if path in _EXEMPT_API:
            return None
        if auth_gate.is_authenticated(request):
            return None
        resp = jsonify(err("unauthorized", code="unauthorized"))
        resp.status_code = 401
        return resp


def main() -> None:
    app = create_app()
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)


if __name__ == "__main__":
    main()