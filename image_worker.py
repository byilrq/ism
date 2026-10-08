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
        Path(app.config.get("BASE_DIR") or "/root/ism").joinpath("upload_spool").mkdir(parents=True, exist_ok=True)
        recover_stale_tasks()
        log(f"ISM image worker started; target={app.config.get('UPLOAD_FOLDER')}")
        while True:
            try:
                task_id = claim_one()
                if task_id is None:
                    time.sleep(POLL_SECONDS)
                    continue
                process_task(task_id)
            except KeyboardInterrupt:
                log("ISM image worker stopped")
                return
            except Exception as exc:
                db.session.rollback()
                log(f"Worker loop error: {exc}", "ERR")
                time.sleep(5)


if __name__ == "__main__":
    main()
