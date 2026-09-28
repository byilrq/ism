#!/usr/bin/env python3
import subprocess
import sys
import tarfile
from datetime import datetime, timedelta

try:
    import yaml
except ModuleNotFoundError:
    print(
        "[ERR] PyYAML is unavailable in the current Python environment. "
        "Run this script with /root/ism/venv/bin/python.",
        file=sys.stderr,
    )
    raise SystemExit(2)
from pathlib import Path

CONFIG_FILE = "/root/ism/config.yaml"
BACKUP_DIR = "/root/ism/backups"
BACKUP_FILE = f"{BACKUP_DIR}/ism_latest.sql"
CODE_BACKUP_FILE = f"{BACKUP_DIR}/ism_code_latest.tar.gz"
LOG_FILE = "/var/log/ism_backup.log"
SERVICE_NAME = "ism"
APP_ROOT = "/root/ism"
INIT_DB_SCRIPT = f"{APP_ROOT}/init_db.py"
VENV_PYTHON = f"{APP_ROOT}/venv/bin/python"
BACKUP_RETENTION_DAYS = 90


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
            cfg = yaml.safe_load(f) or {}
        mysql_cfg = cfg.get("mysql", {}) or {}
        db_name = str(mysql_cfg.get("database") or db_name)
        db_user = str(mysql_cfg.get("user") or db_user)
        db_pass = str(mysql_cfg.get("password") or db_pass)
        upload_folder = str(cfg.get("upload_folder") or upload_folder).strip()
        if not Path(upload_folder).is_absolute():
            raise ValueError(f"upload_folder must be an absolute path: {upload_folder}")
    except Exception as e:
        log_msg(f"Failed to load config: {e}", "ERR")

    return db_name, db_user, db_pass, upload_folder


def backup_code():
    """Archive current runtime code/config, excluding data, venv and caches."""
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    tmp_file = f"{CODE_BACKUP_FILE}.tmp"
    app_root = Path(APP_ROOT).resolve()
    runtime_top_files = {
        "config.yaml", "configure_media.py", "init_db.py", "ism.sh",
        "ism_backup.py", "requirements.txt", "run.py",
    }

    def should_include(path):
        rel = path.relative_to(app_root)
        parts = rel.parts
        if not parts or not path.is_file():
            return False
        if parts[0] == "app":
            if len(parts) >= 2 and parts[1] == "uploads":
                return False
            if "__pycache__" in parts:
                return False
            return path.suffix.lower() not in {".pyc", ".pyo", ".log", ".tmp", ".part"}
        return len(parts) == 1 and parts[0] in runtime_top_files

    try:
        with tarfile.open(tmp_file, "w:gz") as archive:
            for path in sorted(app_root.rglob("*")):
                if should_include(path):
                    archive.add(path, arcname=str(path.relative_to(app_root)), recursive=False)
        if Path(tmp_file).stat().st_size <= 0:
            raise RuntimeError("code backup archive is empty")
        Path(tmp_file).replace(CODE_BACKUP_FILE)
        log_msg(f"Code backup completed: {CODE_BACKUP_FILE}")
        return True
    except Exception as e:
        log_msg(f"Code backup error: {e}", "ERR")
        Path(tmp_file).unlink(missing_ok=True)
        return False


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
    remote_sql_root = Path(upload_folder) / "sql_backups"
    remote_code_root = Path(upload_folder) / "code_backups"
    remote_sql_dated = remote_sql_root / f"ism_latest.{backup_date}.sql"
    remote_code_dated = remote_code_root / f"ism_code.{backup_date}.tar.gz"

    try:
        upload_root = Path(upload_folder)
        if not upload_root.exists():
            log_msg(f"Upload folder not available: {upload_folder}", "ERR")
            return False

        remote_sql_root.mkdir(parents=True, exist_ok=True)
        remote_code_root.mkdir(parents=True, exist_ok=True)

        test_file = upload_root / ".write_test"
        test_file.touch()
        test_file.unlink()

        subprocess.run(["cp", "-f", BACKUP_FILE, str(remote_sql_dated)], check=True)
        subprocess.run(["cp", "-f", CODE_BACKUP_FILE, str(remote_code_dated)], check=True)
        log_msg(f"Database backup synced to: {remote_sql_dated}")
        log_msg(f"Code backup synced to: {remote_code_dated}")

        cutoff = datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
        for old_file in remote_sql_root.glob("ism_latest.*.sql"):
            if datetime.fromtimestamp(old_file.stat().st_mtime) < cutoff:
                old_file.unlink()
                log_msg(f"Deleted old database backup: {old_file}")
        for old_file in remote_code_root.glob("ism_code.*.tar.gz"):
            if datetime.fromtimestamp(old_file.stat().st_mtime) < cutoff:
                old_file.unlink()
                log_msg(f"Deleted old code backup: {old_file}")

        return True
    except Exception as e:
        log_msg(f"Remote sync failed: {e}", "ERR")
        return False


def _run_init_db_after_restore():
    init_script = Path(INIT_DB_SCRIPT)
    if not init_script.exists():
        log_msg(f"Post-restore initializer not found: {init_script}", "ERR")
        return False

    python_bin = VENV_PYTHON if Path(VENV_PYTHON).exists() else sys.executable
    try:
        result = subprocess.run(
            [python_bin, str(init_script)],
            cwd=APP_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=600,
        )
        if result.returncode != 0:
            stdout = result.stdout.decode(errors="ignore").strip()
            stderr = result.stderr.decode(errors="ignore").strip()
            if stdout:
                log_msg(f"init_db stdout: {stdout}")
            log_msg(f"init_db failed: {stderr or 'unknown error'}", "ERR")
            return False
        output = result.stdout.decode(errors="ignore").strip()
        if output:
            log_msg(output)
        log_msg("Post-restore database initialization completed")
        return True
    except Exception as e:
        log_msg(f"Post-restore initialization error: {e}", "ERR")
        return False


def restore_database(db_name, db_user, backup_path=BACKUP_FILE):
    backup_file = Path(backup_path)
    if not backup_file.exists():
        log_msg(f"Backup file not found: {backup_file}", "ERR")
        return False

    if backup_file.stat().st_size <= 0:
        log_msg(f"Backup file is empty: {backup_file}", "ERR")
        return False

    service_stopped = False
    try:
        stop_result = subprocess.run(
            ["systemctl", "stop", SERVICE_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if stop_result.returncode not in (0, 5):
            log_msg(
                f"Failed to stop {SERVICE_NAME}: {stop_result.stderr.decode(errors='ignore')}",
                "ERR",
            )
            return False
        service_stopped = True
        log_msg(f"Service stopped: {SERVICE_NAME}")

        rebuild_sql = (
            f"DROP DATABASE IF EXISTS `{db_name}`;\n"
            f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;\n"
            f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost';\n"
            "FLUSH PRIVILEGES;\n"
        )
        rebuild = subprocess.run(
            ["mysql"],
            input=rebuild_sql.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=3600,
        )
        if rebuild.returncode != 0:
            log_msg(f"Database rebuild failed: {rebuild.stderr.decode(errors='ignore')}", "ERR")
            return False
        log_msg(f"Database recreated: {db_name}")

        with open(backup_file, "rb") as f:
            result = subprocess.run(
                ["mysql", db_name],
                stdin=f,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3600,
            )

        if result.returncode != 0:
            log_msg(f"mysql restore failed: {result.stderr.decode(errors='ignore')}", "ERR")
            return False
        log_msg(f"SQL backup imported: {backup_file}")

        if not _run_init_db_after_restore():
            log_msg(
                "Restore data was imported, but post-restore initialization failed; service remains stopped",
                "ERR",
            )
            return False

        start_result = subprocess.run(
            ["systemctl", "start", SERVICE_NAME],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=300,
        )
        if start_result.returncode != 0:
            log_msg(f"Failed to start {SERVICE_NAME}: {start_result.stderr.decode(errors='ignore')}", "ERR")
            return False

        log_msg(f"Database restore completed: {backup_file}")
        log_msg(f"Service started: {SERVICE_NAME}")
        return True
    except Exception as e:
        log_msg(f"Restore error: {e}", "ERR")
        if service_stopped:
            log_msg(
                f"For safety, {SERVICE_NAME} remains stopped after restore failure",
                "ERR",
            )
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
        log_msg("Database backup failed", "ERR")
        log_msg("=" * 66)
        return 1

    if not backup_code():
        log_msg("Code backup failed", "ERR")
        log_msg("=" * 66)
        return 1

    if not sync_to_remote(upload_folder):
        log_msg("Local backups completed, but remote rotation sync failed", "ERR")
        log_msg("=" * 66)
        return 1

    log_msg(f"Backup process completed; retention={BACKUP_RETENTION_DAYS} days")
    log_msg("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
