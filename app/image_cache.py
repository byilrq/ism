"""Local read cache for ISM detail-page images.

Detail pages register a short-lived lease after render.  The background image
worker prefetches the lease's images from the configured storage root into
/root/ism/imgcache/view_cache.  /uploads serves the local copy first when it is
available, so repeated viewing does not touch rclone/pCloud.

Leases are best-effort: pagehide releases them, a heartbeat keeps live pages
active, and stale leases expire automatically.  Cached files are disposable;
upload_spool remains a separate durable queue and is never cleaned by this
module.
"""
from datetime import UTC, datetime
from pathlib import Path
import json
import os
import re
import time
import uuid

from flask import abort, current_app, jsonify, request
from sqlalchemy.exc import IntegrityError

from app import db

IMAGE_SUBDIRS = frozenset(("assets", "accessories", "asset_locations"))
IMAGE_EXTENSIONS = frozenset(("jpg", "jpeg", "png", "webp"))
CACHE_ROOT_NAME = "imgcache"
VIEW_CACHE_NAME = "view_cache"
# Keep a released detail page warm for five minutes.  The release endpoint
# refreshes the cache mtime so this TTL is measured from page exit, not from
# the time the image happened to be prefetched or last opened.
CACHE_TTL_SECONDS = 5 * 60
LEASE_STALE_SECONDS = 5 * 60
CACHE_MAX_BYTES = 300 * 1024 * 1024
HEARTBEAT_SECONDS = 60


def _utc_naive_now():
    return datetime.now(UTC).replace(tzinfo=None)


class ImageCacheTask(db.Model):
    """One disposable prefetch job per database-relative image path."""
    __tablename__ = "ism_image_cache_tasks"

    id = db.Column(db.Integer, primary_key=True)
    relative_path = db.Column(db.String(500), nullable=False, unique=True, index=True)
    state = db.Column(db.String(20), nullable=False, default="pending", index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_naive_now, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utc_naive_now, index=True)
    next_retry_at = db.Column(db.DateTime, nullable=True, index=True)


class ImageViewLease(db.Model):
    """A browser detail-page lease protecting images from TTL/LRU eviction."""
    __tablename__ = "ism_image_view_leases"

    page_id = db.Column(db.String(64), primary_key=True)
    scope_type = db.Column(db.String(20), nullable=False, index=True)
    scope_key = db.Column(db.String(255), nullable=False)
    image_paths = db.Column(db.Text, nullable=False, default="[]")
    created_at = db.Column(db.DateTime, nullable=False, default=_utc_naive_now, index=True)
    last_seen = db.Column(db.DateTime, nullable=False, default=_utc_naive_now, index=True)


def validate_cache_relative(relative_path):
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


def cache_root():
    root = Path(current_app.config.get("BASE_DIR") or "/root/ism") / CACHE_ROOT_NAME / VIEW_CACHE_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_path(relative_path):
    relative_path = validate_cache_relative(relative_path)
    return cache_root() / relative_path


def cached_file(relative_path, touch=False):
    try:
        path = cache_path(relative_path)
        if not path.is_file():
            return None
        if touch:
            try:
                os.utime(path, None)
            except OSError:
                pass
        return path
    except (OSError, ValueError):
        return None


def remove_cached_file(relative_path):
    try:
        path = cache_path(relative_path)
        path.unlink(missing_ok=True)
        # Empty subdirectories are optional; leave them for low-cost reuse.
        return True
    except (OSError, ValueError):
        return False


def cancel_cache_task(relative_path):
    """Cancel queued prefetch inside the caller's current DB transaction."""
    try:
        relative_path = validate_cache_relative(relative_path)
    except ValueError:
        return 0
    return ImageCacheTask.query.filter_by(relative_path=relative_path).delete(synchronize_session=False)


def invalidate_cached_image(relative_path, cancel_task=True):
    if cancel_task:
        cancel_cache_task(relative_path)
    remove_cached_file(relative_path)


def queue_cache_paths(relative_paths):
    """Queue missing cache files without touching the remote storage in this request."""
    now = _utc_naive_now()
    queued = 0
    for raw in relative_paths:
        try:
            relative = validate_cache_relative(raw)
        except ValueError:
            continue
        hit = cached_file(relative, touch=True)
        if hit is not None:
            continue
        row = ImageCacheTask.query.filter_by(relative_path=relative).first()
        if row is not None:
            # A failed cache fill may be retried immediately when a user opens
            # the page again. Never disturb an actively caching row.
            if row.state == "failed":
                row.state = "pending"
                row.next_retry_at = now
                row.updated_at = now
                row.last_error = ""
            continue
        try:
            with db.session.begin_nested():
                db.session.add(ImageCacheTask(
                    relative_path=relative,
                    state="pending",
                    attempts=0,
                    created_at=now,
                    updated_at=now,
                    next_retry_at=now,
                ))
                db.session.flush()
            queued += 1
        except IntegrityError:
            # Another Gunicorn worker opened the same detail page first.
            pass
    return queued


def queue_cache_path(relative_path, commit=True):
    queued = queue_cache_paths([relative_path])
    if commit:
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            return 0
    return queued


def _resolve_scope_images(scope_type, scope_key):
    from app.models import AssetImage, AccessoryImage
    from app.routes.upload import AssetLocationImage

    scope_type = str(scope_type or "").strip().lower()
    if scope_type == "asset":
        if not str(scope_key).isdigit():
            abort(400)
        rows = AssetImage.query.filter_by(asset_id=int(scope_key)).order_by(AssetImage.id.asc()).all()
    elif scope_type == "accessory":
        if not str(scope_key).isdigit():
            abort(400)
        rows = AccessoryImage.query.filter_by(accessory_id=int(scope_key)).order_by(AccessoryImage.id.asc()).all()
    elif scope_type == "location":
        key = str(scope_key or "").strip()
        if not key or len(key) > 255:
            abort(400)
        rows = AssetLocationImage.query.filter(
            db.func.upper(AssetLocationImage.location_name) == key.upper()
        ).order_by(AssetLocationImage.id.asc()).all()
    else:
        abort(400)

    result = []
    for row in rows:
        try:
            result.append(validate_cache_relative(row.image_path))
        except ValueError:
            continue
    return scope_type, str(scope_key), result


def _json_paths(value):
    try:
        data = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        try:
            result.append(validate_cache_relative(item))
        except ValueError:
            continue
    return result


def active_leased_paths(now=None):
    from datetime import timedelta
    now = now or _utc_naive_now()
    cutoff = now - timedelta(seconds=LEASE_STALE_SECONDS)
    paths = set()
    for lease in ImageViewLease.query.filter(ImageViewLease.last_seen >= cutoff).all():
        paths.update(_json_paths(lease.image_paths))
    return paths


def register_image_cache(app):
    from app.routes import ensure_read_access

    @app.route("/image-cache/open", methods=["POST"])
    def image_cache_open():
        guard = ensure_read_access()
        if guard:
            return guard
        payload = request.get_json(silent=True) or {}
        scope_type, scope_key, paths = _resolve_scope_images(payload.get("scope"), payload.get("key"))
        page_id = uuid.uuid4().hex
        now = _utc_naive_now()
        db.session.add(ImageViewLease(
            page_id=page_id,
            scope_type=scope_type,
            scope_key=scope_key,
            image_paths=json.dumps(paths, ensure_ascii=False, separators=(",", ":")),
            created_at=now,
            last_seen=now,
        ))
        queue_cache_paths(paths)
        db.session.commit()
        response = jsonify(ok=True, page_id=page_id, images=len(paths), heartbeat=HEARTBEAT_SECONDS)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/image-cache/heartbeat/<page_id>", methods=["POST"])
    def image_cache_heartbeat(page_id):
        guard = ensure_read_access()
        if guard:
            return guard
        if not re.fullmatch(r"[A-Fa-f0-9]{32}", page_id):
            abort(404)
        lease = db.session.get(ImageViewLease, page_id)
        if lease is None:
            return jsonify(ok=False, expired=True), 404
        lease.last_seen = _utc_naive_now()
        db.session.commit()
        response = jsonify(ok=True)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/image-cache/release/<page_id>", methods=["POST"])
    def image_cache_release(page_id):
        guard = ensure_read_access()
        if guard:
            return guard
        if not re.fullmatch(r"[A-Fa-f0-9]{32}", page_id):
            abort(404)
        lease = db.session.get(ImageViewLease, page_id)
        if lease is not None:
            # Start the short post-exit grace period now.  Without this touch,
            # a page that stayed open longer than CACHE_TTL_SECONDS could have
            # its cache evicted almost immediately after the user presses Back.
            # If another tab still has an active lease, cleanup protects the
            # same files independently until that final lease is released.
            for relative_path in _json_paths(lease.image_paths):
                cached_file(relative_path, touch=True)
            db.session.delete(lease)
            db.session.commit()
        return ("", 204)
