"""Flask app factory + entry point.

Boot via:
    python -m backend.app
or use ./start.sh which sets up the venv.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, jsonify, send_from_directory

from . import config
from .db import init_schema
from .services.dictionaries import registry as dict_registry
from .services import settings as settings_svc
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

    init_schema()
    dict_registry.bootstrap()
    settings_svc.create_default_settings()
    _register_blueprints(app)

    @app.get("/")
    def index():
        return send_from_directory(config.TEMPLATES_DIR, "index.html")

    @app.get("/manifest.json")
    def manifest():
        return send_from_directory(config.STATIC_DIR, "manifest.json")

    @app.get("/sw.js")
    def service_worker():
        resp = send_from_directory(config.STATIC_DIR, "sw.js")
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

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

    app.register_blueprint(auth_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(languages_bp)
    app.register_blueprint(dictionary_bp)
    app.register_blueprint(vocab_bp)
    app.register_blueprint(structures_bp)
    app.register_blueprint(phrases_bp)


def main() -> None:
    app = create_app()
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)


if __name__ == "__main__":
    main()