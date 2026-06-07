from datetime import datetime, date
from io import BytesIO
import os
import re
import shutil
import uuid
from types import SimpleNamespace

from flask import request, redirect, url_for, render_template, render_template_string, send_file, send_from_directory, abort, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash
from sqlalchemy import or_, func
from openpyxl import Workbook, load_workbook

from app import db
from app.models import (
    User, Asset, Accessory, DictOption,
    AssetImage, AccessoryImage
)
from config import Config

try:
    from .cable import register_cable_routes, Cable
    from .debug import register_debug_routes
except ImportError:
    from cable import register_cable_routes, Cable
    from debug import register_debug_routes


ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
IMPORT_EXPORT_HEADERS = ["类型", "集团编号", "内部编号", "名称", "型号", "责任人", "位置", "时间", "状态", "备注"]
IMPORT_LOG_SUBDIR = "import_logs"

try:
    from .ocr_recognizer import (
        SCAN_TIMEOUT_SECONDS,
        ScanTimeoutError,
        extract_group_no_from_label,
        scan_time_limit,
    )
except ImportError:
    from ocr_recognizer import (
        SCAN_TIMEOUT_SECONDS,
        ScanTimeoutError,
        extract_group_no_from_label,
        scan_time_limit,
    )

try:
    from .ocr_gemeni import (
        SCAN_TIMEOUT_SECONDS as LABEL_SCAN_TIMEOUT_SECONDS,
        ScanTimeoutError as LabelScanTimeoutError,
        extract_group_no_from_label as extract_group_no_from_label_image,
        scan_time_limit as label_scan_time_limit,
    )
except ImportError:
    from ocr_gemeni import (
        SCAN_TIMEOUT_SECONDS as LABEL_SCAN_TIMEOUT_SECONDS,
        ScanTimeoutError as LabelScanTimeoutError,
        extract_group_no_from_label as extract_group_no_from_label_image,
        scan_time_limit as label_scan_time_limit,
    )


def normalize_status_value(value):
    if value is None:
        return ""
    value = str(value).strip()
    if value == "维修":
        return "计量"
    return value


def get_statuses():
    rows = DictOption.query.filter_by(
        dict_type="status",
        is_active=True
    ).order_by(DictOption.sort_order.asc()).all()

    status_items = []
    seen = set()
    for row in rows:
        status_value = normalize_status_value(getattr(row, "dict_value", ""))
        if not status_value or status_value in seen:
            continue
        status_items.append(SimpleNamespace(dict_value=status_value))
        seen.add(status_value)

    if "其它" not in seen:
        status_items.append(SimpleNamespace(dict_value="其它"))

    return status_items


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_empty_to_none(value):
    value = normalize_text(value)
    return value if value else None


def normalize_location(value):
    if value is None:
        return ""

    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        return normalize_location(value[0])

    value = normalize_text(value)
    if not value:
        return ""

    tuple_match = re.fullmatch(r"\(\s*[\'\"](.+?)[\'\"]\s*,\s*\)", value)
    if tuple_match:
        value = normalize_text(tuple_match.group(1))

    return value.upper()


def location_equals(column, value):
    value = normalize_location(value)
    return func.upper(column) == value


def location_like(column, value):
    value = normalize_location(value)
    return func.upper(column).like(f"%{value}%")


def prefer_new_value(new_value, old_value=None):
    new_value = normalize_text(new_value)
    if new_value:
        return new_value
    return normalize_text(old_value)


def prefer_new_date(new_value, old_value=None):
    return new_value if new_value else old_value


def make_accessory_prefix(value):
    value = normalize_text(value)
    if not value:
        return ""
    return value if value.endswith("-") else f"{value}-"


def validate_required_number_pair(internal_no, group_no, internal_label="内部编号", group_label="集团编号"):
    if not normalize_text(internal_no) and not normalize_text(group_no):
        return f"{internal_label}和{group_label}至少填写一个"
    return ""


def validate_accessory_no_format(internal_no, group_no, internal_label="附属资产内部编号", group_label="附属资产集团编号"):
    pattern = re.compile(r"^[A-Za-z0-9]+-\d+$")
    for value, label in [(normalize_text(internal_no), internal_label), (normalize_text(group_no), group_label)]:
        if value and not pattern.match(value):
            return f"{label}格式不正确，应类似 651411001001-001"
    return ""


def validate_asset_group_no(group_no, label="集团编号"):
    group_no = normalize_text(group_no)
    if group_no and not re.fullmatch(r"\d{18}", group_no):
        return f"{label}必须为18位数字"
    return ""


def validate_accessory_group_no(group_no, label="附属资产集团编号"):
    group_no = normalize_text(group_no)
    if group_no and not re.fullmatch(r"\d{18}-\d+", group_no):
        return f"{label}必须为18位主编号-序号"
    return ""


def split_accessory_number(value):
    value = normalize_text(value)
    if not value or "-" not in value:
        return "", ""
    main_no, suffix = value.rsplit("-", 1)
    return normalize_text(main_no), normalize_text(suffix)


def normalize_group_accessory_suffix(suffix):
    """集团配件后缀：去掉前导 0，-004 视为 -4。"""
    suffix = normalize_text(suffix)
    if suffix.isdigit():
        return str(int(suffix))
    return suffix


def normalize_internal_accessory_suffix(suffix):
    """内部配件后缀：数字后缀统一补足 3 位，-4 对应 -004，-14 对应 -014。"""
    suffix = normalize_text(suffix)
    if suffix.isdigit():
        return suffix.zfill(3)
    return suffix


def accessory_suffixes_equivalent(internal_suffix, group_suffix):
    internal_suffix = normalize_text(internal_suffix)
    group_suffix = normalize_text(group_suffix)
    if not internal_suffix or not group_suffix:
        return True
    if internal_suffix.isdigit() and group_suffix.isdigit():
        return int(internal_suffix) == int(group_suffix)
    return internal_suffix == group_suffix


def validate_accessory_pair_consistency(internal_no, group_no, internal_label="附属资产内部编号", group_label="附属资产集团编号"):
    internal_no = normalize_text(internal_no)
    group_no = normalize_text(group_no)

    if not internal_no or not group_no:
        return ""

    base_internal, internal_suffix = split_accessory_number(internal_no)
    base_group, group_suffix = split_accessory_number(group_no)

    if internal_suffix and group_suffix and not accessory_suffixes_equivalent(internal_suffix, group_suffix):
        return f"{internal_label}和{group_label}的序号数字必须一致，当前一个是-{internal_suffix}另一个是-{group_suffix}"

    parent_by_internal = Asset.query.filter_by(internal_no=base_internal).first() if base_internal else None
    parent_by_group = Asset.query.filter_by(group_no=base_group).first() if base_group else None
    if parent_by_internal and parent_by_group and parent_by_internal.id != parent_by_group.id:
        return f"{internal_label}和{group_label}对应的主设备不一致"

    return ""


def find_existing_asset(internal_no="", group_no="", exclude_id=None):
    conditions = []
    internal_no = normalize_text(internal_no)
    group_no = normalize_text(group_no)

    if internal_no:
        conditions.append(Asset.internal_no == internal_no)
    if group_no:
        conditions.append(Asset.group_no == group_no)

    if not conditions:
        return None

    query = Asset.query.filter(or_(*conditions))
    if exclude_id is not None:
        query = query.filter(Asset.id != exclude_id)
    return query.first()


def find_existing_accessory(internal_no="", group_no="", exclude_id=None):
    conditions = []
    internal_no = normalize_text(internal_no)
    group_no = normalize_text(group_no)

    if internal_no:
        conditions.append(Accessory.sub_internal_no == internal_no)
    if group_no:
        conditions.append(Accessory.sub_group_no == group_no)

    if not conditions:
        return None

    query = Accessory.query.filter(or_(*conditions))
    if exclude_id is not None:
        query = query.filter(Accessory.id != exclude_id)
    return query.first()


def find_parent_asset_by_accessory_numbers(internal_no="", group_no=""):
    internal_no = normalize_text(internal_no)
    group_no = normalize_text(group_no)

    if "-" in internal_no:
        base_internal = normalize_text(internal_no.rsplit("-", 1)[0])
        if base_internal:
            parent = Asset.query.filter_by(internal_no=base_internal).first()
            if parent:
                return parent

    if "-" in group_no:
        base_group = normalize_text(group_no.rsplit("-", 1)[0])
        if base_group:
            parent = Asset.query.filter_by(group_no=base_group).first()
            if parent:
                return parent

    return None


def resolve_parent_asset_id(internal_no="", group_no="", fallback_parent_asset_id=None):
    parent = find_parent_asset_by_accessory_numbers(internal_no=internal_no, group_no=group_no)
    if parent:
        return parent.id

    fallback_parent_asset_id = normalize_text(fallback_parent_asset_id)
    if fallback_parent_asset_id.isdigit():
        fallback_parent = Asset.query.get(int(fallback_parent_asset_id))
        if fallback_parent:
            return fallback_parent.id

    return None


def get_accessory_suffix_sort_key(item):
    """配件排序：优先按集团编号末尾 -数字 后缀从小到大，再按内部编号兜底。"""
    group_no = normalize_text(getattr(item, "sub_group_no", ""))
    internal_no = normalize_text(getattr(item, "sub_internal_no", ""))

    for value in [group_no, internal_no]:
        suffix_match = re.search(r"-(\d+)$", value)
        if suffix_match:
            suffix_text = suffix_match.group(1)
            try:
                suffix_num = int(suffix_text)
            except Exception:
                suffix_num = 10 ** 12
            prefix = value[:suffix_match.start()]
            return (0, prefix, suffix_num, len(suffix_text), value, getattr(item, "id", 0))

    return (1, group_no or internal_no or "~~~~", 10 ** 12, 0, group_no or internal_no or "~~~~", getattr(item, "id", 0))


def get_asset_sort_key(item):
    return (normalize_text(getattr(item, "group_no", "")) or normalize_text(getattr(item, "internal_no", "")) or "~~~~", getattr(item, "id", 0))


def get_asset_related_accessories(asset):
    if not asset:
        return []

    accessory_map = {}

    for item in Accessory.query.filter_by(parent_asset_id=asset.id).all():
        accessory_map[item.id] = item

    conditions = []
    if asset.internal_no:
        conditions.append(Accessory.sub_internal_no.like(f"{asset.internal_no}-%"))
    if asset.group_no:
        conditions.append(Accessory.sub_group_no.like(f"{asset.group_no}-%"))

    if conditions:
        standalone_items = Accessory.query.filter(
            Accessory.parent_asset_id.is_(None),
            or_(*conditions)
        ).all()
        for item in standalone_items:
            accessory_map[item.id] = item

    return sorted(accessory_map.values(), key=get_accessory_suffix_sort_key)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_visitor_role():
    return normalize_text(session.get("visitor_role"))


def has_manage_access():
    return bool(getattr(current_user, "is_authenticated", False)) or get_visitor_role() == "editor"


def has_read_access():
    return has_manage_access() or get_visitor_role() == "viewer"


def get_user_display_name():
    if getattr(current_user, "is_authenticated", False):
        return current_user.username
    role = get_visitor_role()
    if role == "editor":
        return "高级访客"
    if role == "viewer":
        return "普通访客"
    return "访客模式"


def ensure_read_access():
    if has_read_access():
        return None
    return redirect(url_for("login"))


def ensure_manage_access():
    if has_manage_access():
        return None
    if get_visitor_role() == "viewer":
        return ("当前访客链接仅支持检索", 403)
    return redirect(url_for("login"))


RECYCLE_SUBDIR = "recycle"


class AssetLocationImage(db.Model):
    __tablename__ = "asset_location_image"

    id = db.Column(db.Integer, primary_key=True)
    location_name = db.Column(db.String(255), nullable=False, index=True)
    image_path = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


def extract_main_number(value):
    value = normalize_text(value)
    if not value:
        return ""
    return value.split("-", 1)[0]


def sanitize_image_prefix(value):
    value = extract_main_number(value)
    value = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("._-")
    return value or "asset"


def build_image_filename_prefix(group_no="", internal_no="", parent_asset=None):
    if parent_asset:
        parent_group_no = sanitize_image_prefix(getattr(parent_asset, "group_no", ""))
        if parent_group_no != "asset":
            return parent_group_no
        parent_internal_no = sanitize_image_prefix(getattr(parent_asset, "internal_no", ""))
        if parent_internal_no != "asset":
            return parent_internal_no

    group_prefix = sanitize_image_prefix(group_no)
    if group_prefix != "asset":
        return group_prefix

    internal_prefix = sanitize_image_prefix(internal_no)
    if internal_prefix != "asset":
        return internal_prefix

    return "asset"


def save_uploaded_image(file_storage, subdir, filename_prefix="asset"):
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_file(file_storage.filename):
        return None

    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    date_part = datetime.now().strftime("%Y.%m.%d")
    random_part = uuid.uuid4().hex[:16]
    safe_prefix = sanitize_image_prefix(filename_prefix)
    filename = f"{safe_prefix}.{date_part}.{random_part}.{ext}"

    folder = os.path.join(Config.UPLOAD_FOLDER, subdir)
    os.makedirs(folder, exist_ok=True)

    abs_path = os.path.join(folder, filename)
    file_storage.save(abs_path)

    return f"{subdir}/{filename}"


def move_image_file_to_recycle(relative_path):
    if not relative_path:
        return

    safe_relative_path = os.path.normpath(relative_path).replace("\\", "/")
    if safe_relative_path.startswith(".."):
        return
    if safe_relative_path == RECYCLE_SUBDIR or safe_relative_path.startswith(f"{RECYCLE_SUBDIR}/"):
        return

    abs_path = os.path.join(Config.UPLOAD_FOLDER, safe_relative_path)
    if not os.path.exists(abs_path) or not os.path.isfile(abs_path):
        return

    recycle_dir = os.path.join(
        Config.UPLOAD_FOLDER,
        RECYCLE_SUBDIR,
        os.path.dirname(safe_relative_path)
    )
    os.makedirs(recycle_dir, exist_ok=True)

    recycle_filename = os.path.basename(safe_relative_path)
    target_path = os.path.join(recycle_dir, recycle_filename)

    if os.path.exists(target_path):
        name, ext = os.path.splitext(recycle_filename)
        target_path = os.path.join(recycle_dir, f"{name}.{uuid.uuid4().hex[:8]}{ext}")

    shutil.move(abs_path, target_path)


def delete_image_file(relative_path):
    move_image_file_to_recycle(relative_path)


def permanent_delete_image_file(relative_path):
    if not relative_path:
        return

    safe_relative_path = os.path.normpath(relative_path).replace("\\", "/")
    if safe_relative_path.startswith(".."):
        return

    abs_path = os.path.join(Config.UPLOAD_FOLDER, safe_relative_path)
    if os.path.exists(abs_path) and os.path.isfile(abs_path):
        os.remove(abs_path)


def trim_asset_images(asset):
    images = AssetImage.query.filter_by(asset_id=asset.id).order_by(AssetImage.created_at.asc(), AssetImage.id.asc()).all()
    while len(images) > 5:
        old = images.pop(0)
        permanent_delete_image_file(old.image_path)
        db.session.delete(old)


def trim_accessory_images(accessory):
    images = AccessoryImage.query.filter_by(accessory_id=accessory.id).order_by(AccessoryImage.created_at.asc(), AccessoryImage.id.asc()).all()
    while len(images) > 5:
        old = images.pop(0)
        permanent_delete_image_file(old.image_path)
        db.session.delete(old)


def get_asset_location_images(location):
    location = normalize_location(location)
    if not location:
        return []
    return AssetLocationImage.query.filter(location_equals(AssetLocationImage.location_name, location)).order_by(AssetLocationImage.created_at.asc(), AssetLocationImage.id.asc()).all()


def trim_asset_location_images(location):
    images = get_asset_location_images(location)
    while len(images) > 5:
        old = images.pop(0)
        permanent_delete_image_file(old.image_path)
        db.session.delete(old)


def delete_accessory_with_files(accessory):
    if not accessory:
        return
    for img in AccessoryImage.query.filter_by(accessory_id=accessory.id).all():
        delete_image_file(img.image_path)
    db.session.delete(accessory)


def delete_asset_with_files(asset):
    if not asset:
        return
    accessories = get_asset_related_accessories(asset)
    for accessory in accessories:
        delete_accessory_with_files(accessory)
    for img in AssetImage.query.filter_by(asset_id=asset.id).all():
        delete_image_file(img.image_path)
    db.session.delete(asset)

def build_search_rows(keyword="", searched=False):
    if not searched:
        return []

    keyword = normalize_text(keyword)
    rows = []

    if not keyword:
        asset_rows = sorted(Asset.query.all(), key=get_asset_sort_key)
        accessory_rows = Accessory.query.all()
        appended_accessory_ids = set()

        for item in asset_rows:
            accessories = get_asset_related_accessories(item)
            rows.append({
                "row_type": "asset",
                "id": item.id,
                "type_text": "主设备",
                "internal_no": item.internal_no or "",
                "group_no": item.group_no or "",
                "name": item.name,
                "model": item.model or "",
                "status": normalize_status_value(item.status),
                "owner": item.owner or "",
                "location": item.location or "",
                "location_detail_url": url_for("asset_location_detail", location=item.location) if item.location else "",
                "parent_asset_id": "",
                "accessory_count": len(accessories),
                "detail_url": url_for("asset_detail", asset_id=item.id),
                "asset_date_text": item.asset_date.isoformat() if item.asset_date else ""
            })

            for accessory in accessories:
                appended_accessory_ids.add(accessory.id)
                rows.append({
                    "row_type": "accessory",
                    "id": accessory.id,
                    "type_text": "配件",
                    "internal_no": accessory.sub_internal_no or "",
                    "group_no": accessory.sub_group_no or "",
                    "name": accessory.name,
                    "model": accessory.model or "",
                    "status": normalize_status_value(accessory.status),
                    "owner": accessory.owner or "",
                    "location": accessory.location or "",
                    "location_detail_url": url_for("asset_location_detail", location=accessory.location) if accessory.location else "",
                    "parent_asset_id": accessory.parent_asset_id or resolve_parent_asset_id(accessory.sub_internal_no, accessory.sub_group_no) or "",
                    "accessory_count": 0,
                    "detail_url": url_for("accessory_detail", accessory_id=accessory.id),
                    "asset_date_text": accessory.asset_date.isoformat() if accessory.asset_date else ""
                })

        standalone_accessory_rows = [item for item in accessory_rows if item.id not in appended_accessory_ids]
        for item in sorted(standalone_accessory_rows, key=get_accessory_suffix_sort_key):
            rows.append({
                "row_type": "accessory",
                "id": item.id,
                "type_text": "配件",
                "internal_no": item.sub_internal_no or "",
                "group_no": item.sub_group_no or "",
                "name": item.name,
                "model": item.model or "",
                "status": normalize_status_value(item.status),
                "owner": item.owner or "",
                "location": item.location or "",
                "location_detail_url": url_for("asset_location_detail", location=item.location) if item.location else "",
                "parent_asset_id": item.parent_asset_id or "",
                "accessory_count": 0,
                "detail_url": url_for("accessory_detail", accessory_id=item.id),
                "asset_date_text": item.asset_date.isoformat() if item.asset_date else ""
            })

        return rows

    is_group_asset_keyword = bool(re.fullmatch(r"\d{18}", keyword))
    is_group_accessory_keyword = bool(re.fullmatch(r"\d{18}-\d+", keyword))
    is_precise_group_keyword = is_group_asset_keyword or is_group_accessory_keyword

    suffix_keyword = "" if is_precise_group_keyword else (keyword[-6:] if len(keyword) >= 6 else "")

    exact_assets = Asset.query.filter(
        or_(
            Asset.internal_no == keyword,
            Asset.group_no == keyword
        )
    ).order_by(Asset.internal_no.asc()).all()

    suffix_assets = []
    if suffix_keyword:
        suffix_assets = Asset.query.filter(
            or_(
                Asset.internal_no.like(f"%{suffix_keyword}"),
                Asset.group_no.like(f"%{suffix_keyword}")
            )
        ).order_by(Asset.internal_no.asc()).all()

    asset_ids = []
    for a in exact_assets + suffix_assets:
        if a.id not in asset_ids:
            asset_ids.append(a.id)

    remark_search_enabled = len(keyword) >= 2

    # ---------- 主设备检索条件 ----------
    owner_asset_conditions = []
    # 名称检索始终启用（只要有关键词）
    if keyword:
        owner_asset_conditions.append(Asset.name.like(f"%{keyword}%"))
    if not is_precise_group_keyword:
        owner_asset_conditions.extend([
            Asset.owner.like(f"%{keyword}%"),
            location_like(Asset.location, keyword)
        ])
    if remark_search_enabled:
        owner_asset_conditions.append(Asset.remark.like(f"%{keyword}%"))

    owner_assets = []
    if owner_asset_conditions:
        owner_assets = Asset.query.filter(or_(*owner_asset_conditions)).order_by(Asset.internal_no.asc()).all()

    for a in owner_assets:
        if a.id not in asset_ids:
            asset_ids.append(a.id)

    # ---------- 配件检索条件 ----------
    exact_accessories = Accessory.query.filter(
        or_(
            Accessory.sub_internal_no == keyword,
            Accessory.sub_group_no == keyword
        )
    ).all()

    suffix_accessories = []
    if suffix_keyword:
        suffix_accessories = Accessory.query.filter(
            or_(
                Accessory.sub_internal_no.like(f"%{suffix_keyword}"),
                Accessory.sub_group_no.like(f"%{suffix_keyword}-%")
            )
        ).all()

    owner_accessory_conditions = []
    if keyword:
        owner_accessory_conditions.append(Accessory.name.like(f"%{keyword}%"))
    if not is_precise_group_keyword:
        owner_accessory_conditions.extend([
            Accessory.owner.like(f"%{keyword}%"),
            location_like(Accessory.location, keyword)
        ])
    if remark_search_enabled:
        owner_accessory_conditions.append(Accessory.remark.like(f"%{keyword}%"))

    owner_accessories = []
    if owner_accessory_conditions:
        owner_accessories = Accessory.query.filter(or_(*owner_accessory_conditions)).all()

    for acc in exact_accessories + suffix_accessories + owner_accessories:
        if acc.parent_asset_id and acc.parent_asset_id not in asset_ids:
            asset_ids.append(acc.parent_asset_id)

    fuzzy_accessories = Accessory.query.filter(
        or_(
            Accessory.sub_internal_no.like(f"{keyword}-%"),
            Accessory.sub_group_no.like(f"{keyword}-%")
        )
    ).all()

    standalone_accessories = []
    standalone_accessory_ids = set()
    for acc in fuzzy_accessories + exact_accessories + suffix_accessories + owner_accessories:
        resolved_parent_id = acc.parent_asset_id or resolve_parent_asset_id(
            internal_no=acc.sub_internal_no,
            group_no=acc.sub_group_no
        )
        if resolved_parent_id:
            if resolved_parent_id not in asset_ids:
                asset_ids.append(resolved_parent_id)
        else:
            if acc.id not in standalone_accessory_ids:
                standalone_accessories.append(acc)
                standalone_accessory_ids.add(acc.id)

    # 主设备及挂载配件
    if asset_ids:
        matched_assets = sorted(Asset.query.filter(Asset.id.in_(asset_ids)).all(), key=get_asset_sort_key)

        for asset in matched_assets:
            accessories = get_asset_related_accessories(asset)

            rows.append({
                "row_type": "asset",
                "id": asset.id,
                "type_text": "主设备",
                "internal_no": asset.internal_no or "",
                "group_no": asset.group_no or "",
                "name": asset.name,
                "model": asset.model or "",
                "status": normalize_status_value(asset.status),
                "owner": asset.owner or "",
                "location": asset.location or "",
                "location_detail_url": url_for("asset_location_detail", location=asset.location) if asset.location else "",
                "parent_asset_id": "",
                "accessory_count": len(accessories),
                "detail_url": url_for("asset_detail", asset_id=asset.id),
                "asset_date_text": asset.asset_date.isoformat() if asset.asset_date else ""
            })

            for item in accessories:
                rows.append({
                    "row_type": "accessory",
                    "id": item.id,
                    "type_text": "配件",
                    "internal_no": item.sub_internal_no or "",
                    "group_no": item.sub_group_no or "",
                    "name": item.name,
                    "model": item.model or "",
                    "status": normalize_status_value(item.status),
                    "owner": item.owner or "",
                    "location": item.location or "",
                    "location_detail_url": url_for("asset_location_detail", location=item.location) if item.location else "",
                    "parent_asset_id": item.parent_asset_id or resolve_parent_asset_id(item.sub_internal_no, item.sub_group_no) or "",
                    "accessory_count": 0,
                    "detail_url": url_for("accessory_detail", accessory_id=item.id),
                    "asset_date_text": item.asset_date.isoformat() if item.asset_date else ""
                })

    existing_row_keys = {(row["row_type"], row["id"]) for row in rows}
    for item in sorted(standalone_accessories, key=get_accessory_suffix_sort_key):
        if ("accessory", item.id) not in existing_row_keys:
            rows.append({
                "row_type": "accessory",
                "id": item.id,
                "type_text": "配件",
                "internal_no": item.sub_internal_no or "",
                "group_no": item.sub_group_no or "",
                "name": item.name,
                "model": item.model or "",
                "status": normalize_status_value(item.status),
                "owner": item.owner or "",
                "location": item.location or "",
                "location_detail_url": url_for("asset_location_detail", location=item.location) if item.location else "",
                "parent_asset_id": item.parent_asset_id or "",
                "accessory_count": 0,
                "detail_url": url_for("accessory_detail", accessory_id=item.id),
                "asset_date_text": item.asset_date.isoformat() if item.asset_date else ""
            })

    # 最终兜底：没有任何关联主设备的配件，单独显示
    if not rows:
        standalone_conditions = [
            Accessory.sub_internal_no == keyword,
            Accessory.sub_group_no == keyword,
            Accessory.sub_internal_no.like(f"{keyword}-%"),
            Accessory.sub_group_no.like(f"{keyword}-%"),
        ]
        # 配件名称检索始终启用
        standalone_conditions.append(Accessory.name.like(f"%{keyword}%"))
        if not is_precise_group_keyword:
            standalone_conditions.extend([
                Accessory.owner.like(f"%{keyword}%"),
                location_like(Accessory.location, keyword)
            ])
        if remark_search_enabled:
            standalone_conditions.append(Accessory.remark.like(f"%{keyword}%"))
        if suffix_keyword:
            standalone_conditions.append(Accessory.sub_internal_no.like(f"%{suffix_keyword}"))
            standalone_conditions.append(Accessory.sub_group_no.like(f"%{suffix_keyword}-%"))

        standalone_exact = Accessory.query.filter(
            or_(*standalone_conditions)
        ).order_by(Accessory.sub_internal_no.asc()).all()

        for item in sorted(standalone_exact, key=get_accessory_suffix_sort_key):
            rows.append({
                "row_type": "accessory",
                "id": item.id,
                "type_text": "配件",
                "internal_no": item.sub_internal_no or "",
                "group_no": item.sub_group_no or "",
                "name": item.name,
                "model": item.model or "",
                "status": normalize_status_value(item.status),
                "owner": item.owner or "",
                "location": item.location or "",
                "location_detail_url": url_for("asset_location_detail", location=item.location) if item.location else "",
                "parent_asset_id": item.parent_asset_id or "",
                "accessory_count": 0,
                "detail_url": url_for("accessory_detail", accessory_id=item.id),
                "asset_date_text": item.asset_date.isoformat() if item.asset_date else ""
            })

    if not rows:
        parent_search_keyword = resolve_accessory_parent_search_keyword(keyword)
        if parent_search_keyword and parent_search_keyword != keyword:
            return build_search_rows(keyword=parent_search_keyword, searched=True)

    return rows





    exact_assets = Asset.query.filter(
        or_(
            Asset.internal_no == keyword,
            Asset.group_no == keyword
        )
    ).order_by(Asset.internal_no.asc()).all()

    asset_ids = []
    for a in exact_assets:
        if a.id not in asset_ids:
            asset_ids.append(a.id)

    owner_assets = Asset.query.filter(
        Asset.owner.like(f"%{keyword}%")
    ).order_by(Asset.internal_no.asc()).all()

    for a in owner_assets:
        if a.id not in asset_ids:
            asset_ids.append(a.id)

    exact_accessories = Accessory.query.filter(
        or_(
            Accessory.sub_internal_no == keyword,
            Accessory.sub_group_no == keyword
        )
    ).all()

    owner_accessories = Accessory.query.filter(
        Accessory.owner.like(f"%{keyword}%")
    ).all()

    for acc in exact_accessories + owner_accessories:
        if acc.parent_asset_id and acc.parent_asset_id not in asset_ids:
            asset_ids.append(acc.parent_asset_id)

    fuzzy_accessories = Accessory.query.filter(
        or_(
            Accessory.sub_internal_no.like(f"{keyword}-%"),
            Accessory.sub_group_no.like(f"{keyword}-%")
        )
    ).all()

    standalone_accessories = []
    standalone_accessory_ids = set()
    for acc in fuzzy_accessories + exact_accessories + owner_accessories:
        resolved_parent_id = acc.parent_asset_id or resolve_parent_asset_id(
            internal_no=acc.sub_internal_no,
            group_no=acc.sub_group_no
        )
        if resolved_parent_id:
            if resolved_parent_id not in asset_ids:
                asset_ids.append(resolved_parent_id)
        else:
            if acc.id not in standalone_accessory_ids:
                standalone_accessories.append(acc)
                standalone_accessory_ids.add(acc.id)

    if asset_ids:
        matched_assets = Asset.query.filter(Asset.id.in_(asset_ids)).order_by(Asset.internal_no.asc()).all()

        for asset in matched_assets:
            accessories = get_asset_related_accessories(asset)

            rows.append({
                "row_type": "asset",
                "id": asset.id,
                "type_text": "主设备",
                "internal_no": asset.internal_no or "",
                "group_no": asset.group_no or "",
                "name": asset.name,
                "model": asset.model or "",
                "status": normalize_status_value(asset.status),
                "owner": asset.owner or "",
                "location": asset.location or "",
                "location_detail_url": url_for("asset_location_detail", location=asset.location) if asset.location else "",
                "parent_asset_id": "",
                "accessory_count": len(accessories),
                "detail_url": url_for("asset_detail", asset_id=asset.id),
                "asset_date_text": asset.asset_date.isoformat() if asset.asset_date else ""
            })

            for item in accessories:
                rows.append({
                    "row_type": "accessory",
                    "id": item.id,
                    "type_text": "配件",
                    "internal_no": item.sub_internal_no or "",
                    "group_no": item.sub_group_no or "",
                    "name": item.name,
                    "model": item.model or "",
                    "status": normalize_status_value(item.status),
                    "owner": item.owner or "",
                    "location": item.location or "",
                    "location_detail_url": url_for("asset_location_detail", location=item.location) if item.location else "",
                    "parent_asset_id": item.parent_asset_id or resolve_parent_asset_id(item.sub_internal_no, item.sub_group_no) or "",
                    "accessory_count": 0,
                    "detail_url": url_for("accessory_detail", accessory_id=item.id),
                    "asset_date_text": item.asset_date.isoformat() if item.asset_date else ""
                })

    existing_row_keys = {(row["row_type"], row["id"]) for row in rows}
    for item in standalone_accessories:
        if ("accessory", item.id) not in existing_row_keys:
            rows.append({
                "row_type": "accessory",
                "id": item.id,
                "type_text": "配件",
                "internal_no": item.sub_internal_no or "",
                "group_no": item.sub_group_no or "",
                "name": item.name,
                "model": item.model or "",
                "status": normalize_status_value(item.status),
                "owner": item.owner or "",
                "location": item.location or "",
                "location_detail_url": url_for("asset_location_detail", location=item.location) if item.location else "",
                "parent_asset_id": item.parent_asset_id or "",
                "accessory_count": 0,
                "detail_url": url_for("accessory_detail", accessory_id=item.id),
                "asset_date_text": item.asset_date.isoformat() if item.asset_date else ""
            })

    if not rows:
        standalone_exact = Accessory.query.filter(
            or_(
                Accessory.sub_internal_no == keyword,
                Accessory.sub_group_no == keyword,
                Accessory.sub_internal_no.like(f"{keyword}-%"),
                Accessory.sub_group_no.like(f"{keyword}-%"),
                Accessory.owner.like(f"%{keyword}%")
            )
        ).order_by(Accessory.sub_internal_no.asc()).all()

        for item in standalone_exact:
            rows.append({
                "row_type": "accessory",
                "id": item.id,
                "type_text": "配件",
                "internal_no": item.sub_internal_no or "",
                "group_no": item.sub_group_no or "",
                "name": item.name,
                "model": item.model or "",
                "status": normalize_status_value(item.status),
                "owner": item.owner or "",
                "location": item.location or "",
                "location_detail_url": url_for("asset_location_detail", location=item.location) if item.location else "",
                "parent_asset_id": item.parent_asset_id or "",
                "accessory_count": 0,
                "detail_url": url_for("accessory_detail", accessory_id=item.id),
                "asset_date_text": item.asset_date.isoformat() if item.asset_date else ""
            })

    return rows


def get_import_header_alias_map():
    return {
        "类型": {"类型", "设备类型"},
        "内部编号": {"内部编号", "附属资产内部编号"},
        "集团编号": {"集团编号", "附属资产集团编号"},
        "名称": {"名称", "资产名称"},
        "型号": {"型号"},
        "责任人": {"责任人", "负责人"},
        "位置": {"位置", "货架位置"},
        "时间": {"时间", "日期", "盘点时间"},
        "状态": {"状态"},
        "备注": {"备注"},
    }


LEGACY_IMPORT_COLUMN_MAP = {
    "集团编号": 0,
    "内部编号": 1,
    "名称": 2,
    "型号": 3,
    "责任人": 4,
    "位置": 5,
    "状态": 6,
    "备注": 7,
    "类型": 8,
}


def build_import_column_map(ws):
    header_values = [normalize_text(cell) for cell in next(ws.iter_rows(min_row=1, max_row=1, values_only=True), [])]
    expected_headers = IMPORT_EXPORT_HEADERS
    actual_headers = header_values[:len(expected_headers)]
    if actual_headers != expected_headers:
        raise ValueError(
            "Excel表头必须与导出格式完全一致："
            + "、".join(expected_headers)
            + "；当前表头："
            + "、".join(actual_headers)
        )

    return {header: idx for idx, header in enumerate(expected_headers)}, 2


def get_import_row_value(row, column_map, column_name):
    idx = column_map.get(column_name)
    if idx is None or idx >= len(row):
        return ""
    return normalize_text(row[idx])


def parse_import_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = normalize_text(value)
    if not text:
        return None

    text = text.replace("年", "-").replace("月", "-").replace("日", "")
    text = text.replace("/", "-").replace(".", "-")

    for fmt in ["%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"]:
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue

    try:
        return datetime.fromisoformat(text).date()
    except Exception:
        return None


def build_import_row_data(row, column_map):
    time_idx = column_map.get("时间")
    time_value = row[time_idx] if time_idx is not None and time_idx < len(row) else None
    return {
        "类型": get_import_row_value(row, column_map, "类型"),
        "内部编号": get_import_row_value(row, column_map, "内部编号"),
        "集团编号": get_import_row_value(row, column_map, "集团编号"),
        "名称": get_import_row_value(row, column_map, "名称"),
        "型号": get_import_row_value(row, column_map, "型号"),
        "责任人": get_import_row_value(row, column_map, "责任人"),
        "位置": get_import_row_value(row, column_map, "位置"),
        "时间": time_value,
        "状态": get_import_row_value(row, column_map, "状态"),
        "备注": get_import_row_value(row, column_map, "备注"),
    }


def is_import_row_empty(row_data):
    return not any(
        normalize_text(row_data.get(column_name))
        for column_name in ["类型", "内部编号", "集团编号", "名称", "型号", "责任人", "位置", "状态", "备注"]
    ) and not parse_import_date(row_data.get("时间"))


def normalize_import_device_type(device_type, internal_no="", group_no=""):
    # 批量上传时不再读取或校验“类型”列，设备类型完全由集团编号决定：
    # 18位纯数字 = 主设备；18位纯数字-序号 = 配件。
    group_no = normalize_text(group_no)

    if re.fullmatch(r"\d{18}-\d+", group_no):
        return "配件"
    if re.fullmatch(r"\d{18}", group_no):
        return "主设备"
    return ""


def validate_import_group_no_required(group_no, label="集团编号"):
    if not normalize_text(group_no):
        return f"{label}必须填写"
    return ""


def validate_import_group_no_type(group_no):
    group_no = normalize_text(group_no)
    if not group_no:
        return "集团编号必须填写"
    if re.fullmatch(r"\d{18}", group_no) or re.fullmatch(r"\d{18}-\d+", group_no):
        return ""
    return "集团编号格式不正确：主设备应为18位数字，配件应为18位数字-序号"


def get_asset_matches(internal_no="", group_no=""):
    internal_no = normalize_text(internal_no)
    group_no = normalize_text(group_no)

    if group_no:
        matches = Asset.query.filter_by(group_no=group_no).all()
        if matches:
            return matches

    # 兼容补齐：数据库中已有内部编号但集团编号为空时，
    # 允许用本次导入的内部编号匹配旧记录，并补上集团编号。
    if internal_no:
        matches = Asset.query.filter(
            Asset.internal_no == internal_no,
            or_(Asset.group_no.is_(None), Asset.group_no == "")
        ).all()
        if matches:
            return matches

    if group_no:
        # 兼容修复：历史错位导入可能把集团编号写进了内部编号，且集团编号为空。
        return Asset.query.filter(
            Asset.internal_no == group_no,
            or_(Asset.group_no.is_(None), Asset.group_no == "")
        ).all()

    return []


def get_accessory_matches(internal_no="", group_no=""):
    internal_no = normalize_text(internal_no)
    group_no = normalize_text(group_no)

    if group_no:
        matches = Accessory.query.filter_by(sub_group_no=group_no).all()
        if matches:
            return matches

    # 兼容补齐：数据库中已有附属资产内部编号但集团编号为空时，
    # 允许用本次导入的内部编号匹配旧记录，并补上附属资产集团编号。
    if internal_no:
        matches = Accessory.query.filter(
            Accessory.sub_internal_no == internal_no,
            or_(Accessory.sub_group_no.is_(None), Accessory.sub_group_no == "")
        ).all()
        if matches:
            return matches

    if group_no:
        # 兼容修复：历史错位导入可能把附属资产集团编号写进了附属资产内部编号，且附属资产集团编号为空。
        return Accessory.query.filter(
            Accessory.sub_internal_no == group_no,
            or_(Accessory.sub_group_no.is_(None), Accessory.sub_group_no == "")
        ).all()

    return []


def resolve_parent_asset_id_by_group_no(group_no=""):
    group_no = normalize_text(group_no)
    if "-" not in group_no:
        return None
    base_group_no = normalize_text(group_no.rsplit("-", 1)[0])
    if not base_group_no:
        return None
    parent = Asset.query.filter_by(group_no=base_group_no).first()
    return parent.id if parent else None


def get_single_match(matches, error_message):
    if len(matches) > 1:
        raise ValueError(error_message)
    return matches[0] if matches else None


def complete_accessory_numbers(internal_no="", group_no=""):
    internal_no = normalize_text(internal_no)
    group_no = normalize_text(group_no)
    parent = find_parent_asset_by_accessory_numbers(internal_no=internal_no, group_no=group_no)

    if internal_no and not group_no and "-" in internal_no and parent and parent.group_no:
        suffix = internal_no.rsplit("-", 1)[1]
        group_suffix = normalize_group_accessory_suffix(suffix)
        group_no = f"{parent.group_no}-{group_suffix}"

    if group_no and not internal_no and "-" in group_no and parent and parent.internal_no:
        suffix = group_no.rsplit("-", 1)[1]
        internal_suffix = normalize_internal_accessory_suffix(suffix)
        internal_no = f"{parent.internal_no}-{internal_suffix}"

    return internal_no, group_no


def upsert_asset_from_import_row(row_data):
    internal_no = normalize_text(row_data.get("内部编号"))
    group_no = normalize_text(row_data.get("集团编号"))
    name = normalize_text(row_data.get("名称"))
    model = normalize_text(row_data.get("型号"))
    owner = normalize_text(row_data.get("责任人"))
    location = normalize_location(row_data.get("位置"))
    status = normalize_status_value(row_data.get("状态"))
    remark = normalize_text(row_data.get("备注"))
    asset_date = parse_import_date(row_data.get("时间"))

    number_error = validate_import_group_no_required(group_no, "集团编号")
    if not number_error:
        number_error = validate_asset_group_no(group_no, "集团编号")
    if number_error:
        raise ValueError(number_error)

    obj = get_single_match(
        get_asset_matches(internal_no=internal_no, group_no=group_no),
        "主设备编号同时匹配到多条现有记录，无法自动更新"
    )

    if obj:
        # 编号字段按“Excel 有值才覆盖，空白保留现有值”处理；
        # 可用于补齐历史记录缺失的集团编号或内部编号。
        if group_no:
            obj.group_no = group_no
        if internal_no:
            obj.internal_no = internal_no
        obj.name = prefer_new_value(name, obj.name) or obj.internal_no or obj.group_no or obj.name
        obj.model = prefer_new_value(model, obj.model)
        obj.owner = prefer_new_value(owner, obj.owner)
        obj.location = prefer_new_value(location, obj.location)
        obj.status = prefer_new_value(status, obj.status)
        obj.remark = prefer_new_value(remark, obj.remark)
        obj.asset_date = prefer_new_date(asset_date, obj.asset_date)
    else:
        obj = Asset(
            group_no=normalize_empty_to_none(group_no),
            internal_no=normalize_empty_to_none(internal_no),
            name=name or internal_no or group_no,
            model=model,
            owner=owner,
            location=location,
            status=status,
            remark=remark,
            asset_date=asset_date or date.today()
        )
        db.session.add(obj)

    db.session.flush()
    return obj


def upsert_accessory_from_import_row(row_data):
    internal_no = normalize_text(row_data.get("内部编号"))
    group_no = normalize_text(row_data.get("集团编号"))
    name = normalize_text(row_data.get("名称"))
    model = normalize_text(row_data.get("型号"))
    owner = normalize_text(row_data.get("责任人"))
    location = normalize_location(row_data.get("位置"))
    status = normalize_status_value(row_data.get("状态"))
    remark = normalize_text(row_data.get("备注"))
    asset_date = parse_import_date(row_data.get("时间"))

    number_error = validate_import_group_no_required(group_no, "附属资产集团编号")
    if not number_error:
        number_error = validate_accessory_group_no(group_no, "附属资产集团编号")
    if number_error:
        raise ValueError(number_error)

    parent_asset_id = resolve_parent_asset_id_by_group_no(group_no=group_no)
    obj = get_single_match(
        get_accessory_matches(internal_no=internal_no, group_no=group_no),
        "配件编号同时匹配到多条现有记录，无法自动更新"
    )

    if obj:
        obj.parent_asset_id = parent_asset_id or obj.parent_asset_id
        # 编号字段按“Excel 有值才覆盖，空白保留现有值”处理；
        # 可用于补齐历史记录缺失的附属资产集团编号或内部编号。
        if group_no:
            obj.sub_group_no = group_no
        if internal_no:
            obj.sub_internal_no = internal_no
        obj.name = prefer_new_value(name, obj.name) or obj.sub_internal_no or obj.sub_group_no or obj.name
        obj.model = prefer_new_value(model, obj.model)
        obj.owner = prefer_new_value(owner, obj.owner)
        obj.location = prefer_new_value(location, obj.location)
        obj.status = prefer_new_value(status, obj.status)
        obj.remark = prefer_new_value(remark, obj.remark)
        obj.asset_date = prefer_new_date(asset_date, obj.asset_date)
    else:
        obj = Accessory(
            parent_asset_id=parent_asset_id,
            sub_group_no=normalize_empty_to_none(group_no),
            sub_internal_no=normalize_empty_to_none(internal_no),
            name=name or internal_no or group_no,
            model=model,
            owner=owner,
            location=location,
            status=status,
            remark=remark,
            asset_date=asset_date or date.today()
        )
        db.session.add(obj)

    db.session.flush()
    return obj


def create_import_failure_log(failure_rows):
    if not failure_rows:
        return ""

    folder = os.path.join(Config.UPLOAD_FOLDER, IMPORT_LOG_SUBDIR)
    os.makedirs(folder, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "导入失败日志"
    ws.append(["Excel行号", "错误原因"] + IMPORT_EXPORT_HEADERS)

    for item in failure_rows:
        row_data = item.get("row_data", {})
        ws.append([
            item.get("row_index"),
            item.get("error"),
            normalize_text(row_data.get("类型")),
            normalize_text(row_data.get("集团编号")),
            normalize_text(row_data.get("内部编号")),
            normalize_text(row_data.get("名称")),
            normalize_text(row_data.get("型号")),
            normalize_text(row_data.get("责任人")),
            normalize_text(row_data.get("位置")),
            parse_import_date(row_data.get("时间")).isoformat() if parse_import_date(row_data.get("时间")) else normalize_text(row_data.get("时间")),
            normalize_text(row_data.get("状态")),
            normalize_text(row_data.get("备注")),
        ])

    filename = f"import_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.xlsx"
    wb.save(os.path.join(folder, filename))
    return filename


def import_devices_from_excel(file_storage):
    wb = load_workbook(file_storage, data_only=True)
    ws = wb.active
    column_map, data_start_row = build_import_column_map(ws)
    success_count = 0
    failure_rows = []

    for row_index, row in enumerate(ws.iter_rows(min_row=data_start_row, values_only=True), start=data_start_row):
        row_data = build_import_row_data(row, column_map)
        if is_import_row_empty(row_data):
            continue

        device_type = normalize_import_device_type(
            row_data.get("类型"),
            row_data.get("内部编号"),
            row_data.get("集团编号")
        )
        row_data["类型"] = device_type

        try:
            if device_type not in ["主设备", "配件"]:
                raise ValueError(validate_import_group_no_type(row_data.get("集团编号")))

            with db.session.begin_nested():
                if device_type == "主设备":
                    upsert_asset_from_import_row(row_data)
                else:
                    upsert_accessory_from_import_row(row_data)
            success_count += 1
        except Exception as e:
            failure_rows.append({
                "row_index": row_index,
                "error": str(e),
                "row_data": row_data,
            })

    log_filename = create_import_failure_log(failure_rows)
    return {
        "success_count": success_count,
        "failed_count": len(failure_rows),
        "failure_rows": failure_rows,
        "log_filename": log_filename,
    }


def is_group_no_value(value):
    value = normalize_text(value)
    return bool(re.fullmatch(r"\d{18}", value))


def get_recognized_no_label(value):
    return "集团编号" if is_group_no_value(value) else "内部编号"


def is_cable_code_value(value):
    return bool(re.fullmatch(r"DMDL\d{6}", normalize_text(value).upper()))


def is_group_asset_code_value(value):
    return bool(re.fullmatch(r"\d{18}", normalize_text(value)))


def is_group_accessory_code_value(value):
    return bool(re.fullmatch(r"\d{18}-\d+", normalize_text(value)))


def is_internal_accessory_code_value(value):
    return bool(re.fullmatch(r"[A-Za-z0-9]+-\d+", normalize_text(value)))


def is_accessory_code_value(value):
    return is_group_accessory_code_value(value) or is_internal_accessory_code_value(value)


def resolve_accessory_parent_asset(value):
    """通过配件号反查主设备。

    支持集团配件号（18位主号-序号）和内部配件号（内部主号-序号）。
    后缀只用于判断这是配件号，不参与主设备匹配；这样 -4、-004 都能先识别为配件，
    再按主号向上查找对应主设备。
    """
    value = normalize_text(value)
    if not is_accessory_code_value(value):
        return None

    main_no, _suffix = split_accessory_number(value)
    if not main_no:
        return None

    if is_group_asset_code_value(main_no):
        parent = Asset.query.filter_by(group_no=main_no).first()
        if parent:
            return parent

    parent = Asset.query.filter_by(internal_no=main_no).first()
    if parent:
        return parent

    return Asset.query.filter_by(group_no=main_no).first()


def resolve_accessory_parent_search_keyword(value):
    parent = resolve_accessory_parent_asset(value)
    if not parent:
        return ""
    return normalize_text(parent.group_no) or normalize_text(parent.internal_no)


def build_parent_search_redirect_payload(code):
    parent_search_keyword = resolve_accessory_parent_search_keyword(code)
    if not parent_search_keyword:
        return None
    return {
        "ok": True,
        "action": "redirect",
        "redirect_url": url_for("search_assets", searched=1, keyword=parent_search_keyword),
        "message": "未找到该配件编号，已按主设备编号检索并显示主设备及全部配件",
        "code": code,
        "parent_keyword": parent_search_keyword,
    }


def resolve_scan_target(recognized_no):
    recognized_no = normalize_text(recognized_no)
    if not recognized_no:
        return None, []

    rows = build_search_rows(keyword=recognized_no, searched=True)
    if not rows:
        return None, []

    asset_row = next(
        (
            row for row in rows
            if row.get("row_type") == "asset"
            and (
                normalize_text(row.get("group_no")) == recognized_no
                or normalize_text(row.get("internal_no")) == recognized_no
            )
        ),
        None,
    )
    if asset_row:
        return asset_row, rows

    accessory_row = next(
        (
            row for row in rows
            if row.get("row_type") == "accessory"
            and (
                normalize_text(row.get("group_no")) == recognized_no
                or normalize_text(row.get("internal_no")) == recognized_no
            )
        ),
        None,
    )
    if accessory_row:
        return accessory_row, rows

    accessory_prefix_row = next(
        (
            row for row in rows
            if row.get("row_type") == "accessory"
            and (
                normalize_text(row.get("group_no")).startswith(f"{recognized_no}-")
                or normalize_text(row.get("internal_no")).startswith(f"{recognized_no}-")
            )
        ),
        None,
    )
    if accessory_prefix_row:
        return accessory_prefix_row, rows

    return rows[0], rows


def process_scan_code_action(scan_mode, recognized_no, assign_location="", confirm_update=False):
    scan_mode = normalize_text(scan_mode) or "search"
    recognized_no = normalize_text(recognized_no)
    assign_location = normalize_location(assign_location)
    if not recognized_no:
        return {"ok": False, "message": "缺少识别结果"}, 400

    normalized_code = recognized_no.upper() if is_cable_code_value(recognized_no) else recognized_no

    if assign_location:
        try:
            if is_group_accessory_code_value(normalized_code):
                accessory = Accessory.query.filter_by(sub_group_no=normalized_code).first()
                if accessory:
                    if not confirm_update:
                        return {
                            "ok": True,
                            "action": "confirm_location_update",
                            "asset_type": "配件",
                            "group_no": accessory.sub_group_no or normalized_code,
                            "name": accessory.name or "",
                            "target_location": assign_location,
                            "message": f"是否将该资产（{accessory.sub_group_no or normalized_code}，{accessory.name or ''}）添加至本货架（{assign_location}）下？",
                            "code": normalized_code
                        }, 200
                    accessory.location = assign_location
                    db.session.commit()
                    return {"ok": True, "action": "updated", "message": f"已更新配件位置到：{assign_location}", "code": normalized_code}, 200
                return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="配件", group_no=normalized_code, location=assign_location), "message": "未找到配件，正在跳转新增", "code": normalized_code}, 200

            if is_group_asset_code_value(normalized_code):
                asset = Asset.query.filter_by(group_no=normalized_code).first()
                if asset:
                    if not confirm_update:
                        return {
                            "ok": True,
                            "action": "confirm_location_update",
                            "asset_type": "主设备",
                            "group_no": asset.group_no or normalized_code,
                            "name": asset.name or "",
                            "target_location": assign_location,
                            "message": f"是否将该资产（{asset.group_no or normalized_code}，{asset.name or ''}）添加至本货架（{assign_location}）下？",
                            "code": normalized_code
                        }, 200
                    asset.location = assign_location
                    db.session.commit()
                    return {"ok": True, "action": "updated", "message": f"已更新主设备位置到：{assign_location}", "code": normalized_code}, 200
                return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="主设备", group_no=normalized_code, location=assign_location), "message": "未找到主设备，正在跳转新增", "code": normalized_code}, 200

            accessory = Accessory.query.filter_by(sub_internal_no=normalized_code).first()
            if accessory:
                if not confirm_update:
                    return {
                        "ok": True,
                        "action": "confirm_location_update",
                        "asset_type": "配件",
                        "group_no": accessory.sub_group_no or "",
                        "name": accessory.name or "",
                        "target_location": assign_location,
                        "message": f"是否将该资产（{accessory.sub_group_no or normalized_code}，{accessory.name or ''}）添加至本货架（{assign_location}）下？",
                        "code": normalized_code
                    }, 200
                accessory.location = assign_location
                db.session.commit()
                return {"ok": True, "action": "updated", "message": f"已更新配件位置到：{assign_location}", "code": normalized_code}, 200

            asset = Asset.query.filter_by(internal_no=normalized_code).first()
            if asset:
                if not confirm_update:
                    return {
                        "ok": True,
                        "action": "confirm_location_update",
                        "asset_type": "主设备",
                        "group_no": asset.group_no or "",
                        "name": asset.name or "",
                        "target_location": assign_location,
                        "message": f"是否将该资产（{asset.group_no or normalized_code}，{asset.name or ''}）添加至本货架（{assign_location}）下？",
                        "code": normalized_code
                    }, 200
                asset.location = assign_location
                db.session.commit()
                return {"ok": True, "action": "updated", "message": f"已更新主设备位置到：{assign_location}", "code": normalized_code}, 200

            if is_internal_accessory_code_value(normalized_code):
                return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="配件", internal_no=normalized_code, location=assign_location), "message": "未找到配件，正在跳转新增", "code": normalized_code}, 200

            return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="主设备", internal_no=normalized_code, location=assign_location), "message": "未找到主设备，正在跳转新增", "code": normalized_code}, 200
        except Exception as e:
            db.session.rollback()
            return {"ok": False, "message": f"位置更新失败：{str(e)}", "code": normalized_code}, 500

    if scan_mode == "inventory":
        try:
            if is_group_accessory_code_value(normalized_code):
                accessory = Accessory.query.filter_by(sub_group_no=normalized_code).first()
                if accessory:
                    accessory.asset_date = date.today()
                    db.session.commit()
                    return {"ok": True, "action": "updated", "message": f"已盘点配件：{accessory.name or normalized_code}", "code": normalized_code}, 200
                return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="配件", group_no=normalized_code), "message": "未找到配件，正在跳转新增", "code": normalized_code}, 200

            if is_group_asset_code_value(normalized_code):
                asset = Asset.query.filter_by(group_no=normalized_code).first()
                if asset:
                    asset.asset_date = date.today()
                    db.session.commit()
                    return {"ok": True, "action": "updated", "message": f"已盘点主设备：{asset.name or normalized_code}", "code": normalized_code}, 200
                return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="主设备", group_no=normalized_code), "message": "未找到主设备，正在跳转新增", "code": normalized_code}, 200

            accessory = Accessory.query.filter_by(sub_internal_no=normalized_code).first()
            if accessory:
                accessory.asset_date = date.today()
                db.session.commit()
                return {"ok": True, "action": "updated", "message": f"已盘点配件：{accessory.name or normalized_code}", "code": normalized_code}, 200

            asset = Asset.query.filter_by(internal_no=normalized_code).first()
            if asset:
                asset.asset_date = date.today()
                db.session.commit()
                return {"ok": True, "action": "updated", "message": f"已盘点主设备：{asset.name or normalized_code}", "code": normalized_code}, 200

            if is_internal_accessory_code_value(normalized_code):
                return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="配件", internal_no=normalized_code), "message": "未找到配件，正在跳转新增", "code": normalized_code}, 200

            return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="主设备", internal_no=normalized_code), "message": "未找到主设备，正在跳转新增", "code": normalized_code}, 200
        except Exception as e:
            db.session.rollback()
            return {"ok": False, "message": f"盘点失败：{str(e)}", "code": normalized_code}, 500

    if is_cable_code_value(normalized_code):
        cable = Cable.query.filter_by(cable_no=normalized_code).first()
        if cable:
            return {"ok": True, "action": "redirect", "redirect_url": url_for("cable_detail", cable_id=cable.id), "message": "已找到电缆，正在打开详情", "code": normalized_code}, 200
        return {"ok": True, "action": "redirect", "redirect_url": url_for("cable_new", cable_no=normalized_code), "message": "未找到电缆，正在跳转新增", "code": normalized_code}, 200

    if is_group_accessory_code_value(normalized_code):
        accessory = Accessory.query.filter_by(sub_group_no=normalized_code).first()
        if accessory:
            return {"ok": True, "action": "redirect", "redirect_url": url_for("accessory_detail", accessory_id=accessory.id), "message": "已找到配件，正在打开详情", "code": normalized_code}, 200
        parent_payload = build_parent_search_redirect_payload(normalized_code)
        if parent_payload:
            return parent_payload, 200
        return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="配件", group_no=normalized_code), "message": "未找到配件，正在跳转新增", "code": normalized_code}, 200

    if is_group_asset_code_value(normalized_code):
        asset = Asset.query.filter_by(group_no=normalized_code).first()
        if asset:
            return {"ok": True, "action": "redirect", "redirect_url": url_for("asset_detail", asset_id=asset.id), "message": "已找到主设备，正在打开详情", "code": normalized_code}, 200
        return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="主设备", group_no=normalized_code), "message": "未找到主设备，正在跳转新增", "code": normalized_code}, 200

    accessory = Accessory.query.filter_by(sub_internal_no=normalized_code).first()
    if accessory:
        return {"ok": True, "action": "redirect", "redirect_url": url_for("accessory_detail", accessory_id=accessory.id), "message": "已找到配件，正在打开详情", "code": normalized_code}, 200

    asset = Asset.query.filter_by(internal_no=normalized_code).first()
    if asset:
        return {"ok": True, "action": "redirect", "redirect_url": url_for("asset_detail", asset_id=asset.id), "message": "已找到主设备，正在打开详情", "code": normalized_code}, 200

    if is_internal_accessory_code_value(normalized_code):
        parent_payload = build_parent_search_redirect_payload(normalized_code)
        if parent_payload:
            return parent_payload, 200
        return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="配件", internal_no=normalized_code), "message": "未找到配件，正在跳转新增", "code": normalized_code}, 200

    return {"ok": True, "action": "redirect", "redirect_url": url_for("device_new", type="主设备", internal_no=normalized_code), "message": "未找到主设备，正在跳转新增", "code": normalized_code}, 200


def register_routes(app):
    with app.app_context():
        AssetLocationImage.__table__.create(bind=db.engine, checkfirst=True)

    register_cable_routes(app)
    register_debug_routes(app)

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        safe_path = os.path.normpath(filename)
        if safe_path.startswith(".."):
            abort(404)
        return send_from_directory(Config.UPLOAD_FOLDER, safe_path)

    @app.route("/import_logs/<path:filename>")
    def download_import_log(filename):
        guard = ensure_read_access()
        if guard:
            return guard
        safe_filename = os.path.basename(filename)
        if not safe_filename:
            abort(404)
        return send_from_directory(
            os.path.join(Config.UPLOAD_FOLDER, IMPORT_LOG_SUBDIR),
            safe_filename,
            as_attachment=True
        )

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            # 只处理用户明确点击“登录”按钮提交的请求。
            # 个别手机浏览器/密码管理器可能在输入完成时触发表单提交，
            # 这类请求不应按空账号密码校验，否则会把页面刷新并清空输入框。
            if normalize_text(request.form.get("login_submit")) != "1":
                return render_template("login.html", error="")

            username = normalize_text(request.form.get("username"))
            password = normalize_text(request.form.get("password"))

            user = User.query.filter_by(username=username).first()

            if user and check_password_hash(user.password_hash, password):
                session.pop("visitor_role", None)
                login_user(user)
                return redirect(url_for("search_assets"))

            return render_template("login.html", error="用户名或密码错误")

        return render_template("login.html", error="")

    @app.route("/logout")
    @login_required
    def logout():
        session.pop("visitor_role", None)
        logout_user()
        return redirect(url_for("login"))

    @app.route("/admin", methods=["GET"])
    @app.route("/ism/admin", defaults={"scan_code": ""}, methods=["GET"])
    @app.route("/ism/admin<scan_code>", methods=["GET"])
    def enter_admin_guest(scan_code=""):
        session["visitor_role"] = "editor"
        raw_code = normalize_text(scan_code) or normalize_text(request.args.get("code")) or normalize_text(request.args.get("keyword"))
        if not raw_code:
            return redirect(url_for("search_assets"))

        normalized_code = raw_code.upper() if is_cable_code_value(raw_code) else raw_code

        if is_cable_code_value(normalized_code):
            cable = Cable.query.filter_by(cable_no=normalized_code).first()
            if cable:
                return redirect(url_for("cable_detail", cable_id=cable.id))
            return redirect(url_for("cable_new", cable_no=normalized_code))

        if is_group_accessory_code_value(normalized_code):
            accessory = Accessory.query.filter_by(sub_group_no=normalized_code).first()
            if accessory:
                return redirect(url_for("accessory_detail", accessory_id=accessory.id))
            parent_search_keyword = resolve_accessory_parent_search_keyword(normalized_code)
            if parent_search_keyword:
                return redirect(url_for("search_assets", searched=1, keyword=parent_search_keyword))
            return redirect(url_for("device_new", type="配件", group_no=normalized_code))

        if is_group_asset_code_value(normalized_code):
            asset = Asset.query.filter_by(group_no=normalized_code).first()
            if asset:
                return redirect(url_for("asset_detail", asset_id=asset.id))
            return redirect(url_for("device_new", type="主设备", group_no=normalized_code))

        accessory = Accessory.query.filter_by(sub_internal_no=normalized_code).first()
        if accessory:
            return redirect(url_for("accessory_detail", accessory_id=accessory.id))

        asset = Asset.query.filter_by(internal_no=normalized_code).first()
        if asset:
            return redirect(url_for("asset_detail", asset_id=asset.id))

        if is_internal_accessory_code_value(normalized_code):
            parent_search_keyword = resolve_accessory_parent_search_keyword(normalized_code)
            if parent_search_keyword:
                return redirect(url_for("search_assets", searched=1, keyword=parent_search_keyword))
            return redirect(url_for("device_new", type="配件", internal_no=normalized_code))

        return redirect(url_for("device_new", type="主设备", internal_no=normalized_code))

    @app.route("/", methods=["GET"])
    def search_assets():
        guard = ensure_read_access()
        if guard:
            return guard
        keyword = normalize_text(request.args.get("keyword"))
        status_filter = normalize_status_value(request.args.get("status_filter"))
        device_type_filter = normalize_text(request.args.get("device_type_filter"))
        searched = normalize_text(request.args.get("searched")) == "1"
        per_page = normalize_text(request.args.get("per_page")) or "30"
        page = normalize_text(request.args.get("page")) or "1"
        sort_field = normalize_text(request.args.get("sort_field"))
        sort_order = normalize_text(request.args.get("sort_order")).lower()
        statuses = get_statuses()

        try:
            per_page = int(per_page)
        except:
            per_page = 30

        if per_page not in [30, 50, 100]:
            per_page = 30

        try:
            page = int(page)
        except:
            page = 1

        all_rows = build_search_rows(keyword=keyword, searched=searched)

        if searched and status_filter:
            all_rows = [
                row for row in all_rows
                if normalize_text(row.get("status")) == status_filter
            ]

        if searched and device_type_filter:
            all_rows = [
                row for row in all_rows
                if normalize_text(row.get("type_text")) == device_type_filter
            ]

        if searched and sort_field == "asset_date" and sort_order in ["asc", "desc"]:
            rows_with_date = [row for row in all_rows if normalize_text(row.get("asset_date_text"))]
            rows_without_date = [row for row in all_rows if not normalize_text(row.get("asset_date_text"))]
            rows_with_date.sort(
                key=lambda row: normalize_text(row.get("asset_date_text")),
                reverse=(sort_order == "desc")
            )
            all_rows = rows_with_date + rows_without_date

        total = len(all_rows)

        start = (page - 1) * per_page
        end = start + per_page
        rows = all_rows[start:end]

        total_pages = (total + per_page - 1) // per_page if total else 1
        all_filtered_selected_items = [f"{row['row_type']}:{row['id']}" for row in all_rows]
        error = ""
        if searched and total == 0:
            if keyword and (status_filter or device_type_filter):
                error = "未找到符合筛选条件的资产"
            elif keyword:
                error = "未找到对应资产"
            elif status_filter or device_type_filter:
                error = "未找到符合筛选条件的资产"

        import_success = normalize_text(request.args.get("import_success"))
        import_failed = normalize_text(request.args.get("import_failed"))
        import_log_filename = normalize_text(request.args.get("import_log"))
        import_error = normalize_text(request.args.get("import_error"))
        import_summary_text = ""
        if import_success or import_failed:
            import_summary_text = f"本次成功录入 {import_success or '0'} 条资产，失败 {import_failed or '0'} 条。"
            if import_log_filename:
                import_summary_text += " 可在批量上传按钮后点击日志下载查看失败明细。"

        return render_template_string(
            SEARCH_HTML,
            current_user=current_user,
            can_manage=has_manage_access(),
            user_display=get_user_display_name(),
            keyword=keyword,
            status_filter=status_filter,
            device_type_filter=device_type_filter,
            statuses=statuses,
            searched=searched,
            per_page=per_page,
            page=page,
            total=total,
            total_pages=total_pages,
            rows=rows,
            error=error,
            sort_field=sort_field,
            sort_order=sort_order,
            import_success=import_success,
            import_failed=import_failed,
            import_log_filename=import_log_filename,
            import_log_url=url_for("download_import_log", filename=import_log_filename) if import_log_filename else "",
            import_summary_text=import_summary_text,
            import_error=import_error,
            all_filtered_selected_items=all_filtered_selected_items
        )


    @app.route("/asset_location", methods=["GET", "POST"])
    def asset_location_detail():
        guard = ensure_read_access()
        if guard:
            return guard
        location = normalize_location(request.args.get("location"))
        if not location:
            location = normalize_location(request.form.get("location"))
        if not location:
            return redirect(url_for("search_assets"))

        message = "货架更新成功" if normalize_text(request.args.get("saved")) == "1" else ""
        error = ""
        current_location = location

        if request.method == "POST":
            manage_guard = ensure_manage_access()
            if manage_guard:
                return manage_guard
            new_location = normalize_location(request.form.get("new_location")) or normalize_location(request.form.get("location"))
            image_files = request.files.getlist("image_files")
            delete_image_ids = request.form.getlist("delete_location_image_ids")

            if not new_location:
                error = "货架位置不能为空"
            else:
                old_location = location
                current_location = new_location
                try:
                    Asset.query.filter(location_equals(Asset.location, old_location)).update({"location": new_location}, synchronize_session=False)
                    Accessory.query.filter(location_equals(Accessory.location, old_location)).update({"location": new_location}, synchronize_session=False)
                    AssetLocationImage.query.filter(location_equals(AssetLocationImage.location_name, old_location)).update({"location_name": new_location}, synchronize_session=False)

                    for image_id in delete_image_ids:
                        try:
                            img_id = int(image_id)
                        except Exception:
                            continue
                        img = AssetLocationImage.query.filter(AssetLocationImage.id == img_id, location_equals(AssetLocationImage.location_name, current_location)).first()
                        if img:
                            delete_image_file(img.image_path)
                            db.session.delete(img)

                    image_prefix = sanitize_image_prefix(current_location)
                    for file_storage in image_files[:5]:
                        rel = save_uploaded_image(file_storage, "asset_locations", image_prefix)
                        if rel:
                            db.session.add(AssetLocationImage(location_name=current_location, image_path=rel))

                    trim_asset_location_images(current_location)
                    db.session.commit()
                    return redirect(url_for("asset_location_detail", location=current_location, saved=1))
                except Exception as e:
                    db.session.rollback()
                    error = f"更新失败：{str(e)}"

        location = current_location
        rows = []

        asset_items = Asset.query.filter(location_equals(Asset.location, location)).order_by(Asset.internal_no.asc(), Asset.group_no.asc(), Asset.id.asc()).all()
        for item in asset_items:
            rows.append({
                "group_no": item.group_no or "",
                "internal_no": item.internal_no or "",
                "name": item.name or "",
                "model": item.model or "",
                "status": normalize_status_value(item.status),
                "owner": item.owner or "",
                "type_text": "主设备",
                "asset_date_text": item.asset_date.isoformat() if item.asset_date else "",
                "detail_url": url_for("asset_detail", asset_id=item.id),
            })

        accessory_items = Accessory.query.filter(location_equals(Accessory.location, location)).order_by(Accessory.sub_internal_no.asc(), Accessory.sub_group_no.asc(), Accessory.id.asc()).all()
        for item in accessory_items:
            rows.append({
                "group_no": item.sub_group_no or "",
                "internal_no": item.sub_internal_no or "",
                "name": item.name or "",
                "model": item.model or "",
                "status": normalize_status_value(item.status),
                "owner": item.owner or "",
                "type_text": "配件",
                "asset_date_text": item.asset_date.isoformat() if item.asset_date else "",
                "detail_url": url_for("accessory_detail", accessory_id=item.id),
            })

        rows.sort(key=lambda x: (x.get("internal_no") or x.get("group_no") or "", x.get("type_text") or "", x.get("name") or ""))

        return render_template_string(
            ASSET_LOCATION_HTML,
            current_user=current_user,
            can_manage=has_manage_access(),
            user_display=get_user_display_name(),
            location=location,
            form_data={"location": location},
            rows=rows,
            readonly=True,
            images=get_asset_location_images(location),
            message=message,
            error=error,
            asset_new_url=url_for("device_new", location=location),
            asset_scan_url=url_for("scan_label_assign", location=location),
            asset_scan_image_url=url_for("scan_label_image", location=location),
        )


    @app.route("/ism", defaults={"scan_code": ""}, methods=["GET"])
    @app.route("/ism<scan_code>", methods=["GET"])
    def ism_dispatch(scan_code=""):
        raw_code = normalize_text(scan_code) or normalize_text(request.args.get("code")) or normalize_text(request.args.get("keyword"))
        if not raw_code:
            session["visitor_role"] = "viewer"
            return redirect(url_for("search_assets"))

        normalized_code = raw_code.upper() if is_cable_code_value(raw_code) else raw_code

        if is_cable_code_value(normalized_code):
            cable = Cable.query.filter_by(cable_no=normalized_code).first()
            if cable:
                return redirect(url_for("cable_detail", cable_id=cable.id))
            return redirect(url_for("cable_new", cable_no=normalized_code))

        if is_group_accessory_code_value(normalized_code):
            accessory = Accessory.query.filter_by(sub_group_no=normalized_code).first()
            if accessory:
                return redirect(url_for("accessory_detail", accessory_id=accessory.id))
            parent_search_keyword = resolve_accessory_parent_search_keyword(normalized_code)
            if parent_search_keyword:
                return redirect(url_for("search_assets", searched=1, keyword=parent_search_keyword))
            return redirect(url_for("device_new", type="配件", group_no=normalized_code))

        if is_group_asset_code_value(normalized_code):
            asset = Asset.query.filter_by(group_no=normalized_code).first()
            if asset:
                return redirect(url_for("asset_detail", asset_id=asset.id))
            return redirect(url_for("device_new", type="主设备", group_no=normalized_code))

        accessory = Accessory.query.filter_by(sub_internal_no=normalized_code).first()
        if accessory:
            return redirect(url_for("accessory_detail", accessory_id=accessory.id))

        asset = Asset.query.filter_by(internal_no=normalized_code).first()
        if asset:
            return redirect(url_for("asset_detail", asset_id=asset.id))

        if is_internal_accessory_code_value(normalized_code):
            parent_search_keyword = resolve_accessory_parent_search_keyword(normalized_code)
            if parent_search_keyword:
                return redirect(url_for("search_assets", searched=1, keyword=parent_search_keyword))
            return redirect(url_for("device_new", type="配件", internal_no=normalized_code))

        return redirect(url_for("device_new", type="主设备", internal_no=normalized_code))

    @app.route("/scan_label", methods=["GET"])
    def scan_label():
        guard = ensure_read_access()
        if guard:
            return guard
        return render_template_string(SCAN_LABEL_HTML)


    @app.route("/scan_label_assign", methods=["GET"])
    def scan_label_assign():
        guard = ensure_read_access()
        if guard:
            return guard
        assign_location = normalize_location(request.args.get("location"))
        if not assign_location:
            return redirect(url_for("search_assets"))
        back_url = url_for("asset_location_detail", location=assign_location)
        return render_template_string(SCAN_LABEL_ASSIGN_HTML, assign_location=assign_location, back_url=back_url)


    @app.route("/scan_label_action", methods=["GET"])
    def scan_label_action():
        guard = ensure_read_access()
        if guard:
            return jsonify({"ok": False, "message": "无访问权限"}), 403

        scan_mode = normalize_text(request.args.get("mode")) or "search"
        if scan_mode == "inventory":
            manage_guard = ensure_manage_access()
            if manage_guard:
                return jsonify({"ok": False, "message": "当前账号无盘点权限"}), 403

        payload, status_code = process_scan_code_action(scan_mode, request.args.get("code"), request.args.get("location"), normalize_text(request.args.get("confirm_update")) == "1")
        return jsonify(payload), status_code

    @app.route("/scan_label_image", methods=["GET"])
    def scan_label_image():
        guard = ensure_read_access()
        if guard:
            return guard
        assign_location = normalize_location(request.args.get("location"))
        back_url = url_for("asset_location_detail", location=assign_location) if assign_location else url_for("search_assets")
        return render_template_string(SCAN_LABEL_IMAGE_HTML, assign_location=assign_location, back_url=back_url)

    @app.route("/scan_label_image_action", methods=["POST"])
    def scan_label_image_action():
        guard = ensure_read_access()
        if guard:
            return jsonify({"ok": False, "message": "无访问权限"}), 403

        scan_mode = normalize_text(request.form.get("mode")) or "search"
        if scan_mode == "inventory":
            manage_guard = ensure_manage_access()
            if manage_guard:
                return jsonify({"ok": False, "message": "当前账号无盘点权限"}), 403

        scan_file = request.files.get("scan_image")
        if not scan_file or not getattr(scan_file, "filename", ""):
            return jsonify({"ok": False, "message": "缺少截图图片"}), 400

        try:
            with label_scan_time_limit(LABEL_SCAN_TIMEOUT_SECONDS):
                recognized_no, debug_texts = extract_group_no_from_label_image(scan_file)
            recognized_no = normalize_text(recognized_no)
            if not recognized_no:
                return jsonify({"ok": False, "message": "未识别到有效标签编号"}), 400
            payload, status_code = process_scan_code_action(scan_mode, recognized_no, request.form.get("location"), normalize_text(request.form.get("confirm_update")) == "1")
            payload.setdefault("code", recognized_no)
            if debug_texts:
                payload.setdefault("debug_texts", debug_texts)
            return jsonify(payload), status_code
        except LabelScanTimeoutError:
            return jsonify({"ok": False, "message": f"识别超时，已自动终止（超过{LABEL_SCAN_TIMEOUT_SECONDS}秒）"}), 408
        except Exception as e:
            return jsonify({"ok": False, "message": f"识别失败：{str(e)}"}), 500

    @app.route("/export", methods=["POST"])
    def export_selected():
        guard = ensure_read_access()
        if guard:
            return guard
        selected_items = request.form.getlist("selected_items")

        # 导出顺序必须与当前查询结果显示顺序一致。
        # 浏览器提交 selected_items 时，跨页“全选”会把当前页可见项放在前面、隐藏项放在后面，
        # 直接按提交顺序导出会打乱“主资产 + 配件按后缀排序”的显示顺序。
        # 因此服务端按当前筛选条件重新生成 all_rows，再按 all_rows 顺序过滤已选项。
        selected_item_set = set(selected_items)
        if selected_item_set:
            export_keyword = normalize_text(request.form.get("keyword"))
            export_status_filter = normalize_status_value(request.form.get("status_filter"))
            export_device_type_filter = normalize_text(request.form.get("device_type_filter"))
            export_sort_field = normalize_text(request.form.get("sort_field"))
            export_sort_order = normalize_text(request.form.get("sort_order")).lower()

            ordered_rows = build_search_rows(keyword=export_keyword, searched=True)

            if export_status_filter:
                ordered_rows = [
                    row for row in ordered_rows
                    if normalize_text(row.get("status")) == export_status_filter
                ]

            if export_device_type_filter:
                ordered_rows = [
                    row for row in ordered_rows
                    if normalize_text(row.get("type_text")) == export_device_type_filter
                ]

            if export_sort_field == "asset_date" and export_sort_order in ["asc", "desc"]:
                rows_with_date = [row for row in ordered_rows if normalize_text(row.get("asset_date_text"))]
                rows_without_date = [row for row in ordered_rows if not normalize_text(row.get("asset_date_text"))]
                rows_with_date.sort(
                    key=lambda row: normalize_text(row.get("asset_date_text")),
                    reverse=(export_sort_order == "desc")
                )
                ordered_rows = rows_with_date + rows_without_date

            ordered_selected_items = []
            remaining_selected_items = set(selected_item_set)
            for row in ordered_rows:
                key = f"{row.get('row_type')}:{row.get('id')}"
                if key in remaining_selected_items:
                    ordered_selected_items.append(key)
                    remaining_selected_items.remove(key)

            # 兜底：如果有极少数已选项不在当前筛选结果中，保留用户提交顺序追加，避免静默丢失。
            for item in selected_items:
                if item in remaining_selected_items:
                    ordered_selected_items.append(item)
                    remaining_selected_items.remove(item)

            selected_items = ordered_selected_items

        wb = Workbook()
        ws = wb.active
        ws.title = "设备导出"

        ws.append(IMPORT_EXPORT_HEADERS)

        for item in selected_items:
            try:
                row_type, row_id = item.split(":")
                row_id = int(row_id)
            except:
                continue

            if row_type == "asset":
                obj = Asset.query.get(row_id)
                if obj:
                    ws.append([
                        "主设备", obj.group_no, obj.internal_no, obj.name, obj.model or "",
                        obj.owner or "", obj.location or "", obj.asset_date.isoformat() if obj.asset_date else "",
                        obj.status or "", obj.remark or ""
                    ])
            elif row_type == "accessory":
                obj = Accessory.query.get(row_id)
                if obj:
                    ws.append([
                        "配件", obj.sub_group_no, obj.sub_internal_no, obj.name, obj.model or "",
                        obj.owner or "", obj.location or "", obj.asset_date.isoformat() if obj.asset_date else "",
                        obj.status or "", obj.remark or ""
                    ])

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        filename = f"asset_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    @app.route("/delete_selected", methods=["POST"])
    def delete_selected():
        guard = ensure_manage_access()
        if guard:
            return guard
        selected_items = request.form.getlist("selected_items")
        delete_pin = normalize_text(request.form.get("delete_pin"))
        keyword = normalize_text(request.form.get("keyword"))
        status_filter = normalize_text(request.form.get("status_filter"))
        device_type_filter = normalize_text(request.form.get("device_type_filter"))
        per_page = normalize_text(request.form.get("per_page")) or "30"

        if delete_pin != "0819":
            return "批量删除失败：Pin码错误"

        asset_ids = set()
        accessory_ids = set()

        for item in selected_items:
            try:
                row_type, row_id = item.split(":")
                row_id = int(row_id)
            except:
                continue

            if row_type == "asset":
                asset_ids.add(row_id)
            elif row_type == "accessory":
                accessory_ids.add(row_id)

        try:
            for asset_id in asset_ids:
                asset = Asset.query.get(asset_id)
                if asset:
                    delete_asset_with_files(asset)

            for accessory_id in accessory_ids:
                accessory = Accessory.query.get(accessory_id)
                resolved_parent_id = None
                if accessory:
                    resolved_parent_id = accessory.parent_asset_id or resolve_parent_asset_id(
                        internal_no=accessory.sub_internal_no,
                        group_no=accessory.sub_group_no
                    )
                if accessory and resolved_parent_id not in asset_ids:
                    delete_accessory_with_files(accessory)

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return f"批量删除失败：{str(e)}"

        return redirect(url_for(
            "search_assets",
            searched=1,
            keyword=keyword,
            status_filter=status_filter,
            device_type_filter=device_type_filter,
            per_page=per_page,
            page=1
        ))

    @app.route("/import_devices", methods=["POST"])
    def import_devices():
        guard = ensure_manage_access()
        if guard:
            return guard
        excel_file = request.files.get("excel_file")
        if not excel_file or not excel_file.filename:
            return redirect(url_for("search_assets"))

        try:
            result = import_devices_from_excel(excel_file)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            error_message = str(e)
            if "Excel表头必须" in error_message:
                error_message = "表头格式及顺序不正确。\n" + error_message
            else:
                error_message = "批量上传失败：" + error_message
            return redirect(url_for("search_assets", import_error=error_message))

        redirect_args = {
            "import_success": result.get("success_count", 0),
            "import_failed": result.get("failed_count", 0),
        }
        if result.get("log_filename"):
            redirect_args["import_log"] = result["log_filename"]

        return redirect(url_for("search_assets", **redirect_args))

    @app.route("/device/new", methods=["GET", "POST"])
    def device_new():
        guard = ensure_manage_access()
        if guard:
            return guard
        statuses = get_statuses()
        error = ""

        device_type = normalize_text(request.args.get("type")) or "主设备"
        parent_asset_id = normalize_text(request.args.get("parent_asset_id"))
        recognized_no = normalize_text(request.args.get("recognized_no")) or normalize_text(request.args.get("recognized_group_no"))
        preset_group_no = normalize_text(request.args.get("group_no"))
        preset_internal_no = normalize_text(request.args.get("internal_no"))
        preset_location = normalize_location(request.args.get("location"))
        parent_asset = Asset.query.get(int(parent_asset_id)) if parent_asset_id.isdigit() else None

        default_group_no = preset_group_no
        default_internal_no = preset_internal_no
        default_location = preset_location
        if not default_group_no and not default_internal_no and device_type == "主设备" and recognized_no:
            if is_group_no_value(recognized_no):
                default_group_no = recognized_no
            else:
                default_internal_no = recognized_no
        if device_type == "配件" and parent_asset:
            default_group_no = default_group_no or make_accessory_prefix(parent_asset.group_no)
            default_internal_no = default_internal_no or make_accessory_prefix(parent_asset.internal_no)
            default_location = default_location or normalize_location(parent_asset.location)

        form_data = {
            "device_type": device_type,
            "group_no": default_group_no,
            "internal_no": default_internal_no,
            "name": "",
            "model": "",
            "owner": "",
            "location": default_location,
            "asset_date": date.today().isoformat(),
            "status": "",
            "remark": "",
            "parent_asset_id": parent_asset_id
        }

        if request.method == "POST":
            device_type = normalize_text(request.form.get("device_type")) or "主设备"
            group_no = normalize_text(request.form.get("group_no"))
            internal_no = normalize_text(request.form.get("internal_no"))
            name = normalize_text(request.form.get("name"))
            model = normalize_text(request.form.get("model"))
            owner = normalize_text(request.form.get("owner"))
            location = normalize_location(request.form.get("location"))
            asset_date_str = normalize_text(request.form.get("asset_date"))
            status = normalize_status_value(request.form.get("status"))
            remark = normalize_text(request.form.get("remark"))
            parent_asset_id = normalize_text(request.form.get("parent_asset_id"))
            image_files = request.files.getlist("image_files")

            form_data = {
                "device_type": device_type,
                "group_no": group_no,
                "internal_no": internal_no,
                "name": name,
                "model": model,
                "owner": owner,
                "location": location,
                "asset_date": asset_date_str or date.today().isoformat(),
                "status": status,
                "remark": remark,
                "parent_asset_id": parent_asset_id
            }

            if device_type == "主设备":
                number_error = validate_required_number_pair(internal_no, group_no, "内部编号", "集团编号")
                if not number_error:
                    number_error = validate_asset_group_no(group_no, "集团编号")
            else:
                number_error = validate_required_number_pair(internal_no, group_no, "附属资产内部编号", "附属资产集团编号")
                if not number_error:
                    number_error = validate_accessory_no_format(internal_no, group_no)
                if not number_error:
                    number_error = validate_accessory_group_no(group_no, "附属资产集团编号")
                if not number_error:
                    number_error = validate_accessory_pair_consistency(internal_no, group_no)

            if number_error:
                error = number_error
            elif not name:
                error = "名称不能为空"
            else:
                try:
                    if device_type == "主设备":
                        existing_group = Asset.query.filter_by(group_no=group_no).first() if group_no else None
                        existing_internal = Asset.query.filter_by(internal_no=internal_no).first() if internal_no else None

                        if existing_group:
                            error = "集团编号已存在"
                        elif existing_internal:
                            error = "内部编号已存在"
                        else:
                            obj = Asset(
                                group_no=normalize_empty_to_none(group_no),
                                internal_no=normalize_empty_to_none(internal_no),
                                name=name,
                                model=model,
                                owner=owner,
                                location=location,
                                asset_date=date.today(),
                                status=status,
                                remark=remark
                            )
                            db.session.add(obj)
                            db.session.flush()

                            image_filename_prefix = build_image_filename_prefix(
                                group_no=group_no,
                                internal_no=internal_no
                            )
                            for file_storage in image_files[:5]:
                                rel = save_uploaded_image(file_storage, "assets", image_filename_prefix)
                                if rel:
                                    db.session.add(AssetImage(asset_id=obj.id, image_path=rel))

                            trim_asset_images(obj)
                            db.session.commit()
                            return redirect(url_for("asset_detail", asset_id=obj.id))

                    else:
                        existing_group = Accessory.query.filter_by(sub_group_no=group_no).first() if group_no else None
                        existing_internal = Accessory.query.filter_by(sub_internal_no=internal_no).first() if internal_no else None

                        if existing_group:
                            error = "附属资产集团编号已存在"
                        elif existing_internal:
                            error = "附属资产内部编号已存在"
                        else:
                            parent_id = resolve_parent_asset_id(
                                internal_no=internal_no,
                                group_no=group_no,
                                fallback_parent_asset_id=parent_asset_id
                            )
                            obj = Accessory(
                                parent_asset_id=parent_id,
                                sub_group_no=normalize_empty_to_none(group_no),
                                sub_internal_no=normalize_empty_to_none(internal_no),
                                name=name,
                                model=model,
                                owner=owner,
                                location=location,
                                asset_date=date.today(),
                                status=status,
                                remark=remark
                            )
                            db.session.add(obj)
                            db.session.flush()

                            parent_asset = Asset.query.get(parent_id) if parent_id else None
                            image_filename_prefix = build_image_filename_prefix(
                                group_no=group_no,
                                internal_no=internal_no,
                                parent_asset=parent_asset
                            )
                            for file_storage in image_files[:5]:
                                rel = save_uploaded_image(file_storage, "accessories", image_filename_prefix)
                                if rel:
                                    db.session.add(AccessoryImage(accessory_id=obj.id, image_path=rel))

                            trim_accessory_images(obj)
                            db.session.commit()
                            return redirect(url_for("accessory_detail", accessory_id=obj.id))

                except Exception as e:
                    db.session.rollback()
                    error = f"保存失败：{str(e)}"

        return render_template_string(
            DEVICE_NEW_HTML,
            current_user=current_user,
            statuses=statuses,
            form_data=form_data,
            error=error
        )

    @app.route("/asset/<int:asset_id>", methods=["GET", "POST"])
    def asset_detail(asset_id):
        guard = ensure_read_access()
        if guard:
            return guard
        asset = Asset.query.get_or_404(asset_id)
        statuses = get_statuses()
        message = ""
        error = ""

        if request.method == "POST":
            manage_guard = ensure_manage_access()
            if manage_guard:
                return manage_guard
            action = normalize_text(request.form.get("action"))

            if action == "inventory_asset":
                try:
                    asset.asset_date = date.today()
                    db.session.commit()
                    message = "主设备盘点时间已更新"
                except Exception as e:
                    db.session.rollback()
                    error = f"盘点失败：{str(e)}"

            elif action == "save_asset":
                group_no = normalize_text(request.form.get("group_no"))
                internal_no = normalize_text(request.form.get("internal_no"))
                name = normalize_text(request.form.get("name"))
                model = normalize_text(request.form.get("model"))
                owner = normalize_text(request.form.get("owner"))
                location = normalize_location(request.form.get("location"))
                status = normalize_status_value(request.form.get("status"))
                remark = normalize_text(request.form.get("remark"))
                image_files = request.files.getlist("image_files")
                delete_image_ids = request.form.getlist("delete_asset_image_ids")

                number_error = validate_required_number_pair(internal_no, group_no, "内部编号", "集团编号")
                if not number_error:
                    number_error = validate_asset_group_no(group_no, "集团编号")

                if number_error:
                    error = number_error
                elif not name:
                    error = "名称不能为空"
                else:
                    try:
                        existing_group = Asset.query.filter(Asset.group_no == group_no, Asset.id != asset.id).first() if group_no else None
                        existing_internal = Asset.query.filter(Asset.internal_no == internal_no, Asset.id != asset.id).first() if internal_no else None

                        if existing_group:
                            error = "集团编号已存在"
                        elif existing_internal:
                            error = "内部编号已存在"
                        else:
                            asset.group_no = normalize_empty_to_none(group_no)
                            asset.internal_no = normalize_empty_to_none(internal_no)
                            asset.name = name
                            asset.model = model
                            asset.owner = owner
                            asset.location = location
                            asset.status = status
                            asset.remark = remark

                            image_filename_prefix = build_image_filename_prefix(
                                group_no=asset.group_no,
                                internal_no=asset.internal_no
                            )

                            for image_id in delete_image_ids:
                                try:
                                    img_id = int(image_id)
                                except:
                                    continue

                                img = AssetImage.query.filter_by(id=img_id, asset_id=asset.id).first()
                                if img:
                                    delete_image_file(img.image_path)
                                    db.session.delete(img)

                            for file_storage in image_files[:5]:
                                rel = save_uploaded_image(file_storage, "assets", image_filename_prefix)
                                if rel:
                                    db.session.add(AssetImage(asset_id=asset.id, image_path=rel))

                            trim_asset_images(asset)
                            db.session.commit()
                            message = "主设备更新成功"
                    except Exception as e:
                        db.session.rollback()
                        error = f"更新失败：{str(e)}"

        accessories = get_asset_related_accessories(asset)
        images = AssetImage.query.filter_by(asset_id=asset.id).order_by(AssetImage.created_at.asc(), AssetImage.id.asc()).all()

        return render_template_string(
            ASSET_DETAIL_HTML,
            current_user=current_user,
            can_manage=has_manage_access(),
            asset=asset,
            accessories=accessories,
            images=images,
            statuses=statuses,
            today=date.today().isoformat(),
            message=message,
            error=error
        )

    @app.route("/asset/<int:asset_id>/inventory", methods=["POST"])
    def inventory_asset(asset_id):
        guard = ensure_manage_access()
        if guard:
            return guard
        asset = Asset.query.get_or_404(asset_id)
        try:
            asset.asset_date = date.today()
            db.session.commit()
            return redirect(url_for("asset_detail", asset_id=asset.id))
        except Exception as e:
            db.session.rollback()
            return f"盘点失败：{str(e)}"

    @app.route("/asset/<int:asset_id>/delete", methods=["POST"])
    def delete_asset(asset_id):
        guard = ensure_manage_access()
        if guard:
            return guard
        asset = Asset.query.get_or_404(asset_id)
        delete_pin = normalize_text(request.form.get("delete_pin"))
        if delete_pin != "0819":
            return "删除失败：Pin码错误"
        try:
            delete_asset_with_files(asset)
            db.session.commit()
            return redirect(url_for("search_assets"))
        except Exception as e:
            db.session.rollback()
            return f"删除失败：{str(e)}"

    @app.route("/accessory/<int:accessory_id>", methods=["GET", "POST"])
    def accessory_detail(accessory_id):
        guard = ensure_read_access()
        if guard:
            return guard
        accessory = Accessory.query.get_or_404(accessory_id)
        statuses = get_statuses()
        message = ""
        error = ""

        if request.method == "POST":
            manage_guard = ensure_manage_access()
            if manage_guard:
                return manage_guard
            action = normalize_text(request.form.get("action"))
            if action == "inventory_accessory":
                try:
                    accessory.asset_date = date.today()
                    db.session.commit()
                    message = "配件盘点时间已更新"
                except Exception as e:
                    db.session.rollback()
                    error = f"盘点失败：{str(e)}"
            sub_group_no = normalize_text(request.form.get("sub_group_no"))
            sub_internal_no = normalize_text(request.form.get("sub_internal_no"))
            name = normalize_text(request.form.get("name"))
            model = normalize_text(request.form.get("model"))
            owner = normalize_text(request.form.get("owner"))
            location = normalize_location(request.form.get("location"))
            status = normalize_status_value(request.form.get("status"))
            remark = normalize_text(request.form.get("remark"))
            image_files = request.files.getlist("image_files")
            delete_image_ids = request.form.getlist("delete_accessory_image_ids")

            if action != "inventory_accessory":
                number_error = validate_required_number_pair(sub_internal_no, sub_group_no, "附属资产内部编号", "附属资产集团编号")
                if not number_error:
                    number_error = validate_accessory_no_format(sub_internal_no, sub_group_no)
                if not number_error:
                    number_error = validate_accessory_group_no(sub_group_no, "附属资产集团编号")
                if not number_error:
                    number_error = validate_accessory_pair_consistency(sub_internal_no, sub_group_no)

                if number_error:
                    error = number_error
                elif not name:
                    error = "名称不能为空"
                else:
                    try:
                        existing_group = Accessory.query.filter(Accessory.sub_group_no == sub_group_no, Accessory.id != accessory.id).first() if sub_group_no else None
                        existing_internal = Accessory.query.filter(Accessory.sub_internal_no == sub_internal_no, Accessory.id != accessory.id).first() if sub_internal_no else None

                        if existing_group:
                            error = "附属资产集团编号已存在"
                        elif existing_internal:
                            error = "附属资产内部编号已存在"
                        else:
                            accessory.parent_asset_id = resolve_parent_asset_id(
                                internal_no=sub_internal_no,
                                group_no=sub_group_no,
                                fallback_parent_asset_id=accessory.parent_asset_id
                            )
                            accessory.sub_group_no = normalize_empty_to_none(sub_group_no)
                            accessory.sub_internal_no = normalize_empty_to_none(sub_internal_no)
                            accessory.name = name
                            accessory.model = model
                            accessory.owner = owner
                            accessory.location = location
                            accessory.status = status
                            accessory.remark = remark

                            parent_asset = Asset.query.get(accessory.parent_asset_id) if accessory.parent_asset_id else None
                            image_filename_prefix = build_image_filename_prefix(
                                group_no=accessory.sub_group_no,
                                internal_no=accessory.sub_internal_no,
                                parent_asset=parent_asset
                            )

                            for image_id in delete_image_ids:
                                try:
                                    img_id = int(image_id)
                                except:
                                    continue

                                img = AccessoryImage.query.filter_by(id=img_id, accessory_id=accessory.id).first()
                                if img:
                                    delete_image_file(img.image_path)
                                    db.session.delete(img)

                            for file_storage in image_files[:5]:
                                rel = save_uploaded_image(file_storage, "accessories", image_filename_prefix)
                                if rel:
                                    db.session.add(AccessoryImage(accessory_id=accessory.id, image_path=rel))

                            trim_accessory_images(accessory)
                            db.session.commit()
                            message = "配件更新成功"
                    except Exception as e:
                        db.session.rollback()
                        error = f"更新失败：{str(e)}"

        images = AccessoryImage.query.filter_by(accessory_id=accessory.id).order_by(AccessoryImage.created_at.asc(), AccessoryImage.id.asc()).all()
        resolved_parent_asset_id = accessory.parent_asset_id or resolve_parent_asset_id(
            internal_no=accessory.sub_internal_no,
            group_no=accessory.sub_group_no
        )
        back_url = url_for("asset_detail", asset_id=resolved_parent_asset_id) if resolved_parent_asset_id else url_for("search_assets")

        return render_template_string(
            ACCESSORY_DETAIL_HTML,
            current_user=current_user,
            can_manage=has_manage_access(),
            accessory=accessory,
            statuses=statuses,
            message=message,
            error=error,
            images=images,
            back_url=back_url
        )

    @app.route("/accessory/<int:accessory_id>/inventory", methods=["POST"])
    def inventory_accessory(accessory_id):
        guard = ensure_manage_access()
        if guard:
            return guard
        accessory = Accessory.query.get_or_404(accessory_id)
        try:
            accessory.asset_date = date.today()
            db.session.commit()
            return redirect(url_for("accessory_detail", accessory_id=accessory.id))
        except Exception as e:
            db.session.rollback()
            return f"盘点失败：{str(e)}"

    @app.route("/accessory/<int:accessory_id>/delete", methods=["POST"])
    def delete_accessory(accessory_id):
        guard = ensure_manage_access()
        if guard:
            return guard
        accessory = Accessory.query.get_or_404(accessory_id)
        delete_pin = normalize_text(request.form.get("delete_pin"))
        if delete_pin != "0819":
            return "删除失败：Pin码错误"
        resolved_parent_asset_id = accessory.parent_asset_id or resolve_parent_asset_id(
            internal_no=accessory.sub_internal_no,
            group_no=accessory.sub_group_no
        )
        back_url = url_for("asset_detail", asset_id=resolved_parent_asset_id) if resolved_parent_asset_id else url_for("search_assets")
        try:
            delete_accessory_with_files(accessory)
            db.session.commit()
            return redirect(back_url)
        except Exception as e:
            db.session.rollback()
            return f"删除失败：{str(e)}"


# 登录页已拆分到 app/templates/login.html


SEARCH_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>资产查询</title>
<style>
:root{--bg:#edf1f5;--card:#ffffff;--line:#d9e1ea;--text:#233243;--muted:#677383;--primary:#5f6f82;--primary-soft:#eef2f6;--green:#2f7d67;--green-soft:#edf7f2;--orange:#b88347;--orange-soft:#faf2e8;--purple-soft:#f2eff7;}
*{box-sizing:border-box;}
body{font-family:Arial,sans-serif;margin:0;color:var(--text);background:linear-gradient(180deg,#edf1f5 0%,#f7f8fa 34%,#f1f4f8 100%);}
.wrap{max-width:1240px;margin:auto;padding:18px 14px 26px;}
.card{background:rgba(255,255,255,.94);border:1px solid rgba(210,218,227,.98);border-radius:22px;padding:18px 18px 16px;margin-bottom:16px;box-shadow:0 16px 40px rgba(27,39,53,.07);backdrop-filter:blur(10px);}
.topbar{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;}
h2{margin:0 0 14px;font-size:26px;color:#263646;letter-spacing:.4px;}
a{color:#5a6675;}
input,select,button{padding:11px 12px;font-size:16px;border-radius:12px;border:1px solid #cfd6df;box-sizing:border-box;transition:all .2s ease;}
input[type=text]{min-width:260px;background:#fbfdff;}
input[type=text]:focus,select:focus{outline:none;border-color:#9ba8b8;box-shadow:0 0 0 4px rgba(95,111,130,.12);background:#fff;}
input[type=file]{background:#fff;}
input[type=checkbox]{accent-color:#5f6f82;width:16px;height:16px;}
button{background:linear-gradient(135deg,#718194 0%,#556274 100%);color:#fff;border:none;cursor:pointer;font-weight:700;box-shadow:0 8px 18px rgba(46,58,74,.14);}
button:hover{transform:translateY(-1px);box-shadow:0 12px 22px rgba(46,58,74,.16);}
button:disabled{cursor:not-allowed;transform:none;box-shadow:none;}
.btn-green{background:linear-gradient(135deg,#4f8d78 0%,#2f6b58 100%);}
.btn-gray{background:linear-gradient(135deg,#8b95a1 0%,#697380 100%);}
.btn-return-secondary{background:linear-gradient(135deg,#8f2d4b 0%,#6f1d36 100%);box-shadow:0 10px 18px rgba(111,29,54,.20);}
.btn-return-secondary:hover{background:linear-gradient(135deg,#9d3656 0%,#7c2140 100%);box-shadow:0 12px 22px rgba(111,29,54,.24);}
.btn-upload-primary{background:linear-gradient(135deg,#67c9ff 0%,#3388ff 100%);box-shadow:0 10px 18px rgba(54,132,255,.2);}
.btn-upload-primary:hover{background:linear-gradient(135deg,#7ad1ff 0%,#247dff 100%);}
.btn-save-primary{background:linear-gradient(135deg,#54ddb1 0%,#159f76 100%);box-shadow:0 10px 18px rgba(21,159,118,.2);}
.btn-save-primary:hover{background:linear-gradient(135deg,#64e4ba 0%,#0f956d 100%);}
.btn-orange{background:linear-gradient(135deg,#c39a68 0%,#a9753a 100%);}
.btn-red{background:linear-gradient(135deg,#d06f72 0%,#b34f55 100%);}
.btn-search-home{background:linear-gradient(135deg,#67c9ff 0%,#3388ff 100%);box-shadow:0 10px 18px rgba(54,132,255,.2);}
.btn-search-home:hover{background:linear-gradient(135deg,#7ad1ff 0%,#247dff 100%);}
.btn-reset-home{background:linear-gradient(135deg,#c5ccd4 0%,#97a2af 100%);box-shadow:0 8px 16px rgba(99,116,135,.16);}
.btn-reset-home:hover{background:linear-gradient(135deg,#d0d7df 0%,#8d98a6 100%);}
.btn-add-home{background:linear-gradient(135deg,#54ddb1 0%,#159f76 100%);box-shadow:0 10px 18px rgba(21,159,118,.2);}
.btn-add-home:hover{background:linear-gradient(135deg,#64e4ba 0%,#0f956d 100%);}
.btn-disabled{background:#adb5bd !important;color:#fff !important;cursor:not-allowed;pointer-events:none;box-shadow:none !important;}
.table-wrap{overflow-x:auto;border:1px solid #d7dee7;border-radius:16px;background:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,.8),0 8px 22px rgba(27,39,53,.04);}
table{width:100%;border-collapse:separate;border-spacing:0;min-width:940px;}
th,td{padding:11px 10px;text-align:left;border:none;border-right:1px solid #e7eef7;border-bottom:1px solid #e7eef7;vertical-align:middle;font-size:15px;font-weight:400;color:#6f7b88;}th:last-child,td:last-child{border-right:none;}
thead th{position:sticky;top:0;z-index:1;background:linear-gradient(180deg,#fafbfd 0%,#eff2f6 100%);color:#5e6977;font-weight:600;white-space:nowrap;}
tbody tr:last-child td{border-bottom:none;}
tbody tr.result-row{transition:transform .16s ease,box-shadow .16s ease;}
tbody tr.result-row td:first-child{border-left:4px solid transparent;}
tbody tr.result-row:nth-child(4n+1) td{background:#fbfcfd;}
tbody tr.result-row:nth-child(4n+2) td{background:#f8fafb;}
tbody tr.result-row:nth-child(4n+3) td{background:#fcfbf8;}
tbody tr.result-row:nth-child(4n+4) td{background:#f9f8fb;}
tbody tr.result-row:hover td{background:#eff3f7;}
tbody tr.result-row:hover{transform:translateY(-1px);box-shadow:0 10px 22px rgba(42,56,74,.08);}
tbody tr.result-row-asset td:first-child{border-left-color:#8fa8c2;}
tbody tr.result-row-accessory td:first-child{border-left-color:#95b6a1;}
tbody tr.empty-row td{background:#fbfdff;color:#6f7b88;text-align:center;padding:20px 12px;}
.err{color:#dc2626;margin-top:10px;font-weight:700;}
.num-link{color:#4e5865;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px;font-weight:400;font-size:18px;}
.num-link:hover{color:#3f4853;text-decoration:underline;}
.sort-link{display:inline-flex;align-items:center;gap:4px;color:#5e6977;text-decoration:none;font-weight:600;}
.sort-link:hover{color:#3f4853;text-decoration:none;}
.action-bar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
.pagination{margin-top:14px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
.pagination a,.pagination span{display:inline-flex;align-items:center;justify-content:center;min-height:38px;padding:0 14px;border-radius:999px;border:1px solid #d7e3f1;background:#fff;color:#334155;text-decoration:none;}
.pagination a:hover{background:#f1f4f7;border-color:#c7d0db;}
.muted{color:var(--muted);font-size:14px;line-height:1.6;}.num-link{color:#4e5865;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px;font-weight:400;font-size:18px;}.num-link:hover{color:#3f4853;text-decoration:underline;}
.summary-badge{display:inline-flex;align-items:center;padding:8px 14px;border-radius:999px;background:var(--primary-soft);color:#4a5767;font-weight:700;border:1px solid #d4dbe4;}
.icon-only-btn{width:44px;min-width:44px;height:44px;padding:0;display:inline-flex;align-items:center;justify-content:center;}
.icon-only-btn svg{width:22px;height:22px;stroke:currentColor;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round;}
.power-link{display:inline-flex;text-decoration:none;}
.btn-power{width:28px;min-width:28px;height:28px;padding:0;border:none;border-radius:999px;display:inline-flex;align-items:center;justify-content:center;background:radial-gradient(circle at 32% 28%,#f47d82 0%,#e45f65 34%,#cb4049 70%,#aa2733 100%);box-shadow:inset 0 2px 3px rgba(255,255,255,.38),inset 0 -6px 10px rgba(120,10,20,.18),0 2px 0 #8f1f2b,0 8px 16px rgba(146,24,36,.28);color:#fff;position:relative;}
.btn-power::before{content:'';position:absolute;inset:3px;border-radius:999px;border:1.2px solid rgba(120,18,28,.28);box-shadow:inset 0 1px 0 rgba(255,255,255,.18);}
.btn-power svg{width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round;filter:drop-shadow(0 1px 0 rgba(120,10,20,.18));position:relative;z-index:1;}
.btn-power:hover{background:radial-gradient(circle at 32% 28%,#fb8d92 0%,#ea676d 34%,#d24750 70%,#b12c37 100%);box-shadow:inset 0 2px 3px rgba(255,255,255,.42),inset 0 -6px 10px rgba(120,10,20,.22),0 2px 0 #8f1f2b,0 12px 22px rgba(146,24,36,.36);}
.switch-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;}
.switch-row a{flex:1 1 160px;}
.switch-row a button{width:100%;}
.switch-row .tab-btn{background:linear-gradient(180deg,#f9fbfd 0%,#eef3f7 100%);color:#677383;border:1px solid #d8e0e8;box-shadow:inset 0 1px 0 rgba(255,255,255,.86),0 5px 12px rgba(63,78,96,.06);font-weight:800;letter-spacing:.2px;}
.switch-row .tab-btn:hover{background:linear-gradient(180deg,#ffffff 0%,#edf3f8 100%);color:#4d5b6b;border-color:#c8d4df;box-shadow:inset 0 1px 0 rgba(255,255,255,.92),0 10px 18px rgba(63,78,96,.1);}
.switch-row .tab-btn.tab-active-asset{background:#ead8aa;color:#6b5630;border-color:#d8c18a;box-shadow:inset 0 1px 0 rgba(255,255,255,.62),0 10px 18px rgba(161,138,84,.16);text-shadow:none;}
.switch-row .tab-btn.tab-active-asset:hover{background:#e4cf9a;color:#5f4c2b;border-color:#ccb277;box-shadow:inset 0 1px 0 rgba(255,255,255,.62),0 12px 20px rgba(161,138,84,.2);transform:translateY(-1px);}
.simple-modal{position:fixed;inset:0;background:rgba(15,23,42,.52);display:none;align-items:center;justify-content:center;z-index:10000;padding:18px;}.simple-modal.show{display:flex;}.simple-modal-card{width:100%;max-width:380px;background:#fff;border-radius:18px;padding:22px 20px;box-shadow:0 18px 50px rgba(15,23,42,.28);}.simple-modal-title{font-size:22px;font-weight:800;color:#111827;text-align:center;margin-bottom:10px;}.simple-modal-text{font-size:18px;font-weight:600;line-height:1.7;color:#1f2937;text-align:center;margin-bottom:14px;white-space:pre-line;}.simple-modal-input-wrap{display:none;margin-bottom:12px;}.simple-modal-input-wrap.show{display:block;}.simple-modal-input{width:100%;box-sizing:border-box;padding:12px 14px;font-size:18px;font-weight:700;border-radius:12px;border:2px solid #9ca3af;color:#111827;}.simple-modal-input:focus{outline:none;border-color:#374151;box-shadow:0 0 0 3px rgba(55,65,81,.12);}.simple-modal-error{min-height:24px;font-size:16px;font-weight:700;color:#b91c1c;text-align:center;margin-bottom:6px;}.simple-modal-actions{display:flex;gap:12px;justify-content:center;margin-top:6px;}.simple-modal-actions button{width:auto;min-width:118px;padding:11px 18px;font-size:17px;font-weight:700;border-radius:12px;border:none;}.simple-modal-cancel{background:#6b7280;color:#fff;}.simple-modal-ok{background:#b91c1c;color:#fff;}@media (max-width:768px){.wrap{padding:12px;}.card{padding:14px;margin-bottom:14px;border-radius:14px;}.topbar{gap:8px;}.switch-row{gap:8px;margin-bottom:12px;}.switch-row a{flex:1 1 160px;}h2{font-size:22px;}.action-bar{gap:8px;}.action-bar button{width:auto;}.pagination{justify-content:flex-start;gap:10px;}.pagination a,.pagination span{font-size:14px;}.simple-modal{padding:16px;}.simple-modal-card{border-radius:16px;padding:18px 16px;}.simple-modal-title{font-size:20px;}.simple-modal-text{font-size:16px;}.simple-modal-input{padding:10px 12px;font-size:16px;border-radius:8px;}.simple-modal-actions button{min-width:108px;padding:10px 16px;font-size:16px;border-radius:10px;}input,select,button{padding:10px;font-size:16px;border-radius:8px;}input[type=text]{width:100%;min-width:0;}.icon-only-btn{width:42px;min-width:42px;height:42px;padding:0;}}

.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3),
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5),
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){white-space:nowrap;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:16%;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:23.25%;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:12.75%;min-width:109px;}
@media (max-width:768px){
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.table-wrap table{width:max-content;table-layout:auto !important;}
.asset-search-table{min-width:1220px !important;}
.asset-location-table{min-width:1120px !important;}
.asset-accessory-table{min-width:980px !important;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:auto !important;min-width:192px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:auto !important;min-width:250px;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:auto !important;min-width:141px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){width:auto !important;min-width:220px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.table-wrap table .num-link{display:inline-block;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;max-width:none !important;word-break:normal !important;}
}
</style>
<script>
function getVisibleSelectionInputs(){
    return Array.from(document.querySelectorAll('#asset-batch-form input[type="checkbox"][name="selected_items"]'));
}

function getAllFilteredSelectedItems(){
    return {{ all_filtered_selected_items|tojson }} || [];
}

function getSelectedItemValues(){
    const form = document.getElementById('asset-batch-form');
    if(!form){ return []; }
    const selected = [];
    form.querySelectorAll('input[name="selected_items"]').forEach(function(el){
        if(el.type === 'hidden'){
            selected.push(el.value);
        }else if(el.checked){
            selected.push(el.value);
        }
    });
    return selected;
}

function clearAllFilteredHiddenSelections(){
    const form = document.getElementById('asset-batch-form');
    if(!form){ return; }
    const box = document.getElementById('all-filtered-selected-box');
    if(box){ box.innerHTML = ''; }
}

function syncAllFilteredSelection(source){
    const form = document.getElementById('asset-batch-form');
    if(!form){ return; }
    const box = document.getElementById('all-filtered-selected-box');
    if(!box){ return; }
    box.innerHTML = '';
    const visibleItems = getVisibleSelectionInputs();
    if(source.checked){
        visibleItems.forEach(function(item){ item.checked = true; });
        const visibleValues = new Set(visibleItems.map(function(item){ return item.value; }));
        getAllFilteredSelectedItems().forEach(function(value){
            if(visibleValues.has(value)){ return; }
            const hidden = document.createElement('input');
            hidden.type = 'hidden';
            hidden.name = 'selected_items';
            hidden.value = value;
            box.appendChild(hidden);
        });
    }else{
        visibleItems.forEach(function(item){ item.checked = false; });
    }
}

function clearGlobalSelectionState(){
    const master = document.getElementById('select-all-filtered');
    if(master && master.checked){
        master.checked = false;
        clearAllFilteredHiddenSelections();
    }
}

const deleteModalState = { onOk: null, onCancel: null };
function closeDeleteModal(){
    const modal = document.getElementById('delete-modal');
    if(modal){ modal.classList.remove('show'); }
    const inputWrap = document.getElementById('delete-modal-input-wrap');
    const input = document.getElementById('delete-modal-input');
    const error = document.getElementById('delete-modal-error');
    const cancelBtn = document.getElementById('delete-modal-cancel');
    const okBtn = document.getElementById('delete-modal-ok');
    if(inputWrap){ inputWrap.classList.remove('show'); }
    if(input){ input.value = ''; }
    if(error){ error.textContent = ''; }
    if(cancelBtn){ cancelBtn.style.display = 'inline-block'; }
    if(okBtn){ okBtn.textContent = '确定'; }
    deleteModalState.onOk = null;
    deleteModalState.onCancel = null;
}
function openDeleteModal(config){
    const modal = document.getElementById('delete-modal');
    const title = document.getElementById('delete-modal-title');
    const text = document.getElementById('delete-modal-text');
    const inputWrap = document.getElementById('delete-modal-input-wrap');
    const input = document.getElementById('delete-modal-input');
    const error = document.getElementById('delete-modal-error');
    const cancelBtn = document.getElementById('delete-modal-cancel');
    const okBtn = document.getElementById('delete-modal-ok');
    if(!modal || !title || !text || !inputWrap || !input || !error || !cancelBtn || !okBtn){ return; }
    title.textContent = config.title || '提示';
    text.textContent = config.text || '';
    error.textContent = '';
    input.value = '';
    if(config.showPin){
        inputWrap.classList.add('show');
        window.setTimeout(() => { try { input.focus(); } catch(e){} }, 30);
    }else{
        inputWrap.classList.remove('show');
    }
    cancelBtn.style.display = config.hideCancel ? 'none' : 'inline-block';
    okBtn.textContent = config.okText || '确定';
    deleteModalState.onOk = typeof config.onOk === 'function' ? config.onOk : null;
    deleteModalState.onCancel = typeof config.onCancel === 'function' ? config.onCancel : null;
    modal.classList.add('show');
}
function handleDeleteModalCancel(){
    const fn = deleteModalState.onCancel;
    closeDeleteModal();
    if(fn){ fn(); }
}
function handleDeleteModalOk(){
    if(!deleteModalState.onOk){ closeDeleteModal(); return; }
    const result = deleteModalState.onOk();
    if(result !== false){ closeDeleteModal(); }
}
function showDeleteNotice(message){
    openDeleteModal({ title:'提示', text: message || '请确认操作', hideCancel: true, onOk: function(){ return true; } });
}
function showDeletePinDialog(onSubmit){
    openDeleteModal({
        title:'Pin码确认',
        text:'请输入4位Pin码',
        showPin:true,
        onOk:function(){
            const input = document.getElementById('delete-modal-input');
            const error = document.getElementById('delete-modal-error');
            const pin = (input && input.value ? input.value : '').trim();
            if(!pin){ if(error){ error.textContent = '请输入4位Pin码'; } return false; }
            if(pin !== '0819'){ if(error){ error.textContent = 'Pin码错误'; } return false; }
            if(typeof onSubmit === 'function'){ onSubmit(pin); }
            return true;
        }
    });
}
function openDeleteFlow(message, onPinConfirmed){
    openDeleteModal({
        title:'删除确认',
        text: message || '确认删除吗？',
        onOk:function(){
            showDeletePinDialog(onPinConfirmed);
            return false;
        }
    });
    return false;
}

document.addEventListener('DOMContentLoaded', function(){
    const importSummaryText = {{ import_summary_text|tojson }};
    const importErrorText = {{ import_error|tojson }};
    if(importErrorText){
        openDeleteModal({
            title:'上传失败',
            text: importErrorText,
            hideCancel: true,
            okText: '知道了',
            onOk: function(){ return true; }
        });
    }else if(importSummaryText){
        openDeleteModal({
            title:'上传完成',
            text: importSummaryText,
            hideCancel: true,
            okText: '知道了',
            onOk: function(){ return true; }
        });
    }
    getVisibleSelectionInputs().forEach(function(item){
        item.addEventListener('change', clearGlobalSelectionState);
    });
});

function confirmDeleteSelected(){
    const selectedValues = getSelectedItemValues();
    if(selectedValues.length === 0){ showDeleteNotice('请先勾选要删除的资产'); return false; }
    const assetIds = new Set();
    const accessoryIds = new Set();
    selectedValues.forEach(function(value){
        const parts = String(value || '').split(':');
        if(parts.length !== 2){ return; }
        if(parts[0] === 'asset'){ assetIds.add(parts[1]); }
        if(parts[0] === 'accessory'){ accessoryIds.add(parts[1]); }
    });
    const assetCount = assetIds.size;
    const accessoryCount = accessoryIds.size;
    return openDeleteFlow(`将删除 ${assetCount} 条主设备、${accessoryCount} 条配件`, function(pin){
        document.getElementById('delete_pin').value = pin;
        const form = document.getElementById('asset-batch-form');
        if(form){
            form.action = '/delete_selected';
            form.method = 'post';
            form.submit();
        }
    });
}
</script>
</head>
<body>
<div class="wrap">
    <div class="card">
        <div class="topbar">
            <div><strong>当前用户：</strong>{{ user_display }}</div>
            <div>{% if current_user.is_authenticated %}<a href="/logout" title="退出登录" class="power-link"><button type="button" class="btn-power" aria-label="退出登录"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v8"></path><path d="M7.05 5.05a9 9 0 1 0 9.9 0"></path></svg></button></a>{% endif %}</div>
        </div>
    </div>

    <div class="card">
        <div class="switch-row">
            <a href="/"><button type="button" class="tab-btn tab-active-asset">资产查询</button></a>
            <a href="/cable"><button type="button" class="tab-btn">电缆查询</button></a>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:14px;">
            <h2 style="margin:0;">资产查询</h2>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <a href="/scan_label?auto=1" title="条形码扫描"><button type="button" class="btn-gray icon-only-btn" aria-label="条形码扫描">
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M8 4H6a2 2 0 0 0-2 2v2"></path><path d="M16 4h2a2 2 0 0 1 2 2v2"></path><path d="M8 20H6a2 2 0 0 1-2-2v-2"></path><path d="M16 20h2a2 2 0 0 0 2-2v-2"></path><path d="M5 12h14"></path>
                    </svg>
                </button></a>
                <a href="/scan_label_image" title="标签识别"><button type="button" class="btn-green icon-only-btn" aria-label="标签识别">
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M8 4H6a2 2 0 0 0-2 2v2"></path><path d="M16 4h2a2 2 0 0 1 2 2v2"></path><path d="M8 20H6a2 2 0 0 1-2-2v-2"></path><path d="M16 20h2a2 2 0 0 0 2-2v-2"></path><path d="M5 12h14"></path>
                    </svg>
                </button></a>
            </div>
        </div>
        <form method="get" action="/">
            <input type="hidden" name="searched" value="1">
            <input type="hidden" name="sort_field" value="{{ sort_field }}">
            <input type="hidden" name="sort_order" value="{{ sort_order }}">
            <div class="action-bar">
                <input type="text" name="keyword" value="{{ keyword }}" placeholder="编号/后6位、责任人、位置、备注">
                <select name="status_filter">
                    <option value="">状态</option>
                    {% for s in statuses %}
                    <option value="{{ s.dict_value }}" {% if status_filter == s.dict_value %}selected{% endif %}>{{ s.dict_value }}</option>
                    {% endfor %}
                </select>
                <select name="device_type_filter">
                    <option value="">类型</option>
                    <option value="主设备" {% if device_type_filter == '主设备' %}selected{% endif %}>主设备</option>
                    <option value="配件" {% if device_type_filter == '配件' %}selected{% endif %}>配件</option>
                </select>
                <button type="submit" class="btn-search-home">搜索</button>
                <a href="/"><button type="button" class="btn-reset-home">重置</button></a>
                {% if can_manage %}
                <a href="/device/new"><button type="button" class="btn-add-home">+</button></a>
                {% else %}
                <button type="button" class="btn-disabled" disabled>+</button>
                {% endif %}
            </div>
        </form>

        <form method="post" action="/import_devices" enctype="multipart/form-data" style="margin-top:12px;">
            <div class="action-bar">
                <input type="file" name="excel_file" accept=".xlsx,.xlsm,.xltx,.xltm" {% if not can_manage %}disabled{% endif %}>
                <button type="submit" class="btn-orange {% if not can_manage %}btn-disabled{% endif %}" {% if not can_manage %}disabled{% endif %}>批量上传设备</button>
                {% if import_log_url %}<a href="{{ import_log_url }}"><button type="button" class="btn-gray">下载失败日志</button></a>{% endif %}
            </div>
            <div class="muted" style="margin-top:8px;">Excel导入、导出格式一致，导入操作只更新，不会空白填充。</div>
        </form>
        {% if error %}<div class="err">{{ error }}</div>{% endif %}
    </div>

    {% if searched %}
    <div class="card">
        <form id="asset-batch-form" method="post" action="/export">
            <div id="all-filtered-selected-box"></div>
            <input type="hidden" name="keyword" value="{{ keyword }}">
            <input type="hidden" name="status_filter" value="{{ status_filter }}">
            <input type="hidden" name="device_type_filter" value="{{ device_type_filter }}">
            <input type="hidden" name="per_page" value="{{ per_page }}">
            <input type="hidden" name="sort_field" value="{{ sort_field }}">
            <input type="hidden" name="sort_order" value="{{ sort_order }}">
            <input type="hidden" name="delete_pin" id="delete_pin" value="">
            <div class="action-bar" style="margin-bottom:10px;">
                <button type="submit" class="btn-green">导出选中</button>
                <button type="submit" class="btn-red {% if not can_manage %}btn-disabled{% endif %}" formaction="/delete_selected" formmethod="post" onclick="return {% if can_manage %}confirmDeleteSelected(){% else %}false{% endif %};" {% if not can_manage %}disabled{% endif %}>删除选中</button>
                <div class="summary-badge">当前总数：{{ total }}</div>
            </div>
            <div class="table-wrap">
                <table class="asset-search-table">
                    <thead>
                        <tr>
                            <th><input type="checkbox" id="select-all-filtered" onclick="syncAllFilteredSelection(this)" title="选中当前筛选结果全部"></th>
                            <th>集团编号</th><th>内部编号</th><th>资产名称</th><th>位置</th><th>状态</th><th>责任人</th>
                            <th>
                                <a class="sort-link" href="/?searched=1&keyword={{ keyword }}&status_filter={{ status_filter }}&device_type_filter={{ device_type_filter }}&per_page={{ per_page }}&page=1&sort_field=asset_date&sort_order={% if sort_field == 'asset_date' and sort_order == 'desc' %}asc{% else %}desc{% endif %}">
                                    时间{% if sort_field == 'asset_date' %}{{ ' ↑' if sort_order == 'asc' else ' ↓' }}{% else %} ↕{% endif %}
                                </a>
                            </th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for item in rows %}
                        <tr class="result-row result-row-{{ item.row_type }}">
                            <td><input type="checkbox" name="selected_items" value="{{ item.row_type }}:{{ item.id }}" data-row-type="{{ item.row_type }}" data-row-id="{{ item.id }}" data-parent-asset-id="{{ item.parent_asset_id }}" data-accessory-count="{{ item.accessory_count }}"></td>
                            <td><a class="num-link" href="{{ item.detail_url }}">{{ item.group_no }}</a></td>
                            <td><a class="num-link" href="{{ item.detail_url }}">{{ item.internal_no }}</a></td>
                            <td>{{ item.name }}</td>
                            <td>{% if item.location_detail_url %}<a class="num-link" href="{{ item.location_detail_url }}">{{ item.location }}</a>{% else %}{{ item.location or '' }}{% endif %}</td>
                            <td>{{ item.status }}</td><td>{{ item.owner }}</td><td>{{ item.asset_date_text or "" }}</td>
                        </tr>
                        {% endfor %}
                        {% if not rows %}<tr class="empty-row"><td colspan="8">暂无数据</td></tr>{% endif %}
                    </tbody>
                </table>
            </div>
        </form>
        <div class="pagination">
            {% if page > 1 %}<a href="/?searched=1&keyword={{ keyword }}&status_filter={{ status_filter }}&device_type_filter={{ device_type_filter }}&per_page={{ per_page }}&page={{ page - 1 }}&sort_field={{ sort_field }}&sort_order={{ sort_order }}">上一页</a>{% endif %}
            <span>第 {{ page }} / {{ total_pages }} 页</span>
            {% if page < total_pages %}<a href="/?searched=1&keyword={{ keyword }}&status_filter={{ status_filter }}&device_type_filter={{ device_type_filter }}&per_page={{ per_page }}&page={{ page + 1 }}&sort_field={{ sort_field }}&sort_order={{ sort_order }}">下一页</a>{% endif %}
        </div>
    </div>
    {% endif %}

<div id="delete-modal" class="simple-modal" onclick="if(event.target===this){closeDeleteModal();}">
    <div class="simple-modal-card">
        <div id="delete-modal-title" class="simple-modal-title">删除确认</div>
        <div id="delete-modal-text" class="simple-modal-text">确认删除吗？</div>
        <div id="delete-modal-input-wrap" class="simple-modal-input-wrap">
            <input id="delete-modal-input" class="simple-modal-input" type="password" inputmode="numeric" maxlength="4" placeholder="请输入4位Pin码">
        </div>
        <div id="delete-modal-error" class="simple-modal-error"></div>
        <div class="simple-modal-actions">
            <button type="button" id="delete-modal-cancel" class="simple-modal-cancel" onclick="handleDeleteModalCancel();">取消</button>
            <button type="button" id="delete-modal-ok" class="simple-modal-ok" onclick="handleDeleteModalOk();">确定</button>
        </div>
    </div>
</div>

</div>
</body>
</html>
"""

ASSET_LOCATION_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>货架详情</title>
<style>
:root{--bg:#edf1f5;--card:#ffffff;--line:#d9e1ea;--text:#233243;--muted:#677383;--primary:#5f6f82;--primary-soft:#eef2f6;--green:#2f7d67;--orange:#b88347;--danger:#dc2626;}
*{box-sizing:border-box;}
body{font-family:Arial,sans-serif;margin:0;color:var(--text);background:linear-gradient(180deg,#edf1f5 0%,#f7f8fa 34%,#f1f4f8 100%);}
.wrap{max-width:1100px;margin:auto;padding:16px 14px 26px;}
.card{background:rgba(255,255,255,.94);border:1px solid rgba(210,218,227,.98);border-radius:22px;padding:18px 18px 16px;margin-bottom:16px;box-shadow:0 16px 40px rgba(27,39,53,.07);backdrop-filter:blur(10px);}
h2{margin:0 0 14px;font-size:26px;color:#263646;letter-spacing:.4px;}
a{color:#5a6675;}
.row{margin-bottom:12px;}
label{display:block;margin-bottom:6px;font-weight:600;color:#5e6977;}
input,select,textarea,button{width:100%;box-sizing:border-box;padding:10px 12px;font-size:16px;border-radius:12px;border:1px solid #cfd6df;transition:all .2s ease;}
input[type=text],input[type=date],input[type=password],select,textarea{background:#fbfdff;}
input[type=text]:focus,input[type=date]:focus,input[type=password]:focus,select:focus,textarea:focus{outline:none;border-color:#9ba8b8;box-shadow:0 0 0 4px rgba(95,111,130,.12);background:#fff;}
input[type=file]{background:#fff;}
input[type=checkbox]{accent-color:#5f6f82;width:16px;height:16px;}
textarea{min-height:90px;resize:vertical;}
button{background:linear-gradient(135deg,#718194 0%,#556274 100%);color:#fff;border:none;cursor:pointer;font-weight:700;box-shadow:0 8px 18px rgba(46,58,74,.14);}
button:hover{transform:translateY(-1px);box-shadow:0 12px 22px rgba(46,58,74,.16);}
button:disabled{cursor:not-allowed;transform:none;box-shadow:none;}
.btn-green{background:linear-gradient(135deg,#4f8d78 0%,#2f6b58 100%);}
.btn-gray{background:linear-gradient(135deg,#8b95a1 0%,#697380 100%);}.btn-return-secondary{background:linear-gradient(135deg,#8f2d4b 0%,#6f1d36 100%);box-shadow:0 10px 18px rgba(111,29,54,.20);}.btn-return-secondary:hover{background:linear-gradient(135deg,#9d3656 0%,#7c2140 100%);box-shadow:0 12px 22px rgba(111,29,54,.24);}
.btn-orange{background:linear-gradient(135deg,#c39a68 0%,#a9753a 100%);}
.btn-red{background:linear-gradient(135deg,#d06f72 0%,#b34f55 100%);}
.btn-upload-primary{background:linear-gradient(135deg,#67c9ff 0%,#3388ff 100%);box-shadow:0 10px 18px rgba(54,132,255,.2);}
.btn-save-primary{background:linear-gradient(135deg,#54ddb1 0%,#159f76 100%);box-shadow:0 10px 18px rgba(21,159,118,.2);}
.btn-return-secondary{background:linear-gradient(135deg,#8f2d4b 0%,#6f1d36 100%);box-shadow:0 10px 18px rgba(111,29,54,.20);}.btn-return-secondary:hover{background:linear-gradient(135deg,#9d3656 0%,#7c2140 100%);box-shadow:0 12px 22px rgba(111,29,54,.24);}
.btn-disabled{background:#adb5bd !important;color:#fff !important;cursor:not-allowed;pointer-events:none;box-shadow:none !important;}
.readonly{background:#eef2f6;color:#677383;border-color:#d8e0e8;}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.table-wrap{overflow-x:auto;border:1px solid #d7dee7;border-radius:16px;background:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,.8),0 8px 22px rgba(27,39,53,.04);}
table{width:100%;border-collapse:separate;border-spacing:0;min-width:780px;}
th,td{padding:11px 10px;text-align:left;border:none;border-right:1px solid #e7eef7;border-bottom:1px solid #e7eef7;vertical-align:middle;font-size:15px;font-weight:400;color:#6f7b88;}th:last-child,td:last-child{border-right:none;}
thead th{background:linear-gradient(180deg,#fafbfd 0%,#eff2f6 100%);color:#5e6977;font-weight:600;white-space:nowrap;}
tbody tr:last-child td{border-bottom:none;}
tbody tr.result-row{transition:transform .16s ease,box-shadow .16s ease;}
tbody tr.result-row td:first-child{border-left:4px solid transparent;}
tbody tr.result-row:nth-child(4n+1) td{background:#fbfcfd;}
tbody tr.result-row:nth-child(4n+2) td{background:#f8fafb;}
tbody tr.result-row:nth-child(4n+3) td{background:#fcfbf8;}
tbody tr.result-row:nth-child(4n+4) td{background:#f9f8fb;}
tbody tr.result-row:hover td{background:#eff3f7;}
tbody tr.result-row:hover{transform:translateY(-1px);box-shadow:0 10px 22px rgba(42,56,74,.08);}
tbody tr.result-row-asset td:first-child{border-left-color:#8fa8c2;}
tbody tr.result-row-accessory td:first-child{border-left-color:#95b6a1;}
tbody tr.empty-row td{background:#fbfdff;color:#6f7b88;text-align:center;padding:20px 12px;}
.msg{color:#15803d;margin-bottom:12px;font-weight:700;background:#eef8f3;border:1px solid #cfe4d9;border-radius:12px;padding:10px 12px;}
.err{color:#dc2626;margin-bottom:12px;font-weight:700;background:#fbf1f2;border:1px solid #e7cfd3;border-radius:12px;padding:10px 12px;}
.image-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;padding:10px 12px;background:#fafbfd;border:1px solid #dde4ec;border-radius:12px;}
.upload-actions{display:flex;gap:8px;flex-wrap:wrap;}
.upload-actions button{width:auto;min-width:120px;}
.file-list{margin-top:8px;color:#66788a;font-size:14px;word-break:break-all;line-height:1.6;}
.upload-preview-list{display:flex;flex-direction:column;gap:6px;margin-top:8px;}.upload-preview-item{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 9px;border:1px solid #e1e7ef;border-radius:10px;background:#fbfdff;color:#4d5b6b;}.upload-preview-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}.upload-remove-btn{width:28px;min-width:28px;height:28px;padding:0;border-radius:999px;background:#dc2626;color:#fff;border:none;box-shadow:none;font-weight:900;line-height:1;display:inline-flex;align-items:center;justify-content:center;}
.upload-dialog{position:fixed;left:0;top:0;width:100vw;height:100dvh;background:transparent;display:none;z-index:9999;overflow:hidden;} .upload-dialog.show{display:block;} .upload-dialog-card{position:fixed;left:50%;top:56%;transform:translate(-50%,-50%);width:min(320px,calc(100vw - 24px));max-width:320px;background:#fff;border-radius:16px;padding:18px;box-shadow:0 20px 40px rgba(15,23,42,.22);z-index:10000;}
.upload-dialog-title{font-size:18px;font-weight:bold;margin-bottom:12px;text-align:center;color:#163047;}
.upload-dialog-actions{display:flex;flex-direction:column;gap:10px;}
.upload-dialog-actions button{width:100%;}
.upload-choice-file{position:relative;display:block;width:100%;box-sizing:border-box;padding:10px;font-size:16px;border-radius:12px;border:1px solid #cfd9e6;color:#fff;text-align:center;overflow:hidden;box-shadow:0 8px 18px rgba(46,58,74,.14);}
.upload-choice-camera{background:linear-gradient(135deg,#67c9ff 0%,#3388ff 100%);box-shadow:0 10px 18px rgba(54,132,255,.2);}
.upload-choice-local{background:linear-gradient(135deg,#54ddb1 0%,#159f76 100%);box-shadow:0 10px 18px rgba(21,159,118,.2);}
.upload-choice-file input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;}
.upload-dialog-actions .btn-cancel{background:linear-gradient(135deg,#c5ccd4 0%,#97a2af 100%);box-shadow:0 8px 16px rgba(99,116,135,.16);}
.title-row{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px;}
.title-row h2{margin:0;}
.title-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.title-row{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px;}
.title-row h2{margin:0;}
.title-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap;}
.detail-actions{display:flex;gap:82px;flex-wrap:wrap;align-items:center;margin-top:12px;}
.detail-actions button{width:auto;min-width:120px;}
.link-row{margin-top:-2px;margin-bottom:12px;color:#607284;font-size:14px;}
.muted{color:var(--muted);font-size:14px;line-height:1.6;}.num-link{color:#4e5865;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px;font-weight:400;font-size:18px;}.num-link:hover{color:#3f4853;text-decoration:underline;}
.section-head{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;}
.icon-only-btn{width:44px;min-width:44px;height:44px;padding:0;display:inline-flex;align-items:center;justify-content:center;}
.icon-only-btn svg{width:22px;height:22px;stroke:currentColor;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round;}

.switch-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;}
.switch-row a{flex:1 1 160px;}
.switch-row a button{width:100%;}
.simple-modal{position:fixed;inset:0;background:rgba(15,23,42,.52);display:none;align-items:center;justify-content:center;z-index:10000;padding:18px;}
.simple-modal.show{display:flex;}
.simple-modal-card{width:100%;max-width:380px;background:#fff;border-radius:18px;padding:22px 20px;box-shadow:0 18px 50px rgba(15,23,42,.28);}
.simple-modal-title{font-size:22px;font-weight:800;color:#111827;text-align:center;margin-bottom:10px;}
.simple-modal-text{font-size:18px;font-weight:600;line-height:1.7;color:#1f2937;text-align:center;margin-bottom:14px;}
.simple-modal-input-wrap{display:none;margin-bottom:12px;}
.simple-modal-input-wrap.show{display:block;}
.simple-modal-input{width:100%;box-sizing:border-box;padding:12px 14px;font-size:18px;font-weight:700;border-radius:12px;border:2px solid #9ca3af;color:#111827;}
.simple-modal-input:focus{outline:none;border-color:#374151;box-shadow:0 0 0 3px rgba(55,65,81,.12);}
.simple-modal-error{min-height:24px;font-size:16px;font-weight:700;color:#b91c1c;text-align:center;margin-bottom:6px;}
.simple-modal-actions{display:flex;gap:12px;justify-content:center;margin-top:6px;}
.simple-modal-actions button{width:auto;min-width:118px;padding:11px 18px;font-size:17px;font-weight:700;border-radius:12px;border:none;}
.simple-modal-cancel{background:#6b7280;color:#fff;}
.simple-modal-ok{background:#b91c1c;color:#fff;}
@media (max-width:768px){.wrap{padding:14px 12px 20px;}.card{padding:16px;margin-bottom:14px;border-radius:14px;}.grid{grid-template-columns:1fr;}h2{font-size:22px;}.switch-row{gap:8px;}.switch-row a{flex:1 1 160px;}input,select,textarea,button{padding:10px;font-size:16px;border-radius:8px;}.upload-choice-file{padding:10px;font-size:16px;border-radius:8px;}.upload-dialog-card{border-radius:14px;padding:16px;width:min(320px,calc(100vw - 20px));top:58%;}.simple-modal{padding:16px;}.simple-modal-card{border-radius:16px;padding:18px 16px;}.simple-modal-title{font-size:20px;}.simple-modal-text{font-size:16px;}.simple-modal-input{padding:10px 12px;font-size:16px;border-radius:8px;}.simple-modal-actions button{min-width:108px;padding:10px 16px;font-size:16px;border-radius:10px;}.icon-only-btn{width:42px;min-width:42px;height:42px;padding:0;}}

.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3),
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5),
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){white-space:nowrap;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:16%;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:23.25%;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:12.75%;min-width:109px;}
@media (max-width:768px){
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.table-wrap table{width:max-content;table-layout:auto !important;}
.asset-search-table{min-width:1220px !important;}
.asset-location-table{min-width:1120px !important;}
.asset-accessory-table{min-width:980px !important;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:auto !important;min-width:192px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:auto !important;min-width:250px;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:auto !important;min-width:141px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){width:auto !important;min-width:220px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.table-wrap table .num-link{display:inline-block;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;max-width:none !important;word-break:normal !important;}
}
</style>
<script>
function openUploadChooser(dialogId, triggerEl){ const dialog = document.getElementById(dialogId); if(dialog){ dialog.classList.add('show'); } }
function closeUploadChooser(dialogId){ const dialog = document.getElementById(dialogId); if(dialog){ dialog.classList.remove('show'); } }
const uploadSelectionState = window.uploadSelectionState || (window.uploadSelectionState = {});
function getUploadInputIds(textEl, fallbackInputId){ return (textEl.dataset.inputs || fallbackInputId || '').split(',').map(item => item.trim()).filter(Boolean); }
function makeShortUploadName(file){
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const ms = String(now.getMilliseconds()).padStart(3, '0');
    const timePart = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}${ms}`;
    const originalName = file && file.name ? file.name : '';
    const extMatch = originalName.match(/[.]([A-Za-z0-9]+)$/);
    const ext = extMatch ? extMatch[1].toLowerCase() : 'jpg';
    return `${timePart}.${ext}`;
}
function cloneUploadFileWithShortName(file){
    if(!file){ return file; }
    try{
        if(file.__shortUploadName){ return file; }
        const newFile = new File([file], makeShortUploadName(file), { type: file.type || 'image/jpeg', lastModified: file.lastModified || Date.now() });
        Object.defineProperty(newFile, '__shortUploadName', { value: true });
        return newFile;
    }catch(e){
        return file;
    }
}
function syncUploadInputs(textId, activeInputId){
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || { files: [] };
    const inputIds = getUploadInputIds(textEl, activeInputId);
    if(typeof DataTransfer !== 'undefined'){
        const transfer = new DataTransfer();
        state.files.forEach(file => transfer.items.add(file));
        inputIds.forEach((id, index) => {
            const input = document.getElementById(id);
            if(!input){ return; }
            try{ input.files = (id === activeInputId || index === 0) ? transfer.files : new DataTransfer().files; }catch(e){}
        });
    }
}
function renderUploadSelection(textId, activeInputId){
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || { files: [] };
    if(state.files.length <= 0){
        textEl.textContent = '未选择图片';
        syncUploadInputs(textId, activeInputId);
        return;
    }
    textEl.innerHTML = '';
    const summary = document.createElement('div');
    summary.textContent = `已选择 ${state.files.length} 张（本次最多5张）`;
    textEl.appendChild(summary);
    const list = document.createElement('div');
    list.className = 'upload-preview-list';
    state.files.forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'upload-preview-item';
        const name = document.createElement('span');
        name.className = 'upload-preview-name';
        name.textContent = file.name || `图片${index + 1}`;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'upload-remove-btn';
        remove.setAttribute('aria-label', '删除该图片');
        remove.textContent = '×';
        remove.onclick = function(){ removeSelectedUploadFile(textId, index, activeInputId); };
        item.appendChild(name);
        item.appendChild(remove);
        list.appendChild(item);
    });
    textEl.appendChild(list);
    syncUploadInputs(textId, activeInputId);
}
function removeSelectedUploadFile(textId, index, activeInputId){
    const state = uploadSelectionState[textId];
    if(!state){ return; }
    state.files.splice(index, 1);
    renderUploadSelection(textId, activeInputId);
}
function updateSelectedFiles(inputId, textId, dialogId){
    const input = document.getElementById(inputId);
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || (uploadSelectionState[textId] = { files: [] });
    const incoming = input && input.files ? Array.from(input.files).map(cloneUploadFileWithShortName) : [];
    incoming.forEach(file => {
        if(state.files.length < 5){ state.files.push(file); }
    });
    if(input){ try{ input.value = ''; }catch(e){} }
    renderUploadSelection(textId, inputId);
    if(dialogId){ closeUploadChooser(dialogId); }
    if(incoming.length > 0 && state.files.length >= 5){
        const summary = textEl.querySelector('div');
        if(summary){ summary.textContent = `已选择 ${state.files.length} 张（已达本次最多5张）`; }
    }
}

function enableEdit(formId){
    const form = document.getElementById(formId);
    const fields = form.querySelectorAll('.edit-field');
    fields.forEach(el => { el.disabled = false; el.classList.remove('readonly'); });
    document.getElementById(formId + '-save').style.display = 'inline-block';
    document.getElementById(formId + '-edit').style.display = 'none';
}
function confirmLocationSave(){ return confirm('确认保存货架位置和图片吗？'); }
</script>
</head>
<body>
<div class="wrap">
    <div class="card"><div><strong>当前用户：</strong>{{ user_display }}</div></div>

    <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px;">
            <h2 style="margin:0;">货架详情</h2>
            <a href="/"><button type="button" class="btn-return-secondary" style="width:auto;min-width:108px;">返回</button></a>
        </div>
        <div class="muted" style="margin-bottom:10px;">货架就是位置，修改会同步更新所有相关设备。</div>
        {% if message %}<div class="msg">{{ message }}</div>{% endif %}
        {% if error %}<div class="err">{{ error }}</div>{% endif %}
        <form id="asset-location-form" method="post" enctype="multipart/form-data" onsubmit="return confirmLocationSave()">
            <input type="hidden" name="location" value="{{ location }}">
            <div class="grid">
                <div class="row"><label>货架位置</label><input class="{{ 'edit-field readonly' if readonly else '' }}" {% if readonly %}disabled{% endif %} type="text" name="new_location" value="{{ form_data.location }}"></div>
                <div class="row">
                    <label>上传货架图片（最多5张）</label>
                    <div class="upload-actions">
                        <button type="button" class="{{ 'edit-field readonly' if readonly else '' }} {% if not can_manage %}btn-disabled{% endif %}" {% if readonly or not can_manage %}disabled{% endif %} onclick=\"openUploadChooser('asset-location-upload-choice-dialog', this)\">上传图片</button>
                    </div>
                    <div id="asset-location-image-files-text" class="file-list" data-inputs="asset-location-camera-files,asset-location-file-files">未选择图片</div>
                    <div id="asset-location-upload-choice-dialog" class="upload-dialog" onclick="if(event.target === this){closeUploadChooser('asset-location-upload-choice-dialog');}">
                        <div class="upload-dialog-card">
                            <div class="upload-dialog-title">请选择上传方式</div>
                            <div class="upload-dialog-actions">
                                <label class="upload-choice-file upload-choice-camera">拍照<input class="{{ 'edit-field readonly' if readonly else '' }}" {% if readonly or not can_manage %}disabled{% endif %} type="file" id="asset-location-camera-files" name="image_files" accept="image/*" capture="environment" multiple onchange="updateSelectedFiles('asset-location-camera-files', 'asset-location-image-files-text', 'asset-location-upload-choice-dialog')"></label>
                                <label class="upload-choice-file upload-choice-local">本地上传<input class="{{ 'edit-field readonly' if readonly else '' }}" {% if readonly or not can_manage %}disabled{% endif %} type="file" id="asset-location-file-files" name="image_files" accept="image/*" multiple onchange="updateSelectedFiles('asset-location-file-files', 'asset-location-image-files-text', 'asset-location-upload-choice-dialog')"></label>
                                <button type="button" class="btn-cancel" onclick="closeUploadChooser('asset-location-upload-choice-dialog')">取消</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="row">
                <label>当前货架图片</label>
                {% if images %}
                    {% for img in images %}
                    <div class="image-row">
                        <a href="/uploads/{{ img.image_path }}" target="_blank">图片{{ loop.index }}</a>
                        <label style="display:flex;align-items:center;gap:6px;font-weight:normal;"><input class="edit-field readonly" disabled type="checkbox" name="delete_location_image_ids" value="{{ img.id }}"> 删除</label>
                    </div>
                    {% endfor %}
                    <div style="color:#666;font-size:13px;">先点击“修改”，勾选要删除的图片，再点击“确认”。</div>
                {% else %}
                    <div>暂无图片</div>
                {% endif %}
            </div>
            <div class="detail-actions">
                {% if can_manage %}
                <button type="button" id="asset-location-form-edit" onclick="enableEdit('asset-location-form')">修改</button>
                <button type="submit" id="asset-location-form-save" style="display:none;">确认</button>
                {% else %}
                <button type="button" class="btn-disabled" disabled>修改</button>
                {% endif %}
            </div>
        </form>
    </div>

    <div class="card">
        <div class="section-head">
            <h2 style="margin:0;">该位置下的资产</h2>
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
            {% if can_manage %}
            <a href="{{ asset_new_url }}"><button type="button" class="btn-green" style="width:auto;">+</button></a>
            <a href="{{ asset_scan_url }}" title="条形码扫描"><button type="button" class="btn-gray icon-only-btn" aria-label="条形码扫描">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M8 4H6a2 2 0 0 0-2 2v2"></path><path d="M16 4h2a2 2 0 0 1 2 2v2"></path><path d="M8 20H6a2 2 0 0 1-2-2v-2"></path><path d="M16 20h2a2 2 0 0 0 2-2v-2"></path><path d="M5 12h14"></path>
                </svg>
            </button></a>
            <a href="{{ asset_scan_image_url }}" title="标签识别"><button type="button" class="btn-green icon-only-btn" aria-label="标签识别">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M7 4h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"></path><path d="M8 8h8"></path><path d="M8 12h6"></path><path d="M8 16h5"></path>
                </svg>
            </button></a>
            {% else %}
            <button type="button" class="btn-disabled" style="width:auto;" disabled>+</button>
            <button type="button" class="btn-disabled icon-only-btn" disabled aria-label="条形码扫描">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M8 4H6a2 2 0 0 0-2 2v2"></path><path d="M16 4h2a2 2 0 0 1 2 2v2"></path><path d="M8 20H6a2 2 0 0 1-2-2v-2"></path><path d="M16 20h2a2 2 0 0 0 2-2v-2"></path><path d="M5 12h14"></path>
                </svg>
            </button>
            <button type="button" class="btn-disabled icon-only-btn" disabled aria-label="标签识别">
                <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M7 4h10a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z"></path><path d="M8 8h8"></path><path d="M8 12h6"></path><path d="M8 16h5"></path>
                </svg>
            </button>
            {% endif %}
            </div>
        </div>
        <div class="table-wrap" style="margin-top:12px;">
            <table class="asset-location-table">
                <thead><tr><th>集团编号</th><th>内部编号</th><th>资产名称</th><th>型号</th><th>状态</th><th>责任人</th><th>类型</th><th>时间</th></tr></thead>
                <tbody>
                    {% for item in rows %}
                    <tr class="result-row result-row-{{ 'asset' if item.type_text == '主设备' else 'accessory' }}">
                        <td><a class="num-link" href="{{ item.detail_url }}">{{ item.group_no }}</a></td>
                        <td><a class="num-link" href="{{ item.detail_url }}">{{ item.internal_no }}</a></td>
                        <td>{{ item.name }}</td>
                        <td>{{ item.model }}</td>
                        <td>{{ item.status }}</td>
                        <td>{{ item.owner }}</td>
                        <td>{{ item.type_text }}</td>
                        <td>{{ item.asset_date_text or "" }}</td>
                    </tr>
                    {% endfor %}
                    {% if not rows %}<tr class="empty-row"><td colspan="8">该位置下暂无资产</td></tr>{% endif %}
                </tbody>
            </table>
        </div>
    </div>
</div>
</body>
</html>
"""


SCAN_LABEL_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>条形码扫描</title>
<style>
:root{--bg:#edf3ff;--card:rgba(255,255,255,.92);--line:#d8e2f0;--text:#18324a;--muted:#66788a;--primary:#2f6df3;--primary-deep:#1d4ed8;--green:#1f9d68;--gray:#6c7a89;--danger:#c62828;}
*{box-sizing:border-box;}
body{font-family:Arial,sans-serif;margin:0;color:var(--text);background:linear-gradient(180deg,#eef4ff 0%,#f8fbff 35%,#f3f7fb 100%);}
.wrap{max-width:920px;margin:auto;padding:8px 8px 10px;min-height:100vh;}
.card{background:var(--card);border:1px solid rgba(216,226,240,.95);border-radius:16px;padding:10px 10px 8px;margin-bottom:8px;box-shadow:0 10px 28px rgba(37,99,235,.08);backdrop-filter:blur(8px);}
.toolbar{display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:8px;}
.torch-btn{width:46px;min-width:46px;height:44px;padding:0;border-radius:12px;font-size:22px;line-height:1;background:linear-gradient(135deg,#7b8794 0%,#5f6b77 100%);box-shadow:0 6px 14px rgba(46,58,74,.16);}
.torch-btn.on{background:linear-gradient(135deg,#ffe082 0%,#ffca28 100%);color:#4b3b00;box-shadow:0 8px 18px rgba(255,202,40,.36);}
.video-wrap{position:relative;width:100%;aspect-ratio:3/4;max-height:62vh;background:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #cfe0f2;box-shadow:inset 0 1px 0 rgba(255,255,255,.18);}
video{width:100%;height:100%;object-fit:cover;background:#0f172a;}
.scan-mask{position:absolute;inset:0;pointer-events:none;display:flex;align-items:center;justify-content:center;}
.scan-box{width:min(78vw,420px);height:min(26vw,150px);border:3px solid rgba(255,255,255,.96);border-radius:18px;box-shadow:0 0 0 9999px rgba(15,23,42,.28);position:relative;}
.scan-box:before{content:"";position:absolute;left:10px;right:10px;height:2px;background:rgba(96,165,250,.95);box-shadow:0 0 16px rgba(96,165,250,.95);animation:scanline 2.2s linear infinite;top:16px;}
@keyframes scanline{0%{top:16px;}50%{top:calc(100% - 18px);}100%{top:16px;}}
.status-box{display:none;margin-top:8px;padding:8px 10px;border-radius:12px;font-size:13px;line-height:1.5;}
.status-box.show{display:block;}
.status-box.info{background:#eff3f7;border:1px solid #d5dde6;color:#415264;}
.status-box.error{background:#fff1f0;border:1px solid #f5c2c0;color:#c62828;}
.status-box.success{background:#eef8f3;border:1px solid #cfe4d9;color:#0f8a56;}
.result-box{margin-top:8px;padding:8px 10px;border:1px solid #d7dee7;border-radius:12px;background:linear-gradient(180deg,#fcfcfd 0%,#f3f5f8 100%);font-size:18px;font-weight:800;letter-spacing:1px;word-break:break-all;box-shadow:inset 0 1px 0 rgba(255,255,255,.7);min-height:46px;display:flex;align-items:center;justify-content:center;text-align:center;}
.result-box.empty{color:#91a0b2;font-size:14px;font-weight:600;letter-spacing:0;}
.action-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:center;margin-top:8px;}
.action-row button,.action-row a button{width:auto;min-width:110px;}
button{padding:10px 16px;font-size:16px;border-radius:12px;border:none;cursor:pointer;font-weight:700;color:#fff;background:linear-gradient(135deg,var(--primary) 0%,var(--primary-deep) 100%);box-shadow:0 6px 14px rgba(37,99,235,.16);}
button:hover{transform:translateY(-1px);box-shadow:0 12px 22px rgba(46,58,74,.16);}
.btn-gray{background:linear-gradient(135deg,#8b95a1 0%,#697380 100%);}.btn-return-secondary{background:linear-gradient(135deg,#8f2d4b 0%,#6f1d36 100%);box-shadow:0 10px 18px rgba(111,29,54,.20);}.btn-return-secondary:hover{background:linear-gradient(135deg,#9d3656 0%,#7c2140 100%);box-shadow:0 12px 22px rgba(111,29,54,.24);}
.btn-green{background:linear-gradient(135deg,#4f8d78 0%,#2f6b58 100%);}
.mode-btn{min-width:120px;}
.summary-badge{display:inline-flex;align-items:center;padding:8px 12px;border-radius:999px;background:#eef2f6;color:#4a5767;font-weight:700;border:1px solid #d4dbe4;font-size:14px;}
@media (max-width:768px){.wrap{padding:8px 8px 10px;}.card{padding:10px 10px 8px;margin-bottom:8px;border-radius:14px;}.video-wrap{aspect-ratio:3/4;max-height:60vh;border-radius:14px;}.scan-box{width:min(82vw,340px);height:min(28vw,120px);border-radius:14px;}.result-box{padding:8px 10px;border-radius:10px;font-size:18px;min-height:46px;}.result-box.empty{font-size:14px;}.action-row{margin-top:8px;gap:8px;}.action-row button,.action-row a button{min-width:102px;}.status-box{margin-top:8px;padding:8px 10px;font-size:13px;}.scan-box:before{left:8px;right:8px;}.summary-badge{padding:7px 10px;font-size:13px;}}
</style>
<script>
const assignLocation = {{ assign_location|default('', true)|tojson|safe }};
const backUrl = {{ back_url|default(url_for('search_assets'), true)|tojson|safe }};
let scanStream = null;
let barcodeDetector = null;
let scanTimer = null;
let scanning = false;
let processingCode = false;
let lastDetectedCode = '';
let scanMode = 'search';
let inventoryScannedCodes = new Set();
let inventoryCount = 0;
let torchEnabled = false;

function setStatus(message, variant){
    const el = document.getElementById('scan-status');
    if(!el){ return; }
    if(!message){
        el.textContent = '';
        el.className = 'status-box';
        return;
    }
    el.textContent = message;
    el.className = 'status-box show ' + (variant || 'info');
}

function setResult(code){
    const el = document.getElementById('scan-result');
    if(!el){ return; }
    if(!code){
        el.textContent = '等待识别条形码';
        el.className = 'result-box empty';
        return;
    }
    el.textContent = code;
    el.className = 'result-box';
}

function updateModeButton(){
    const btn = document.getElementById('scan-mode-btn');
    if(!btn){ return; }
    btn.textContent = scanMode === 'inventory' ? '盘点' : '检索';
}

function updateInventoryCount(){
    const el = document.getElementById('inventory-count');
    if(!el){ return; }
    el.textContent = '已盘点 ' + String(inventoryCount);
}

function updateTorchButton(){
    const btn = document.getElementById('torch-btn');
    if(!btn){ return; }
    btn.classList.toggle('on', !!torchEnabled);
    btn.setAttribute('aria-pressed', torchEnabled ? 'true' : 'false');
    btn.title = torchEnabled ? '关闭手电筒' : '打开手电筒';
}

function getTorchTrack(){
    if(!scanStream){ return null; }
    const tracks = scanStream.getVideoTracks ? scanStream.getVideoTracks() : [];
    return tracks && tracks.length ? tracks[0] : null;
}

async function setTorchState(nextState){
    const track = getTorchTrack();
    if(!track){
        setStatus('相机未启动，无法切换手电筒。', 'error');
        return false;
    }
    const capabilities = typeof track.getCapabilities === 'function' ? track.getCapabilities() : {};
    if(!capabilities || !capabilities.torch){
        setStatus('当前设备或浏览器不支持手电筒。', 'info');
        return false;
    }
    try{
        await track.applyConstraints({advanced:[{torch: !!nextState}]});
        torchEnabled = !!nextState;
        updateTorchButton();
        return true;
    }catch(err){
        setStatus('手电筒切换失败：' + (err && err.message ? err.message : '未知错误'), 'error');
        return false;
    }
}

async function toggleTorch(){
    await setTorchState(!torchEnabled);
    return false;
}

function updateTorchButton(){
    const btn = document.getElementById('torch-btn');
    if(!btn){ return; }
    btn.classList.toggle('on', !!torchEnabled);
    btn.setAttribute('aria-pressed', torchEnabled ? 'true' : 'false');
    btn.title = torchEnabled ? '关闭手电筒' : '打开手电筒';
}

function getTorchTrack(){
    if(!scanStream){ return null; }
    const tracks = scanStream.getVideoTracks ? scanStream.getVideoTracks() : [];
    return tracks && tracks.length ? tracks[0] : null;
}

async function setTorchState(nextState){
    const track = getTorchTrack();
    if(!track){
        setStatus('相机未启动，无法切换手电筒。', 'error');
        return false;
    }
    const capabilities = typeof track.getCapabilities === 'function' ? track.getCapabilities() : {};
    if(!capabilities || !capabilities.torch){
        setStatus('当前设备或浏览器不支持手电筒。', 'info');
        return false;
    }
    try{
        await track.applyConstraints({advanced:[{torch: !!nextState}]});
        torchEnabled = !!nextState;
        updateTorchButton();
        return true;
    }catch(err){
        setStatus('手电筒切换失败：' + (err && err.message ? err.message : '未知错误'), 'error');
        return false;
    }
}

async function toggleTorch(){
    await setTorchState(!torchEnabled);
    return false;
}

function toggleScanMode(){
    scanMode = scanMode === 'search' ? 'inventory' : 'search';
    updateModeButton();
    updateInventoryCount();
    setStatus(scanMode === 'inventory' ? '已切换到盘点模式' : '已切换到检索模式', 'info');
    setResult('');
    lastDetectedCode = '';
    processingCode = false;
}

function normalizeCode(value){
    return String(value || '').trim().replace(/\s+/g, '');
}

function stopScan(){
    scanning = false;
    processingCode = false;
    if(scanTimer){
        window.clearTimeout(scanTimer);
        scanTimer = null;
    }
    const video = document.getElementById('scan-video');
    if(video){
        try{ video.pause(); }catch(e){}
        video.srcObject = null;
    }
    if(scanStream){
        try{
            const track = getTorchTrack();
            if(track && torchEnabled){
                try{ track.applyConstraints({advanced:[{torch:false}]}); }catch(e){}
            }
            scanStream.getTracks().forEach(track => track.stop());
        }catch(e){}
        scanStream = null;
    }
    torchEnabled = false;
    updateTorchButton();
}

async function ensureDetector(){
    if(!('BarcodeDetector' in window)){
        setStatus('当前浏览器不支持原生条形码扫描，请使用支持 BarcodeDetector 的浏览器。', 'error');
        return false;
    }
    const supported = await BarcodeDetector.getSupportedFormats();
    const formats = ['code_39', 'code_128'].filter(item => supported.includes(item));
    if(!formats.length){
        setStatus('当前浏览器不支持 Code39 / Code128 条形码识别。', 'error');
        return false;
    }
    barcodeDetector = new BarcodeDetector({formats: formats});
    return true;
}

async function startScan(){
    stopScan();
    setResult('');
    setStatus('', 'info');
    const ready = await ensureDetector();
    if(!ready){ return; }
    try{
        scanStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}},audio:false});
    }catch(err){
        setStatus('无法打开相机，请检查相机权限。' + (err && err.message ? ' ' + err.message : ''), 'error');
        return;
    }
    const video = document.getElementById('scan-video');
    if(!video){
        setStatus('视频组件初始化失败。', 'error');
        stopScan();
        return;
    }
    video.srcObject = scanStream;
    try{
        await video.play();
    }catch(err){
        setStatus('相机启动失败。' + (err && err.message ? ' ' + err.message : ''), 'error');
        stopScan();
        return;
    }
    torchEnabled = false;
    updateTorchButton();
    scanning = true;
    processingCode = false;
    lastDetectedCode = '';
    scanLoop();
}

async function handleDetectedCode(rawValue){
    if(scanMode === 'inventory' && inventoryScannedCodes.has(rawValue)){
        setResult(rawValue);
        setStatus('该设备已重复盘点', 'info');
        processingCode = true;
        window.setTimeout(function(){
            processingCode = false;
            lastDetectedCode = '';
            if(scanning){ scanLoop(); }
        }, 700);
        return;
    }
    processingCode = true;
    setResult(rawValue);
    setStatus(scanMode === 'inventory' ? '识别成功，正在盘点...' : '识别成功，正在检索...', 'success');
    try{
        const response = await fetch('/scan_label_action?mode=' + encodeURIComponent(scanMode) + '&code=' + encodeURIComponent(rawValue), {cache:'no-store'});
        const data = await response.json();
        if(!response.ok || !data || !data.ok){
            setStatus((data && data.message) ? data.message : '识别处理失败', 'error');
            processingCode = false;
            lastDetectedCode = '';
            if(scanning){ scanLoop(); }
            return;
        }
        if(data.message){
            setStatus(data.message, data.action === 'updated' ? 'success' : 'info');
        }
        if(data.action === 'redirect' && data.redirect_url){
            stopScan();
            window.setTimeout(function(){ window.location.assign(data.redirect_url); }, 180);
            return;
        }
        if(data.action === 'updated'){
            if(scanMode === 'inventory' && !inventoryScannedCodes.has(rawValue)){
                inventoryScannedCodes.add(rawValue);
                inventoryCount += 1;
                updateInventoryCount();
            }
            window.setTimeout(function(){
                processingCode = false;
                lastDetectedCode = '';
                setResult('');
                setStatus('', 'info');
                if(scanning){ scanLoop(); }
            }, 700);
            return;
        }
        processingCode = false;
        lastDetectedCode = '';
        if(scanning){ scanLoop(); }
    }catch(err){
        setStatus('处理失败：' + (err && err.message ? err.message : '未知错误'), 'error');
        processingCode = false;
        lastDetectedCode = '';
        if(scanning){ scanLoop(); }
    }
}

async function scanLoop(){
    if(!scanning || !barcodeDetector || processingCode){ return; }
    const video = document.getElementById('scan-video');
    try{
        const results = await barcodeDetector.detect(video);
        if(results && results.length){
            for(const item of results){
                const rawValue = normalizeCode(item.rawValue || '');
                const format = String(item.format || '').toLowerCase();
                if(!rawValue){ continue; }
                if(format && ['code_39','code_128'].indexOf(format) === -1){ continue; }
                if(rawValue === lastDetectedCode){ continue; }
                lastDetectedCode = rawValue;
                handleDetectedCode(rawValue);
                return;
            }
        }
    }catch(err){
        setStatus('条形码识别失败：' + (err && err.message ? err.message : '未知错误'), 'error');
    }
    scanTimer = window.setTimeout(scanLoop, 180);
}

function handleBack(){
    stopScan();
    window.location.assign(backUrl || '/');
}

window.addEventListener('beforeunload', stopScan);
window.addEventListener('pagehide', stopScan);
window.addEventListener('DOMContentLoaded', function(){
    updateModeButton();
    updateInventoryCount();
    setResult('');
    startScan();
});
</script>
</head>
<body>
<div class="wrap">
    <div class="card">
        <div class="toolbar">
            <button id="torch-btn" type="button" class="torch-btn" aria-pressed="false" title="打开手电筒" onclick="return toggleTorch();">💡</button>
            {% if assign_location %}
            <div class="summary-badge">货架：{{ assign_location }}</div>
            {% else %}
            <button id="scan-mode-btn" type="button" class="mode-btn" onclick="toggleScanMode(); return false;">检索</button>
            <div id="inventory-count" class="summary-badge">已盘点 0</div>
            {% endif %}
        </div>
        <div class="video-wrap">
            <video id="scan-video" autoplay playsinline muted></video>
            <div class="scan-mask"><div class="scan-box"></div></div>
        </div>
        <div id="scan-status" class="status-box"></div>
        <div id="scan-result" class="result-box empty">等待识别条形码</div>
        <div class="action-row">
            <button type="button" class="btn-green" onclick="startScan(); return false;">重新扫描</button>
            <button type="button" class="btn-return-secondary" onclick="handleBack(); return false;">返回</button>
        </div>
    </div>
</div>
</body>
</html>
"""

SCAN_LABEL_ASSIGN_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>条形码扫描</title>
<style>
:root{--bg:#edf3ff;--card:rgba(255,255,255,.92);--line:#d8e2f0;--text:#18324a;--muted:#66788a;--primary:#2f6df3;--primary-deep:#1d4ed8;--green:#1f9d68;--gray:#6c7a89;--danger:#c62828;}
*{box-sizing:border-box;}
body{font-family:Arial,sans-serif;margin:0;color:var(--text);background:linear-gradient(180deg,#eef4ff 0%,#f8fbff 35%,#f3f7fb 100%);}
.wrap{max-width:920px;margin:auto;padding:8px 8px 10px;min-height:100vh;}
.card{background:var(--card);border:1px solid rgba(216,226,240,.95);border-radius:16px;padding:10px 10px 8px;margin-bottom:8px;box-shadow:0 10px 28px rgba(37,99,235,.08);backdrop-filter:blur(8px);}
.toolbar{display:flex;align-items:center;justify-content:center;gap:8px;margin-bottom:8px;}
.torch-btn{width:46px;min-width:46px;height:44px;padding:0;border-radius:12px;font-size:22px;line-height:1;background:linear-gradient(135deg,#7b8794 0%,#5f6b77 100%);box-shadow:0 6px 14px rgba(46,58,74,.16);}
.torch-btn.on{background:linear-gradient(135deg,#ffe082 0%,#ffca28 100%);color:#4b3b00;box-shadow:0 8px 18px rgba(255,202,40,.36);}
.video-wrap{position:relative;width:100%;aspect-ratio:3/4;max-height:62vh;background:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #cfe0f2;box-shadow:inset 0 1px 0 rgba(255,255,255,.18);}
video{width:100%;height:100%;object-fit:cover;background:#0f172a;}
.scan-mask{position:absolute;inset:0;pointer-events:none;display:flex;align-items:center;justify-content:center;}
.scan-box{width:min(78vw,420px);height:min(26vw,150px);border:3px solid rgba(255,255,255,.96);border-radius:18px;box-shadow:0 0 0 9999px rgba(15,23,42,.28);position:relative;}
.scan-box:before{content:"";position:absolute;left:10px;right:10px;height:2px;background:rgba(96,165,250,.95);box-shadow:0 0 16px rgba(96,165,250,.95);animation:scanline 2.2s linear infinite;top:16px;}
@keyframes scanline{0%{top:16px;}50%{top:calc(100% - 18px);}100%{top:16px;}}
.status-box{display:none;margin-top:8px;padding:8px 10px;border-radius:12px;font-size:13px;line-height:1.5;}
.status-box.show{display:block;}
.status-box.info{background:#eff3f7;border:1px solid #d5dde6;color:#415264;}
.status-box.error{background:#fff1f0;border:1px solid #f5c2c0;color:#c62828;}
.status-box.success{background:#eef8f3;border:1px solid #cfe4d9;color:#0f8a56;}
.result-box{margin-top:8px;padding:8px 10px;border:1px solid #d7dee7;border-radius:12px;background:linear-gradient(180deg,#fcfcfd 0%,#f3f5f8 100%);font-size:18px;font-weight:800;letter-spacing:1px;word-break:break-all;box-shadow:inset 0 1px 0 rgba(255,255,255,.7);min-height:46px;display:flex;align-items:center;justify-content:center;text-align:center;}
.result-box.empty{color:#91a0b2;font-size:14px;font-weight:600;letter-spacing:0;}
.action-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:center;margin-top:8px;}
.action-row button,.action-row a button{width:auto;min-width:110px;}
button{padding:10px 16px;font-size:16px;border-radius:12px;border:none;cursor:pointer;font-weight:700;color:#fff;background:linear-gradient(135deg,var(--primary) 0%,var(--primary-deep) 100%);box-shadow:0 6px 14px rgba(37,99,235,.16);}
button:hover{transform:translateY(-1px);box-shadow:0 12px 22px rgba(46,58,74,.16);}
.btn-gray{background:linear-gradient(135deg,#8b95a1 0%,#697380 100%);}.btn-return-secondary{background:linear-gradient(135deg,#8f2d4b 0%,#6f1d36 100%);box-shadow:0 10px 18px rgba(111,29,54,.20);}.btn-return-secondary:hover{background:linear-gradient(135deg,#9d3656 0%,#7c2140 100%);box-shadow:0 12px 22px rgba(111,29,54,.24);}
.btn-green{background:linear-gradient(135deg,#4f8d78 0%,#2f6b58 100%);}
.mode-btn{min-width:120px;}
.summary-badge{display:inline-flex;align-items:center;padding:8px 12px;border-radius:999px;background:#eef2f6;color:#4a5767;font-weight:700;border:1px solid #d4dbe4;font-size:14px;}
@media (max-width:768px){.wrap{padding:8px 8px 10px;}.card{padding:10px 10px 8px;margin-bottom:8px;border-radius:14px;}.video-wrap{aspect-ratio:3/4;max-height:60vh;border-radius:14px;}.scan-box{width:min(82vw,340px);height:min(28vw,120px);border-radius:14px;}.result-box{padding:8px 10px;border-radius:10px;font-size:18px;min-height:46px;}.result-box.empty{font-size:14px;}.action-row{margin-top:8px;gap:8px;}.action-row button,.action-row a button{min-width:102px;}.status-box{margin-top:8px;padding:8px 10px;font-size:13px;}.scan-box:before{left:8px;right:8px;}.summary-badge{padding:7px 10px;font-size:13px;}}
</style>
<script>
const assignLocation = {{ assign_location|tojson|safe }};
const backUrl = {{ back_url|tojson|safe }};
let scanStream = null;
let barcodeDetector = null;
let scanTimer = null;
let scanning = false;
let processingCode = false;
let lastDetectedCode = '';
let scanMode = 'search';
let inventoryScannedCodes = new Set();
let inventoryCount = 0;
let torchEnabled = false;

function setStatus(message, variant){
    const el = document.getElementById('scan-status');
    if(!el){ return; }
    if(!message){
        el.textContent = '';
        el.className = 'status-box';
        return;
    }
    el.textContent = message;
    el.className = 'status-box show ' + (variant || 'info');
}

function setResult(code){
    const el = document.getElementById('scan-result');
    if(!el){ return; }
    if(!code){
        el.textContent = '等待识别条形码';
        el.className = 'result-box empty';
        return;
    }
    el.textContent = code;
    el.className = 'result-box';
}

function updateModeButton(){
    const btn = document.getElementById('scan-mode-btn');
    if(!btn){ return; }
    btn.textContent = scanMode === 'inventory' ? '盘点' : '检索';
}

function updateInventoryCount(){
    const el = document.getElementById('inventory-count');
    if(!el){ return; }
    el.textContent = '已盘点 ' + String(inventoryCount);
}

function updateTorchButton(){
    const btn = document.getElementById('torch-btn');
    if(!btn){ return; }
    btn.classList.toggle('on', !!torchEnabled);
    btn.setAttribute('aria-pressed', torchEnabled ? 'true' : 'false');
    btn.title = torchEnabled ? '关闭手电筒' : '打开手电筒';
}

function getTorchTrack(){
    if(!scanStream){ return null; }
    const tracks = scanStream.getVideoTracks ? scanStream.getVideoTracks() : [];
    return tracks && tracks.length ? tracks[0] : null;
}

async function setTorchState(nextState){
    const track = getTorchTrack();
    if(!track){
        setStatus('相机未启动，无法切换手电筒。', 'error');
        return false;
    }
    const capabilities = typeof track.getCapabilities === 'function' ? track.getCapabilities() : {};
    if(!capabilities || !capabilities.torch){
        setStatus('当前设备或浏览器不支持手电筒。', 'info');
        return false;
    }
    try{
        await track.applyConstraints({advanced:[{torch: !!nextState}]});
        torchEnabled = !!nextState;
        updateTorchButton();
        return true;
    }catch(err){
        setStatus('手电筒切换失败：' + (err && err.message ? err.message : '未知错误'), 'error');
        return false;
    }
}

async function toggleTorch(){
    await setTorchState(!torchEnabled);
    return false;
}

function toggleScanMode(){
    scanMode = scanMode === 'search' ? 'inventory' : 'search';
    updateModeButton();
    updateInventoryCount();
    setStatus(scanMode === 'inventory' ? '已切换到盘点模式' : '已切换到检索模式', 'info');
    setResult('');
    lastDetectedCode = '';
    processingCode = false;
}

function normalizeCode(value){
    return String(value || '').trim().replace(/\s+/g, '');
}

function stopScan(){
    scanning = false;
    processingCode = false;
    if(scanTimer){
        window.clearTimeout(scanTimer);
        scanTimer = null;
    }
    const video = document.getElementById('scan-video');
    if(video){
        try{ video.pause(); }catch(e){}
        video.srcObject = null;
    }
    if(scanStream){
        try{
            const track = getTorchTrack();
            if(track && torchEnabled){
                try{ track.applyConstraints({advanced:[{torch:false}]}); }catch(e){}
            }
            scanStream.getTracks().forEach(track => track.stop());
        }catch(e){}
        scanStream = null;
    }
    torchEnabled = false;
    updateTorchButton();
}

async function ensureDetector(){
    if(!('BarcodeDetector' in window)){
        setStatus('当前浏览器不支持原生条形码扫描，请使用支持 BarcodeDetector 的浏览器。', 'error');
        return false;
    }
    const supported = await BarcodeDetector.getSupportedFormats();
    const formats = ['code_39', 'code_128'].filter(item => supported.includes(item));
    if(!formats.length){
        setStatus('当前浏览器不支持 Code39 / Code128 条形码识别。', 'error');
        return false;
    }
    barcodeDetector = new BarcodeDetector({formats: formats});
    return true;
}

async function startScan(){
    stopScan();
    setResult('');
    setStatus('', 'info');
    const ready = await ensureDetector();
    if(!ready){ return; }
    try{
        scanStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}},audio:false});
    }catch(err){
        setStatus('无法打开相机，请检查相机权限。' + (err && err.message ? ' ' + err.message : ''), 'error');
        return;
    }
    const video = document.getElementById('scan-video');
    if(!video){
        setStatus('视频组件初始化失败。', 'error');
        stopScan();
        return;
    }
    video.srcObject = scanStream;
    try{
        await video.play();
    }catch(err){
        setStatus('相机启动失败。' + (err && err.message ? ' ' + err.message : ''), 'error');
        stopScan();
        return;
    }
    torchEnabled = false;
    updateTorchButton();
    scanning = true;
    processingCode = false;
    lastDetectedCode = '';
    scanLoop();
}

async function handleDetectedCode(rawValue){
    if(scanMode === 'inventory' && inventoryScannedCodes.has(rawValue)){
        setResult(rawValue);
        setStatus('该设备已重复盘点', 'info');
        processingCode = true;
        window.setTimeout(function(){
            processingCode = false;
            lastDetectedCode = '';
            if(scanning){ scanLoop(); }
        }, 700);
        return;
    }
    processingCode = true;
    setResult(rawValue);
    setStatus(scanMode === 'inventory' ? '识别成功，正在盘点...' : '识别成功，正在检索...', 'success');
    try{
        const response = await fetch('/scan_label_action?mode=' + encodeURIComponent(scanMode) + '&code=' + encodeURIComponent(rawValue) + '&location=' + encodeURIComponent(assignLocation), {cache:'no-store'});
        const data = await response.json();
        if(!response.ok || !data || !data.ok){
            setStatus((data && data.message) ? data.message : '识别处理失败', 'error');
            processingCode = false;
            lastDetectedCode = '';
            if(scanning){ scanLoop(); }
            return;
        }
        if(data.action === 'confirm_location_update'){
            const confirmMessage = data.message || ('是否将该资产（' + (data.group_no || rawValue) + '，' + (data.name || '') + '）添加至本货架（' + assignLocation + '）下？');
            if(window.confirm(confirmMessage)){
                const confirmResponse = await fetch('/scan_label_action?mode=' + encodeURIComponent(scanMode) + '&code=' + encodeURIComponent(rawValue) + '&location=' + encodeURIComponent(assignLocation) + '&confirm_update=1', {cache:'no-store'});
                const confirmData = await confirmResponse.json();
                if(!confirmResponse.ok || !confirmData || !confirmData.ok){
                    setStatus((confirmData && confirmData.message) ? confirmData.message : '位置更新失败', 'error');
                }else if(confirmData.action === 'redirect' && confirmData.redirect_url){
                    stopScan();
                    window.setTimeout(function(){ window.location.assign(confirmData.redirect_url); }, 180);
                    return;
                }else{
                    setStatus(confirmData.message || '位置更新成功', 'success');
                }
            }else{
                setStatus('已取消位置更新', 'info');
            }
            window.setTimeout(function(){
                processingCode = false;
                lastDetectedCode = '';
                setResult('');
                if(scanning){ scanLoop(); }
            }, 700);
            return;
        }
        if(data.message){
            setStatus(data.message, data.action === 'updated' ? 'success' : 'info');
        }
        if(data.action === 'redirect' && data.redirect_url){
            stopScan();
            window.setTimeout(function(){ window.location.assign(data.redirect_url); }, 180);
            return;
        }
        if(data.action === 'updated'){
            if(scanMode === 'inventory' && !inventoryScannedCodes.has(rawValue)){
                inventoryScannedCodes.add(rawValue);
                inventoryCount += 1;
                updateInventoryCount();
            }
            window.setTimeout(function(){
                processingCode = false;
                lastDetectedCode = '';
                setResult('');
                setStatus('', 'info');
                if(scanning){ scanLoop(); }
            }, 700);
            return;
        }
        processingCode = false;
        lastDetectedCode = '';
        if(scanning){ scanLoop(); }
    }catch(err){
        setStatus('处理失败：' + (err && err.message ? err.message : '未知错误'), 'error');
        processingCode = false;
        lastDetectedCode = '';
        if(scanning){ scanLoop(); }
    }
}

async function scanLoop(){
    if(!scanning || !barcodeDetector || processingCode){ return; }
    const video = document.getElementById('scan-video');
    try{
        const results = await barcodeDetector.detect(video);
        if(results && results.length){
            for(const item of results){
                const rawValue = normalizeCode(item.rawValue || '');
                const format = String(item.format || '').toLowerCase();
                if(!rawValue){ continue; }
                if(format && ['code_39','code_128'].indexOf(format) === -1){ continue; }
                if(rawValue === lastDetectedCode){ continue; }
                lastDetectedCode = rawValue;
                handleDetectedCode(rawValue);
                return;
            }
        }
    }catch(err){
        setStatus('条形码识别失败：' + (err && err.message ? err.message : '未知错误'), 'error');
    }
    scanTimer = window.setTimeout(scanLoop, 180);
}

function handleBack(){
    stopScan();
    window.location.assign(backUrl || '/');
}

window.addEventListener('beforeunload', stopScan);
window.addEventListener('pagehide', stopScan);
window.addEventListener('DOMContentLoaded', function(){
    updateModeButton();
    updateInventoryCount();
    setResult('');
    setStatus('当前货架：' + assignLocation, 'info');
    startScan();
});
</script>
</head>
<body>
<div class="wrap">
    <div class="card">
        <div class="toolbar">
            <button id="torch-btn" type="button" class="torch-btn" aria-pressed="false" title="打开手电筒" onclick="return toggleTorch();">💡</button>
            <div class="summary-badge">货架：{{ assign_location }}</div>
        </div>
        <div class="video-wrap">
            <video id="scan-video" autoplay playsinline muted></video>
            <div class="scan-mask"><div class="scan-box"></div></div>
        </div>
        <div id="scan-status" class="status-box"></div>
        <div id="scan-result" class="result-box empty">等待识别条形码</div>
        <div class="action-row">
            <button type="button" class="btn-green" onclick="startScan(); return false;">重新扫描</button>
            <button type="button" class="btn-return-secondary" onclick="handleBack(); return false;">返回</button>
        </div>
    </div>
</div>
</body>
</html>
"""

SCAN_LABEL_IMAGE_HTML = r"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>标签识别</title>
<style>
:root{--bg:#edf3ff;--card:rgba(255,255,255,.92);--line:#d8e2f0;--text:#18324a;--muted:#66788a;--primary:#2f6df3;--primary-deep:#1d4ed8;--green:#1f9d68;--gray:#6c7a89;--danger:#c62828;}
*{box-sizing:border-box;}
body{font-family:Arial,sans-serif;margin:0;color:var(--text);background:linear-gradient(180deg,#eef4ff 0%,#f8fbff 35%,#f3f7fb 100%);}
.wrap{max-width:920px;margin:auto;padding:8px 8px 10px;min-height:100vh;}
.card{background:var(--card);border:1px solid rgba(216,226,240,.95);border-radius:16px;padding:10px 10px 8px;margin-bottom:8px;box-shadow:0 10px 28px rgba(37,99,235,.08);backdrop-filter:blur(8px);}
.toolbar{display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap;margin-bottom:8px;}
.torch-btn{width:46px;min-width:46px;height:44px;padding:0;border-radius:12px;font-size:22px;line-height:1;background:linear-gradient(135deg,#7b8794 0%,#5f6b77 100%);box-shadow:0 6px 14px rgba(46,58,74,.16);}
.torch-btn.on{background:linear-gradient(135deg,#ffe082 0%,#ffca28 100%);color:#4b3b00;box-shadow:0 8px 18px rgba(255,202,40,.36);}
.video-wrap{position:relative;width:100%;aspect-ratio:3/4;max-height:62vh;background:#0f172a;border-radius:16px;overflow:hidden;border:1px solid #cfe0f2;box-shadow:inset 0 1px 0 rgba(255,255,255,.18);}
video{width:100%;height:100%;object-fit:cover;background:#0f172a;}
.scan-mask{position:absolute;inset:0;pointer-events:none;display:flex;align-items:center;justify-content:center;}
.scan-box{width:min(78vw,420px);height:min(13vw,75px);border:3px solid rgba(255,255,255,.96);border-radius:18px;box-shadow:0 0 0 9999px rgba(15,23,42,.28);position:relative;}
.scan-box:before{content:"";position:absolute;left:10px;right:10px;height:2px;background:rgba(96,165,250,.95);box-shadow:0 0 16px rgba(96,165,250,.95);top:50%;transform:translateY(-50%);}
.status-box{display:none;margin-top:8px;padding:8px 10px;border-radius:12px;font-size:13px;line-height:1.5;}
.status-box.show{display:block;}
.status-box.info{background:#eff3f7;border:1px solid #d5dde6;color:#415264;}
.status-box.error{background:#fff1f0;border:1px solid #f5c2c0;color:#c62828;}
.status-box.success{background:#eef8f3;border:1px solid #cfe4d9;color:#0f8a56;}
.result-box{margin-top:8px;padding:8px 10px;border:1px solid #d7dee7;border-radius:12px;background:linear-gradient(180deg,#fcfcfd 0%,#f3f5f8 100%);font-size:18px;font-weight:800;letter-spacing:1px;word-break:break-all;box-shadow:inset 0 1px 0 rgba(255,255,255,.7);min-height:46px;display:flex;align-items:center;justify-content:center;text-align:center;}
.result-box.empty{color:#91a0b2;font-size:14px;font-weight:600;letter-spacing:0;}
.action-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;justify-content:center;margin-top:8px;}
.action-row button,.action-row a button{width:auto;min-width:110px;}
button{padding:10px 16px;font-size:16px;border-radius:12px;border:none;cursor:pointer;font-weight:700;color:#fff;background:linear-gradient(135deg,var(--primary) 0%,var(--primary-deep) 100%);box-shadow:0 6px 14px rgba(37,99,235,.16);}
button:hover{transform:translateY(-1px);box-shadow:0 12px 22px rgba(46,58,74,.16);}
.btn-gray{background:linear-gradient(135deg,#8b95a1 0%,#697380 100%);}.btn-return-secondary{background:linear-gradient(135deg,#8f2d4b 0%,#6f1d36 100%);box-shadow:0 10px 18px rgba(111,29,54,.20);}.btn-return-secondary:hover{background:linear-gradient(135deg,#9d3656 0%,#7c2140 100%);box-shadow:0 12px 22px rgba(111,29,54,.24);}
.btn-green{background:linear-gradient(135deg,#4f8d78 0%,#2f6b58 100%);}
.mode-btn{min-width:120px;}
.summary-badge{display:inline-flex;align-items:center;padding:8px 12px;border-radius:999px;background:#eef2f6;color:#4a5767;font-weight:700;border:1px solid #d4dbe4;font-size:14px;}
@media (max-width:768px){.wrap{padding:8px 8px 10px;}.card{padding:10px 10px 8px;margin-bottom:8px;border-radius:14px;}.video-wrap{aspect-ratio:3/4;max-height:60vh;border-radius:14px;}.scan-box{width:min(65.6vw,272px);height:min(10vw,50px);border-radius:14px;}.result-box{padding:8px 10px;border-radius:10px;font-size:18px;min-height:46px;}.result-box.empty{font-size:14px;}.action-row{margin-top:8px;gap:8px;}.action-row button,.action-row a button{min-width:102px;}.status-box{margin-top:8px;padding:8px 10px;font-size:13px;}.scan-box:before{left:8px;right:8px;}.summary-badge{padding:7px 10px;font-size:13px;}}
</style>
<script>
const assignLocation = {{ assign_location|default('', true)|tojson|safe }};
const backUrl = {{ back_url|default(url_for('search_assets'), true)|tojson|safe }};
let scanStream = null;
let scanMode = 'search';
let inventoryScannedCodes = new Set();
let inventoryCount = 0;
let busy = false;
let torchEnabled = false;

function setStatus(message, variant){
    const el = document.getElementById('scan-status');
    if(!el){ return; }
    if(!message){
        el.textContent = '';
        el.className = 'status-box';
        return;
    }
    el.textContent = message;
    el.className = 'status-box show ' + (variant || 'info');
}

function setResult(code){
    const el = document.getElementById('scan-result');
    if(!el){ return; }
    if(!code){
        el.textContent = '等待识别标签';
        el.className = 'result-box empty';
        return;
    }
    el.textContent = code;
    el.className = 'result-box';
}

function updateModeButton(){
    const btn = document.getElementById('scan-mode-btn');
    if(!btn){ return; }
    btn.textContent = scanMode === 'inventory' ? '盘点' : '检索';
}

function updateInventoryCount(){
    const el = document.getElementById('inventory-count');
    if(!el){ return; }
    el.textContent = '已盘点 ' + String(inventoryCount);
}

function updateTorchButton(){
    const btn = document.getElementById('torch-btn');
    if(!btn){ return; }
    btn.classList.toggle('on', !!torchEnabled);
    btn.setAttribute('aria-pressed', torchEnabled ? 'true' : 'false');
    btn.title = torchEnabled ? '关闭手电筒' : '打开手电筒';
}

function getTorchTrack(){
    if(!scanStream){ return null; }
    const tracks = scanStream.getVideoTracks ? scanStream.getVideoTracks() : [];
    return tracks && tracks.length ? tracks[0] : null;
}

async function setTorchState(nextState){
    const track = getTorchTrack();
    if(!track){
        setStatus('相机未启动，无法切换手电筒。', 'error');
        return false;
    }
    const capabilities = typeof track.getCapabilities === 'function' ? track.getCapabilities() : {};
    if(!capabilities || !capabilities.torch){
        setStatus('当前设备或浏览器不支持手电筒。', 'info');
        return false;
    }
    try{
        await track.applyConstraints({advanced:[{torch: !!nextState}]});
        torchEnabled = !!nextState;
        updateTorchButton();
        return true;
    }catch(err){
        setStatus('手电筒切换失败：' + (err && err.message ? err.message : '未知错误'), 'error');
        return false;
    }
}

async function toggleTorch(){
    await setTorchState(!torchEnabled);
    return false;
}

function toggleScanMode(){
    scanMode = scanMode === 'search' ? 'inventory' : 'search';
    updateModeButton();
    updateInventoryCount();
    setStatus(scanMode === 'inventory' ? '已切换到盘点模式' : '已切换到检索模式', 'info');
    setResult('');
}

function normalizeCode(value){
    return String(value || '').trim().replace(/\s+/g, '');
}

function stopCamera(){
    busy = false;
    const video = document.getElementById('scan-video');
    if(video){
        try{ video.pause(); }catch(e){}
        video.srcObject = null;
    }
    if(scanStream){
        try{
            const track = getTorchTrack();
            if(track && torchEnabled){
                try{ track.applyConstraints({advanced:[{torch:false}]}); }catch(e){}
            }
            scanStream.getTracks().forEach(track => track.stop());
        }catch(e){}
        scanStream = null;
    }
    torchEnabled = false;
    updateTorchButton();
}

async function startCamera(){
    stopCamera();
    setStatus('', 'info');
    setResult('');
    try{
        scanStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1280},height:{ideal:720}},audio:false});
    }catch(err){
        setStatus('无法打开相机，请检查相机权限。' + (err && err.message ? ' ' + err.message : ''), 'error');
        return;
    }
    const video = document.getElementById('scan-video');
    if(!video){
        setStatus('视频组件初始化失败。', 'error');
        stopCamera();
        return;
    }
    video.srcObject = scanStream;
    try{
        await video.play();
    }catch(err){
        setStatus('相机启动失败。' + (err && err.message ? ' ' + err.message : ''), 'error');
        stopCamera();
        return;
    }
    torchEnabled = false;
    updateTorchButton();
}

function computeCropSourceRect(video, boxRect, videoRect){
    const sourceW = video.videoWidth || 0;
    const sourceH = video.videoHeight || 0;
    const displayW = videoRect.width;
    const displayH = videoRect.height;
    if(!sourceW || !sourceH || !displayW || !displayH){ return null; }
    const scale = Math.max(displayW / sourceW, displayH / sourceH);
    const renderedW = sourceW * scale;
    const renderedH = sourceH * scale;
    const offsetX = (renderedW - displayW) / 2;
    const offsetY = (renderedH - displayH) / 2;
    const x = boxRect.left - videoRect.left;
    const y = boxRect.top - videoRect.top;
    const w = boxRect.width;
    const h = boxRect.height;
    const sx = Math.max(0, (x + offsetX) / scale);
    const sy = Math.max(0, (y + offsetY) / scale);
    const sw = Math.min(sourceW - sx, w / scale);
    const sh = Math.min(sourceH - sy, h / scale);
    if(sw <= 0 || sh <= 0){ return null; }
    return {sx, sy, sw, sh};
}

async function captureScanImageBlob(){
    const video = document.getElementById('scan-video');
    const box = document.getElementById('scan-box');
    if(!video || !box || !video.videoWidth || !video.videoHeight){
        throw new Error('相机画面尚未准备好');
    }
    const videoRect = video.getBoundingClientRect();
    const boxRect = box.getBoundingClientRect();
    const crop = computeCropSourceRect(video, boxRect, videoRect);
    if(!crop){
        throw new Error('无法获取扫描框区域');
    }
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(crop.sw));
    canvas.height = Math.max(1, Math.round(crop.sh));
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, crop.sx, crop.sy, crop.sw, crop.sh, 0, 0, canvas.width, canvas.height);
    return await new Promise(function(resolve, reject){
        canvas.toBlob(function(blob){
            if(blob){ resolve(blob); }
            else{ reject(new Error('截图失败')); }
        }, 'image/jpeg', 0.92);
    });
}

async function recognizeLabel(){
    if(busy){ return false; }
    busy = true;
    setStatus(scanMode === 'inventory' ? '正在识别并盘点...' : '正在识别并检索...', 'info');
    try{
        const blob = await captureScanImageBlob();
        const formData = new FormData();
        formData.append('mode', scanMode);
        if(assignLocation){ formData.append('location', assignLocation); }
        formData.append('scan_image', blob, 'scan_box.jpg');
        const response = await fetch('/scan_label_image_action', {method:'POST', body:formData, cache:'no-store'});
        const data = await response.json();
        if(!response.ok || !data || !data.ok){
            setStatus((data && data.message) ? data.message : '识别处理失败', 'error');
            if(data && data.code){ setResult(data.code); }
            else{ setResult(''); }
            busy = false;
            return false;
        }
        const rawValue = normalizeCode(data.code || '');
        if(rawValue){ setResult(rawValue); }
        else{ setResult(''); }
        if(scanMode === 'inventory' && rawValue && inventoryScannedCodes.has(rawValue)){
            setStatus('该设备已重复盘点', 'info');
            busy = false;
            return false;
        }
        if(data.action === 'confirm_location_update'){
            const confirmMessage = data.message || ('是否将该资产（' + (data.group_no || rawValue) + '，' + (data.name || '') + '）添加至本货架（' + assignLocation + '）下？');
            if(window.confirm(confirmMessage)){
                const confirmResponse = await fetch('/scan_label_action?mode=' + encodeURIComponent(scanMode) + '&code=' + encodeURIComponent(rawValue) + '&location=' + encodeURIComponent(assignLocation) + '&confirm_update=1', {cache:'no-store'});
                const confirmData = await confirmResponse.json();
                if(!confirmResponse.ok || !confirmData || !confirmData.ok){
                    setStatus((confirmData && confirmData.message) ? confirmData.message : '位置更新失败', 'error');
                }else if(confirmData.action === 'redirect' && confirmData.redirect_url){
                    stopCamera();
                    window.setTimeout(function(){ window.location.assign(confirmData.redirect_url); }, 180);
                    return false;
                }else{
                    setStatus(confirmData.message || '位置更新成功', 'success');
                }
            }else{
                setStatus('已取消位置更新', 'info');
            }
            busy = false;
            return false;
        }
        if(data.message){
            setStatus(data.message, data.action === 'updated' ? 'success' : 'info');
        }
        if(data.action === 'redirect' && data.redirect_url){
            stopCamera();
            window.setTimeout(function(){ window.location.assign(data.redirect_url); }, 180);
            return false;
        }
        if(data.action === 'updated' && scanMode === 'inventory' && rawValue && !inventoryScannedCodes.has(rawValue)){
            inventoryScannedCodes.add(rawValue);
            inventoryCount += 1;
            updateInventoryCount();
        }
    }catch(err){
        setStatus('识别失败：' + (err && err.message ? err.message : '未知错误'), 'error');
    }
    busy = false;
    return false;
}

function handleBack(){
    stopCamera();
    window.location.assign(backUrl || '/');
}

window.addEventListener('beforeunload', stopCamera);
window.addEventListener('pagehide', stopCamera);
window.addEventListener('DOMContentLoaded', function(){
    updateModeButton();
    updateInventoryCount();
    updateTorchButton();
    setResult('');
    if(assignLocation){ setStatus('当前货架：' + assignLocation, 'info'); }
    startCamera();
});
</script>
</head>
<body>
<div class="wrap">
    <div class="card">
        <div class="toolbar">
            <button id="torch-btn" type="button" class="torch-btn" aria-pressed="false" title="打开手电筒" onclick="return toggleTorch();">💡</button>
            {% if assign_location %}
            <div class="summary-badge">货架：{{ assign_location }}</div>
            {% else %}
            <button id="scan-mode-btn" type="button" class="mode-btn" onclick="toggleScanMode(); return false;">检索</button>
            <div id="inventory-count" class="summary-badge">已盘点 0</div>
            {% endif %}
        </div>
        <div class="video-wrap">
            <video id="scan-video" autoplay playsinline muted></video>
            <div class="scan-mask"><div id="scan-box" class="scan-box"></div></div>
        </div>
        <div id="scan-status" class="status-box"></div>
        <div id="scan-result" class="result-box empty">等待识别标签</div>
        <div class="action-row">
            <button type="button" class="btn-green" onclick="recognizeLabel(); return false;">识别</button>
            <button type="button" class="btn-return-secondary" onclick="handleBack(); return false;">返回</button>
        </div>
    </div>
</div>
</body>
</html>
"""



DEVICE_NEW_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>新增设备</title>
<style>
:root{--bg:#edf1f5;--card:#ffffff;--line:#d9e1ea;--text:#233243;--muted:#677383;--primary:#5f6f82;--primary-soft:#eef2f6;--green:#2f7d67;--orange:#b88347;--danger:#dc2626;}
*{box-sizing:border-box;}
body{font-family:Arial,sans-serif;margin:0;color:var(--text);background:linear-gradient(180deg,#edf1f5 0%,#f7f8fa 34%,#f1f4f8 100%);}
.wrap{max-width:900px;margin:auto;padding:16px 14px 26px;}
.card{background:rgba(255,255,255,.94);border:1px solid rgba(210,218,227,.98);border-radius:22px;padding:18px 18px 16px;margin-bottom:16px;box-shadow:0 16px 40px rgba(27,39,53,.07);backdrop-filter:blur(10px);}
h2{margin:0 0 14px;font-size:26px;color:#263646;letter-spacing:.4px;}
a{color:#5a6675;}
.row{margin-bottom:12px;}
label{display:block;margin-bottom:6px;font-weight:600;color:#5e6977;}
input,select,textarea,button{width:100%;box-sizing:border-box;padding:10px 12px;font-size:16px;border-radius:12px;border:1px solid #cfd6df;transition:all .2s ease;}
input[type=text],input[type=date],input[type=password],select,textarea{background:#fbfdff;}
input[type=text]:focus,input[type=date]:focus,input[type=password]:focus,select:focus,textarea:focus{outline:none;border-color:#9ba8b8;box-shadow:0 0 0 4px rgba(95,111,130,.12);background:#fff;}
input[type=file]{background:#fff;}
input[type=checkbox]{accent-color:#5f6f82;width:16px;height:16px;}
textarea{min-height:90px;resize:vertical;}
button{background:linear-gradient(135deg,#718194 0%,#556274 100%);color:#fff;border:none;cursor:pointer;font-weight:700;box-shadow:0 8px 18px rgba(46,58,74,.14);}
button:hover{transform:translateY(-1px);box-shadow:0 12px 22px rgba(46,58,74,.16);}
button:disabled{cursor:not-allowed;transform:none;box-shadow:none;}
.btn-green{background:linear-gradient(135deg,#4f8d78 0%,#2f6b58 100%);}
.btn-gray{background:linear-gradient(135deg,#8b95a1 0%,#697380 100%);}.btn-return-secondary{background:linear-gradient(135deg,#8f2d4b 0%,#6f1d36 100%);box-shadow:0 10px 18px rgba(111,29,54,.20);}.btn-return-secondary:hover{background:linear-gradient(135deg,#9d3656 0%,#7c2140 100%);box-shadow:0 12px 22px rgba(111,29,54,.24);}
.btn-orange{background:linear-gradient(135deg,#c39a68 0%,#a9753a 100%);}
.btn-red{background:linear-gradient(135deg,#d06f72 0%,#b34f55 100%);}
.btn-disabled{background:#adb5bd !important;color:#fff !important;cursor:not-allowed;pointer-events:none;box-shadow:none !important;}
.readonly{background:#eef2f6;color:#677383;border-color:#d8e0e8;}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.table-wrap{overflow-x:auto;border:1px solid #d7dee7;border-radius:16px;background:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,.8),0 8px 22px rgba(27,39,53,.04);}
table{width:100%;border-collapse:separate;border-spacing:0;min-width:780px;}
th,td{padding:11px 10px;text-align:left;border:none;border-right:1px solid #e7eef7;border-bottom:1px solid #e7eef7;vertical-align:middle;font-size:15px;font-weight:400;color:#6f7b88;}th:last-child,td:last-child{border-right:none;}
thead th{background:linear-gradient(180deg,#fafbfd 0%,#eff2f6 100%);color:#5e6977;font-weight:600;white-space:nowrap;}
tbody tr:last-child td{border-bottom:none;}
tbody tr.result-row{transition:transform .16s ease,box-shadow .16s ease;}
tbody tr.result-row td:first-child{border-left:4px solid transparent;}
tbody tr.result-row:nth-child(4n+1) td{background:#fbfcfd;}
tbody tr.result-row:nth-child(4n+2) td{background:#f8fafb;}
tbody tr.result-row:nth-child(4n+3) td{background:#fcfbf8;}
tbody tr.result-row:nth-child(4n+4) td{background:#f9f8fb;}
tbody tr.result-row:hover td{background:#eff3f7;}
tbody tr.result-row:hover{transform:translateY(-1px);box-shadow:0 10px 22px rgba(42,56,74,.08);}
tbody tr.result-row-asset td:first-child{border-left-color:#8fa8c2;}
tbody tr.result-row-accessory td:first-child{border-left-color:#95b6a1;}
tbody tr.empty-row td{background:#fbfdff;color:#6f7b88;text-align:center;padding:20px 12px;}
.msg{color:#15803d;margin-bottom:12px;font-weight:700;background:#eef8f3;border:1px solid #cfe4d9;border-radius:12px;padding:10px 12px;}
.err{color:#dc2626;margin-bottom:12px;font-weight:700;background:#fbf1f2;border:1px solid #e7cfd3;border-radius:12px;padding:10px 12px;}
.image-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;padding:10px 12px;background:#fafbfd;border:1px solid #dde4ec;border-radius:12px;}
.upload-actions{display:flex;gap:8px;flex-wrap:wrap;}
.upload-actions button{width:auto;min-width:120px;}
.file-list{margin-top:8px;color:#66788a;font-size:14px;word-break:break-all;line-height:1.6;}
.upload-preview-list{display:flex;flex-direction:column;gap:6px;margin-top:8px;}.upload-preview-item{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 9px;border:1px solid #e1e7ef;border-radius:10px;background:#fbfdff;color:#4d5b6b;}.upload-preview-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}.upload-remove-btn{width:28px;min-width:28px;height:28px;padding:0;border-radius:999px;background:#dc2626;color:#fff;border:none;box-shadow:none;font-weight:900;line-height:1;display:inline-flex;align-items:center;justify-content:center;}
.upload-dialog{position:fixed;left:0;top:0;width:100vw;height:100dvh;background:transparent;display:none;z-index:9999;overflow:hidden;} .upload-dialog.show{display:block;} .upload-dialog-card{position:fixed;left:50%;top:56%;transform:translate(-50%,-50%);width:min(320px,calc(100vw - 24px));max-width:320px;background:#fff;border-radius:16px;padding:18px;box-shadow:0 20px 40px rgba(15,23,42,.22);z-index:10000;}
.upload-dialog-title{font-size:18px;font-weight:bold;margin-bottom:12px;text-align:center;color:#163047;}
.upload-dialog-actions{display:flex;flex-direction:column;gap:10px;}
.upload-dialog-actions button{width:100%;}
.upload-choice-file{position:relative;display:block;width:100%;box-sizing:border-box;padding:10px;font-size:16px;border-radius:12px;border:1px solid #cfd9e6;color:#fff;text-align:center;overflow:hidden;box-shadow:0 8px 18px rgba(46,58,74,.14);}
.upload-choice-camera{background:linear-gradient(135deg,#67c9ff 0%,#3388ff 100%);box-shadow:0 10px 18px rgba(54,132,255,.2);}
.upload-choice-local{background:linear-gradient(135deg,#54ddb1 0%,#159f76 100%);box-shadow:0 10px 18px rgba(21,159,118,.2);}
.upload-choice-file input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;}
.upload-dialog-actions .btn-cancel{background:linear-gradient(135deg,#c5ccd4 0%,#97a2af 100%);box-shadow:0 8px 16px rgba(99,116,135,.16);}
.title-row{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:nowrap;margin-bottom:14px;}
.title-row h2{margin:0;flex:0 1 auto;}
.title-actions{display:flex;align-items:center;justify-content:flex-end;gap:12px;flex-wrap:nowrap;margin-left:auto;flex:0 0 auto;}
.title-actions > *{flex:0 0 auto;}
.title-actions button{width:auto;min-width:96px;padding:10px 14px;}
.detail-actions{display:flex;gap:20px;flex-wrap:nowrap;align-items:center;justify-content:space-between;margin-top:12px;}
.detail-actions button{width:auto;min-width:118px;padding:10px 14px;flex:0 0 auto;}
.link-row{margin-top:-2px;margin-bottom:12px;color:#607284;font-size:14px;}
.muted{color:var(--muted);font-size:14px;line-height:1.6;}.num-link{color:#4e5865;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px;font-weight:400;font-size:18px;}.num-link:hover{color:#3f4853;text-decoration:underline;}
.simple-modal{position:fixed;inset:0;background:rgba(15,23,42,.52);display:none;align-items:center;justify-content:center;z-index:10000;padding:18px;}
.simple-modal.show{display:flex;}
.simple-modal-card{width:100%;max-width:380px;background:#fff;border-radius:18px;padding:22px 20px;box-shadow:0 18px 50px rgba(15,23,42,.28);}
.simple-modal-title{font-size:22px;font-weight:800;color:#111827;text-align:center;margin-bottom:10px;}
.simple-modal-text{font-size:18px;font-weight:600;line-height:1.7;color:#1f2937;text-align:center;margin-bottom:14px;}
.simple-modal-input-wrap{display:none;margin-bottom:12px;}
.simple-modal-input-wrap.show{display:block;}
.simple-modal-input{width:100%;box-sizing:border-box;padding:12px 14px;font-size:18px;font-weight:700;border-radius:12px;border:2px solid #9ca3af;color:#111827;}
.simple-modal-input:focus{outline:none;border-color:#374151;box-shadow:0 0 0 3px rgba(55,65,81,.12);}
.simple-modal-error{min-height:24px;font-size:16px;font-weight:700;color:#b91c1c;text-align:center;margin-bottom:6px;}
.simple-modal-actions{display:flex;gap:12px;justify-content:center;margin-top:6px;}
.simple-modal-actions button{width:auto;min-width:118px;padding:11px 18px;font-size:17px;font-weight:700;border-radius:12px;border:none;}
.simple-modal-cancel{background:#6b7280;color:#fff;}
.simple-modal-ok{background:#b91c1c;color:#fff;}
@media (max-width:768px){.wrap{padding:14px 12px 20px;}.card{padding:16px;margin-bottom:14px;border-radius:14px;}.grid{grid-template-columns:1fr;}h2{font-size:22px;}.switch-row{gap:8px;}.switch-row a{flex:1 1 160px;}input,select,textarea,button{padding:10px;font-size:16px;border-radius:8px;}.upload-choice-file{padding:10px;font-size:16px;border-radius:8px;}.upload-dialog-card{border-radius:14px;padding:16px;width:min(320px,calc(100vw - 20px));top:58%;}.simple-modal{padding:16px;}.simple-modal-card{border-radius:16px;padding:18px 16px;}.simple-modal-title{font-size:20px;}.simple-modal-text{font-size:16px;}.simple-modal-input{padding:10px 12px;font-size:16px;border-radius:8px;}.simple-modal-actions button{min-width:108px;padding:10px 16px;font-size:16px;border-radius:10px;}}

.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3),
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5),
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){white-space:nowrap;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:16%;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:23.25%;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:12.75%;min-width:109px;}
@media (max-width:768px){
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.table-wrap table{width:max-content;table-layout:auto !important;}
.asset-search-table{min-width:1220px !important;}
.asset-location-table{min-width:1120px !important;}
.asset-accessory-table{min-width:980px !important;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:auto !important;min-width:192px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:auto !important;min-width:250px;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:auto !important;min-width:141px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){width:auto !important;min-width:220px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.table-wrap table .num-link{display:inline-block;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;max-width:none !important;word-break:normal !important;}
}
</style>
<script>
let activeImageInputId = '';

function validateAssetGroupNoValue(value){
    value = (value || '').trim();
    if(!value){
        return '';
    }
    return /^\\d{18}$/.test(value) ? '' : '集团编号必须为18位数字';
}

function validateAccessoryGroupNoValue(value){
    value = (value || '').trim();
    if(!value){
        return '';
    }
    return /^\\d{18}-\\d+$/.test(value) ? '' : '附属资产集团编号必须为18位主编号-序号';
}

function confirmDeviceSave(form){
    const deviceType = (form.querySelector('[name="device_type"]') || {}).value || '主设备';
    const groupNo = ((form.querySelector('[name="group_no"]') || {}).value || '').trim();
    const message = deviceType === '配件' ? validateAccessoryGroupNoValue(groupNo) : validateAssetGroupNoValue(groupNo);
    if(message){
        alert(message);
        return false;
    }
    return confirm('确认保存吗？');
}

function openUploadChooser(dialogId, triggerEl){ const dialog = document.getElementById(dialogId); if(dialog){ dialog.classList.add('show'); } }
function closeUploadChooser(dialogId){ const dialog = document.getElementById(dialogId); if(dialog){ dialog.classList.remove('show'); } }
const uploadSelectionState = window.uploadSelectionState || (window.uploadSelectionState = {});
function getUploadInputIds(textEl, fallbackInputId){ return (textEl.dataset.inputs || fallbackInputId || '').split(',').map(item => item.trim()).filter(Boolean); }
function makeShortUploadName(file){
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const ms = String(now.getMilliseconds()).padStart(3, '0');
    const timePart = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}${ms}`;
    const originalName = file && file.name ? file.name : '';
    const extMatch = originalName.match(/[.]([A-Za-z0-9]+)$/);
    const ext = extMatch ? extMatch[1].toLowerCase() : 'jpg';
    return `${timePart}.${ext}`;
}
function cloneUploadFileWithShortName(file){
    if(!file){ return file; }
    try{
        if(file.__shortUploadName){ return file; }
        const newFile = new File([file], makeShortUploadName(file), { type: file.type || 'image/jpeg', lastModified: file.lastModified || Date.now() });
        Object.defineProperty(newFile, '__shortUploadName', { value: true });
        return newFile;
    }catch(e){
        return file;
    }
}
function syncUploadInputs(textId, activeInputId){
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || { files: [] };
    const inputIds = getUploadInputIds(textEl, activeInputId);
    if(typeof DataTransfer !== 'undefined'){
        const transfer = new DataTransfer();
        state.files.forEach(file => transfer.items.add(file));
        inputIds.forEach((id, index) => {
            const input = document.getElementById(id);
            if(!input){ return; }
            try{ input.files = (id === activeInputId || index === 0) ? transfer.files : new DataTransfer().files; }catch(e){}
        });
    }
}
function renderUploadSelection(textId, activeInputId){
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || { files: [] };
    if(state.files.length <= 0){
        textEl.textContent = '未选择图片';
        syncUploadInputs(textId, activeInputId);
        return;
    }
    textEl.innerHTML = '';
    const summary = document.createElement('div');
    summary.textContent = `已选择 ${state.files.length} 张（本次最多5张）`;
    textEl.appendChild(summary);
    const list = document.createElement('div');
    list.className = 'upload-preview-list';
    state.files.forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'upload-preview-item';
        const name = document.createElement('span');
        name.className = 'upload-preview-name';
        name.textContent = file.name || `图片${index + 1}`;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'upload-remove-btn';
        remove.setAttribute('aria-label', '删除该图片');
        remove.textContent = '×';
        remove.onclick = function(){ removeSelectedUploadFile(textId, index, activeInputId); };
        item.appendChild(name);
        item.appendChild(remove);
        list.appendChild(item);
    });
    textEl.appendChild(list);
    syncUploadInputs(textId, activeInputId);
}
function removeSelectedUploadFile(textId, index, activeInputId){
    const state = uploadSelectionState[textId];
    if(!state){ return; }
    state.files.splice(index, 1);
    renderUploadSelection(textId, activeInputId);
}
function updateSelectedFiles(inputId, textId, dialogId){
    const input = document.getElementById(inputId);
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || (uploadSelectionState[textId] = { files: [] });
    const incoming = input && input.files ? Array.from(input.files).map(cloneUploadFileWithShortName) : [];
    incoming.forEach(file => {
        if(state.files.length < 5){ state.files.push(file); }
    });
    if(input){ try{ input.value = ''; }catch(e){} }
    renderUploadSelection(textId, inputId);
    if(dialogId){ closeUploadChooser(dialogId); }
    if(incoming.length > 0 && state.files.length >= 5){
        const summary = textEl.querySelector('div');
        if(summary){ summary.textContent = `已选择 ${state.files.length} 张（已达本次最多5张）`; }
    }
}

</script>
</head>
<body>
<div class="wrap">
    <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px;">
            <h2 style="margin:0;">新增设备</h2>
            <a href="/"><button type="button" class="btn-return-secondary" style="width:auto;min-width:108px;">返回</button></a>
        </div>
        <div style="color:#666;margin-bottom:10px;">内部编号和集团编号至少填写一个。新增配件时，已自动带出主设备编号前缀。</div>
        {% if error %}<div class="err">{{ error }}</div>{% endif %}
        <form method="post" enctype="multipart/form-data">
            <input type="hidden" name="parent_asset_id" value="{{ form_data.parent_asset_id }}">
            <div class="grid">
                <div class="row">
                    <label>类型</label>
                    <select name="device_type" id="device_type">
                        <option value="主设备" {% if form_data.device_type == '主设备' %}selected{% endif %}>主设备</option>
                        <option value="配件" {% if form_data.device_type == '配件' %}selected{% endif %}>配件</option>
                    </select>
                </div>
                <div class="row"><label>集团编号（可空）</label><input type="text" name="group_no" value="{{ form_data.group_no }}"></div>
                <div class="row"><label>内部编号（可空）</label><input type="text" name="internal_no" value="{{ form_data.internal_no }}"></div>
                <div class="row"><label>名称</label><input type="text" name="name" value="{{ form_data.name }}"></div>
                <div class="row"><label>型号</label><input type="text" name="model" value="{{ form_data.model }}"></div>
                <div class="row"><label>责任人</label><input type="text" name="owner" value="{{ form_data.owner }}"></div>
                <div class="row"><label>位置</label><input type="text" name="location" value="{{ form_data.location }}"></div>
                <div class="row"><label>时间</label><input type="date" value="{{ form_data.asset_date }}" disabled class="readonly"></div>
                <div class="row">
                    <label>状态</label>
                    <select name="status">
                        <option value="">请选择</option>
                        {% for s in statuses %}
                        <option value="{{ s.dict_value }}" {% if form_data.status == s.dict_value %}selected{% endif %}>{{ s.dict_value }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="row">
                    <label>上传图片（最多5张）</label>
                    <div class="upload-actions">
                        <button type="button" class="btn-upload-primary" onclick=\"openUploadChooser('device-upload-choice-dialog', this)\">上传图片</button>
                    </div>
                    <div id="device-image-files-text" class="file-list" data-inputs="device-camera-files,device-file-files">未选择图片</div>
                    <div id="device-upload-choice-dialog" class="upload-dialog" onclick="if(event.target === this){closeUploadChooser('device-upload-choice-dialog');}">
                        <div class="upload-dialog-card">
                            <div class="upload-dialog-title">请选择上传方式</div>
                            <div class="upload-dialog-actions">
                                <label class="upload-choice-file upload-choice-camera">拍照
                                    <input type="file" id="device-camera-files" name="image_files" accept="image/*" capture="environment" multiple onchange="updateSelectedFiles('device-camera-files', 'device-image-files-text', 'device-upload-choice-dialog')">
                                </label>
                                <label class="upload-choice-file upload-choice-local">本地上传
                                    <input type="file" id="device-file-files" name="image_files" accept="image/*" multiple onchange="updateSelectedFiles('device-file-files', 'device-image-files-text', 'device-upload-choice-dialog')">
                                </label>
                                <button type="button" class="btn-cancel" onclick="closeUploadChooser('device-upload-choice-dialog')">取消</button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="row"><label>备注</label><textarea name="remark">{{ form_data.remark }}</textarea></div>
            <div style="margin-top:12px;"><button type="submit" class="btn-save-primary">保存</button></div>
        </form>
    </div>
</div>
</body>
</html>
"""

ASSET_DETAIL_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>主设备详情</title>
<style>
:root{--bg:#edf1f5;--card:#ffffff;--line:#d9e1ea;--text:#233243;--muted:#677383;--primary:#5f6f82;--primary-soft:#eef2f6;--green:#2f7d67;--orange:#b88347;--danger:#dc2626;}
*{box-sizing:border-box;}
body{font-family:Arial,sans-serif;margin:0;color:var(--text);background:linear-gradient(180deg,#edf1f5 0%,#f7f8fa 34%,#f1f4f8 100%);}
.wrap{max-width:1100px;margin:auto;padding:16px 14px 26px;}
.card{background:rgba(255,255,255,.94);border:1px solid rgba(210,218,227,.98);border-radius:22px;padding:18px 18px 16px;margin-bottom:16px;box-shadow:0 16px 40px rgba(27,39,53,.07);backdrop-filter:blur(10px);}
h2{margin:0 0 14px;font-size:26px;color:#263646;letter-spacing:.4px;}
a{color:#5a6675;}
.row{margin-bottom:12px;}
label{display:block;margin-bottom:6px;font-weight:600;color:#5e6977;}
input,select,textarea,button{width:100%;box-sizing:border-box;padding:10px 12px;font-size:16px;border-radius:12px;border:1px solid #cfd6df;transition:all .2s ease;}
input[type=text],input[type=date],input[type=password],select,textarea{background:#fbfdff;}
input[type=text]:focus,input[type=date]:focus,input[type=password]:focus,select:focus,textarea:focus{outline:none;border-color:#9ba8b8;box-shadow:0 0 0 4px rgba(95,111,130,.12);background:#fff;}
input[type=file]{background:#fff;}
input[type=checkbox]{accent-color:#5f6f82;width:16px;height:16px;}
textarea{min-height:90px;resize:vertical;}
button{background:linear-gradient(135deg,#718194 0%,#556274 100%);color:#fff;border:none;cursor:pointer;font-weight:700;box-shadow:0 8px 18px rgba(46,58,74,.14);}
button:hover{transform:translateY(-1px);box-shadow:0 12px 22px rgba(46,58,74,.16);}
button:disabled{cursor:not-allowed;transform:none;box-shadow:none;}
.btn-green{background:linear-gradient(135deg,#4f8d78 0%,#2f6b58 100%);}
.btn-gray{background:linear-gradient(135deg,#8b95a1 0%,#697380 100%);}.btn-return-secondary{background:linear-gradient(135deg,#8f2d4b 0%,#6f1d36 100%);box-shadow:0 10px 18px rgba(111,29,54,.20);}.btn-return-secondary:hover{background:linear-gradient(135deg,#9d3656 0%,#7c2140 100%);box-shadow:0 12px 22px rgba(111,29,54,.24);}
.btn-orange{background:linear-gradient(135deg,#c39a68 0%,#a9753a 100%);}
.btn-red{background:linear-gradient(135deg,#d06f72 0%,#b34f55 100%);}
.btn-disabled{background:#adb5bd !important;color:#fff !important;cursor:not-allowed;pointer-events:none;box-shadow:none !important;}
.readonly{background:#eef2f6;color:#677383;border-color:#d8e0e8;}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.table-wrap{overflow-x:auto;border:1px solid #d7dee7;border-radius:16px;background:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,.8),0 8px 22px rgba(27,39,53,.04);}
table{width:100%;border-collapse:separate;border-spacing:0;min-width:780px;}
th,td{padding:11px 10px;text-align:left;border:none;border-right:1px solid #e7eef7;border-bottom:1px solid #e7eef7;vertical-align:middle;font-size:15px;font-weight:400;color:#6f7b88;}th:last-child,td:last-child{border-right:none;}
thead th{background:linear-gradient(180deg,#fafbfd 0%,#eff2f6 100%);color:#5e6977;font-weight:600;white-space:nowrap;}
tbody tr:last-child td{border-bottom:none;}
tbody tr.result-row{transition:transform .16s ease,box-shadow .16s ease;}
tbody tr.result-row td:first-child{border-left:4px solid transparent;}
tbody tr.result-row:nth-child(4n+1) td{background:#fbfcfd;}
tbody tr.result-row:nth-child(4n+2) td{background:#f8fafb;}
tbody tr.result-row:nth-child(4n+3) td{background:#fcfbf8;}
tbody tr.result-row:nth-child(4n+4) td{background:#f9f8fb;}
tbody tr.result-row:hover td{background:#eff3f7;}
tbody tr.result-row:hover{transform:translateY(-1px);box-shadow:0 10px 22px rgba(42,56,74,.08);}
tbody tr.result-row-asset td:first-child{border-left-color:#8fa8c2;}
tbody tr.result-row-accessory td:first-child{border-left-color:#95b6a1;}
tbody tr.empty-row td{background:#fbfdff;color:#6f7b88;text-align:center;padding:20px 12px;}
.msg{color:#15803d;margin-bottom:12px;font-weight:700;background:#eef8f3;border:1px solid #cfe4d9;border-radius:12px;padding:10px 12px;}
.err{color:#dc2626;margin-bottom:12px;font-weight:700;background:#fbf1f2;border:1px solid #e7cfd3;border-radius:12px;padding:10px 12px;}
.image-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;padding:10px 12px;background:#fafbfd;border:1px solid #dde4ec;border-radius:12px;}
.upload-actions{display:flex;gap:8px;flex-wrap:wrap;}
.upload-actions button{width:auto;min-width:120px;}
.file-list{margin-top:8px;color:#66788a;font-size:14px;word-break:break-all;line-height:1.6;}
.upload-preview-list{display:flex;flex-direction:column;gap:6px;margin-top:8px;}.upload-preview-item{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 9px;border:1px solid #e1e7ef;border-radius:10px;background:#fbfdff;color:#4d5b6b;}.upload-preview-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}.upload-remove-btn{width:28px;min-width:28px;height:28px;padding:0;border-radius:999px;background:#dc2626;color:#fff;border:none;box-shadow:none;font-weight:900;line-height:1;display:inline-flex;align-items:center;justify-content:center;}
.upload-dialog{position:fixed;left:0;top:0;width:100vw;height:100dvh;background:transparent;display:none;z-index:9999;overflow:hidden;} .upload-dialog.show{display:block;} .upload-dialog-card{position:fixed;left:50%;top:56%;transform:translate(-50%,-50%);width:min(320px,calc(100vw - 24px));max-width:320px;background:#fff;border-radius:16px;padding:18px;box-shadow:0 20px 40px rgba(15,23,42,.22);z-index:10000;}
.upload-dialog-title{font-size:18px;font-weight:bold;margin-bottom:12px;text-align:center;color:#163047;}
.upload-dialog-actions{display:flex;flex-direction:column;gap:10px;}
.upload-dialog-actions button{width:100%;}
.upload-choice-file{position:relative;display:block;width:100%;box-sizing:border-box;padding:10px;font-size:16px;border-radius:12px;border:1px solid #cfd9e6;color:#fff;text-align:center;overflow:hidden;box-shadow:0 8px 18px rgba(46,58,74,.14);}
.upload-choice-camera{background:linear-gradient(135deg,#67c9ff 0%,#3388ff 100%);box-shadow:0 10px 18px rgba(54,132,255,.2);}
.upload-choice-local{background:linear-gradient(135deg,#54ddb1 0%,#159f76 100%);box-shadow:0 10px 18px rgba(21,159,118,.2);}
.upload-choice-file input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;}
.upload-dialog-actions .btn-cancel{background:linear-gradient(135deg,#c5ccd4 0%,#97a2af 100%);box-shadow:0 8px 16px rgba(99,116,135,.16);}
.title-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:nowrap;margin-bottom:14px;}
.title-row h2{margin:0;flex:0 0 auto;}
.title-actions{display:flex !important;flex-direction:row !important;align-items:center;justify-content:flex-end;gap:12px;flex-wrap:nowrap;margin-left:auto;flex:0 0 auto;width:auto !important;max-width:none !important;}
.title-actions > *{display:inline-flex;flex:0 0 auto;width:auto !important;max-width:none !important;}
.title-actions a{display:inline-flex;flex:0 0 auto;width:auto !important;text-decoration:none;}
.title-actions button{display:inline-flex !important;align-items:center;justify-content:center;width:auto !important;min-width:96px !important;padding:10px 14px !important;max-width:none !important;flex:0 0 auto;}
.detail-actions{display:flex;gap:14px;flex-wrap:nowrap;align-items:center;justify-content:space-between;margin-top:12px;}
.detail-actions button{width:auto;min-width:104px;padding:10px 14px;flex:0 0 auto;}
.link-row{margin-top:-2px;margin-bottom:12px;color:#607284;font-size:14px;}
.muted{color:var(--muted);font-size:14px;line-height:1.6;}.num-link{color:#4e5865;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px;font-weight:400;font-size:18px;}.num-link:hover{color:#3f4853;text-decoration:underline;}
.simple-modal{position:fixed;inset:0;background:rgba(15,23,42,.52);display:none;align-items:center;justify-content:center;z-index:10000;padding:18px;}
.simple-modal.show{display:flex;}
.simple-modal-card{width:100%;max-width:380px;background:#fff;border-radius:18px;padding:22px 20px;box-shadow:0 18px 50px rgba(15,23,42,.28);}
.simple-modal-title{font-size:22px;font-weight:800;color:#111827;text-align:center;margin-bottom:10px;}
.simple-modal-text{font-size:18px;font-weight:600;line-height:1.7;color:#1f2937;text-align:center;margin-bottom:14px;}
.simple-modal-input-wrap{display:none;margin-bottom:12px;}
.simple-modal-input-wrap.show{display:block;}
.simple-modal-input{width:100%;box-sizing:border-box;padding:12px 14px;font-size:18px;font-weight:700;border-radius:12px;border:2px solid #9ca3af;color:#111827;}
.simple-modal-input:focus{outline:none;border-color:#374151;box-shadow:0 0 0 3px rgba(55,65,81,.12);}
.simple-modal-error{min-height:24px;font-size:16px;font-weight:700;color:#b91c1c;text-align:center;margin-bottom:6px;}
.simple-modal-actions{display:flex;gap:12px;justify-content:center;margin-top:6px;}
.simple-modal-actions button{width:auto;min-width:118px;padding:11px 18px;font-size:17px;font-weight:700;border-radius:12px;border:none;}
.simple-modal-cancel{background:#6b7280;color:#fff;}
.simple-modal-ok{background:#b91c1c;color:#fff;}
@media (max-width:768px){.wrap{padding:14px 12px 20px;}.card{padding:16px;margin-bottom:14px;border-radius:14px;}.grid{grid-template-columns:1fr;gap:10px;}.row{margin-bottom:6px;}label{margin-bottom:4px;font-size:16px;}.image-row{margin-bottom:10px;padding:10px 12px;}.title-row{align-items:flex-start;gap:10px;margin-bottom:12px;flex-direction:column;}.title-actions{width:100% !important;margin-left:0;display:flex !important;justify-content:space-between;gap:14px;flex-wrap:nowrap;}.title-actions > *{width:auto !important;max-width:none;flex:0 0 auto;}.title-actions a{width:auto !important;}.title-actions button{min-width:104px !important;width:auto !important;max-width:none;padding:10px 14px !important;font-size:16px;}.detail-actions{gap:14px;margin-top:12px;justify-content:space-between;flex-wrap:nowrap;}.detail-actions button{min-width:104px;width:auto;max-width:none;padding:10px 14px;font-size:16px;}.switch-row{gap:8px;}.switch-row a{flex:1 1 160px;}h2{font-size:22px;}.muted{font-size:14px;line-height:1.6;}.file-list{margin-top:8px;font-size:14px;line-height:1.6;}input,select,textarea,button{padding:10px 12px;font-size:16px;border-radius:8px;}.upload-choice-file{padding:10px;font-size:16px;border-radius:8px;}.upload-dialog-card{border-radius:14px;padding:16px;width:min(320px,calc(100vw - 20px));top:58%;}.simple-modal{padding:16px;}.simple-modal-card{border-radius:16px;padding:18px 16px;}.simple-modal-title{font-size:20px;}.simple-modal-text{font-size:16px;}.simple-modal-input{padding:10px 12px;font-size:16px;border-radius:8px;}.simple-modal-actions button{min-width:108px;padding:10px 16px;font-size:16px;border-radius:10px;}}

.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3),
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5),
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){white-space:nowrap;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:16%;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:23.25%;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:12.75%;min-width:109px;}
@media (max-width:768px){
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.table-wrap table{width:max-content;table-layout:auto !important;}
.asset-search-table{min-width:1220px !important;}
.asset-location-table{min-width:1120px !important;}
.asset-accessory-table{min-width:980px !important;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:auto !important;min-width:192px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:auto !important;min-width:250px;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:auto !important;min-width:141px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){width:auto !important;min-width:220px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.table-wrap table .num-link{display:inline-block;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;max-width:none !important;word-break:normal !important;}
}
</style>
<script>
function validateAssetGroupNoValue(value){ value = (value || '').trim(); if(!value){ return ''; } return /^\\d{18}$/.test(value) ? '' : '集团编号必须为18位数字'; }
function confirmAssetSave(form){ const groupNo = ((form.querySelector('[name="group_no"]') || {}).value || '').trim(); const message = validateAssetGroupNoValue(groupNo); if(message){ alert(message); return false; } return confirm('确认保存吗？'); }
function beginEdit(formId, buttonId){ const form = document.getElementById(formId); if(!form){ return false; } const fields = form.querySelectorAll('.edit-field'); fields.forEach(el => { el.disabled = false; el.classList.remove('readonly'); }); const button = document.getElementById(buttonId); if(button){ button.textContent = '确认'; button.setAttribute('data-mode', 'save'); } return false; }
function handleEditOrSave(formId, buttonId){ const button = document.getElementById(buttonId); if(!button){ return false; } const mode = button.getAttribute('data-mode') || 'edit'; if(mode === 'save'){ const form = document.getElementById(formId); if(form){ if(form.requestSubmit){ form.requestSubmit(); } else { form.submit(); } } return false; } return beginEdit(formId, buttonId); }
function openUploadChooser(dialogId, triggerEl){ const dialog = document.getElementById(dialogId); if(dialog){ dialog.classList.add('show'); } }
function closeUploadChooser(dialogId){ const dialog = document.getElementById(dialogId); if(dialog){ dialog.classList.remove('show'); } }
const uploadSelectionState = window.uploadSelectionState || (window.uploadSelectionState = {});
function getUploadInputIds(textEl, fallbackInputId){ return (textEl.dataset.inputs || fallbackInputId || '').split(',').map(item => item.trim()).filter(Boolean); }
function makeShortUploadName(file){
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const ms = String(now.getMilliseconds()).padStart(3, '0');
    const timePart = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}${ms}`;
    const originalName = file && file.name ? file.name : '';
    const extMatch = originalName.match(/[.]([A-Za-z0-9]+)$/);
    const ext = extMatch ? extMatch[1].toLowerCase() : 'jpg';
    return `${timePart}.${ext}`;
}
function cloneUploadFileWithShortName(file){
    if(!file){ return file; }
    try{
        if(file.__shortUploadName){ return file; }
        const newFile = new File([file], makeShortUploadName(file), { type: file.type || 'image/jpeg', lastModified: file.lastModified || Date.now() });
        Object.defineProperty(newFile, '__shortUploadName', { value: true });
        return newFile;
    }catch(e){
        return file;
    }
}
function syncUploadInputs(textId, activeInputId){
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || { files: [] };
    const inputIds = getUploadInputIds(textEl, activeInputId);
    if(typeof DataTransfer !== 'undefined'){
        const transfer = new DataTransfer();
        state.files.forEach(file => transfer.items.add(file));
        inputIds.forEach((id, index) => {
            const input = document.getElementById(id);
            if(!input){ return; }
            try{ input.files = (id === activeInputId || index === 0) ? transfer.files : new DataTransfer().files; }catch(e){}
        });
    }
}
function renderUploadSelection(textId, activeInputId){
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || { files: [] };
    if(state.files.length <= 0){
        textEl.textContent = '未选择图片';
        syncUploadInputs(textId, activeInputId);
        return;
    }
    textEl.innerHTML = '';
    const summary = document.createElement('div');
    summary.textContent = `已选择 ${state.files.length} 张（本次最多5张）`;
    textEl.appendChild(summary);
    const list = document.createElement('div');
    list.className = 'upload-preview-list';
    state.files.forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'upload-preview-item';
        const name = document.createElement('span');
        name.className = 'upload-preview-name';
        name.textContent = file.name || `图片${index + 1}`;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'upload-remove-btn';
        remove.setAttribute('aria-label', '删除该图片');
        remove.textContent = '×';
        remove.onclick = function(){ removeSelectedUploadFile(textId, index, activeInputId); };
        item.appendChild(name);
        item.appendChild(remove);
        list.appendChild(item);
    });
    textEl.appendChild(list);
    syncUploadInputs(textId, activeInputId);
}
function removeSelectedUploadFile(textId, index, activeInputId){
    const state = uploadSelectionState[textId];
    if(!state){ return; }
    state.files.splice(index, 1);
    renderUploadSelection(textId, activeInputId);
}
function updateSelectedFiles(inputId, textId, dialogId){
    const input = document.getElementById(inputId);
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || (uploadSelectionState[textId] = { files: [] });
    const incoming = input && input.files ? Array.from(input.files).map(cloneUploadFileWithShortName) : [];
    incoming.forEach(file => {
        if(state.files.length < 5){ state.files.push(file); }
    });
    if(input){ try{ input.value = ''; }catch(e){} }
    renderUploadSelection(textId, inputId);
    if(dialogId){ closeUploadChooser(dialogId); }
    if(incoming.length > 0 && state.files.length >= 5){
        const summary = textEl.querySelector('div');
        if(summary){ summary.textContent = `已选择 ${state.files.length} 张（已达本次最多5张）`; }
    }
}


const deleteModalState = { onOk: null, onCancel: null };
function closeDeleteModal(){
    const modal = document.getElementById('delete-modal');
    if(modal){ modal.classList.remove('show'); }
    const inputWrap = document.getElementById('delete-modal-input-wrap');
    const input = document.getElementById('delete-modal-input');
    const error = document.getElementById('delete-modal-error');
    const cancelBtn = document.getElementById('delete-modal-cancel');
    const okBtn = document.getElementById('delete-modal-ok');
    if(inputWrap){ inputWrap.classList.remove('show'); }
    if(input){ input.value = ''; }
    if(error){ error.textContent = ''; }
    if(cancelBtn){ cancelBtn.style.display = 'inline-block'; }
    if(okBtn){ okBtn.textContent = '确定'; }
    deleteModalState.onOk = null;
    deleteModalState.onCancel = null;
}
function openDeleteModal(config){
    const modal = document.getElementById('delete-modal');
    const title = document.getElementById('delete-modal-title');
    const text = document.getElementById('delete-modal-text');
    const inputWrap = document.getElementById('delete-modal-input-wrap');
    const input = document.getElementById('delete-modal-input');
    const error = document.getElementById('delete-modal-error');
    const cancelBtn = document.getElementById('delete-modal-cancel');
    const okBtn = document.getElementById('delete-modal-ok');
    if(!modal || !title || !text || !inputWrap || !input || !error || !cancelBtn || !okBtn){ return; }
    title.textContent = config.title || '提示';
    text.textContent = config.text || '';
    error.textContent = '';
    input.value = '';
    if(config.showPin){
        inputWrap.classList.add('show');
        window.setTimeout(() => { try { input.focus(); } catch(e){} }, 30);
    }else{
        inputWrap.classList.remove('show');
    }
    cancelBtn.style.display = config.hideCancel ? 'none' : 'inline-block';
    okBtn.textContent = config.okText || '确定';
    deleteModalState.onOk = typeof config.onOk === 'function' ? config.onOk : null;
    deleteModalState.onCancel = typeof config.onCancel === 'function' ? config.onCancel : null;
    modal.classList.add('show');
}
function handleDeleteModalCancel(){
    const fn = deleteModalState.onCancel;
    closeDeleteModal();
    if(fn){ fn(); }
}
function handleDeleteModalOk(){
    if(!deleteModalState.onOk){ closeDeleteModal(); return; }
    const result = deleteModalState.onOk();
    if(result !== false){ closeDeleteModal(); }
}
function showDeleteNotice(message){
    openDeleteModal({ title:'提示', text: message || '请确认操作', hideCancel: true, onOk: function(){ return true; } });
}
function showDeletePinDialog(onSubmit){
    openDeleteModal({
        title:'Pin码确认',
        text:'请输入4位Pin码',
        showPin:true,
        onOk:function(){
            const input = document.getElementById('delete-modal-input');
            const error = document.getElementById('delete-modal-error');
            const pin = (input && input.value ? input.value : '').trim();
            if(!pin){ if(error){ error.textContent = '请输入4位Pin码'; } return false; }
            if(pin !== '0819'){ if(error){ error.textContent = 'Pin码错误'; } return false; }
            if(typeof onSubmit === 'function'){ onSubmit(pin); }
            return true;
        }
    });
}
function openDeleteFlow(message, onPinConfirmed){
    openDeleteModal({
        title:'删除确认',
        text: message || '确认删除吗？',
        onOk:function(){
            showDeletePinDialog(onPinConfirmed);
            return false;
        }
    });
    return false;
}

function requestDeleteWithPin(formId, message){
    return openDeleteFlow(message || '确认删除吗？', function(pin){
        const form = document.getElementById(formId);
        if(!form){ return; }
        const pinInput = form.querySelector('input[name="delete_pin"]');
        if(pinInput){ pinInput.value = pin; }
        form.submit();
    });
}
function requestInventory(formId, message){
    openDeleteModal({
        title:'盘点确认',
        text: message || '确认更新盘点时间吗？',
        onOk:function(){
            const form = document.getElementById(formId);
            if(form){ form.submit(); }
            return true;
        }
    });
    return false;
}
</script>
</head>
<body>
<div class="wrap">
    <div class="card">
        <div class="title-row">
            <h2>主设备详情</h2>
            <div class="title-actions">
                <a href="/"><button type="button" class="btn-return-secondary" style="width:auto;min-width:96px;">返回</button></a>
                {% if can_manage %}<button type="button" style="width:auto;min-width:96px;" onclick="requestInventory('asset-inventory-form', '确认将主设备盘点时间更新为今天吗？')">盘点</button>{% else %}<button type="button" class="btn-disabled" style="width:auto;min-width:96px;" disabled>盘点</button>{% endif %}
            </div>
        </div>
        {% if message %}<div class="msg">{{ message }}</div>{% endif %}
        {% if error %}<div class="err">{{ error }}</div>{% endif %}
        <form id="asset-form" method="post" enctype="multipart/form-data" onsubmit="return confirmAssetSave(this)">
            <input type="hidden" name="action" value="save_asset">
            <div class="grid">
                <div class="row"><label>集团编号</label><input class="edit-field readonly" disabled type="text" name="group_no" value="{{ asset.group_no or '' }}"></div>
                <div class="row"><label>内部编号</label><input class="edit-field readonly" disabled type="text" name="internal_no" value="{{ asset.internal_no or '' }}"></div>
                <div class="row"><label>名称</label><input class="edit-field readonly" disabled type="text" name="name" value="{{ asset.name }}"></div>
                <div class="row"><label>型号</label><input class="edit-field readonly" disabled type="text" name="model" value="{{ asset.model or '' }}"></div>
                <div class="row"><label>责任人</label><input class="edit-field readonly" disabled type="text" name="owner" value="{{ asset.owner or '' }}"></div>
                <div class="row"><label>位置</label><input class="edit-field readonly" disabled type="text" name="location" value="{{ asset.location or '' }}"></div>
                <div class="row"><label>时间</label><input class="readonly" disabled type="date" value="{{ asset.asset_date.isoformat() if asset.asset_date else today }}"></div>
                <div class="row"><label>状态</label><select class="edit-field readonly" disabled name="status"><option value="">请选择</option>{% for s in statuses %}<option value="{{ s.dict_value }}" {% if (asset.status or '') == s.dict_value or ((asset.status or '') == '维修' and s.dict_value == '计量') %}selected{% endif %}>{{ s.dict_value }}</option>{% endfor %}</select></div>
                <div class="row">
                    <label>上传图片（最多5张）</label>
                    <div class="upload-actions"><button type="button" class="edit-field readonly" disabled onclick=\"openUploadChooser('asset-upload-choice-dialog', this)\">上传图片</button></div>
                    <div id="asset-image-files-text" class="file-list" data-inputs="asset-camera-files,asset-file-files">未选择图片</div>
                    <div id="asset-upload-choice-dialog" class="upload-dialog" onclick="if(event.target === this){closeUploadChooser('asset-upload-choice-dialog');}">
                        <div class="upload-dialog-card"><div class="upload-dialog-title">请选择上传方式</div><div class="upload-dialog-actions">
                            <label class="upload-choice-file upload-choice-camera">拍照<input class="edit-field readonly" disabled type="file" id="asset-camera-files" name="image_files" accept="image/*" capture="environment" multiple onchange="updateSelectedFiles('asset-camera-files', 'asset-image-files-text', 'asset-upload-choice-dialog')"></label>
                            <label class="upload-choice-file upload-choice-local">本地上传<input class="edit-field readonly" disabled type="file" id="asset-file-files" name="image_files" accept="image/*" multiple onchange="updateSelectedFiles('asset-file-files', 'asset-image-files-text', 'asset-upload-choice-dialog')"></label>
                            <button type="button" class="btn-cancel" onclick="closeUploadChooser('asset-upload-choice-dialog')">取消</button>
                        </div></div>
                    </div>
                </div>
            </div>
            <div class="row">
                <label>当前图片</label>
                {% if images %}
                    {% for img in images %}
                    <div class="image-row"><a href="/uploads/{{ img.image_path }}" target="_blank">图片{{ loop.index }}</a><label style="display:flex;align-items:center;gap:6px;font-weight:normal;"><input class="edit-field readonly" disabled type="checkbox" name="delete_asset_image_ids" value="{{ img.id }}"> 删除</label></div>
                    {% endfor %}
                    <div style="color:#666;font-size:13px;">先点击“修改”，勾选要删除的图片，再点击“确认”。</div>
                {% else %}<div>暂无图片</div>{% endif %}
            </div>
            <div class="row"><label>备注</label><textarea class="edit-field readonly" disabled name="remark">{{ asset.remark or '' }}</textarea></div>
            <div class="detail-actions">
                {% if can_manage %}
                <button type="button" id="asset-form-edit" data-mode="edit" onclick="handleEditOrSave('asset-form', 'asset-form-edit')">修改</button>
                <button type="button" class="btn-red" onclick="requestDeleteWithPin('asset-delete-form', '确认删除该主设备及其所有配件？')">删除</button>
                {% else %}
                <button type="button" class="btn-disabled" disabled>修改</button>
                <button type="button" class="btn-disabled" disabled>删除</button>
                {% endif %}
            </div>
        </form>
        <form id="asset-delete-form" method="post" action="/asset/{{ asset.id }}/delete" style="display:none;"><input type="hidden" name="delete_pin" value=""></form>
        <form id="asset-inventory-form" method="post" action="/asset/{{ asset.id }}" style="display:none;"><input type="hidden" name="action" value="inventory_asset"></form>
    </div>

    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
            <h2 style="margin:0;">关联配件（共 {{ accessories|length }} 个）</h2>
            {% if can_manage %}<a href="/device/new?type=配件&parent_asset_id={{ asset.id }}"><button type="button" class="btn-green" style="width:auto;">+</button></a>{% else %}<button type="button" class="btn-disabled" style="width:auto;" disabled>+</button>{% endif %}
        </div>
        <div class="table-wrap" style="margin-top:12px;">
            <table class="asset-accessory-table">
                <thead><tr><th>附属资产集团编号</th><th>附属资产内部编号</th><th>名称</th><th>型号</th><th>状态</th><th>责任人</th></tr></thead>
                <tbody>
                    {% for item in accessories %}
                    <tr class="result-row result-row-accessory"><td><a class="num-link" href="/accessory/{{ item.id }}">{{ item.sub_group_no or '' }}</a></td><td><a class="num-link" href="/accessory/{{ item.id }}">{{ item.sub_internal_no or '' }}</a></td><td>{{ item.name }}</td><td>{{ item.model or '' }}</td><td>{{ item.status or '' }}</td><td>{{ item.owner or '' }}</td></tr>
                    {% endfor %}
                    {% if not accessories %}<tr class="empty-row"><td colspan="6">暂无配件</td></tr>{% endif %}
                </tbody>
            </table>
        </div>
    </div>

<div id="delete-modal" class="simple-modal" onclick="if(event.target===this){closeDeleteModal();}">
    <div class="simple-modal-card">
        <div id="delete-modal-title" class="simple-modal-title">删除确认</div>
        <div id="delete-modal-text" class="simple-modal-text">确认删除吗？</div>
        <div id="delete-modal-input-wrap" class="simple-modal-input-wrap">
            <input id="delete-modal-input" class="simple-modal-input" type="password" inputmode="numeric" maxlength="4" placeholder="请输入4位Pin码">
        </div>
        <div id="delete-modal-error" class="simple-modal-error"></div>
        <div class="simple-modal-actions">
            <button type="button" id="delete-modal-cancel" class="simple-modal-cancel" onclick="handleDeleteModalCancel();">取消</button>
            <button type="button" id="delete-modal-ok" class="simple-modal-ok" onclick="handleDeleteModalOk();">确定</button>
        </div>
    </div>
</div>

</div>
</body>
</html>
"""

ACCESSORY_DETAIL_HTML = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>配件详情</title>
<style>
:root{--bg:#edf1f5;--card:#ffffff;--line:#d9e1ea;--text:#233243;--muted:#677383;--primary:#5f6f82;--primary-soft:#eef2f6;--green:#2f7d67;--orange:#b88347;--danger:#dc2626;}
*{box-sizing:border-box;}
body{font-family:Arial,sans-serif;margin:0;color:var(--text);background:linear-gradient(180deg,#edf1f5 0%,#f7f8fa 34%,#f1f4f8 100%);}
.wrap{max-width:950px;margin:auto;padding:16px 14px 26px;}
.card{background:rgba(255,255,255,.94);border:1px solid rgba(210,218,227,.98);border-radius:22px;padding:18px 18px 16px;margin-bottom:16px;box-shadow:0 16px 40px rgba(27,39,53,.07);backdrop-filter:blur(10px);}
h2{margin:0 0 14px;font-size:26px;color:#263646;letter-spacing:.4px;}
a{color:#5a6675;}
.row{margin-bottom:12px;}
label{display:block;margin-bottom:6px;font-weight:600;color:#5e6977;}
input,select,textarea,button{width:100%;box-sizing:border-box;padding:10px 12px;font-size:16px;border-radius:12px;border:1px solid #cfd6df;transition:all .2s ease;}
input[type=text],input[type=date],input[type=password],select,textarea{background:#fbfdff;}
input[type=text]:focus,input[type=date]:focus,input[type=password]:focus,select:focus,textarea:focus{outline:none;border-color:#9ba8b8;box-shadow:0 0 0 4px rgba(95,111,130,.12);background:#fff;}
input[type=file]{background:#fff;}
input[type=checkbox]{accent-color:#5f6f82;width:16px;height:16px;}
textarea{min-height:90px;resize:vertical;}
button{background:linear-gradient(135deg,#718194 0%,#556274 100%);color:#fff;border:none;cursor:pointer;font-weight:700;box-shadow:0 8px 18px rgba(46,58,74,.14);}
button:hover{transform:translateY(-1px);box-shadow:0 12px 22px rgba(46,58,74,.16);}
button:disabled{cursor:not-allowed;transform:none;box-shadow:none;}
.btn-green{background:linear-gradient(135deg,#4f8d78 0%,#2f6b58 100%);}
.btn-gray{background:linear-gradient(135deg,#8b95a1 0%,#697380 100%);}.btn-return-secondary{background:linear-gradient(135deg,#8f2d4b 0%,#6f1d36 100%);box-shadow:0 10px 18px rgba(111,29,54,.20);}.btn-return-secondary:hover{background:linear-gradient(135deg,#9d3656 0%,#7c2140 100%);box-shadow:0 12px 22px rgba(111,29,54,.24);}
.btn-orange{background:linear-gradient(135deg,#c39a68 0%,#a9753a 100%);}
.btn-red{background:linear-gradient(135deg,#d06f72 0%,#b34f55 100%);}
.btn-disabled{background:#adb5bd !important;color:#fff !important;cursor:not-allowed;pointer-events:none;box-shadow:none !important;}
.readonly{background:#eef2f6;color:#677383;border-color:#d8e0e8;}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.table-wrap{overflow-x:auto;border:1px solid #d7dee7;border-radius:16px;background:#fff;box-shadow:inset 0 1px 0 rgba(255,255,255,.8),0 8px 22px rgba(27,39,53,.04);}
table{width:100%;border-collapse:separate;border-spacing:0;min-width:780px;}
th,td{padding:11px 10px;text-align:left;border:none;border-right:1px solid #e7eef7;border-bottom:1px solid #e7eef7;vertical-align:middle;font-size:15px;font-weight:400;color:#6f7b88;}th:last-child,td:last-child{border-right:none;}
thead th{background:linear-gradient(180deg,#fafbfd 0%,#eff2f6 100%);color:#5e6977;font-weight:600;white-space:nowrap;}
tbody tr:last-child td{border-bottom:none;}
tbody tr:nth-child(odd) td{background:#fbfcfd;}
tbody tr:nth-child(even) td{background:#f8fafb;}
tbody tr:hover td{background:#eff3f7;}
.msg{color:#15803d;margin-bottom:12px;font-weight:700;background:#eef8f3;border:1px solid #cfe4d9;border-radius:12px;padding:10px 12px;}
.err{color:#dc2626;margin-bottom:12px;font-weight:700;background:#fbf1f2;border:1px solid #e7cfd3;border-radius:12px;padding:10px 12px;}
.image-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px;padding:10px 12px;background:#fafbfd;border:1px solid #dde4ec;border-radius:12px;}
.upload-actions{display:flex;gap:8px;flex-wrap:wrap;}
.upload-actions button{width:auto;min-width:120px;}
.file-list{margin-top:8px;color:#66788a;font-size:14px;word-break:break-all;line-height:1.6;}
.upload-preview-list{display:flex;flex-direction:column;gap:6px;margin-top:8px;}.upload-preview-item{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 9px;border:1px solid #e1e7ef;border-radius:10px;background:#fbfdff;color:#4d5b6b;}.upload-preview-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}.upload-remove-btn{width:28px;min-width:28px;height:28px;padding:0;border-radius:999px;background:#dc2626;color:#fff;border:none;box-shadow:none;font-weight:900;line-height:1;display:inline-flex;align-items:center;justify-content:center;}
.upload-dialog{position:fixed;left:0;top:0;width:100vw;height:100dvh;background:transparent;display:none;z-index:9999;overflow:hidden;} .upload-dialog.show{display:block;} .upload-dialog-card{position:fixed;left:50%;top:56%;transform:translate(-50%,-50%);width:min(320px,calc(100vw - 24px));max-width:320px;background:#fff;border-radius:16px;padding:18px;box-shadow:0 20px 40px rgba(15,23,42,.22);z-index:10000;}
.upload-dialog-title{font-size:18px;font-weight:bold;margin-bottom:12px;text-align:center;color:#163047;}
.upload-dialog-actions{display:flex;flex-direction:column;gap:10px;}
.upload-dialog-actions button{width:100%;}
.upload-choice-file{position:relative;display:block;width:100%;box-sizing:border-box;padding:10px;font-size:16px;border-radius:12px;border:1px solid #cfd9e6;color:#fff;text-align:center;overflow:hidden;box-shadow:0 8px 18px rgba(46,58,74,.14);}
.upload-choice-camera{background:linear-gradient(135deg,#67c9ff 0%,#3388ff 100%);box-shadow:0 10px 18px rgba(54,132,255,.2);}
.upload-choice-local{background:linear-gradient(135deg,#54ddb1 0%,#159f76 100%);box-shadow:0 10px 18px rgba(21,159,118,.2);}
.upload-choice-file input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer;}
.upload-dialog-actions .btn-cancel{background:linear-gradient(135deg,#c5ccd4 0%,#97a2af 100%);box-shadow:0 8px 16px rgba(99,116,135,.16);}
.title-row{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:nowrap;margin-bottom:14px;}
.title-row h2{margin:0;flex:0 0 auto;}
.title-actions{display:flex !important;flex-direction:row !important;align-items:center;justify-content:flex-end;gap:12px;flex-wrap:nowrap;margin-left:auto;flex:0 0 auto;width:auto !important;max-width:none !important;}
.title-actions > *{display:inline-flex;flex:0 0 auto;width:auto !important;max-width:none !important;}
.title-actions a{display:inline-flex;flex:0 0 auto;width:auto !important;text-decoration:none;}
.title-actions button{display:inline-flex !important;align-items:center;justify-content:center;width:auto !important;min-width:96px !important;padding:10px 14px !important;max-width:none !important;flex:0 0 auto;}
.detail-actions{display:flex;gap:82px;flex-wrap:wrap;align-items:center;margin-top:12px;}
.detail-actions button{width:auto;min-width:120px;}
.link-row{margin-top:-2px;margin-bottom:12px;color:#607284;font-size:14px;}
.muted{color:var(--muted);font-size:14px;line-height:1.6;}.num-link{color:#4e5865;text-decoration:underline;text-decoration-thickness:1px;text-underline-offset:2px;font-weight:400;font-size:18px;}.num-link:hover{color:#3f4853;text-decoration:underline;}
.simple-modal{position:fixed;inset:0;background:rgba(15,23,42,.52);display:none;align-items:center;justify-content:center;z-index:10000;padding:18px;}
.simple-modal.show{display:flex;}
.simple-modal-card{width:100%;max-width:380px;background:#fff;border-radius:18px;padding:22px 20px;box-shadow:0 18px 50px rgba(15,23,42,.28);}
.simple-modal-title{font-size:22px;font-weight:800;color:#111827;text-align:center;margin-bottom:10px;}
.simple-modal-text{font-size:18px;font-weight:600;line-height:1.7;color:#1f2937;text-align:center;margin-bottom:14px;}
.simple-modal-input-wrap{display:none;margin-bottom:12px;}
.simple-modal-input-wrap.show{display:block;}
.simple-modal-input{width:100%;box-sizing:border-box;padding:12px 14px;font-size:18px;font-weight:700;border-radius:12px;border:2px solid #9ca3af;color:#111827;}
.simple-modal-input:focus{outline:none;border-color:#374151;box-shadow:0 0 0 3px rgba(55,65,81,.12);}
.simple-modal-error{min-height:24px;font-size:16px;font-weight:700;color:#b91c1c;text-align:center;margin-bottom:6px;}
.simple-modal-actions{display:flex;gap:12px;justify-content:center;margin-top:6px;}
.simple-modal-actions button{width:auto;min-width:118px;padding:11px 18px;font-size:17px;font-weight:700;border-radius:12px;border:none;}
.simple-modal-cancel{background:#6b7280;color:#fff;}
.simple-modal-ok{background:#b91c1c;color:#fff;}
@media (max-width:768px){.wrap{padding:14px 12px 20px;}.card{padding:16px;margin-bottom:14px;border-radius:14px;}.grid{grid-template-columns:1fr;}h2{font-size:22px;}.switch-row{gap:8px;}.switch-row a{flex:1 1 160px;}input,select,textarea,button{padding:10px;font-size:16px;border-radius:8px;}.upload-choice-file{padding:10px;font-size:16px;border-radius:8px;}.upload-dialog-card{border-radius:14px;padding:16px;width:min(320px,calc(100vw - 20px));top:58%;}.simple-modal{padding:16px;}.simple-modal-card{border-radius:16px;padding:18px 16px;}.simple-modal-title{font-size:20px;}.simple-modal-text{font-size:16px;}.simple-modal-input{padding:10px 12px;font-size:16px;border-radius:8px;}.simple-modal-actions button{min-width:108px;padding:10px 16px;font-size:16px;border-radius:10px;}}
@media (max-width:768px){.title-row{align-items:flex-start;gap:10px;margin-bottom:12px;flex-direction:column;}.title-actions{width:100% !important;margin-left:0;display:flex !important;justify-content:space-between;gap:14px;flex-wrap:nowrap;}.title-actions > *{width:auto !important;max-width:none;flex:0 0 auto;}.title-actions a{width:auto !important;}.title-actions button{min-width:104px !important;width:auto !important;max-width:none;padding:10px 14px !important;font-size:16px;}}

.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3),
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5),
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){white-space:nowrap;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:16%;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:23.25%;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:12.75%;min-width:109px;}
@media (max-width:768px){
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;}
.table-wrap table{width:max-content;table-layout:auto !important;}
.asset-search-table{min-width:1220px !important;}
.asset-location-table{min-width:1120px !important;}
.asset-accessory-table{min-width:980px !important;}
.asset-search-table th:nth-child(2),.asset-search-table td:nth-child(2),
.asset-search-table th:nth-child(3),.asset-search-table td:nth-child(3){width:auto !important;min-width:192px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-search-table th:nth-child(4),.asset-search-table td:nth-child(4){width:auto !important;min-width:250px;}
.asset-search-table th:nth-child(5),.asset-search-table td:nth-child(5){width:auto !important;min-width:141px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.asset-location-table th:nth-child(1),.asset-location-table td:nth-child(1),
.asset-location-table th:nth-child(2),.asset-location-table td:nth-child(2),
.asset-accessory-table th:nth-child(1),.asset-accessory-table td:nth-child(1),
.asset-accessory-table th:nth-child(2),.asset-accessory-table td:nth-child(2){width:auto !important;min-width:220px;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;}
.table-wrap table .num-link{display:inline-block;white-space:nowrap !important;overflow:visible !important;text-overflow:clip !important;max-width:none !important;word-break:normal !important;}
}
</style>
<script>
function enableEdit(formId){ const form = document.getElementById(formId); const fields = form.querySelectorAll('.edit-field'); fields.forEach(el => { el.disabled = false; el.classList.remove('readonly'); }); document.getElementById(formId + '-save').style.display = 'inline-block'; document.getElementById(formId + '-edit').style.display = 'none'; }
function openUploadChooser(dialogId, triggerEl){ const dialog = document.getElementById(dialogId); if(dialog){ dialog.classList.add('show'); } }
function closeUploadChooser(dialogId){ const dialog = document.getElementById(dialogId); if(dialog){ dialog.classList.remove('show'); } }
const uploadSelectionState = window.uploadSelectionState || (window.uploadSelectionState = {});
function getUploadInputIds(textEl, fallbackInputId){ return (textEl.dataset.inputs || fallbackInputId || '').split(',').map(item => item.trim()).filter(Boolean); }
function makeShortUploadName(file){
    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const ms = String(now.getMilliseconds()).padStart(3, '0');
    const timePart = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}${ms}`;
    const originalName = file && file.name ? file.name : '';
    const extMatch = originalName.match(/[.]([A-Za-z0-9]+)$/);
    const ext = extMatch ? extMatch[1].toLowerCase() : 'jpg';
    return `${timePart}.${ext}`;
}
function cloneUploadFileWithShortName(file){
    if(!file){ return file; }
    try{
        if(file.__shortUploadName){ return file; }
        const newFile = new File([file], makeShortUploadName(file), { type: file.type || 'image/jpeg', lastModified: file.lastModified || Date.now() });
        Object.defineProperty(newFile, '__shortUploadName', { value: true });
        return newFile;
    }catch(e){
        return file;
    }
}
function syncUploadInputs(textId, activeInputId){
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || { files: [] };
    const inputIds = getUploadInputIds(textEl, activeInputId);
    if(typeof DataTransfer !== 'undefined'){
        const transfer = new DataTransfer();
        state.files.forEach(file => transfer.items.add(file));
        inputIds.forEach((id, index) => {
            const input = document.getElementById(id);
            if(!input){ return; }
            try{ input.files = (id === activeInputId || index === 0) ? transfer.files : new DataTransfer().files; }catch(e){}
        });
    }
}
function renderUploadSelection(textId, activeInputId){
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || { files: [] };
    if(state.files.length <= 0){
        textEl.textContent = '未选择图片';
        syncUploadInputs(textId, activeInputId);
        return;
    }
    textEl.innerHTML = '';
    const summary = document.createElement('div');
    summary.textContent = `已选择 ${state.files.length} 张（本次最多5张）`;
    textEl.appendChild(summary);
    const list = document.createElement('div');
    list.className = 'upload-preview-list';
    state.files.forEach((file, index) => {
        const item = document.createElement('div');
        item.className = 'upload-preview-item';
        const name = document.createElement('span');
        name.className = 'upload-preview-name';
        name.textContent = file.name || `图片${index + 1}`;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'upload-remove-btn';
        remove.setAttribute('aria-label', '删除该图片');
        remove.textContent = '×';
        remove.onclick = function(){ removeSelectedUploadFile(textId, index, activeInputId); };
        item.appendChild(name);
        item.appendChild(remove);
        list.appendChild(item);
    });
    textEl.appendChild(list);
    syncUploadInputs(textId, activeInputId);
}
function removeSelectedUploadFile(textId, index, activeInputId){
    const state = uploadSelectionState[textId];
    if(!state){ return; }
    state.files.splice(index, 1);
    renderUploadSelection(textId, activeInputId);
}
function updateSelectedFiles(inputId, textId, dialogId){
    const input = document.getElementById(inputId);
    const textEl = document.getElementById(textId);
    if(!textEl){ return; }
    const state = uploadSelectionState[textId] || (uploadSelectionState[textId] = { files: [] });
    const incoming = input && input.files ? Array.from(input.files).map(cloneUploadFileWithShortName) : [];
    incoming.forEach(file => {
        if(state.files.length < 5){ state.files.push(file); }
    });
    if(input){ try{ input.value = ''; }catch(e){} }
    renderUploadSelection(textId, inputId);
    if(dialogId){ closeUploadChooser(dialogId); }
    if(incoming.length > 0 && state.files.length >= 5){
        const summary = textEl.querySelector('div');
        if(summary){ summary.textContent = `已选择 ${state.files.length} 张（已达本次最多5张）`; }
    }
}

function confirmAccessorySave(){ return confirm('确认保存吗？'); }
function beginEdit(formId, buttonId){ const form = document.getElementById(formId); if(!form){ return false; } const fields = form.querySelectorAll('.edit-field'); fields.forEach(el => { el.disabled = false; el.classList.remove('readonly'); }); const button = document.getElementById(buttonId); if(button){ button.textContent = '确认'; button.setAttribute('data-mode', 'save'); } return false; }
function handleEditOrSave(formId, buttonId){ const button = document.getElementById(buttonId); if(!button){ return false; } const mode = button.getAttribute('data-mode') || 'edit'; if(mode === 'save'){ const form = document.getElementById(formId); if(form){ if(form.requestSubmit){ form.requestSubmit(); } else { form.submit(); } } return false; } return beginEdit(formId, buttonId); }

const deleteModalState = { onOk: null, onCancel: null };
function closeDeleteModal(){
    const modal = document.getElementById('delete-modal');
    if(modal){ modal.classList.remove('show'); }
    const inputWrap = document.getElementById('delete-modal-input-wrap');
    const input = document.getElementById('delete-modal-input');
    const error = document.getElementById('delete-modal-error');
    const cancelBtn = document.getElementById('delete-modal-cancel');
    const okBtn = document.getElementById('delete-modal-ok');
    if(inputWrap){ inputWrap.classList.remove('show'); }
    if(input){ input.value = ''; }
    if(error){ error.textContent = ''; }
    if(cancelBtn){ cancelBtn.style.display = 'inline-block'; }
    if(okBtn){ okBtn.textContent = '确定'; }
    deleteModalState.onOk = null;
    deleteModalState.onCancel = null;
}
function openDeleteModal(config){
    const modal = document.getElementById('delete-modal');
    const title = document.getElementById('delete-modal-title');
    const text = document.getElementById('delete-modal-text');
    const inputWrap = document.getElementById('delete-modal-input-wrap');
    const input = document.getElementById('delete-modal-input');
    const error = document.getElementById('delete-modal-error');
    const cancelBtn = document.getElementById('delete-modal-cancel');
    const okBtn = document.getElementById('delete-modal-ok');
    if(!modal || !title || !text || !inputWrap || !input || !error || !cancelBtn || !okBtn){ return; }
    title.textContent = config.title || '提示';
    text.textContent = config.text || '';
    error.textContent = '';
    input.value = '';
    if(config.showPin){
        inputWrap.classList.add('show');
        window.setTimeout(() => { try { input.focus(); } catch(e){} }, 30);
    }else{
        inputWrap.classList.remove('show');
    }
    cancelBtn.style.display = config.hideCancel ? 'none' : 'inline-block';
    okBtn.textContent = config.okText || '确定';
    deleteModalState.onOk = typeof config.onOk === 'function' ? config.onOk : null;
    deleteModalState.onCancel = typeof config.onCancel === 'function' ? config.onCancel : null;
    modal.classList.add('show');
}
function handleDeleteModalCancel(){
    const fn = deleteModalState.onCancel;
    closeDeleteModal();
    if(fn){ fn(); }
}
function handleDeleteModalOk(){
    if(!deleteModalState.onOk){ closeDeleteModal(); return; }
    const result = deleteModalState.onOk();
    if(result !== false){ closeDeleteModal(); }
}
function showDeleteNotice(message){
    openDeleteModal({ title:'提示', text: message || '请确认操作', hideCancel: true, onOk: function(){ return true; } });
}
function showDeletePinDialog(onSubmit){
    openDeleteModal({
        title:'Pin码确认',
        text:'请输入4位Pin码',
        showPin:true,
        onOk:function(){
            const input = document.getElementById('delete-modal-input');
            const error = document.getElementById('delete-modal-error');
            const pin = (input && input.value ? input.value : '').trim();
            if(!pin){ if(error){ error.textContent = '请输入4位Pin码'; } return false; }
            if(pin !== '0819'){ if(error){ error.textContent = 'Pin码错误'; } return false; }
            if(typeof onSubmit === 'function'){ onSubmit(pin); }
            return true;
        }
    });
}
function openDeleteFlow(message, onPinConfirmed){
    openDeleteModal({
        title:'删除确认',
        text: message || '确认删除吗？',
        onOk:function(){
            showDeletePinDialog(onPinConfirmed);
            return false;
        }
    });
    return false;
}

function requestDeleteWithPin(formId, message){
    return openDeleteFlow(message || '确认删除吗？', function(pin){
        const form = document.getElementById(formId);
        if(!form){ return; }
        const pinInput = form.querySelector('input[name="delete_pin"]');
        if(pinInput){ pinInput.value = pin; }
        form.submit();
    });
}
function requestInventory(formId, message){
    openDeleteModal({
        title:'盘点确认',
        text: message || '确认更新盘点时间吗？',
        onOk:function(){
            const form = document.getElementById(formId);
            if(form){ form.submit(); }
            return true;
        }
    });
    return false;
}
</script>
</head>
<body>
<div class="wrap">
    <div class="card">
        <div class="title-row">
            <h2>配件详情</h2>
            <div class="title-actions">
                <a href="{{ back_url }}"><button type="button" class="btn-return-secondary" style="width:auto;min-width:96px;">返回</button></a>
                {% if can_manage %}<button type="button" onclick="requestInventory('accessory-inventory-form', '确认将配件盘点时间更新为今天吗？')" style="width:auto;min-width:96px;">盘点</button>{% else %}<button type="button" class="btn-disabled" style="width:auto;min-width:96px;" disabled>盘点</button>{% endif %}
            </div>
        </div>
        {% if message %}<div class="msg">{{ message }}</div>{% endif %}
        {% if error %}<div class="err">{{ error }}</div>{% endif %}
        <form id="accessory-form" method="post" enctype="multipart/form-data" onsubmit="return confirmAccessorySave()">
            <div class="grid">
                <div class="row"><label>附属资产集团编号</label><input class="edit-field readonly" disabled type="text" name="sub_group_no" value="{{ accessory.sub_group_no or '' }}"></div>
                <div class="row"><label>附属资产内部编号</label><input class="edit-field readonly" disabled type="text" name="sub_internal_no" value="{{ accessory.sub_internal_no or '' }}"></div>
                <div class="row"><label>名称</label><input class="edit-field readonly" disabled type="text" name="name" value="{{ accessory.name }}"></div>
                <div class="row"><label>型号</label><input class="edit-field readonly" disabled type="text" name="model" value="{{ accessory.model or '' }}"></div>
                <div class="row"><label>责任人</label><input class="edit-field readonly" disabled type="text" name="owner" value="{{ accessory.owner or '' }}"></div>
                <div class="row"><label>位置</label><input class="edit-field readonly" disabled type="text" name="location" value="{{ accessory.location or '' }}"></div>
                <div class="row"><label>时间</label><input class="edit-field readonly" disabled type="date" name="asset_date" value="{{ accessory.asset_date.isoformat() if accessory.asset_date else '' }}"></div>
                <div class="row"><label>状态</label><select class="edit-field readonly" disabled name="status"><option value="">请选择</option>{% for s in statuses %}<option value="{{ s.dict_value }}" {% if (accessory.status or '') == s.dict_value or ((accessory.status or '') == '维修' and s.dict_value == '计量') %}selected{% endif %}>{{ s.dict_value }}</option>{% endfor %}</select></div>
                <div class="row">
                    <label>上传图片（最多5张）</label>
                    <div class="upload-actions"><button type="button" class="edit-field readonly" disabled onclick=\"openUploadChooser('accessory-upload-choice-dialog', this)\">上传图片</button></div>
                    <div id="accessory-image-files-text" class="file-list" data-inputs="accessory-camera-files,accessory-file-files">未选择图片</div>
                    <div id="accessory-upload-choice-dialog" class="upload-dialog" onclick="if(event.target === this){closeUploadChooser('accessory-upload-choice-dialog');}"><div class="upload-dialog-card"><div class="upload-dialog-title">请选择上传方式</div><div class="upload-dialog-actions"><label class="upload-choice-file upload-choice-camera">拍照<input class="edit-field readonly" disabled type="file" id="accessory-camera-files" name="image_files" accept="image/*" capture="environment" multiple onchange="updateSelectedFiles('accessory-camera-files', 'accessory-image-files-text', 'accessory-upload-choice-dialog')"></label><label class="upload-choice-file upload-choice-local">本地上传<input class="edit-field readonly" disabled type="file" id="accessory-file-files" name="image_files" accept="image/*" multiple onchange="updateSelectedFiles('accessory-file-files', 'accessory-image-files-text', 'accessory-upload-choice-dialog')"></label><button type="button" class="btn-cancel" onclick="closeUploadChooser('accessory-upload-choice-dialog')">取消</button></div></div></div>
                </div>
            </div>
            <div class="row"><label>当前图片</label>{% if images %}{% for img in images %}<div class="image-row"><a href="/uploads/{{ img.image_path }}" target="_blank">图片{{ loop.index }}</a><label style="display:flex;align-items:center;gap:6px;font-weight:normal;"><input class="edit-field readonly" disabled type="checkbox" name="delete_accessory_image_ids" value="{{ img.id }}"> 删除</label></div>{% endfor %}<div style="color:#666;font-size:13px;">先点击“修改”，勾选要删除的图片，再点击“确认”。</div>{% else %}<div>暂无图片</div>{% endif %}</div>
            <div class="row"><label>备注</label><textarea class="edit-field readonly" disabled name="remark">{{ accessory.remark or '' }}</textarea></div>
            <div class="detail-actions">
                {% if can_manage %}
                <button type="button" id="accessory-form-edit" data-mode="edit" onclick="handleEditOrSave('accessory-form', 'accessory-form-edit')">修改</button>
                <button type="button" class="btn-red" onclick="requestDeleteWithPin('accessory-delete-form', '确认删除该配件？')">删除</button>
                {% else %}
                <button type="button" class="btn-disabled" disabled>修改</button>
                <button type="button" class="btn-disabled" disabled>删除</button>
                {% endif %}
            </div>
        </form>
        <form id="accessory-delete-form" method="post" action="/accessory/{{ accessory.id }}/delete" style="display:none;"><input type="hidden" name="delete_pin" value=""></form>
        <form id="accessory-inventory-form" method="post" action="/accessory/{{ accessory.id }}" style="display:none;"><input type="hidden" name="action" value="inventory_accessory"></form>
    </div>

<div id="delete-modal" class="simple-modal" onclick="if(event.target===this){closeDeleteModal();}">
    <div class="simple-modal-card">
        <div id="delete-modal-title" class="simple-modal-title">删除确认</div>
        <div id="delete-modal-text" class="simple-modal-text">确认删除吗？</div>
        <div id="delete-modal-input-wrap" class="simple-modal-input-wrap">
            <input id="delete-modal-input" class="simple-modal-input" type="password" inputmode="numeric" maxlength="4" placeholder="请输入4位Pin码">
        </div>
        <div id="delete-modal-error" class="simple-modal-error"></div>
        <div class="simple-modal-actions">
            <button type="button" id="delete-modal-cancel" class="simple-modal-cancel" onclick="handleDeleteModalCancel();">取消</button>
            <button type="button" id="delete-modal-ok" class="simple-modal-ok" onclick="handleDeleteModalOk();">确定</button>
        </div>
    </div>
</div>

</div>
</body>
</html>
"""
