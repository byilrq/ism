from flask import Flask, Request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import os
import json
import io
import hashlib
import uuid
import stat
import time
from datetime import datetime
from pathlib import Path
import yaml

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
UPLOAD_SPOOL_DIR = os.path.join(BASE_DIR, "upload_spool")
_IMAGE_UPLOAD_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


class _HashingUploadStream(io.BufferedRandom):
    """Write an incoming image directly to the durable VPS spool while hashing it.

    Werkzeug writes multipart image bytes into this stream as they arrive.  This
    removes the old second full-file copy/read after the browser finishes the
    upload.  Unclaimed partial files are deleted automatically when the request
    closes; image_uploads._stage_save atomically renames a completed stream to
    its durable .ready name.
    """

    def __init__(self, path):
        raw = io.FileIO(str(path), mode="w+b")
        super().__init__(raw)
        self._ism_spool_path = str(path)
        self._ism_hash = hashlib.sha256()
        self._ism_size = 0
        self._ism_claimed = False
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def write(self, data):
        written = super().write(data)
        if written:
            self._ism_hash.update(memoryview(data)[:written])
            self._ism_size += written
        return written

    @property
    def ism_sha256(self):
        return self._ism_hash.hexdigest()

    @property
    def ism_size(self):
        return self._ism_size

    def close(self):
        if self.closed:
            return
        path = self._ism_spool_path
        claimed = bool(self._ism_claimed)
        try:
            super().close()
        finally:
            if not claimed:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass


class ISMRequest(Request):
    """Stream supported image uploads directly into /root/ism/upload_spool."""

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        name = str(filename or "")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in _IMAGE_UPLOAD_EXTENSIONS:
            os.makedirs(UPLOAD_SPOOL_DIR, mode=0o700, exist_ok=True)
            path = os.path.join(UPLOAD_SPOOL_DIR, f".incoming-{uuid.uuid4().hex}.part")
            return _HashingUploadStream(path)
        return super()._get_file_stream(total_content_length, content_type, filename, content_length)

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



_STORAGE_HEALTH_CACHE = {
    "target": "",
    "checked_at": 0.0,
    "value": {"ok": True, "title": "存储路径正常"},
}
_STORAGE_HEALTH_TTL = 12.0

def _storage_status_for_template(upload_folder):
    """Lightweight cached storage probe used only for the header indicator.

    A real directory stat + directory scan is intentional: mountpoint(1) alone can
    still report a stale FUSE/rclone mount as mounted while children return EIO.
    The short cache avoids touching remote storage on every HTTP request.
    """
    target = os.path.abspath(str(upload_folder or "").strip()) if upload_folder else ""
    now = time.monotonic()
    cached_target = _STORAGE_HEALTH_CACHE.get("target")
    cached_at = float(_STORAGE_HEALTH_CACHE.get("checked_at") or 0.0)
    if target == cached_target and now - cached_at < _STORAGE_HEALTH_TTL:
        return dict(_STORAGE_HEALTH_CACHE.get("value") or {"ok": True, "title": "存储路径正常"})

    ok = True
    title = "存储路径正常"
    try:
        if not target:
            raise OSError("未配置存储路径")
        st = os.stat(target)
        if not stat.S_ISDIR(st.st_mode):
            raise OSError("配置的存储路径不是目录")
        # scandir forces a real directory read. This catches stale FUSE mounts that
        # still appear in findmnt/mountpoint but return EIO for child paths.
        with os.scandir(target) as it:
            next(it, None)
        if not os.access(target, os.R_OK | os.W_OK):
            raise PermissionError("存储路径不可读写")
    except (OSError, ValueError) as exc:
        ok = False
        err_text = str(exc).strip() or exc.__class__.__name__
        title = f"存储路径异常：{err_text}"

    value = {"ok": ok, "title": title}
    _STORAGE_HEALTH_CACHE.update(target=target, checked_at=now, value=value)
    return dict(value)

def _backup_status_for_template(upload_folder):
    """Show the last successful backup date; red means the latest completed v2 run failed.

    Storage health has its own independent red/green indicator. A stale date by itself
    is not a failure. Legacy v16-v24 status files are normalised to green when they
    already contain a successful date; the next completed backup writes schema v2.
    """
    status_file = Path(BASE_DIR) / "backups" / "backup_status.json"
    state = {}
    try:
        if status_file.exists():
            loaded = json.loads(status_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
    except Exception:
        state = {}

    success_at = str(state.get("last_success_at") or "")
    date_text = "--"
    if success_at:
        try:
            date_text = datetime.fromisoformat(success_at).strftime("%m-%d")
        except Exception:
            pass

    try:
        schema_version = int(state.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    last_result = str(state.get("last_result") or "")

    # v25 rule: the date is the last successful snapshot date. It is green normally,
    # red only after a completed backup explicitly failed, and green again on success.
    if schema_version >= 2 and last_result == "failed":
        ok = False
        detail = str(state.get("last_error") or "最近一次备份失败")
    elif success_at:
        ok = True
        if schema_version < 2:
            detail = "最近一次成功备份日期；后续备份若失败将变红"
        elif last_result == "success":
            detail = "最近一次数据库+代码备份成功"
        else:
            detail = "最近一次成功备份日期"
    else:
        ok = False
        detail = "尚无成功备份记录"

    return {
        "date": date_text,
        "ok": ok,
        "title": detail,
    }


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
    app.request_class = ISMRequest
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
    from app.image_cache import register_image_cache
    register_image_cache(app)

    @app.context_processor
    def inject_runtime_status():
        skin_file = Path(app.static_folder or "") / "skin.css"
        try:
            skin_version = str(int(skin_file.stat().st_mtime_ns))
        except OSError:
            skin_version = "1"
        upload_folder = app.config.get("UPLOAD_FOLDER", "")
        return {
            "backup_status": _backup_status_for_template(upload_folder),
            "storage_status": _storage_status_for_template(upload_folder),
            "skin_version": skin_version,
        }

    return app
