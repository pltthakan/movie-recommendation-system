# app/__init__.py
import warnings
from flask import Flask
from dotenv import load_dotenv

from .config import load_config
from .db import init_db
from .services.events import register_event_logging

from .blueprints.pages import bp as pages_bp
from .blueprints.auth import bp as auth_bp
from .blueprints.api import bp as api_bp

def create_app():
    load_dotenv()

    app = Flask(__name__, template_folder="templates", static_folder="static")
    load_config(app)
    app.secret_key = app.config["SECRET_KEY"]

    warnings.filterwarnings(
        "ignore",
        message="`clean_up_tokenization_spaces` was not set.*",
        category=FutureWarning,
    )

    init_db()
    register_event_logging(app)

    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)

    return app
