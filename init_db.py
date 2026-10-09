#!/usr/bin/env python3
"""Single-process, additive initialization. Run before starting Gunicorn.

Never import the bundled SQL snapshot over an existing database. Existing
images/business records are not rewritten, deleted or backfilled with hashes.
"""
from datetime import UTC, datetime, timedelta
from pathlib import Path
import fcntl

from sqlalchemy import inspect, or_, text

from app import create_app, db, BASE_DIR
from app.models import DictOption, DeviceChangeLog, Asset, Accessory


def initialize():
    lock_path = Path(BASE_DIR) / '.ism-init-db.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        app = create_app()
        with app.app_context():
            from app.image_uploads import ImageUploadSubmission, ImageSyncTask
            from app.image_cache import ImageCacheTask, ImageViewLease
            db.create_all()

            # Search acceleration is additive and does not rewrite business data.
            # The generated suffix columns preserve the established strict 6-digit
            # identifier rule while making that lookup indexable.
            search_column_ddl = {
                "assets": {
                    "internal_no_suffix6": (
                        "ALTER TABLE assets ADD COLUMN internal_no_suffix6 VARCHAR(6) "
                        "GENERATED ALWAYS AS (RIGHT(internal_no, 6)) PERSISTENT"
                    ),
                    "group_no_suffix6": (
                        "ALTER TABLE assets ADD COLUMN group_no_suffix6 VARCHAR(6) "
                        "GENERATED ALWAYS AS (RIGHT(group_no, 6)) PERSISTENT"
                    ),
                },
                "accessories": {
                    "sub_internal_no_suffix6": (
                        "ALTER TABLE accessories ADD COLUMN sub_internal_no_suffix6 VARCHAR(6) "
                        "GENERATED ALWAYS AS (CASE "
                        "WHEN sub_internal_no IS NULL THEN NULL "
                        "WHEN LOCATE('-', sub_internal_no) > 0 THEN RIGHT(SUBSTRING_INDEX(sub_internal_no, '-', 1), 6) "
                        "ELSE RIGHT(sub_internal_no, 6) END) PERSISTENT"
                    ),
                    "sub_group_no_suffix6": (
                        "ALTER TABLE accessories ADD COLUMN sub_group_no_suffix6 VARCHAR(6) "
                        "GENERATED ALWAYS AS (CASE "
                        "WHEN sub_group_no IS NULL THEN NULL "
                        "WHEN LOCATE('-', sub_group_no) > 0 THEN RIGHT(SUBSTRING_INDEX(sub_group_no, '-', 1), 6) "
                        "ELSE RIGHT(sub_group_no, 6) END) PERSISTENT"
                    ),
                },
            }

            inspector = inspect(db.engine)
            table_names = set(inspector.get_table_names())
            for table_name, columns in search_column_ddl.items():
                if table_name not in table_names:
                    continue
                existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
                for column_name, ddl in columns.items():
                    if column_name not in existing_columns:
                        db.session.execute(text(ddl))
                        db.session.commit()
                inspector = inspect(db.engine)

            search_indexes = {
                "assets": {
                    "idx_assets_internal_suffix6": "CREATE INDEX idx_assets_internal_suffix6 ON assets (internal_no_suffix6)",
                    "idx_assets_group_suffix6": "CREATE INDEX idx_assets_group_suffix6 ON assets (group_no_suffix6)",
                    "idx_assets_search_filter": "CREATE INDEX idx_assets_search_filter ON assets (deleted_at, status, asset_date, id)",
                },
                "accessories": {
                    "idx_accessories_internal_suffix6": "CREATE INDEX idx_accessories_internal_suffix6 ON accessories (sub_internal_no_suffix6)",
                    "idx_accessories_group_suffix6": "CREATE INDEX idx_accessories_group_suffix6 ON accessories (sub_group_no_suffix6)",
                    "idx_accessories_search_filter": "CREATE INDEX idx_accessories_search_filter ON accessories (deleted_at, status, asset_date, id)",
                },
            }
            inspector = inspect(db.engine)
            for table_name, indexes in search_indexes.items():
                if table_name not in table_names:
                    continue
                existing_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
                for index_name, ddl in indexes.items():
                    if index_name not in existing_indexes:
                        db.session.execute(text(ddl))
                        db.session.commit()
                inspector = inspect(db.engine)

            # db.create_all() does not add columns to an existing table. Keep
            # the device audit table additive so manual code updates can be
            # applied safely without rebuilding the business database.
            inspector = inspect(db.engine)
            if "device_change_logs" in inspector.get_table_names():
                log_columns = {col["name"] for col in inspector.get_columns("device_change_logs")}
                if "group_no" not in log_columns:
                    db.session.execute(text(
                        "ALTER TABLE device_change_logs ADD COLUMN group_no VARCHAR(128) NULL AFTER device_id"
                    ))
                if "asset_name" not in log_columns:
                    db.session.execute(text(
                        "ALTER TABLE device_change_logs ADD COLUMN asset_name VARCHAR(255) NULL AFTER asset_no"
                    ))
                db.session.commit()

                # Fill the new display columns for logs created by v7/v8 when
                # the referenced device still exists. Future rows store a true
                # snapshot at the time of the operation.
                old_logs = DeviceChangeLog.query.filter(
                    or_(DeviceChangeLog.group_no.is_(None), DeviceChangeLog.asset_name.is_(None))
                ).all()
                for log_row in old_logs:
                    obj = None
                    if log_row.device_id:
                        if log_row.device_type == "配件":
                            obj = db.session.get(Accessory, log_row.device_id)
                        else:
                            obj = db.session.get(Asset, log_row.device_id)
                    if obj is not None:
                        if log_row.group_no is None:
                            log_row.group_no = (
                                (obj.sub_group_no or "") if isinstance(obj, Accessory) else (obj.group_no or "")
                            )
                        if log_row.asset_name is None:
                            log_row.asset_name = obj.name or ""
                db.session.commit()

            # Fresh installs no longer import a bundled SQL snapshot. Seed only
            # the small built-in status dictionary required by the UI. Existing
            # installations are left untouched.
            default_statuses = [
                ('在库', 1),
                ('借出', 2),
                ('报废', 3),
                ('开箱', 5),
                ('其它', 6),
            ]
            for value, order in default_statuses:
                exists = DictOption.query.filter_by(dict_type='status', dict_value=value).first()
                if exists is None:
                    db.session.add(DictOption(
                        dict_type='status',
                        dict_value=value,
                        sort_order=order,
                        is_active=True,
                    ))
            db.session.commit()

            # Receipts guard stale form retries for 90 days. Per-owner file
            # content deduplication remains active independently of this TTL.
            ImageUploadSubmission.query.filter(
                ImageUploadSubmission.created_at < datetime.now(UTC).replace(tzinfo=None) - timedelta(days=90),
                ImageUploadSubmission.redirect_url.isnot(None),
            ).delete(synchronize_session=False)
            db.session.commit()

            # Keep pending/failed tasks forever until handled; trim only completed
            # task metadata after 90 days. Image files themselves live in storage.
            ImageSyncTask.query.filter(
                ImageSyncTask.state == "done",
                ImageSyncTask.synced_at.isnot(None),
                ImageSyncTask.synced_at < datetime.now(UTC).replace(tzinfo=None) - timedelta(days=90),
            ).delete(synchronize_session=False)
            db.session.commit()
    print('ISM database initialization complete (additive only).')


if __name__ == '__main__':
    initialize()
