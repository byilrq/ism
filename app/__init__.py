from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import os
import yaml

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

def _load_cfg():
    path = os.path.join(BASE_DIR, "config.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    else:
        cfg = {}
    _mysql = cfg.get("mysql", {})
    cfg.setdefault("upload_folder", os.path.join(BASE_DIR, "app", "uploads"))
    upload_folder = str(cfg.get("upload_folder") or "").strip()
    if not upload_folder or not os.path.isabs(upload_folder):
        raise ValueError("config.yaml upload_folder must be an absolute path")
    cfg["upload_folder"] = upload_folder
    cfg.setdefault("secret_key", "e345ede60e6e")
    cfg.setdefault("max_content_length", 20 * 1024 * 1024)
    cfg.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    cfg["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL") or \
        f"mysql+pymysql://{_mysql.get('user', 'asset_user')}:{_mysql.get('password', 'by123')}@{_mysql.get('host', 'localhost')}/{_mysql.get('database', 'ism')}"
    cfg["UPLOAD_FOLDER"] = cfg.get("upload_folder")
    cfg["SECRET_KEY"] = os.environ.get("SECRET_KEY") or cfg["secret_key"]
    cfg["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH") or cfg["max_content_length"])
    if cfg["MAX_CONTENT_LENGTH"] <= 0:
        raise ValueError("MAX_CONTENT_LENGTH must be a positive number of bytes")
    cfg["BASE_DIR"] = BASE_DIR
    return cfg

_app_cfg = _load_cfg()

class FlaskConfig:
    SECRET_KEY = _app_cfg["SECRET_KEY"]
    SQLALCHEMY_DATABASE_URI = _app_cfg["SQLALCHEMY_DATABASE_URI"]
    SQLALCHEMY_TRACK_MODIFICATIONS = _app_cfg.get("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    UPLOAD_FOLDER = _app_cfg["UPLOAD_FOLDER"]
    MAX_CONTENT_LENGTH = _app_cfg["MAX_CONTENT_LENGTH"]
    BASE_DIR = _app_cfg["BASE_DIR"]

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"

def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(FlaskConfig)
    if test_config:
        app.config.update(test_config)
    app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    })

    db.init_app(app)
    login_manager.init_app(app)

    from app.routes import register_routes
    register_routes(app)
    from app.image_uploads import register_image_uploads
    register_image_uploads(app)

    return app
