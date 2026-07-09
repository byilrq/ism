from datetime import datetime, date
from io import BytesIO
import os
import re
import uuid
from types import SimpleNamespace

import yaml

from flask import request, redirect, url_for, render_template, render_template_string, send_file, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import or_, func, and_
from openpyxl import Workbook
from app.models import (
    User, Asset, Accessory, DictOption,
    AssetImage, AccessoryImage
)
from app import db, FlaskConfig as Config

try:
    from .cable import register_cable_routes, Cable
    from .debug import register_debug_routes
except ImportError:
    from cable import register_cable_routes, Cable
    from debug import register_debug_routes

from .upload import register_upload_routes, AssetLocationImage, delete_image_file, save_uploaded_image, trim_asset_images, trim_accessory_images, trim_asset_location_images, get_asset_location_images, delete_accessory_with_files, delete_asset_with_files, build_image_filename_prefix, sanitize_image_prefix, restore_asset, restore_accessory, permanent_delete_asset, permanent_delete_accessory


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
        if not status_value or status_value in seen or status_value == "已删除":
            continue
        status_items.append(SimpleNamespace(dict_value=status_value))
        seen.add(status_value)

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


def validate_accessory_group_no(group_no, internal_no="", label="附属资产集团编号"):
    group_no = normalize_text(group_no)
    if not group_no:
        return ""
    # 内部编号已有 - 后缀标识为配件时，集团编号不强制带序号
    internal_no = normalize_text(internal_no)
    if internal_no and "-" in internal_no:
        return ""
    if not re.fullmatch(r"\d{18}-\d+", group_no):
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

    for item in Accessory.query.filter(Accessory.parent_asset_id == asset.id, Accessory.deleted_at.is_(None)).all():
        accessory_map[item.id] = item

    conditions = []
    if asset.internal_no:
        conditions.append(Accessory.sub_internal_no.like(f"{asset.internal_no}-%"))
    if asset.group_no:
        conditions.append(Accessory.sub_group_no.like(f"{asset.group_no}-%"))

    if conditions:
        standalone_items = Accessory.query.filter(
            Accessory.parent_asset_id.is_(None),
            or_(*conditions),
            Accessory.deleted_at.is_(None)
        ).all()
        for item in standalone_items:
            accessory_map[item.id] = item

    return sorted(accessory_map.values(), key=get_accessory_suffix_sort_key)


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


def build_search_rows(keyword="", searched=False):
    if not searched:
        return []

    keyword = normalize_text(keyword)
    rows = []

    if not keyword:
        asset_rows = sorted(Asset.query.filter(Asset.deleted_at.is_(None)).all(), key=get_asset_sort_key)
        accessory_rows = Accessory.query.filter(Accessory.deleted_at.is_(None)).all()
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

    # 完整集团编号按完整编号检索，避免 18 位编号因后 6 位相同误命中其他主资产。
    # 普通关键词仍保留原来的后 6 位编号检索。
    suffix_keyword = "" if is_precise_group_keyword else (keyword[-6:] if len(keyword) >= 6 else "")

    exact_assets = Asset.query.filter(
        and_(
            or_(
                Asset.internal_no == keyword,
                Asset.group_no == keyword
            ),
            Asset.deleted_at.is_(None)
        )
    ).order_by(Asset.internal_no.asc()).all()

    suffix_assets = []
    if suffix_keyword:
        suffix_assets = Asset.query.filter(
            and_(
                or_(
                    Asset.internal_no.like(f"%{suffix_keyword}"),
                    Asset.group_no.like(f"%{suffix_keyword}")
                ),
                Asset.deleted_at.is_(None)
            )
        ).order_by(Asset.internal_no.asc()).all()

    asset_ids = []
    for a in exact_assets + suffix_assets:
        if a.id not in asset_ids:
            asset_ids.append(a.id)

    # 备注支持关键词模糊搜索；为避免短关键词误伤，至少连续 2 个字符才启用备注匹配。
    # 完整集团编号搜索时，只保留备注模糊匹配，不再按责任人/位置模糊匹配。
    remark_search_enabled = len(keyword) >= 2
    owner_asset_conditions = []
    if not is_precise_group_keyword:
        owner_asset_conditions.extend([
            Asset.owner.like(f"%{keyword}%"),
            location_like(Asset.location, keyword),
            Asset.name.like(f"%{keyword}%")
        ])
    if remark_search_enabled:
        owner_asset_conditions.append(Asset.remark.like(f"%{keyword}%"))

    owner_assets = []
    if owner_asset_conditions:
        owner_assets = Asset.query.filter(and_(or_(*owner_asset_conditions), Asset.deleted_at.is_(None))).order_by(Asset.internal_no.asc()).all()

    for a in owner_assets:
        if a.id not in asset_ids:
            asset_ids.append(a.id)

    exact_accessories = Accessory.query.filter(
        and_(
            or_(
                Accessory.sub_internal_no == keyword,
                Accessory.sub_group_no == keyword
            ),
            Accessory.deleted_at.is_(None)
        )
    ).all()

    suffix_accessories = []
    if suffix_keyword:
        suffix_accessories = Accessory.query.filter(
            and_(
                or_(
                    Accessory.sub_internal_no.like(f"%{suffix_keyword}"),
                    Accessory.sub_group_no.like(f"%{suffix_keyword}-%")
                ),
                Accessory.deleted_at.is_(None)
            )
        ).all()

    owner_accessory_conditions = []
    if not is_precise_group_keyword:
        owner_accessory_conditions.extend([
            Accessory.owner.like(f"%{keyword}%"),
            location_like(Accessory.location, keyword),
            Accessory.name.like(f"%{keyword}%")
        ])
    if remark_search_enabled:
        owner_accessory_conditions.append(Accessory.remark.like(f"%{keyword}%"))

    owner_accessories = []
    if owner_accessory_conditions:
        owner_accessories = Accessory.query.filter(and_(or_(*owner_accessory_conditions), Accessory.deleted_at.is_(None))).all()

    for acc in exact_accessories + suffix_accessories + owner_accessories:
        if acc.parent_asset_id and acc.parent_asset_id not in asset_ids:
            asset_ids.append(acc.parent_asset_id)

    fuzzy_accessories = Accessory.query.filter(
        and_(
            or_(
                Accessory.sub_internal_no.like(f"{keyword}-%"),
                Accessory.sub_group_no.like(f"{keyword}-%")
            ),
            Accessory.deleted_at.is_(None)
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

    if not rows:
        standalone_conditions = [
            Accessory.sub_internal_no == keyword,
            Accessory.sub_group_no == keyword,
            Accessory.sub_internal_no.like(f"{keyword}-%"),
            Accessory.sub_group_no.like(f"{keyword}-%"),
        ]
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
        (row for row in rows
         if row.get("row_type") == "asset"
         and (normalize_text(row.get("group_no")) == recognized_no
              or normalize_text(row.get("internal_no")) == recognized_no)),
        None,
    )
    if asset_row:
        return asset_row, rows
    accessory_row = next(
        (row for row in rows
         if row.get("row_type") == "accessory"
         and (normalize_text(row.get("group_no")) == recognized_no
              or normalize_text(row.get("internal_no")) == recognized_no)),
        None,
    )
    if accessory_row:
        return accessory_row, rows
    accessory_prefix_row = next(
        (row for row in rows
         if row.get("row_type") == "accessory"
         and (normalize_text(row.get("group_no")).startswith(f"{recognized_no}-")
              or normalize_text(row.get("internal_no")).startswith(f"{recognized_no}-"))),
        None,
    )
    if accessory_prefix_row:
        return accessory_prefix_row, rows
    return rows[0], rows


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


# ---------------------------------------------------------------------------
# Scan code action (dispatch)
# ---------------------------------------------------------------------------

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
    register_upload_routes(app)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            if normalize_text(request.form.get("login_submit")) != "1":
                return render_template("login.html", error="")

            username = normalize_text(request.form.get("username"))
            password = normalize_text(request.form.get("password"))

            admin_config_path = os.path.join(Config.BASE_DIR, "config.yaml")
            admin_user = "admin"
            admin_pass = "Plex0819$"
            if os.path.exists(admin_config_path):
                try:
                    with open(admin_config_path, encoding="utf-8") as f:
                        cfg = yaml.safe_load(f)
                    if cfg and "admin" in cfg:
                        admin_user = cfg["admin"].get("username", "admin")
                        admin_pass = cfg["admin"].get("password", "Plex0819$")
                except Exception:
                    pass

            if username == admin_user and password == admin_pass:
                session.pop("visitor_role", None)
                user = User.query.filter_by(username=username).first()
                if not user:
                    user = User(username=admin_user, password=admin_pass)
                    db.session.add(user)
                    db.session.commit()
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
                import_summary_text += " 可在上传按钮后点击日志下载查看失败明细。"

        return render_template(
            "dashboard.html",
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

        return render_template(
            "asset_location.html",
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
        return render_template("scan_label.html")


    @app.route("/scan_label_assign", methods=["GET"])
    def scan_label_assign():
        guard = ensure_read_access()
        if guard:
            return guard
        assign_location = normalize_location(request.args.get("location"))
        if not assign_location:
            return redirect(url_for("search_assets"))
        back_url = url_for("asset_location_detail", location=assign_location)
        return render_template("scan_label_assign.html", assign_location=assign_location, back_url=back_url)


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
        back_url = normalize_text(request.args.get("back_url")) or (url_for("asset_location_detail", location=assign_location) if assign_location else url_for("search_assets"))
        return render_template("scan_label_image.html", assign_location=assign_location, back_url=back_url)

    @app.route("/scan_label_image_action", methods=["POST"])
    def scan_label_image_action():
        guard = ensure_read_access()
        if guard:
            return jsonify({"ok": False, "message": "无访问权限"}), 403

        scan_mode = normalize_text(request.form.get("mode")) or "search"
        target_fill = normalize_text(request.form.get("target"))
        scan_file = request.files.get("scan_image")

        # 如果是从设备详情/新增页面调用的填充模式，不做检索
        if target_fill:
            if not scan_file or not getattr(scan_file, "filename", ""):
                return jsonify({"ok": False, "message": "请先拍摄标签照片"}), 400
            try:
                with label_scan_time_limit(LABEL_SCAN_TIMEOUT_SECONDS):
                    recognized_no, debug_texts = extract_group_no_from_label_image(scan_file)
                if not recognized_no:
                    return jsonify({"ok": False, "message": "未识别到有效标签编号"}), 400
                return jsonify({"ok": True, "action": "fill", "code": normalize_text(recognized_no)}), 200
            except LabelScanTimeoutError:
                return jsonify({"ok": False, "message": f"识别超时，已自动终止（超过{LABEL_SCAN_TIMEOUT_SECONDS}秒）"}), 408
            except Exception as e:
                return jsonify({"ok": False, "message": f"识别失败：{str(e)}"}), 500

        # 主界面标签识别：检索数据库
        if scan_mode == "inventory":
            manage_guard = ensure_manage_access()
            if manage_guard:
                return jsonify({"ok": False, "message": "当前账号无盘点权限"}), 403

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
        # 浏览器提交 selected_items 时，跨页"全选"会把当前页可见项放在前面、隐藏项放在后面，
        # 直接按提交顺序导出会打乱"主资产 + 配件按后缀排序"的显示顺序。
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
                if accessory:
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
        preset_group_no = normalize_text(request.args.get("group_no")) or normalize_text(request.args.get("scan_fill"))
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
                if status == "开箱":
                    number_error = ""
                    if group_no:
                        number_error = validate_asset_group_no(group_no, "集团编号")
                    if not internal_no:
                        internal_no = datetime.now().strftime("%Y%m%d%H%M")
                        form_data["internal_no"] = internal_no
                else:
                    number_error = validate_required_number_pair(internal_no, group_no, "内部编号", "集团编号")
                    if not number_error:
                        number_error = validate_asset_group_no(group_no, "集团编号")
            else:
                number_error = validate_required_number_pair(internal_no, group_no, "附属资产内部编号", "附属资产集团编号")
                if not number_error:
                    number_error = validate_accessory_no_format(internal_no, group_no)
                if not number_error:
                    number_error = validate_accessory_group_no(group_no, internal_no, "附属资产集团编号")
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

        return render_template(
            "device_new.html",
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

                if status == "开箱":
                    number_error = ""
                    if group_no:
                        number_error = validate_asset_group_no(group_no, "集团编号")
                    if not internal_no:
                        internal_no = datetime.now().strftime("%Y%m%d%H%M")
                else:
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
                            return redirect(url_for("asset_detail", asset_id=asset.id))
                    except Exception as e:
                        db.session.rollback()
                        error = f"更新失败：{str(e)}"

        scan_fill = normalize_text(request.args.get("scan_fill"))
        scan_target = normalize_text(request.args.get("scan_target"))
        scan_editing = normalize_text(request.args.get("scan_editing"))
        accessories = get_asset_related_accessories(asset)
        images = AssetImage.query.filter_by(asset_id=asset.id).order_by(AssetImage.created_at.asc(), AssetImage.id.asc()).all()

        return render_template(
            "asset_detail.html",
            current_user=current_user,
            can_manage=has_manage_access(),
            asset=asset,
            accessories=accessories,
            images=images,
            statuses=statuses,
            today=date.today().isoformat(),
            message=message,
            error=error,
            scan_fill=scan_fill,
            scan_target=scan_target,
            scan_editing=scan_editing
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

    @app.route("/recycle_bin", methods=["GET"])
    def recycle_bin():
        guard = ensure_manage_access()
        if guard:
            return guard

        from datetime import datetime, timedelta
        cutoff_time = datetime.now() - timedelta(days=30)

        deleted_assets = Asset.query.filter(and_(Asset.deleted_at.isnot(None), Asset.status == "已删除")).all()
        for asset in deleted_assets:
            if asset.deleted_at < cutoff_time:
                try:
                    permanent_delete_asset(asset)
                except Exception as e:
                    print(f"[ERR] 删除资产 {asset.id} 失败: {str(e)}")

        deleted_accessories = Accessory.query.filter(and_(Accessory.deleted_at.isnot(None), Accessory.status == "已删除")).all()
        for accessory in deleted_accessories:
            if accessory.deleted_at < cutoff_time:
                try:
                    permanent_delete_accessory(accessory)
                except Exception as e:
                    print(f"[ERR] 删除配件 {accessory.id} 失败: {str(e)}")

        db.session.commit()

        deleted_assets = Asset.query.filter(and_(Asset.deleted_at.isnot(None), Asset.status == "已删除")).order_by(Asset.deleted_at.desc()).all()
        deleted_accessories = Accessory.query.filter(and_(Accessory.deleted_at.isnot(None), Accessory.status == "已删除")).order_by(Accessory.deleted_at.desc()).all()
        return render_template(
            "recycle.html",
            current_user=current_user,
            can_manage=has_manage_access(),
            user_display=get_user_display_name(),
            deleted_assets=deleted_assets,
            deleted_accessories=deleted_accessories
        )

    @app.route("/asset/<int:asset_id>/restore", methods=["POST"])
    def restore_asset_route(asset_id):
        guard = ensure_manage_access()
        if guard:
            return guard
        asset = Asset.query.get_or_404(asset_id)
        try:
            restore_asset(asset)
            for accessory in Accessory.query.filter_by(parent_asset_id=asset.id, status="已删除").all():
                restore_accessory(accessory)
            db.session.commit()
            return redirect(url_for("recycle_bin"))
        except Exception as e:
            db.session.rollback()
            return f"恢复失败：{str(e)}"

    @app.route("/accessory/<int:accessory_id>/restore", methods=["POST"])
    def restore_accessory_route(accessory_id):
        guard = ensure_manage_access()
        if guard:
            return guard
        accessory = Accessory.query.get_or_404(accessory_id)
        try:
            restore_accessory(accessory)
            db.session.commit()
            return redirect(url_for("recycle_bin"))
        except Exception as e:
            db.session.rollback()
            return f"恢复失败：{str(e)}"

    @app.route("/recycle/permanent-delete", methods=["POST"])
    def permanent_delete_selected():
        guard = ensure_manage_access()
        if guard:
            return guard

        import json
        selected_items_json = request.form.get("selected_items", "[]")
        delete_pin = normalize_text(request.form.get("delete_pin"))

        if delete_pin != "0819":
            return redirect(url_for("recycle_bin"))

        try:
            selected_items = json.loads(selected_items_json)
        except:
            return redirect(url_for("recycle_bin"))

        try:
            for item in selected_items:
                try:
                    row_type, row_id = item.split(":")
                    row_id = int(row_id)
                except:
                    continue

                if row_type == "asset":
                    asset = Asset.query.get(row_id)
                    if asset:
                        permanent_delete_asset(asset)
                elif row_type == "accessory":
                    accessory = Accessory.query.get(row_id)
                    if accessory:
                        permanent_delete_accessory(accessory)

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return f"删除失败：{str(e)}"

        return redirect(url_for("recycle_bin"))

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
                    number_error = validate_accessory_group_no(sub_group_no, sub_internal_no, "附属资产集团编号")
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

        scan_fill = normalize_text(request.args.get("scan_fill"))
        scan_target = normalize_text(request.args.get("scan_target"))
        scan_editing = normalize_text(request.args.get("scan_editing"))
        images = AccessoryImage.query.filter_by(accessory_id=accessory.id).order_by(AccessoryImage.created_at.asc(), AccessoryImage.id.asc()).all()
        resolved_parent_asset_id = accessory.parent_asset_id or resolve_parent_asset_id(
            internal_no=accessory.sub_internal_no,
            group_no=accessory.sub_group_no
        )
        back_url = url_for("asset_detail", asset_id=resolved_parent_asset_id) if resolved_parent_asset_id else url_for("search_assets")

        return render_template(
            "accessory_detail.html",
            current_user=current_user,
            can_manage=has_manage_access(),
            accessory=accessory,
            statuses=statuses,
            message=message,
            error=error,
            images=images,
            back_url=back_url,
            scan_fill=scan_fill,
            scan_target=scan_target,
            scan_editing=scan_editing
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




