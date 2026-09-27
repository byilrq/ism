#!/usr/bin/env python3
"""Single-process, additive initialization. Run before starting Gunicorn.

Never import the bundled SQL snapshot over an existing database. Existing
images/business records are not rewritten, deleted or backfilled with hashes.
"""
from datetime import datetime, timedelta
from pathlib import Path
import fcntl

from app import create_app, db, BASE_DIR
from app.models import DictOption


def initialize():
    lock_path = Path(BASE_DIR) / '.ism-init-db.lock'
    with lock_path.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        app = create_app()
        with app.app_context():
            from app.image_uploads import AppMigration, ImageUploadSubmission
            from app.routes.cable import backfill_cable_shelves
            db.create_all()

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
                ImageUploadSubmission.created_at < datetime.utcnow() - timedelta(days=90),
                ImageUploadSubmission.redirect_url.isnot(None),
            ).delete(synchronize_session=False)
            db.session.commit()
    print('ISM database initialization complete (additive only).')


if __name__ == '__main__':
    initialize()
