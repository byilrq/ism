from contextlib import contextmanager
from io import BytesIO
import re
import signal

# 全局导入 PIL 模块
try:
    from PIL import Image, ImageOps, ImageEnhance, ImageFilter
    import pytesseract
except ImportError:
    raise RuntimeError("服务器未安装OCR依赖，请先安装 Pillow、pytesseract 和 tesseract-ocr")

SCAN_TIMEOUT_SECONDS = 15
TESSERACT_CMD = "/usr/bin/tesseract"
MAX_IMAGE_WIDTH = 1500  # 保持 1500 兼顾速度与 18 位数字的精度

# 配置 tesseract 路径
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

class ScanTimeoutError(Exception):
    """OCR 识别超时。"""

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
    """
    统一大写，修复相似字符，移除标点。
    """
    value = normalize_text(value).upper()
    if not value:
        return ""
    
    trans = str.maketrans({
        "O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1",
        "|": "1", "Z": "2", "S": "5", "B": "8", "G": "6",
        ":": "", ";": "", "，": "", " ": "", "。": ""
    })
    return value.translate(trans)

def extract_group_no_from_ocr_text(text):
    """
    提取逻辑：针对 18 位长数字和内部编号。
    """
    if not text:
        return ""
    
    normalized = normalize_ocr_digit_text(text)
    
    # 策略 1: 18位集团编号 (308开头)
    # 增加对常见误判字符的后置处理（如果识别出2023，在特定长度下进行校验）
    patterns_308 = [r"308\d{15}", r"308[\d\-]{15,22}"]
    for p in patterns_308:
        matches = re.findall(p, normalized)
        for m in matches:
            digits = re.sub(r"\D", "", m)
            if digits.startswith("308") and len(digits) >= 18:
                return digits[:18]

    # 策略 2: 带连字符的内部编号
    internal_pattern = r"\d{8,15}-\d{1,5}"
    internal_match = re.search(internal_pattern, normalized)
    if internal_match:
        return internal_match.group(0)

    # 策略 3: 最长纯数字块
    long_digits = re.findall(r"\d{10,22}", normalized)
    if long_digits:
        return sorted(long_digits, key=len, reverse=True)[0]

    return ""

def _build_variants(crop, mode="full"):
    """
    预处理：针对数字 6/3/8 误判，微调锐化参数。
    """
    gray = ImageOps.grayscale(crop)
    resample_method = getattr(Image, 'Resampling', Image).LANCZOS 
    
    enlarged = gray.resize((int(gray.width * 1.5), int(gray.height * 1.5)), resample_method)
    
    # 稍微降低对比度增强倍数，防止 6 的圆圈部分因为过载变成实心（导致误判为 8 或 3）
    # 同时增加锐化度
    contrast = ImageEnhance.Contrast(enlarged).enhance(1.8)
    sharp = ImageEnhance.Sharpness(contrast).enhance(2.5)
    
    if mode == "fast":
        return [("sharp", sharp)]

    # 深度识别变体：增加一个适度的二值化
    return [
        ("sharp", sharp),
        ("th_clean", sharp.point(lambda p: 255 if p > 135 else 0)),
    ]

def _run_stage(*, crop_map, crop_names, mode, configs, debug_texts, stage_name):
    lang_set = "chi_sim+eng" 
    
    for crop_name in crop_names:
        if crop_name not in crop_map: continue
        crop = crop_map[crop_name]
        variants = _build_variants(crop, mode=mode)
        for suffix, variant in variants:
            for config in configs:
                try:
                    raw_text = pytesseract.image_to_string(variant, lang=lang_set, config=config)
                    raw_text = normalize_text(raw_text)
                    if raw_text:
                        debug_texts.append(f"{stage_name}:{crop_name}_{suffix}: {raw_text}")
                        group_no = extract_group_no_from_ocr_text(raw_text)
                        if group_no:
                            return group_no
                except:
                    continue
    return ""

def extract_group_no_from_label(file_storage):
    image_bytes = file_storage.read()
    if not image_bytes: return "", []

    try:
        img = Image.open(BytesIO(image_bytes))
    except:
        raise RuntimeError("图片文件读取失败")

    img = ImageOps.exif_transpose(img).convert("RGB")

    if img.width > MAX_IMAGE_WIDTH:
        ratio = MAX_IMAGE_WIDTH / float(img.width)
        img = img.resize((MAX_IMAGE_WIDTH, int(img.height * ratio)), getattr(Image, 'Resampling', Image).LANCZOS)

    width, height = img.size

    crop_map = {
        # 保持上一版证明有效的宽裁剪范围
        "num_band": img.crop((int(width * 0.05), int(height * 0.60), int(width * 0.98), int(width * 0.98))),
        "full": img,
    }

    debug_texts = []
    # 使用 PSM 7（单行）提高数字串的连贯性
    fast_configs = ["--oem 3 --psm 7"]
    full_configs = ["--oem 3 --psm 6"]

    # 1. 快速识别
    group_no = _run_stage(
        crop_map=crop_map, crop_names=["num_band"],
        mode="fast", configs=fast_configs,
        debug_texts=debug_texts, stage_name="fast",
    )
    if group_no: return group_no, debug_texts

    # 2. 深度识别
    group_no = _run_stage(
        crop_map=crop_map, crop_names=["num_band", "full"],
        mode="full", configs=full_configs,
        debug_texts=debug_texts, stage_name="full",
    )
    
    if group_no: return group_no, debug_texts

    return extract_group_no_from_ocr_text(" ".join(debug_texts)), debug_texts

__all__ = [
    "SCAN_TIMEOUT_SECONDS",
    "ScanTimeoutError",
    "extract_group_no_from_label",
    "extract_group_no_from_ocr_text",
    "normalize_ocr_digit_text",
    "scan_time_limit",
]