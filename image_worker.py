#!/usr/bin/env python3
"""Durable asynchronous image synchronization worker for ISM.

HTTP requests only persist original images to /root/ism/upload_spool and commit a
DB task. This worker copies those originals to config.yaml -> upload_folder.
Transient mount/network errors never delete the local spool copy.
"""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
import os
import shutil
import time
import uuid

from sqlalchemy import or_

from app import create_app, db
from app.image_uploads import ImageSyncTask, _upload_root, _validate_image_relative, spool_path
from app.image_cache import (
    CACHE_MAX_BYTES, CACHE_TTL_SECONDS, LEASE_STALE_SECONDS,
    ImageCacheTask, ImageViewLease, active_leased_paths, cache_path, cache_root,
    cached_file, validate_cache_relative,
)
from app.models import DeviceChangeLog

POLL_SECONDS = 2
FAILURE_LOG_AFTER = 3
COPY_CHUNK = 1024 * 1024


def now_naive():
    return datetime.now(UTC).replace(tzinfo=None)


def log(message, level="INFO"):
    print(f"[{level}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def file_sha256(path):
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(COPY_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def retry_delay(attempts):
    # Fast retries cover a short rclone reconnect; sustained failures back off.
    schedule = (15, 30, 60, 120, 300)
    return schedule[min(max(int(attempts), 1), len(schedule)) - 1]


def add_sync_log(task, content):
    db.session.add(DeviceChangeLog(
        device_type=task.device_type or "主设备",
        device_id=task.device_id,
        group_no=task.group_no or "",
        asset_no=task.asset_no or task.group_no or f"同步任务:{task.id}",
        asset_name=task.asset_name or "",
        change_content=content,
        created_at=datetime.now(),
    ))


def cleanup_orphan_spool(app_obj, max_age_seconds=24 * 60 * 60):
    """Remove abandoned partial/unregistered spool files from crashed requests."""
    root = Path(app_obj.config.get("BASE_DIR") or "/root/ism") / "upload_spool"
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    cutoff = time.time() - max_age_seconds
    removed = 0
    for path in root.iterdir():
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            if path.name.startswith(".incoming-") and path.suffix == ".part":
                path.unlink(missing_ok=True)
                removed += 1
                continue
            if path.suffix == ".ready":
                task = ImageSyncTask.query.filter_by(spool_name=path.name).first()
                if task is None:
                    path.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            continue
    if removed:
        log(f"Cleaned {removed} orphan spool file(s)", "WARN")
    return removed


def recover_stale_tasks():
    # This service is intentionally single-instance.  If it has just started,
    # every task left in "syncing" belongs to a previous crashed/stopped worker
    # and is safe to put back in the queue immediately.
    rows = ImageSyncTask.query.filter(ImageSyncTask.state == "syncing").all()
    if not rows:
        return 0
    now = now_naive()
    for task in rows:
        task.state = "pending"
        task.updated_at = now
        task.next_retry_at = now
        task.last_error = "后台同步进程中断，已自动重新排队"
    db.session.commit()
    log(f"Recovered {len(rows)} stale image sync task(s)", "WARN")
    return len(rows)




def recover_stale_cache_tasks():
    rows = ImageCacheTask.query.filter(ImageCacheTask.state == "caching").all()
    if not rows:
        return 0
    now = now_naive()
    for task in rows:
        task.state = "pending"
        task.updated_at = now
        task.next_retry_at = now
        task.last_error = "缓存进程中断，已自动重新排队"
    db.session.commit()
    log(f"Recovered {len(rows)} stale image cache task(s)", "WARN")
    return len(rows)


def claim_cache_one():
    now = now_naive()
    task = (ImageCacheTask.query
            .filter(ImageCacheTask.state.in_(("pending", "failed")))
            .filter(or_(ImageCacheTask.next_retry_at.is_(None), ImageCacheTask.next_retry_at <= now))
            .order_by(ImageCacheTask.created_at.asc(), ImageCacheTask.id.asc())
            .with_for_update()
            .first())
    if task is None:
        db.session.rollback()
        return None
    task.state = "caching"
    task.updated_at = now
    task.next_retry_at = None
    task_id = task.id
    db.session.commit()
    return task_id


def mark_cache_failure(task_id, error):
    db.session.remove()
    task = db.session.get(ImageCacheTask, task_id)
    if task is None:
        return
    task.attempts = int(task.attempts or 0) + 1
    task.state = "failed"
    task.updated_at = now_naive()
    task.last_error = str(error or "unknown error")[:1000]
    task.next_retry_at = now_naive() + timedelta(seconds=retry_delay(task.attempts))
    db.session.commit()
    log(f"Image cache prefetch failed task={task_id} attempt={task.attempts}: {task.last_error}", "WARN")


def copy_to_cache(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".ism-cache-{uuid.uuid4().hex}.part"
    expected_size = source.stat().st_size
    try:
        with source.open("rb") as src, temporary.open("wb") as dst:
            shutil.copyfileobj(src, dst, COPY_CHUNK)
            dst.flush()
            os.fsync(dst.fileno())
        os.chmod(temporary, 0o644)
        if temporary.stat().st_size != expected_size:
            raise OSError("缓存文件大小校验失败")
        os.replace(temporary, target)
        if target.stat().st_size != expected_size:
            raise OSError("缓存文件落盘校验失败")
        os.utime(target, None)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def process_cache_task(task_id):
    task = db.session.get(ImageCacheTask, task_id)
    if task is None:
        return
    relative = task.relative_path
    try:
        validate_cache_relative(relative)
        hit = cached_file(relative, touch=True)
        if hit is not None:
            db.session.delete(task)
            db.session.commit()
            return

        # Newly uploaded images may still live only in upload_spool. They are
        # already local and fast to serve, so wait until final storage sync is done
        # before creating a disposable read cache copy.
        sync_task = ImageSyncTask.query.filter_by(relative_path=relative).order_by(ImageSyncTask.id.desc()).first()
        if sync_task is not None and sync_task.state != "done":
            task.state = "pending"
            task.updated_at = now_naive()
            task.next_retry_at = now_naive() + timedelta(seconds=15)
            db.session.commit()
            return

        source = destination_path(relative)
        if not source.is_file():
            raise FileNotFoundError(f"源图片不存在或存储不可用: {source}")
        target = cache_path(relative)
        copy_to_cache(source, target)

        db.session.remove()
        current = db.session.get(ImageCacheTask, task_id)
        if current is None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return
        db.session.delete(current)
        db.session.commit()
        log(f"Image prefetched to VPS cache: {relative}")
    except (OSError, ValueError) as exc:
        db.session.rollback()
        mark_cache_failure(task_id, exc)
    except Exception as exc:
        db.session.rollback()
        mark_cache_failure(task_id, exc)


def cleanup_view_cache():
    """Expire dead leases and evict disposable cache by TTL then LRU size."""
    now = now_naive()
    stale_cutoff = now - timedelta(seconds=LEASE_STALE_SECONDS)
    stale = ImageViewLease.query.filter(ImageViewLease.last_seen < stale_cutoff).delete(synchronize_session=False)
    if stale:
        db.session.commit()
    else:
        db.session.rollback()

    active = active_leased_paths(now)
    root = cache_root()
    now_ts = time.time()
    entries = []
    removed = 0
    total = 0

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
            st = path.stat()
            if path.name.startswith(".ism-cache-") and path.suffix == ".part":
                if now_ts - st.st_mtime > 600:
                    path.unlink(missing_ok=True)
                    removed += 1
                continue
            try:
                validate_cache_relative(rel)
            except ValueError:
                continue
            if rel not in active and now_ts - st.st_mtime > CACHE_TTL_SECONDS:
                path.unlink(missing_ok=True)
                removed += 1
                continue
            total += st.st_size
            entries.append((st.st_mtime, st.st_size, rel, path))
        except OSError:
            continue

    if total > CACHE_MAX_BYTES:
        for _mtime, size, rel, path in sorted(entries, key=lambda item: item[0]):
            if total <= CACHE_MAX_BYTES:
                break
            if rel in active:
                continue
            try:
                path.unlink(missing_ok=True)
                total -= size
                removed += 1
            except OSError:
                pass

    if removed or stale:
        log(f"Image cache cleanup: removed={removed}, stale_leases={stale}, bytes={total}")
    return removed

def claim_one():
    now = now_naive()
    task = (ImageSyncTask.query
            .filter(ImageSyncTask.state.in_(("pending", "failed")))
            .filter(or_(ImageSyncTask.next_retry_at.is_(None), ImageSyncTask.next_retry_at <= now))
            .order_by(ImageSyncTask.created_at.asc(), ImageSyncTask.id.asc())
            .with_for_update()
            .first())
    if task is None:
        db.session.rollback()
        return None
    task.state = "syncing"
    task.updated_at = now
    task.next_retry_at = None
    task_id = task.id
    db.session.commit()
    return task_id


def destination_path(relative_path):
    relative = _validate_image_relative(relative_path)
    return _upload_root() / relative


def copy_to_storage(source, target, expected_size):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".ism-sync-{uuid.uuid4().hex}.part"
    try:
        with source.open("rb") as src, temporary.open("wb") as dst:
            shutil.copyfileobj(src, dst, COPY_CHUNK)
            dst.flush()
            os.fsync(dst.fileno())
        os.chmod(temporary, 0o644)
        if temporary.stat().st_size != int(expected_size):
            raise OSError("目标暂存文件大小校验失败")
        os.replace(temporary, target)
        if target.stat().st_size != int(expected_size):
            raise OSError("目标文件大小校验失败")
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _owner_image_rows(task):
    """Return this owner's current image rows; used only by the background worker."""
    from app.models import AssetImage, AccessoryImage
    from app.routes.upload import AssetLocationImage

    if task.device_type == "主设备" and task.device_id is not None:
        return AssetImage.query.filter_by(asset_id=task.device_id).all()
    if task.device_type == "配件" and task.device_id is not None:
        return AccessoryImage.query.filter_by(accessory_id=task.device_id).all()
    location = (task.asset_no or task.group_no or "").strip()
    if not location:
        return []
    return AssetLocationImage.query.filter(
        db.func.upper(AssetLocationImage.location_name) == location.upper()
    ).all()


def _remove_current_image_row(task):
    from app.models import AssetImage, AccessoryImage
    from app.routes.upload import AssetLocationImage

    if task.device_type == "主设备":
        row = AssetImage.query.filter_by(image_path=task.relative_path).first()
    elif task.device_type == "配件":
        row = AccessoryImage.query.filter_by(image_path=task.relative_path).first()
    else:
        row = AssetLocationImage.query.filter_by(image_path=task.relative_path).first()
    if row is not None:
        db.session.delete(row)


def find_background_duplicate(task):
    """Check historical images without delaying the HTTP upload request.

    Modern images usually have a sync-task SHA and are compared without I/O.
    Old pre-v26 images are hashed here, in the worker, only when needed.
    """
    for row in _owner_image_rows(task):
        relative = getattr(row, "image_path", "") or ""
        if not relative or relative == task.relative_path:
            continue
        old_task = ImageSyncTask.query.filter_by(relative_path=relative).order_by(ImageSyncTask.id.desc()).first()
        if old_task is not None and old_task.sha256:
            if old_task.sha256 == task.sha256:
                return relative
            continue
        try:
            candidate = destination_path(relative)
            if candidate.is_file() and file_sha256(candidate) == task.sha256:
                return relative
        except (OSError, ValueError):
            # A stale/slow mount must not turn duplicate detection into the
            # reason the task fails; the actual copy below will report storage
            # health if necessary.
            continue
    return None


def finish_as_duplicate(task, source, duplicate_relative):
    relative_path = task.relative_path
    filename = Path(relative_path).name
    _remove_current_image_row(task)
    try:
        from app.image_cache import cancel_cache_task, remove_cached_file
        cancel_cache_task(relative_path)
        remove_cached_file(relative_path)
    except Exception:
        pass
    add_sync_log(
        task,
        f"图片后台同步成功（重复图片已跳过）：{filename}；与已有图片内容一致",
    )
    db.session.delete(task)
    db.session.commit()
    try:
        source.unlink(missing_ok=True)
    except OSError as exc:
        log(f"Duplicate spool cleanup failed: {exc}", "WARN")
    log(f"Duplicate image skipped: {relative_path} == {duplicate_relative}")


def mark_failure(task_id, error, permanent=False):
    db.session.remove()
    task = db.session.get(ImageSyncTask, task_id)
    if task is None:
        return
    task.attempts = int(task.attempts or 0) + 1
    task.state = "failed"
    task.updated_at = now_naive()
    task.last_error = str(error or "unknown error")[:1000]
    delay = 600 if permanent else retry_delay(task.attempts)
    task.next_retry_at = now_naive() + timedelta(seconds=delay)
    if (permanent or task.attempts >= FAILURE_LOG_AFTER) and not task.failure_logged:
        filename = Path(task.relative_path).name
        add_sync_log(
            task,
            f"图片后台同步失败：{filename}；原图已保留在VPS本地暂存，系统将继续自动重试；原因：{task.last_error}",
        )
        task.failure_logged = True
    db.session.commit()
    log(f"Image sync failed task={task_id} attempt={task.attempts}: {task.last_error}", "ERR")


def process_task(task_id):
    task = db.session.get(ImageSyncTask, task_id)
    if task is None:
        return
    try:
        source = spool_path(task.spool_name)
        if not source.is_file():
            raise FileNotFoundError(f"本地暂存文件不存在: {source}")
        if source.stat().st_size != int(task.size_bytes):
            raise OSError("本地暂存文件大小校验失败")
        if file_sha256(source) != task.sha256:
            raise OSError("本地暂存文件 SHA-256 校验失败")

        duplicate_relative = find_background_duplicate(task)
        if duplicate_relative:
            finish_as_duplicate(task, source, duplicate_relative)
            return

        target = destination_path(task.relative_path)
        copy_to_storage(source, target, task.size_bytes)

        # Another request may have deleted this image while the copy was in
        # progress. Re-open the DB session before publishing success.
        db.session.remove()
        current = db.session.get(ImageSyncTask, task_id)
        if current is None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                source.unlink(missing_ok=True)
            except OSError:
                pass
            log(f"Cancelled image sync task={task_id}; copied file removed", "WARN")
            return

        current.state = "done"
        current.updated_at = now_naive()
        current.synced_at = now_naive()
        current.next_retry_at = None
        current.last_error = ""
        current.attempts = int(current.attempts or 0)
        add_sync_log(current, f"图片后台同步成功：{Path(current.relative_path).name}")
        db.session.commit()

        try:
            source.unlink(missing_ok=True)
        except OSError as exc:
            log(f"Synced image spool cleanup failed task={task_id}: {exc}", "WARN")
        log(f"Image sync completed task={task_id}: {current.relative_path}")
    except FileNotFoundError as exc:
        db.session.rollback()
        mark_failure(task_id, exc, permanent=True)
    except (OSError, ValueError) as exc:
        db.session.rollback()
        mark_failure(task_id, exc)
    except Exception as exc:
        db.session.rollback()
        mark_failure(task_id, exc)


def main():
    app = create_app()
    with app.app_context():
        spool_root = Path(app.config.get("BASE_DIR") or "/root/ism").joinpath("upload_spool")
        spool_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(spool_root, 0o700)
        except OSError:
            pass
        view_root = cache_root()
        try:
            os.chmod(view_root.parent, 0o755)
            os.chmod(view_root, 0o755)
        except OSError:
            pass
        recover_stale_tasks()
        recover_stale_cache_tasks()
        cleanup_orphan_spool(app)
        cleanup_view_cache()
        last_spool_cleanup = time.monotonic()
        last_cache_cleanup = time.monotonic()
        log(f"ISM image worker started; target={app.config.get('UPLOAD_FOLDER')}; cache={view_root}")
        while True:
            try:
                now_mono = time.monotonic()
                if now_mono - last_spool_cleanup >= 3600:
                    cleanup_orphan_spool(app)
                    last_spool_cleanup = now_mono
                if now_mono - last_cache_cleanup >= 60:
                    cleanup_view_cache()
                    last_cache_cleanup = now_mono

                did_work = False
                task_id = claim_one()
                if task_id is not None:
                    process_task(task_id)
                    did_work = True

                cache_task_id = claim_cache_one()
                if cache_task_id is not None:
                    process_cache_task(cache_task_id)
                    did_work = True

                if not did_work:
                    time.sleep(POLL_SECONDS)
            except KeyboardInterrupt:
                log("ISM image worker stopped")
                return
            except Exception as exc:
                db.session.rollback()
                log(f"Worker loop error: {exc}", "ERR")
                time.sleep(5)


if __name__ == "__main__":
    main()
