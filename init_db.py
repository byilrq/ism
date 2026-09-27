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
            from app.image_uploads import AppMigration, ImageUploadSubmission
            from app.routes.cable import backfill_cable_shelves
            db.create_all()

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

            name = 'cable_shelf_backfill_v1'
            if db.session.get(AppMigration, name) is None:
                backfill_cable_shelves()
                db.session.add(AppMigration(name=name))
                db.session.commit()
            # Receipts guard stale form retries for 90 days. Per-owner file
            # content deduplication remains active independently of this TTL.
            ImageUploadSubmission.query.filter(
                ImageUploadSubmission.created_at < datetime.now(UTC).replace(tzinfo=None) - timedelta(days=90),
                ImageUploadSubmission.redirect_url.isnot(None),
            ).delete(synchronize_session=False)
            db.session.commit()
    print('ISM database initialization complete (additive only).')


if __name__ == '__main__':
    initialize()
