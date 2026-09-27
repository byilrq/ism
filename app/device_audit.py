from datetime import date

from app import db
from app.models import Asset, Accessory, DeviceChangeLog


_ASSET_FIELDS = [
    ("group_no", "集团编号"),
    ("internal_no", "内部编号"),
    ("name", "名称"),
    ("model", "型号"),
    ("owner", "责任人"),
    ("location", "位置"),
    ("status", "状态"),
    ("remark", "备注"),
]

_ACCESSORY_FIELDS = [
    ("sub_group_no", "集团编号"),
    ("sub_internal_no", "内部编号"),
    ("name", "名称"),
    ("model", "型号"),
    ("owner", "责任人"),
    ("location", "位置"),
    ("status", "状态"),
    ("remark", "备注"),
]


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def device_type_name(obj):
    return "配件" if isinstance(obj, Accessory) else "主设备"


def device_group_no(obj):
    if isinstance(obj, Accessory):
        return _clean(obj.sub_group_no)
    return _clean(obj.group_no)


def device_asset_no(obj):
    if isinstance(obj, Accessory):
        return _clean(obj.sub_internal_no) or _clean(obj.sub_group_no) or f"ID:{obj.id}"
    return _clean(obj.internal_no) or _clean(obj.group_no) or f"ID:{obj.id}"


def device_asset_name(obj):
    return _clean(getattr(obj, "name", ""))


def device_snapshot(obj):
    fields = _ACCESSORY_FIELDS if isinstance(obj, Accessory) else _ASSET_FIELDS
    return {attr: _clean(getattr(obj, attr, None)) for attr, _label in fields}


def _describe_image_changes(image_result):
    if not image_result:
        return []
    parts = []
    saved = int(image_result.get("saved", 0) or 0)
    deleted = int(image_result.get("deleted", 0) or 0)
    duplicates = int(image_result.get("duplicates", 0) or 0)
    if saved:
        parts.append(f"图片：上传{saved}张")
    if deleted:
        parts.append(f"图片：删除{deleted}张")
    if duplicates:
        parts.append(f"图片：重复跳过{duplicates}张")
    return parts


def describe_device_changes(before, obj, image_result=None, default="保存设备"):
    """Describe every audited field that changed on a device.

    Includes both numbering fields, name, model, owner, location, status,
    remark and image operations.  The date field is deliberately omitted from
    the textual diff because it is automatically refreshed on every save.
    """
    fields = _ACCESSORY_FIELDS if isinstance(obj, Accessory) else _ASSET_FIELDS
    parts = []
    after = device_snapshot(obj)
    for attr, label in fields:
        old = _clean((before or {}).get(attr, ""))
        new = _clean(after.get(attr, ""))
        if old != new:
            parts.append(f"{label}：{old or '空'} → {new or '空'}")
    parts.extend(_describe_image_changes(image_result))
    return "；".join(parts) if parts else default


def describe_device_creation(obj, image_result=None, prefix=None):
    """Describe the initial values for a newly-created device."""
    fields = _ACCESSORY_FIELDS if isinstance(obj, Accessory) else _ASSET_FIELDS
    parts = [prefix or ("新增配件" if isinstance(obj, Accessory) else "新增主设备")]
    snapshot = device_snapshot(obj)
    for attr, label in fields:
        value = _clean(snapshot.get(attr, ""))
        if value:
            parts.append(f"{label}：{value}")
    parts.extend(_describe_image_changes(image_result))
    return "；".join(parts)


def log_device_change(obj, content, touch_date=True):
    if touch_date and hasattr(obj, "asset_date"):
        obj.asset_date = date.today()
    cleaned_content = _clean(content) or "保存设备"
    if cleaned_content == "盘点":
        cleaned_content = f"盘点：{date.today().isoformat()}"
    row = DeviceChangeLog(
        device_type=device_type_name(obj),
        device_id=getattr(obj, "id", None),
        group_no=device_group_no(obj),
        asset_no=device_asset_no(obj),
        asset_name=device_asset_name(obj),
        change_content=cleaned_content,
    )
    db.session.add(row)
    return row
