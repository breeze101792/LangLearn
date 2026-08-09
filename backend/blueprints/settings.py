"""Settings blueprint."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import config
from ..services import settings as settings_svc
from ..util import err, ok

bp = Blueprint("settings", __name__, url_prefix="/api/settings")


@bp.get("")
def get_settings():
    return ok(settings_svc.get_settings(config.DEFAULT_USER_ID))


@bp.put("")
def update_settings():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(err("body must be a JSON object")), 400
    try:
        updated = settings_svc.update_settings(body, config.DEFAULT_USER_ID)
    except ValueError as e:
        return jsonify(err(str(e))), 400
    return ok(updated)