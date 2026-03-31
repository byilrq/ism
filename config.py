import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "e345ede60e6e")
    SQLALCHEMY_DATABASE_URI = "mysql+pymysql://asset_user:by123@localhost/asset_manager"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "app", "uploads", "images")
    MAX_CONTENT_LENGTH = 20 * 1024 * 1024
