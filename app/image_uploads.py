"""Original-image uploads: transactional deduplication and retry receipts.

No decoder, resize, recompression or metadata stripping is used. Scope locks
live in the database so two Gunicorn processes share the same exclusion lock.
"""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
import json
import mimetypes
import os
import re
import shutil
import tempfile
import uuid
import random
import string

from flask import (Response, abort, current_app, g, has_request_context,
                   jsonify, redirect, request, send_from_directory, session)
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app import db

IMAGE_SUBDIRS = frozenset(("assets", "accessories", "asset_locations"))
IMAGE_EXTENSIONS = frozenset(("jpg", "jpeg", "png", "webp"))
UPLOAD_ENDPOINTS = frozenset(("device_new", "asset_detail", "accessory_detail",
                              "asset_location_detail"))
CHUNK_SIZE = 1024 * 1024
IMAGE_CACHE_SECONDS = 30 * 24 * 60 * 60


def _utc_naive_now():
    return datetime.now(UTC).replace(tzinfo=None)


class ImageUploadScope(db.Model):
    """One row lock per owner; cached hashes apply only to that owner's images."""
    __tablename__ = "ism_image_upload_scopes"
    scope_key = db.Column(db.String(64), primary_key=True)
    fingerprints = db.Column(db.Text, nullable=False, default="{}")


class ImageUploadSubmission(db.Model):
    """A successful form submission is committed with its retry receipt."""
    __tablename__ = "ism_image_upload_submissions"
    submission_key = db.Column(db.String(64), primary_key=True)
    payload_hash = db.Column(db.String(64), nullable=False)
    redirect_url = db.Column(db.String(1000), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_naive_now, index=True)


class ImageSyncTask(db.Model):
    """Durable local-spool -> configured storage synchronization task."""
    __tablename__ = "ism_image_sync_tasks"

    id = db.Column(db.Integer, primary_key=True)
    relative_path = db.Column(db.String(500), nullable=False, unique=True, index=True)
    spool_name = db.Column(db.String(128), nullable=False, unique=True)
    sha256 = db.Column(db.String(64), nullable=False)
    size_bytes = db.Column(db.BigInteger, nullable=False)
    state = db.Column(db.String(20), nullable=False, default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    failure_logged = db.Column(db.Boolean, nullable=False, default=False)
    device_type = db.Column(db.String(20), nullable=False, default="主设备")
    device_id = db.Column(db.Integer, nullable=True)
    group_no = db.Column(db.String(128), nullable=True)
    asset_no = db.Column(db.String(128), nullable=False, default="")
    asset_name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_naive_now, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utc_naive_now, index=True)
    next_retry_at = db.Column(db.DateTime, nullable=True, index=True)
    synced_at = db.Column(db.DateTime, nullable=True)


def _lock_row(model, key_name, key, **initial):
    """Atomic insert-or-lock. Never use a process-local mutex for this."""
    table = model.__table__
    values = {key_name: key, **initial}
    dialect = db.session.get_bind().dialect.name
    if dialect in ("mysql", "mariadb"):
        from sqlalchemy.dialects.mysql import insert
        statement = insert(table).values(**values).on_duplicate_key_update(**{key_name: key})
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
        # SQLite serializes writers; useful for tests and small local deployments.
        statement = insert(table).values(**values).on_conflict_do_nothing(index_elements=[key_name])
    else:
        raise RuntimeError("Image upload locking supports MySQL/MariaDB and SQLite only")
    db.session.execute(statement)
    # A locking/current read avoids stale snapshots under MySQL REPEATABLE READ.
    return db.session.execute(
        select(model).where(getattr(model, key_name) == key).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()


def file_digest(file_storage):
    cached = getattr(file_storage, "_ism_sha256", None)
    if cached:
        return cached
    stream = file_storage.stream
    previous = stream.tell()
    stream.seek(0)
    digest = sha256()
    size = 0
    try:
        while True:
            chunk = stream.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    finally:
        stream.seek(previous)
    result = (digest.hexdigest(), size)
    file_storage._ism_sha256 = result
    return result


def _upload_root():
    # Do not resolve through a possibly stale FUSE mount here.  Validation of
    # relative paths is lexical; actual I/O is allowed to fail in the worker.
    return Path(os.path.abspath(str(current_app.config["UPLOAD_FOLDER"])))


def _spool_root():
    root = Path(current_app.config.get("BASE_DIR") or "/root/ism") / "upload_spool"
    root.mkdir(parents=True, exist_ok=True)
    return root


def spool_path(spool_name):
    name = str(spool_name or "")
    if not re.fullmatch(r"[A-Fa-f0-9]{32}\.ready", name):
        raise ValueError("Invalid spool file")
    return _spool_root() / name


def _validate_image_relative(relative_path):
    """Validate the database-relative image path without choosing a storage root."""
    if not isinstance(relative_path, str) or "\\" in relative_path:
        raise ValueError("Invalid image path")
    parts = relative_path.split("/")
    if len(parts) != 2 or parts[0] not in IMAGE_SUBDIRS or parts[1] in ("", ".", ".."): 
        raise ValueError("Invalid image path")
    if any(ord(c) < 32 or ord(c) == 127 for c in relative_path):
        raise ValueError("Invalid image path")
    if parts[1].rsplit(".", 1)[-1].lower() not in IMAGE_EXTENSIONS:
        raise ValueError("Unsupported image type")
    return relative_path


def _path_under(root, relative_path):
    relative_path = _validate_image_relative(relative_path)
    root = Path(os.path.abspath(str(root)))
    # _validate_image_relative permits exactly <known-subdir>/<filename>, so no
    # filesystem resolve() is needed.  Avoiding resolve is important when the
    # configured rclone/FUSE tree is temporarily returning EIO.
    return root / relative_path


def _compatible_upload_roots():
    """Return current root plus the historical local-root spelling.

    Older ISM management scripts used /app/uploads/images while some release
    configs used /app/uploads. Database rows store only paths such as
    assets/foo.jpg, so a config overwrite could make every historical image
    appear missing even though the files were still on disk. Keep both roots
    readable without moving or renaming user files.
    """
    primary = _upload_root()
    roots = [primary]
    app_uploads = Path(os.path.abspath(str(Path(current_app.root_path) / "uploads")))
    app_images = app_uploads / "images"
    if primary == app_uploads:
        roots.append(app_images)
    elif primary == app_images:
        roots.append(app_uploads)
    # Preserve order and avoid duplicates. Never probe arbitrary external paths.
    result = []
    seen = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            result.append(root)
    return result


def _safe_image_path(relative_path):
    """Primary path used for new writes."""
    return _path_under(_upload_root(), relative_path)


def _existing_image_path(relative_path):
    """Find an existing image in current or historical local storage root."""
    relative_path = _validate_image_relative(relative_path)
    for root in _compatible_upload_roots():
        path = _path_under(root, relative_path)
        try:
            if path.is_file():
                return path, root
        except OSError:
            # A stale FUSE mount may still be mounted while all child stats
            # return EIO.  Pending uploads must remain usable from local spool.
            continue
    # Return the primary location for consistent missing-file handling.
    root = _upload_root()
    return _path_under(root, relative_path), root


def _disk_digest(relative_path, cache):
    entry = cache.get(relative_path)
    if isinstance(entry, dict) and entry.get("sha256"):
        # New uploads persist their content digest in the scope cache before
        # cloud synchronization, so dedup never needs to wait on rclone.
        if "stat" not in entry:
            return entry.get("sha256")

    task = ImageSyncTask.query.filter_by(relative_path=relative_path).order_by(ImageSyncTask.id.desc()).first()
    if task is not None and task.sha256:
        cache[relative_path] = {"sha256": task.sha256, "size": int(task.size_bytes or 0)}
        return task.sha256

    path, _root = _existing_image_path(relative_path)
    try:
        stat_result = path.stat()
    except OSError:
        # Do not make a new local upload fail merely because historical remote
        # images cannot currently be read.  New-image dedup remains exact.
        return entry.get("sha256") if isinstance(entry, dict) else None
    signature = [stat_result.st_size, stat_result.st_mtime_ns]
    if isinstance(entry, dict) and entry.get("stat") == signature:
        return entry.get("sha256")
    digest = sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
                digest.update(chunk)
    except OSError:
        return entry.get("sha256") if isinstance(entry, dict) else None
    value = digest.hexdigest()
    cache[relative_path] = {"sha256": value, "stat": signature}
    return value


def _task_identity(model, owner_key):
    from app.models import Asset, Accessory
    if model.__tablename__ == "asset_images":
        obj = db.session.get(Asset, int(owner_key))
        if obj is not None:
            return {
                "device_type": "主设备", "device_id": obj.id,
                "group_no": (obj.group_no or "").strip(),
                "asset_no": (obj.internal_no or obj.group_no or f"ID:{obj.id}").strip(),
                "asset_name": (obj.name or "").strip(),
            }
    elif model.__tablename__ == "accessory_images":
        obj = db.session.get(Accessory, int(owner_key))
        if obj is not None:
            return {
                "device_type": "配件", "device_id": obj.id,
                "group_no": (obj.sub_group_no or "").strip(),
                "asset_no": (obj.sub_internal_no or obj.sub_group_no or f"ID:{obj.id}").strip(),
                "asset_name": (obj.name or "").strip(),
            }
    location = str(owner_key or "").strip()
    return {
        "device_type": "货架", "device_id": None, "group_no": location,
        "asset_no": location or "货架", "asset_name": "货架图片",
    }


def _stage_save(file_storage, subdir, prefix, expected_digest=None, expected_size=None):
    """Persist one upload on local VPS disk; cloud/mount I/O is deferred."""
    if subdir not in IMAGE_SUBDIRS:
        raise ValueError("Invalid upload folder")
    extension = file_storage.filename.rsplit(".", 1)[-1].lower()
    if extension not in IMAGE_EXTENSIONS:
        raise ValueError("仅支持 JPG、JPEG、PNG 和 WebP 原图")
    prefix = re.sub(r"[^A-Za-z0-9_-]+", "_", str(prefix)).strip("._-") or "asset"
    random_part = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    filename = f"{prefix}.{datetime.now():%Y.%m.%d}.{random_part}.{extension}"
    relative = f"{subdir}/{filename}"

    spool_name = uuid.uuid4().hex + ".ready"
    final_spool = spool_path(spool_name)
    temporary = final_spool.with_suffix(".part")
    digest = sha256()
    size = 0
    try:
        file_storage.stream.seek(0)
        with temporary.open("wb") as handle:
            while True:
                chunk = file_storage.stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        actual_digest = digest.hexdigest()
        if expected_size is not None and int(expected_size) != size:
            raise OSError("上传暂存文件大小校验失败")
        if expected_digest and str(expected_digest) != actual_digest:
            raise OSError("上传暂存文件内容校验失败")
        os.replace(temporary, final_spool)
    except BaseException:
        temporary.unlink(missing_ok=True)
        final_spool.unlink(missing_ok=True)
        raise
    finally:
        file_storage.stream.seek(0)
    db.session.info.setdefault("ism_new_spool_files", []).append(str(final_spool))
    return relative, spool_name, actual_digest, size


def _cancel_pending_sync(relative_path):
    tasks = ImageSyncTask.query.filter(
        ImageSyncTask.relative_path == relative_path,
        ImageSyncTask.state != "done",
    ).all()
    for task in tasks:
        try:
            db.session.info.setdefault("ism_delete_spool_files", []).append(str(spool_path(task.spool_name)))
        except ValueError:
            pass
        db.session.delete(task)


def _atomic_save(file_storage, subdir, prefix):
    if subdir not in IMAGE_SUBDIRS:
        raise ValueError("Invalid upload folder")
    extension = file_storage.filename.rsplit(".", 1)[-1].lower()
    if extension not in IMAGE_EXTENSIONS:
        raise ValueError("\u4ec5\u652f\u6301 JPG\u3001JPEG\u3001PNG \u548c WebP \u539f\u56fe")
    # Keep the historical ISM filename convention exactly:
    # <asset/location prefix>.YYYY.MM.DD.<6 alnum>.<ext>
    # Content deduplication is SHA-256 based and does not depend on filenames.
    prefix = re.sub(r"[^A-Za-z0-9_-]+", "_", str(prefix)).strip("._-")
    if not prefix:
        prefix = "asset"
    random_part = "".join(random.choices(string.ascii_letters + string.digits, k=6))
    filename = f"{prefix}.{datetime.now():%Y.%m.%d}.{random_part}.{extension}"
    relative = f"{subdir}/{filename}"
    path = _safe_image_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A half-written file is never exposed under the final URL.
    fd, temporary = tempfile.mkstemp(prefix=".ism-upload-", suffix=".part", dir=path.parent)
    try:
        file_storage.stream.seek(0)
        with os.fdopen(fd, "wb") as handle:
            shutil.copyfileobj(file_storage.stream, handle, CHUNK_SIZE)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    finally:
        file_storage.stream.seek(0)
    db.session.info.setdefault("ism_new_image_files", []).append(str(path))
    return relative


def _schedule_delete(relative_path, recycle=False):
    _cancel_pending_sync(relative_path)
    path, root = _existing_image_path(relative_path)
    db.session.info.setdefault("ism_delete_image_files", []).append(
        (str(path), str(root), relative_path, bool(recycle))
    )


@event.listens_for(Session, "after_commit")
def _after_commit(session_obj):
    # This module does not use savepoints. Do not finalize an outer transaction
    # in response to somebody else's nested transaction commit.
    if session_obj.in_nested_transaction():
        return
    session_obj.info.pop("ism_new_image_files", None)
    # New uploads live in local spool after commit; the background worker owns them.
    session_obj.info.pop("ism_new_spool_files", None)
    for spool_file in session_obj.info.pop("ism_delete_spool_files", []):
        try:
            Path(spool_file).unlink(missing_ok=True)
        except OSError:
            if has_request_context():
                current_app.logger.exception("Cancelled spool cleanup failed: %s", spool_file)
    for filename, root, relative, recycle in session_obj.info.pop("ism_delete_image_files", []):
        try:
            source = Path(filename)
            if not source.is_file():
                continue
            if recycle:
                target = Path(root) / "recycle" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target = target.with_name(f"{target.stem}.{uuid.uuid4().hex[:8]}{target.suffix}")
                shutil.move(str(source), str(target))
                # Recycle retention starts at deletion time, not the photo's
                # original filesystem mtime.
                os.utime(target, None)
            else:
                source.unlink(missing_ok=True)
        except OSError:
            # The database is already committed. Never tell the client that a
            # successful upload failed merely because old-file cleanup failed.
            if has_request_context():
                current_app.logger.exception("Old image cleanup failed: %s", filename)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session_obj):
    if session_obj.in_nested_transaction():
        return
    session_obj.info.pop("ism_delete_image_files", None)
    session_obj.info.pop("ism_delete_spool_files", None)
    for filename in session_obj.info.pop("ism_new_spool_files", []):
        try:
            Path(filename).unlink(missing_ok=True)
        except OSError:
            if has_request_context():
                current_app.logger.exception("Uncommitted spool cleanup failed: %s", filename)
    for filename in session_obj.info.pop("ism_new_image_files", []):
        try:
            Path(filename).unlink(missing_ok=True)
        except OSError:
            if has_request_context():
                current_app.logger.exception("Uncommitted image cleanup failed: %s", filename)


def update_images(model, owner_field, owner_key, files, subdir, prefix,
                  delete_ids=(), location_casefold=False):
    """Update one owner's images, at most five; leave historical duplicates alone.

    Both the scope row and the actual image rows are locked until the caller's
    commit. Identical bytes on different owners are intentionally independent.
    """
    scope_name = f"{model.__tablename__}:{str(owner_key).upper() if location_casefold else owner_key}"
    scope = _lock_row(ImageUploadScope, "scope_key", sha256(scope_name.encode()).hexdigest(), fingerprints="{}")
    try:
        cache = json.loads(scope.fingerprints or "{}")
        if not isinstance(cache, dict):
            cache = {}
    except (TypeError, ValueError):
        cache = {}
    column = getattr(model, owner_field)
    condition = db.func.upper(column) == str(owner_key).upper() if location_casefold else column == owner_key
    images = list(db.session.execute(
        select(model).where(condition).order_by(model.created_at.asc(), model.id.asc())
        .with_for_update().execution_options(populate_existing=True)
    ).scalars())
    delete_set = {int(value) for value in delete_ids if str(value).isdigit()}
    deleted = 0
    remaining = []
    for image in images:
        if image.id in delete_set:
            deleted += 1
            _schedule_delete(image.image_path, recycle=True)
            db.session.delete(image)
        else:
            remaining.append(image)
    known = set()
    for image in remaining:
        digest = _disk_digest(image.image_path, cache)
        if digest:
            known.add(digest)
    incoming = [item for item in files if item and item.filename]
    if len(incoming) > 5:
        raise ValueError("\u6bcf\u6b21\u6700\u591a\u4e0a\u4f20 5 \u5f20\u56fe\u7247")
    saved = skipped = 0
    for item in incoming:
        extension = item.filename.rsplit(".", 1)[-1].lower()
        if "." not in item.filename or extension not in IMAGE_EXTENSIONS:
            raise ValueError("\u4ec5\u652f\u6301 JPG\u3001JPEG\u3001PNG \u548c WebP \u539f\u56fe")
        digest, size = file_digest(item)
        if size == 0:
            raise ValueError("\u56fe\u7247\u6587\u4ef6\u4e3a\u7a7a\uff0c\u8bf7\u91cd\u65b0\u9009\u62e9")
        if digest in known:
            skipped += 1
            continue
        relative, spool_name, actual_digest, staged_size = _stage_save(
            item, subdir, prefix, expected_digest=digest, expected_size=size
        )
        image = model(**{owner_field: owner_key, "image_path": relative})
        db.session.add(image)
        identity = _task_identity(model, owner_key)
        db.session.add(ImageSyncTask(
            relative_path=relative, spool_name=spool_name, sha256=actual_digest,
            size_bytes=staged_size, state="pending", attempts=0,
            created_at=_utc_naive_now(), updated_at=_utc_naive_now(), **identity
        ))
        remaining.append(image)
        cache[relative] = {"sha256": actual_digest, "size": staged_size}
        known.add(actual_digest)
        saved += 1
    while len(remaining) > 5:
        old = remaining.pop(0)
        _schedule_delete(old.image_path, recycle=False)
        db.session.delete(old)
    scope.fingerprints = json.dumps({img.image_path: cache[img.image_path] for img in remaining if img.image_path in cache})
    if has_request_context():
        g.ism_uploaded_count = getattr(g, "ism_uploaded_count", 0) + saved
        g.ism_queued_count = getattr(g, "ism_queued_count", 0) + saved
        g.ism_duplicate_count = getattr(g, "ism_duplicate_count", 0) + skipped
    return {"saved": saved, "queued": saved, "duplicates": skipped, "deleted": deleted}


def _wants_json():
    # Some reverse proxies/security layers may drop non-standard X-* headers.
    # The uploader also sends Accept: application/json, so either signal is
    # sufficient.  Returning JSON avoids an unnecessary 303 round-trip after
    # a successful multi-megabyte upload.
    return (request.headers.get("X-ISM-Upload") == "1"
            or "application/json" in (request.headers.get("Accept") or "").lower())


def _success_response(destination, repeated=False):
    if _wants_json():
        return jsonify(ok=True, redirect_url=destination, repeated=repeated,
                       uploaded=getattr(g, "ism_uploaded_count", 0),
                       queued=getattr(g, "ism_queued_count", 0),
                       duplicates=getattr(g, "ism_duplicate_count", 0))
    return redirect(destination, code=303)


def finish_upload(destination):
    """Single commit for the business data, image records, and retry receipt."""
    receipt = getattr(g, "ism_upload_receipt", None)
    if receipt is not None:
        receipt.redirect_url = destination
    db.session.commit()
    g.ism_upload_saved = True
    return _success_response(destination)


def register_image_uploads(app):
    @app.before_request
    def _protect_upload_retry():
        if request.method != "POST" or request.endpoint not in UPLOAD_ENDPOINTS:
            return None
        if not request.mimetype == "multipart/form-data":
            return None
        # Check the signed session before hashing large bodies or touching SQL.
        if not session.get("_user_id") and session.get("visitor_role") != "editor":
            return None
        token = request.form.get("_upload_request_id", "")
        if not token:
            # Older clients are still protected by per-owner content dedup.
            return None
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", token):
            abort(400, description="Invalid upload request identifier")
        digest = sha256()
        fields = sorted((key, request.form.getlist(key)) for key in request.form if key != "_upload_request_id")
        digest.update(json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode())
        for key, item in request.files.items(multi=True):
            if item and item.filename:
                value, size = file_digest(item)
                digest.update(json.dumps([key, value, size], separators=(",", ":")).encode())
        identity = str(session.get("_user_id") or ("visitor:" + session.get("visitor_role", "")))
        key = sha256(json.dumps([identity, request.endpoint, request.path, token]).encode()).hexdigest()
        receipt = _lock_row(ImageUploadSubmission, "submission_key", key,
                            payload_hash=digest.hexdigest(), created_at=_utc_naive_now())
        if receipt.payload_hash != digest.hexdigest():
            db.session.rollback()
            message = "\u672c\u6b21\u63d0\u4ea4\u5185\u5bb9\u5df2\u53d8\u5316\uff0c\u8bf7\u91cd\u65b0\u70b9\u51fb\u786e\u8ba4"
            return jsonify(ok=False, error=message, renew_token=True), 409
        if receipt.redirect_url:
            destination = receipt.redirect_url
            db.session.rollback()  # Release the no-op upsert/lock immediately.
            return _success_response(destination, repeated=True)
        g.ism_upload_receipt = receipt
        return None

    @app.after_request
    def _private_upload_responses(response):
        if request.method == "POST" and request.endpoint in UPLOAD_ENDPOINTS:
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.teardown_request
    def _rollback_unfinished_upload(_error):
        # Release retry/scope locks on validation errors (including HTTP 200
        # HTML error forms) and clean files staged before any failure.
        if (getattr(g, "ism_upload_receipt", None) is not None or db.session.info.get("ism_new_image_files") or db.session.info.get("ism_new_spool_files") or db.session.info.get("ism_delete_image_files")) and not getattr(g, "ism_upload_saved", False):
            db.session.rollback()

    @app.errorhandler(413)
    def _upload_too_large(_error):
        limit = int(current_app.config["MAX_CONTENT_LENGTH"]) / 1024 / 1024
        message = f"\u4e0a\u4f20\u8bf7\u6c42\u8d85\u8fc7 {limit:g} MB\uff0c\u8bf7\u5206\u6279\u4e0a\u4f20\u539f\u56fe"
        if _wants_json():
            return jsonify(ok=False, error=message), 413
        return message, 413


def serve_image(filename):
    from app.routes import ensure_read_access
    guard = ensure_read_access()
    if guard:
        return guard
    try:
        path, actual_root = _existing_image_path(filename)
    except (TypeError, ValueError):
        abort(404)
    try:
        exists = path.is_file()
    except OSError:
        exists = False
    if not exists:
        task = ImageSyncTask.query.filter_by(relative_path=filename).order_by(ImageSyncTask.id.desc()).first()
        if task is None:
            abort(404)
        try:
            pending = spool_path(task.spool_name)
            if not pending.is_file():
                abort(404)
        except (OSError, ValueError):
            abort(404)
        response = send_from_directory(str(pending.parent), pending.name, conditional=True, max_age=0)
        response.mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    primary_root = _upload_root()
    # X-Accel-Redirect is configured only for the active root. Historical
    # local-root images fall back to Flask so old and new layouts can coexist.
    if (actual_root == primary_root
            and request.headers.get("X-ISM-Accel") == "1"
            and request.headers.get("X-ISM-Accel-Root") == sha256(str(primary_root).encode()).hexdigest()
            and request.remote_addr in ("127.0.0.1", "::1")):
        response = Response(mimetype=mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        response.headers["X-Accel-Redirect"] = "/_ism_media/" + quote(filename, safe="/")
        response.headers["Vary"] = "Cookie"
        return response
    response = send_from_directory(str(actual_root), filename, conditional=True, max_age=IMAGE_CACHE_SECONDS)
    response.headers["Cache-Control"] = f"private, max-age={IMAGE_CACHE_SECONDS}, immutable"
    response.headers["Vary"] = "Cookie"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response
