#!/usr/bin/env python3
"""Patch only the ISM Nginx site, validate before reload, preserve listen/TLS.

Internal X-Accel-Redirect delivers image bytes without exposing the upload
root (which can also contain database dumps or recycle files).
"""
from pathlib import Path
import argparse
from hashlib import sha256
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import time

import yaml

BEGIN = '# BEGIN ISM IMAGE PERFORMANCE v1'
END = '# END ISM IMAGE PERFORMANCE v1'
SUBDIRS = ('assets', 'accessories', 'asset_locations', 'cable', 'cable_shelf')


def quoted_path(path):
    value = str(Path(path).resolve())
    if any(char in value for char in ('"', '\\', '$', '\n', '\r', ';', '{', '}')):
        raise ValueError('Unsupported Nginx path characters')
    return '"' + value.rstrip('/') + '/"'


def patch_site(text, app_root, upload_root, max_bytes, version=(1, 26, 0),
               http2_supported=True, static_enabled=True, media_enabled=True):
    if int(max_bytes) <= 0:
        raise ValueError('Upload request limit must be positive')
    text = re.sub(r'(?ms)^\s*' + re.escape(BEGIN) + r'.*?' + re.escape(END) + r'[^\S\n]*\n?', '\n', text)
    servers = list(re.finditer(r'(?m)^\s*server\s*\{', text))
    if len(servers) != 1:
        raise ValueError('Expected one ISM server block; refusing to rewrite a shared/multi-server site')
    if not re.search(r'proxy_pass\s+http://127\.0\.0\.1:\d+\s*;', text):
        raise ValueError('No ISM loopback proxy found; refusing to modify this site')
    # Strip our previous per-location flag. Always overwrite inbound client
    # flags, including when an inaccessible mount requires Flask fallback.
    text = re.sub(r'(?m)^\s*proxy_set_header\s+X-ISM-Accel(?:-Root)?\s+[^;]*;[^\n]*\n?', '\n', text)
    flag = '1' if media_enabled else '0'
    root_hash = sha256(str(Path(upload_root).resolve()).encode()).hexdigest()
    text = re.sub(r'(proxy_pass\s+http://127\.0\.0\.1:\d+\s*;)',
                  r'\1\n        proxy_set_header X-ISM-Accel ' + flag + '; # ISM managed\n        proxy_set_header X-ISM-Accel-Root ' + root_hash + '; # ISM managed', text)
    settings = {
        'client_max_body_size': str(int(max_bytes)),
        'client_body_timeout': '300s',
        'send_timeout': '300s',
        'sendfile': 'on',
        'tcp_nopush': 'on',
        'proxy_request_buffering': 'on',
        'proxy_buffering': 'on',
        'proxy_next_upstream': 'off',
        'proxy_read_timeout': '300s',
        'proxy_send_timeout': '300s',
        'gzip': 'on',
        'gzip_vary': 'on',
        'gzip_types': 'text/plain text/css application/javascript application/json application/xml image/svg+xml',
    }
    directives = []
    for name, value in settings.items():
        pattern = r'(?m)^(\s*)' + re.escape(name) + r'\s+[^;]*;'
        if re.search(pattern, text):
            text = re.sub(pattern, lambda m, n=name, v=value: m.group(1) + n + ' ' + v + ';', text)
        else:
            directives.append(f'    {name} {value};')
    # Bound server-side processing, not total upload duration. Body buffering
    # means the Gunicorn worker is not held for a client's slow uplink.
    for name in ('proxy_read_timeout', 'proxy_send_timeout'):
        text = re.sub(r'(?m)^(\s*)' + name + r'\s+[^;]*;', lambda m, n=name: m.group(1) + n + ' 300s;', text)
    ssl = bool(re.search(r'(?m)^\s*listen\s+[^;]*\bssl\b', text))
    if ssl and http2_supported:
        if version >= (1, 25, 1):
            text = re.sub(r'(?m)^(\s*listen\s+[^;]*?)\s+http2\b', r'\1', text)
            if not re.search(r'(?m)^\s*http2\s+', text):
                directives.append('    http2 on;')
            else:
                text = re.sub(r'(?m)^(\s*)http2\s+[^;]*;', r'\1http2 on;', text)
        else:
            text = re.sub(r'(?m)^\s*http2\s+[^;]*;\s*\n?', '', text)
            def legacy_http2(match):
                line = match.group(0)
                return line if re.search(r'\bhttp2\b', line) else line[:-1] + ' http2;'
            text = re.sub(r'(?m)^\s*listen\s+[^;]*\bssl\b[^;]*;', legacy_http2, text)
    if static_enabled and not re.search(r'location\s+(?:\^~\s+)?/static/', text):
        directives += [
            '    location ^~ /static/ {',
            '        alias ' + quoted_path(Path(app_root) / 'app/static') + ';',
            '        autoindex off;',
            '        etag on;',
            '        add_header Cache-Control "public, max-age=604800";',
            '        add_header X-Content-Type-Options "nosniff";',
            '    }',
        ]
    if media_enabled:
        if re.search(r'location\s+[^\n{]*/_ism_media/', text):
            raise ValueError('An unmanaged /_ism_media location already exists')
        directives += [
            '    location ^~ /_ism_media/ {',
            '        internal;',
            '        alias ' + quoted_path(upload_root) + ';',
            '        autoindex off;',
            '        etag on;',
            '        add_header Cache-Control "private, max-age=2592000, immutable";',
            '        add_header Vary "Cookie";',
            '        add_header X-Content-Type-Options "nosniff";',
            '    }',
        ]
    block = '\n    ' + BEGIN + '\n' + '\n'.join(directives) + '\n    ' + END + '\n'
    match = re.search(r'(?m)^\s*server\s*\{', text)
    return text[:match.end()] + block + text[match.end():]


def worker_can(user, path, mode):
    result = subprocess.run(['runuser', '-u', user, '--', 'test', mode, str(path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def grant_tree(user, directory):
    """Grant only traversal on parents; never chmod 755 the whole /root."""
    path = Path(directory).resolve()
    path.mkdir(parents=True, exist_ok=True)
    for parent in reversed(path.parents):
        if str(parent) == '/':
            continue
        if not worker_can(user, parent, '-x'):
            subprocess.run(['setfacl', '-m', f'u:{user}:--x', str(parent)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    # Existing files and future files need readable ACLs only inside this
    # specific static/image subtree. Never recurse over sql_backups/recycle.
    subprocess.run(['setfacl', '-R', '-m', f'u:{user}:rX', str(path)], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for base, dirs, _files in os.walk(path, followlinks=False):
        subprocess.run(['setfacl', '-m', f'd:u:{user}:r-x', base], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return worker_can(user, path, '-x') and worker_can(user, path, '-r')


def replace_text(path, value):
    path = Path(path)
    old_mode = path.stat().st_mode & 0o777
    fd, temporary = tempfile.mkstemp(prefix='.ism-nginx-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(value)
        os.chmod(temporary, old_mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(Path(__file__).resolve().parent))
    parser.add_argument('--site', default='/etc/nginx/sites-available/ism.conf')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--reload', action='store_true')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    site = Path(args.site).resolve(strict=True)
    config = yaml.safe_load((root / 'config.yaml').read_text()) or {}
    configured_uploads = Path(config.get('upload_folder', root / 'app/uploads'))
    if not configured_uploads.is_absolute():
        raise SystemExit('config.yaml upload_folder must be an absolute path')
    uploads = configured_uploads.resolve()
    limit = int(os.environ.get('MAX_CONTENT_LENGTH') or config.get('max_content_length', 20 * 1024 * 1024))
    nginx_info = subprocess.run(['nginx', '-V'], capture_output=True, text=True, check=True)
    info = nginx_info.stdout + nginx_info.stderr
    match = re.search(r'nginx/(\d+)\.(\d+)\.(\d+)', info)
    version = tuple(map(int, match.groups())) if match else (1, 24, 0)
    original = site.read_text()
    static_ok = media_ok = True
    if args.apply:
        if os.geteuid() != 0:
            raise SystemExit('Run --apply as root')
        user_match = re.search(r'(?m)^\s*user\s+(\S+)', Path('/etc/nginx/nginx.conf').read_text())
        user = user_match.group(1).rstrip(';') if user_match else 'www-data'
        pwd.getpwnam(user)
        static_path = root / 'app/static'
        static_path.mkdir(parents=True, exist_ok=True)
        for name in SUBDIRS:
            (uploads / name).mkdir(parents=True, exist_ok=True)

        if shutil.which('setfacl'):
            try:
                static_ok = grant_tree(user, static_path)
            except (OSError, subprocess.CalledProcessError) as exc:
                static_ok = False
                print('WARNING: static ACL optimization unavailable; /static stays on Flask fallback:', exc)
            try:
                for name in SUBDIRS:
                    if not grant_tree(user, uploads / name):
                        media_ok = False
            except (OSError, subprocess.CalledProcessError) as exc:
                media_ok = False
                print('WARNING: image ACL optimization unavailable; /uploads stays on Flask fallback:', exc)
        else:
            # ACL is an optional performance enhancement, never a runtime
            # dependency.  A manually upgraded server may not have the `acl`
            # package.  If nginx can already read the paths, keep acceleration;
            # otherwise remove the direct aliases and let Flask serve them.
            probe = static_path / 'login.webp'
            static_ok = worker_can(user, static_path, '-x') and (not probe.exists() or worker_can(user, probe, '-r'))
            media_ok = True
            for name in SUBDIRS:
                path = uploads / name
                if not (worker_can(user, path, '-x') and worker_can(user, path, '-r')):
                    media_ok = False
                    break
            print('WARNING: setfacl not installed; ACL optimization skipped. '
                  f'static_direct={static_ok}, image_direct={media_ok}. Flask fallback remains available.')
    patched = patch_site(original, root, uploads, limit, version,
                         '--with-http_v2_module' in info, static_ok, media_ok)
    if not args.apply:
        print(patched)
        return
    backup = site.with_name(site.name + '.ism-before-media.' + time.strftime('%Y%m%d-%H%M%S') + '.bak')
    shutil.copy2(site, backup)
    replace_text(site, patched)
    check = subprocess.run(['nginx', '-t'], capture_output=True, text=True)
    if check.returncode:
        replace_text(site, original)
        raise SystemExit('Nginx validation failed; original site restored:\n' + check.stderr)
    if args.reload:
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    print(f'ISM Nginx configured: request_limit={limit}, original_image_accel={media_ok}, static={static_ok}.')
    print('Site backup:', backup)


if __name__ == '__main__':
    main()
