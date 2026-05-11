from collections import deque
from datetime import datetime
import json
import os

from flask import request, render_template_string


DEBUG_LOG_FILE = "/tmp/asset_manager_debug_requests.jsonl"
REQUEST_HISTORY = deque(maxlen=20)
SCAN_PARAM_NAMES = ["code", "barcode", "text", "result", "data", "content", "q", "keyword"]


DEBUG_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Debug 请求查看</title>
<style>
body{font-family:Arial,sans-serif;margin:0;background:#f5f7fb;}
.wrap{max-width:980px;margin:auto;padding:12px;}
.card{background:#fff;border-radius:10px;padding:14px;margin-bottom:14px;box-shadow:0 1px 6px rgba(0,0,0,.08);}
.switch-row{display:flex;gap:8px;flex-wrap:wrap;}
.switch-row a{flex:1 1 160px;}
.switch-row a button{width:100%;}
button{padding:10px 14px;font-size:14px;border:none;border-radius:6px;background:#0d6efd;color:#fff;cursor:pointer;}
.btn-gray{background:#6c757d;}
.button-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
pre{white-space:pre-wrap;word-break:break-all;background:#0f172a;color:#e2e8f0;padding:14px;border-radius:10px;overflow:auto;font-size:13px;line-height:1.55;}
.muted{color:#666;font-size:14px;}
.section-title{font-weight:bold;margin:0 0 10px 0;}
</style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <div class="switch-row">
            <a href="/"><button type="button" class="btn-gray">资产查询</button></a>
            <a href="/cable"><button type="button" class="btn-gray">电缆查询</button></a>
            <a href="/debug"><button type="button">Debug</button></a>
        </div>
    </div>

    <div class="card">
        <div class="section-title">说明</div>
        <div class="muted">本页只保留“本次”和“上次”的有效信息，重点查看对方实际传来的格式和内容。</div>
        <div class="muted" style="margin-top:6px;">推荐测试地址：<code>{{ debug_direct_example }}</code></div>
        <div class="button-row" style="margin-top:12px;">
            <form method="post">
                <input type="hidden" name="action" value="clear_history">
                <button type="submit">清空记录</button>
            </form>
        </div>
    </div>

    <div class="card">
        <div class="section-title">本次收到的有效信息</div>
        {% if current_request_text %}
            <pre>{{ current_request_text }}</pre>
        {% else %}
            <div class="muted">暂无</div>
        {% endif %}
    </div>

    <div class="card">
        <div class="section-title">上次收到的有效信息</div>
        {% if previous_request_text %}
            <pre>{{ previous_request_text }}</pre>
        {% else %}
            <div class="muted">暂无</div>
        {% endif %}
    </div>
</div>
</body>
</html>
"""


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def flatten_multidict_values(multidict):
    result = {}
    for key in multidict.keys():
        values = [normalize_text(v) for v in multidict.getlist(key)]
        values = [v for v in values if v]
        if not values:
            continue
        result[key] = values[0] if len(values) == 1 else values
    return result


def safe_body_preview():
    data = request.get_data(cache=True) or b""
    if not data:
        return ""
    preview = data[:2000]
    try:
        return preview.decode("utf-8", errors="replace").strip()
    except Exception:
        return repr(preview)


def extract_json_payload():
    payload = request.get_json(silent=True)
    if payload in (None, "", [], {}):
        return None
    return payload


def detect_effective_source(path_scan_code, args_data, form_data, json_data, raw_body):
    if path_scan_code:
        return "路径尾巴", path_scan_code

    for key in SCAN_PARAM_NAMES:
        value = args_data.get(key)
        if value:
            return "Query参数", {key: value}

    if args_data:
        return "Query参数", args_data

    for key in SCAN_PARAM_NAMES:
        value = form_data.get(key)
        if value:
            return "Form表单", {key: value}

    if form_data:
        return "Form表单", form_data

    if json_data is not None:
        return "JSON", json_data

    if raw_body:
        return "原始Body", raw_body

    return "未识别到有效内容", ""


def clean_payload_for_display(payload):
    """移除内部使用的字段，只保留显示需要的字段"""
    if not isinstance(payload, dict):
        return payload
    cleaned = {k: v for k, v in payload.items() if k not in ["pretty"]}
    return cleaned


def to_display_text(payload):
    """将payload转换为显示用的JSON字符串，不包含pretty字段"""
    display_payload = clean_payload_for_display(payload)
    return json.dumps(display_payload, ensure_ascii=False, indent=2, default=str)


def extract_request_payload(scan_code=""):
    path_scan_code = normalize_text(scan_code)
    args_data = flatten_multidict_values(request.args)
    form_data = flatten_multidict_values(request.form)
    if form_data.get("action") == "clear_history":
        form_data.pop("action", None)
    json_data = extract_json_payload()
    raw_body = safe_body_preview()

    source_type, source_content = detect_effective_source(
        path_scan_code=path_scan_code,
        args_data=args_data,
        form_data=form_data,
        json_data=json_data,
        raw_body=raw_body,
    )

    payload = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": request.method,
        "url": request.url,
        "path": request.path,
        "收到方式": source_type,
        "收到内容": source_content,
    }

    extras = {}
    if path_scan_code:
        extras["path_scan_code"] = path_scan_code
    if args_data:
        extras["args"] = args_data
    if form_data:
        extras["form"] = form_data
    if json_data is not None:
        extras["json"] = json_data
    if raw_body and source_type != "原始Body":
        extras["raw_body_preview"] = raw_body

    if extras:
        payload["附带信息"] = extras

    return payload


def append_to_file(payload):
    os.makedirs(os.path.dirname(DEBUG_LOG_FILE), exist_ok=True)
    with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
        # 存储时不包含pretty字段
        f.write(json.dumps(clean_payload_for_display(payload), ensure_ascii=False, default=str) + "\n")


def load_recent_from_file(limit=2):
    if not os.path.exists(DEBUG_LOG_FILE):
        return []
    with open(DEBUG_LOG_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    rows = []
    for line in reversed(lines[-limit:]):
        try:
            item = json.loads(line)
        except Exception:
            continue
        rows.append(item)
    return rows


def clear_history_storage():
    REQUEST_HISTORY.clear()
    if os.path.exists(DEBUG_LOG_FILE):
        try:
            os.remove(DEBUG_LOG_FILE)
        except OSError:
            pass


def register_debug_routes(app):
    @app.route("/debug", defaults={"scan_code": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.route("/debug<scan_code>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def debug_request_view(scan_code=""):
        if request.method == "POST" and request.form.get("action") == "clear_history":
            clear_history_storage()
            current_text = ""
            previous_text = ""
        else:
            path_scan_code = normalize_text(scan_code)
            payload = extract_request_payload(path_scan_code)
            REQUEST_HISTORY.appendleft(payload)
            append_to_file(payload)

            history = load_recent_from_file(limit=2)
            current_text = to_display_text(history[0]) if len(history) >= 1 else to_display_text(payload)
            previous_text = to_display_text(history[1]) if len(history) >= 2 else ""

        debug_url_root = request.url_root.rstrip("/")
        return render_template_string(
            DEBUG_HTML,
            current_request_text=current_text,
            previous_request_text=previous_text,
            debug_direct_example=debug_url_root + "/debug",
        )