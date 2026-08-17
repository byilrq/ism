#!/usr/bin/env python3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

CONFIG_FILE = "/root/ism/config.yaml"
BACKUP_DIR = "/root/ism/backups"
BACKUP_FILE = f"{BACKUP_DIR}/ism_latest.sql"
LOG_FILE = "/var/log/ism_backup.log"
SERVICE_NAME = "ism"


def log_msg(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{level}] {ts} {msg}"
    print(full_msg)

    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(full_msg + "\n")


def load_config():
    db_name = "ism"
    db_user = "asset_user"
    db_pass = "by123"
    upload_folder = "/root/ism/app/uploads"

    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            in_mysql_section = False
            for line in f:
                line = line.rstrip()
                stripped = line.strip()

                if stripped.startswith("mysql:"):
                    in_mysql_section = True
                elif stripped and not line.startswith(" ") and not line.startswith("\t"):
                    in_mysql_section = False

                if in_mysql_section:
                    if stripped.startswith("database:"):
                        db_name = stripped.split(":", 1)[1].strip().strip("'\"")
                    elif stripped.startswith("user:"):
                        db_user = stripped.split(":", 1)[1].strip().strip("'\"")
                    elif stripped.startswith("password:"):
                        db_pass = stripped.split(":", 1)[1].strip().strip("'\"")

                if stripped.startswith("upload_folder:"):
                    upload_folder = stripped.split(":", 1)[1].strip().strip("'\"")
    except Exception as e:
        log_msg(f"Failed to load config: {e}", "ERR")

    return db_name, db_user, db_pass, upload_folder


def backup_database(db_name, db_user, db_pass):
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    tmp_file = f"{BACKUP_FILE}.tmp"

    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            result = subprocess.run(
                ["mysqldump", f"-u{db_user}", f"-p{db_pass}", db_name],
                stdout=f,
                stderr=subprocess.PIPE,
                timeout=3600,
            )

        if result.returncode != 0:
            log_msg(f"mysqldump failed: {result.stderr.decode(errors='ignore')}", "ERR")
            Path(tmp_file).unlink(missing_ok=True)
            return False

        if Path(tmp_file).stat().st_size <= 0:
            log_msg(f"Backup file is empty: {tmp_file}", "ERR")
            Path(tmp_file).unlink(missing_ok=True)
            return False

        Path(tmp_file).rename(BACKUP_FILE)
        log_msg(f"Database backup completed: {BACKUP_FILE}")
        return True
    except Exception as e:
        log_msg(f"Backup error: {e}", "ERR")
        Path(tmp_file).unlink(missing_ok=True)
        return False


def sync_to_remote(upload_folder):
    backup_date = datetime.now().strftime("%Y.%m.%d")
    remote_backup_root = f"{upload_folder}/sql_backups"
    remote_backup_dated = f"{remote_backup_root}/ism_latest.{backup_date}.sql"

    try:
        if not Path(upload_folder).exists():
            log_msg(f"Upload folder not available: {upload_folder}")
            return False

        Path(remote_backup_root).mkdir(parents=True, exist_ok=True)

        test_file = f"{upload_folder}/.write_test"
        Path(test_file).touch()
        Path(test_file).unlink()

        subprocess.run(["cp", "-f", BACKUP_FILE, remote_backup_dated], check=True)
        log_msg(f"Backup synced to: {remote_backup_dated}")

        for old_file in Path(remote_backup_root).glob("ism_latest.*.sql"):
            mtime = datetime.fromtimestamp(old_file.stat().st_mtime)
            if datetime.now() - mtime > timedelta(days=30):
                old_file.unlink()
                log_msg(f"Deleted old backup: {old_file}")

        return True
    except Exception as e:
        log_msg(f"Remote sync failed: {e}", "ERR")
        return False


def restore_database(db_name, db_user, backup_path=BACKUP_FILE):
    backup_file = Path(backup_path)
    if not backup_file.exists():
        log_msg(f"Backup file not found: {backup_file}", "ERR")
        return False

    if backup_file.stat().st_size <= 0:
        log_msg(f"Backup file is empty: {backup_file}", "ERR")
        return False

    try:
        subprocess.run(["systemctl", "stop", SERVICE_NAME], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(
            ["mysql"],
            input=(
                f"DROP DATABASE IF EXISTS `{db_name}`;\n"
                f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;\n"
                f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost';\n"
                "FLUSH PRIVILEGES;\n"
            ).encode("utf-8"),
            check=True,
            stderr=subprocess.PIPE,
            timeout=3600,
        )

        with open(backup_file, "rb") as f:
            result = subprocess.run(
                ["mysql", db_name],
                stdin=f,
                stderr=subprocess.PIPE,
                timeout=3600,
            )

        if result.returncode != 0:
            log_msg(f"mysql restore failed: {result.stderr.decode(errors='ignore')}", "ERR")
            return False

        subprocess.run(["systemctl", "start", SERVICE_NAME], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_msg(f"Database restore completed: {backup_file}")
        return True
    except Exception as e:
        subprocess.run(["systemctl", "start", SERVICE_NAME], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_msg(f"Restore error: {e}", "ERR")
        return False


def main():
    log_msg("=" * 66)

    db_name, db_user, db_pass, upload_folder = load_config()
    action = sys.argv[1] if len(sys.argv) > 1 else "backup"

    if action == "restore":
        target_file = sys.argv[2] if len(sys.argv) > 2 else BACKUP_FILE
        log_msg(f"Starting database restore from: {target_file}")
        ok = restore_database(db_name, db_user, target_file)
        if ok:
            log_msg("Restore process completed")
            log_msg("=" * 66)
            return 0
        log_msg("Restore failed", "ERR")
        log_msg("=" * 66)
        return 1

    log_msg("Starting database backup")
    log_msg(f"Config loaded: db={db_name}, upload_folder={upload_folder}")

    if not backup_database(db_name, db_user, db_pass):
        log_msg("Backup failed", "ERR")
        log_msg("=" * 66)
        return 1

    sync_to_remote(upload_folder)
    log_msg("Backup process completed")
    log_msg("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
