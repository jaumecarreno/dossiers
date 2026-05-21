from __future__ import annotations

from flask import Flask
from dotenv import load_dotenv

from app.config import Config
from app.extensions import db


def create_app(config_overrides: dict | None = None) -> Flask:
    load_dotenv()

    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)

    from app.routes import bp as main_bp

    app.register_blueprint(main_bp)
    return app
