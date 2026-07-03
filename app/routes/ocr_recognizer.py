from contextlib import contextmanager
from io import BytesIO
import re
import signal

try:
    from PIL import Image, ImageOps, ImageEnhance
except ImportError:
    Image = None
    ImageOps = None
    ImageEnhance = None

try:
    from pyzbar.pyzbar import decode as zbar_decode, ZBarSymbol
except ImportError:
    zbar_decode = None
    ZBarSymbol = None

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

SCAN_TIMEOUT_SECONDS = 15
BARCODE_TYPES = ("CODE39", "CODE128")


class ScanTimeoutError(Exception):
    """条形码识别超时。"""


@contextmanager
def scan_time_limit(seconds):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handle_timeout(signum, frame):
        raise ScanTimeoutError(f"识别超时（超过{seconds}秒）")

    signal.signal(signal.SIGALRM, _handle_timeout)
    if hasattr(signal, "setitimer"):
        signal.setitimer(signal.ITIMER_REAL, float(seconds))
    else:
        signal.alarm(int(seconds))
    try:
        yield
    finally:
        if hasattr(signal, "setitimer"):
            signal.setitimer(signal.ITIMER_REAL, 0)
        else:
            signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_ocr_digit_text(value):
    """兼容旧接口名，现统一用于条形码结果清洗。"""
    value = normalize_text(value).upper()
    if not value:
        return ""
    return re.sub(r"\s+", "", value)


def extract_group_no_from_ocr_text(text):
    """兼容旧接口名，当前直接返回清洗后的条码值。"""
    return normalize_ocr_digit_text(text)


def _require_runtime():
    if Image is None:
        raise RuntimeError("服务器未安装 Pillow，请先安装 Pillow")
    if zbar_decode is None and (cv2 is None or np is None):
        raise RuntimeError(
            "服务器未安装条形码识别依赖，请安装 pyzbar+zbar，或安装 opencv-python 和 numpy"
        )


def _build_variants(img):
    rgb = img.convert("RGB")
    gray = ImageOps.grayscale(rgb)
    contrast = ImageEnhance.Contrast(gray).enhance(2.2)
    sharp = ImageEnhance.Sharpness(contrast).enhance(2.0)
    binary = sharp.point(lambda p: 255 if p > 140 else 0)
    binary_inv = sharp.point(lambda p: 0 if p > 140 else 255)
    return [
        ("rgb", rgb),
        ("gray", gray),
        ("sharp", sharp),
        ("binary", binary),
        ("binary_inv", binary_inv),
    ]


def _decode_with_pyzbar(image_obj):
    if zbar_decode is None:
        return []

    symbols = []
    if ZBarSymbol is not None:
        if hasattr(ZBarSymbol, "CODE39"):
            symbols.append(ZBarSymbol.CODE39)
        if hasattr(ZBarSymbol, "CODE128"):
            symbols.append(ZBarSymbol.CODE128)

    try:
        decoded = zbar_decode(image_obj, symbols=symbols or None)
    except TypeError:
        decoded = zbar_decode(image_obj)
    except Exception:
        return []

    results = []
    for item in decoded or []:
        try:
            raw_value = item.data.decode("utf-8", errors="ignore")
        except Exception:
            raw_value = str(getattr(item, "data", b""))
        barcode_type = normalize_text(getattr(item, "type", "")).upper()
        if barcode_type and barcode_type not in BARCODE_TYPES:
            continue
        normalized = normalize_ocr_digit_text(raw_value)
        if normalized:
            results.append((barcode_type or "PYZBAR", normalized))
    return results


def _decode_with_opencv(image_obj):
    if cv2 is None or np is None:
        return []
    if not hasattr(cv2, "barcode_BarcodeDetector"):
        return []

    try:
        detector = cv2.barcode_BarcodeDetector()
        rgb = image_obj.convert("RGB")
        arr = np.array(rgb)
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        ok, decoded_infos, decoded_types, _ = detector.detectAndDecodeMulti(bgr)
        if not ok:
            single = detector.detectAndDecode(bgr)
            if isinstance(single, tuple) and len(single) >= 2:
                value = normalize_ocr_digit_text(single[0])
                dtype = normalize_text(single[1]).upper() if len(single) > 1 else ""
                if value and (not dtype or dtype in BARCODE_TYPES):
                    return [(dtype or "OPENCV", value)]
            return []
    except Exception:
        return []

    results = []
    decoded_infos = decoded_infos or []
    decoded_types = decoded_types or []
    for idx, raw_value in enumerate(decoded_infos):
        value = normalize_ocr_digit_text(raw_value)
        dtype = normalize_text(decoded_types[idx] if idx < len(decoded_types) else "").upper()
        if not value:
            continue
        if dtype and dtype not in BARCODE_TYPES:
            continue
        results.append((dtype or "OPENCV", value))
    return results


def extract_group_no_from_label(file_storage):
    _require_runtime()

    image_bytes = file_storage.read()
    if not image_bytes:
        return "", []

    try:
        img = Image.open(BytesIO(image_bytes))
    except Exception:
        raise RuntimeError("图片文件读取失败")

    img = ImageOps.exif_transpose(img).convert("RGB")
    debug_texts = []

    for variant_name, variant in _build_variants(img):
        pyzbar_results = _decode_with_pyzbar(variant)
        for barcode_type, value in pyzbar_results:
            debug_texts.append(f"pyzbar:{variant_name}:{barcode_type}:{value}")
            return value, debug_texts

        opencv_results = _decode_with_opencv(variant)
        for barcode_type, value in opencv_results:
            debug_texts.append(f"opencv:{variant_name}:{barcode_type}:{value}")
            return value, debug_texts

    return "", debug_texts


__all__ = [
    "SCAN_TIMEOUT_SECONDS",
    "ScanTimeoutError",
    "extract_group_no_from_label",
    "extract_group_no_from_ocr_text",
    "normalize_ocr_digit_text",
    "scan_time_limit",
]
