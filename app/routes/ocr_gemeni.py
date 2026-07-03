import os
import re
import signal
from collections import Counter
from contextlib import contextmanager
from io import BytesIO

# ==========================================
# 线程控制：限制为 4 避免骁龙 8 核心调度引发降频发热
# ==========================================
os.environ["OMP_THREAD_LIMIT"] = "4"

try:
    from PIL import Image, ImageOps, ImageEnhance, ImageFilter
    import pytesseract
except ImportError:
    raise RuntimeError("服务器未安装OCR依赖，请先安装 Pillow、pytesseract 和 tesseract-ocr")

SCAN_TIMEOUT_SECONDS = 15
TESSERACT_CMD = "/usr/bin/tesseract"
MAX_IMAGE_WIDTH = 1500  

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
    value = normalize_text(value).upper()
    if not value: return ""
    
    # 【修复重点 1】：将 "-" 从替换列表中移除，保护子编号连接符
    for char in [":", "：", ";", "；", "，", ",", "。", ".", "“", "”", "_", "号", "编", "产", "资"]:
        value = value.replace(char, " ")

    trans = str.maketrans({
        "O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1",
        "|": "1", "Z": "2", "S": "5", "B": "8", "G": "6"
    })
    return value.translate(trans)

def extract_group_no_from_ocr_text(text):
    if not text: return ""
    normalized = normalize_ocr_digit_text(text)
    tokens = normalized.split()
    candidates = []

    for token in tokens:
        # 【修复重点 2】：优先捕获并保留带 - 的子编号格式，如 123456789-001
        internal_match = re.search(r"\d{8,22}-\d{1,5}", token)
        if internal_match:
            candidates.append(internal_match.group(0))
            continue

        # 匹配常规纯数字
        digits = re.sub(r"\D", "", token)
        if digits.startswith("308") and len(digits) >= 18:
            candidates.append(digits[:18])
        elif len(digits) >= 8:  
            candidates.append(digits)

    if candidates:
        # 排序权重：带有 "-" 连字符的优先排前面，其次按长度排序
        candidates.sort(key=lambda x: (1 if "-" in x else 0, len(x)), reverse=True)
        return candidates[0]

    # 兜底：抹平空格后的强行匹配
    merged_text = normalized.replace(" ", "")
    
    # 兜底优先找带连字符的
    internal_match = re.search(r"\d{8,22}-\d{1,5}", merged_text)
    if internal_match: 
        return internal_match.group(0)

    # 然后找 308 开头的特定长数字
    for p in [r"308\d{15}"]:
        matches = re.findall(p, merged_text)
        for m in matches:
            if m.startswith("308"): return m[:18]

    # 最后找最长的纯数字块
    long_digits = re.findall(r"\d{8,22}", merged_text)
    if long_digits:
        return sorted(long_digits, key=len, reverse=True)[0]

    return ""

def _build_variants(crop, mode="full"):
    gray = ImageOps.grayscale(crop)
    resample_method = getattr(Image, 'Resampling', Image).BICUBIC 
    enlarged = gray.resize((int(gray.width * 1.5), int(gray.height * 1.5)), resample_method)
    
    contrast = ImageEnhance.Contrast(enlarged).enhance(1.8)
    sharp = ImageEnhance.Sharpness(contrast).enhance(2.5)
    
    # 构建高通滤波抗反光图层（极高效的 PIL 数学运算，不卡顿）
    blurred_light_map = enlarged.filter(ImageFilter.GaussianBlur(radius=15))
    inverted_blurred = ImageOps.invert(blurred_light_map)
    high_pass = Image.blend(enlarged, inverted_blurred, alpha=0.5)
    th_glare = high_pass.point(lambda p: 0 if p < 115 else 255)

    if mode == "fast":
        # 提速：快速模式只做这最有效的 2 种图像预处理，扫中直接退出
        return [("sharp", sharp), ("th_glare", th_glare)]

    # 深度模式：增加形态学加粗和极端二值化
    variants = [
        ("sharp", sharp),
        ("th_glare", th_glare),
    ]
    thickened = th_glare.filter(ImageFilter.MinFilter(3))
    variants.append(("morph_thick", thickened))
    
    return variants

def _run_stage(*, crop_map, crop_names, mode, configs, debug_texts, stage_name):
    lang_set = "chi_sim+eng" 
    found_numbers = [] 
    
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
                        debug_texts.append(f"{stage_name}:{crop_name}_{suffix}_{config[-5:]}: {raw_text}")
                        group_no = extract_group_no_from_ocr_text(raw_text)
                        
                        if group_no:
                            # 【提速重点】：如果是 fast 通道，发现有效数字立刻返回，终结循环！
                            if mode == "fast":
                                return group_no
                            found_numbers.append(group_no)
                except:
                    continue
    
    # 只有进入深层 mode == "full" 时，才动用计算密集的投票决议机制
    if mode == "full" and found_numbers:
        best_match, count = Counter(found_numbers).most_common(1)[0]
        debug_texts.append(f"--- 投票胜出: {best_match} (得票数: {count}/{len(found_numbers)}) ---")
        return best_match
        
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
        img = img.resize((MAX_IMAGE_WIDTH, int(img.height * ratio)), getattr(Image, 'Resampling', Image).BICUBIC)

    width, height = img.size

    # 保留最核心的居中剪裁和全图
    crop_map = {
        "center_band": img.crop((int(width * 0.05), int(height * 0.20), int(width * 0.98), int(height * 0.85))),
        "full": img,
    }

    debug_texts = []
    
    # 快速通道：仅用 PSM 7 配合极速抗反光算法
    fast_configs = ["--oem 3 --psm 7"]
    
    # 深度通道：去掉极其拖拉的 PSM 11，保留 6 和 7 的黄金组合
    full_configs = [
        "--oem 3 --psm 6",
        "--oem 3 --psm 7"
    ]

    # ==========================
    # 阶段 1：闪电打击 (Fast Pass)
    # 针对条件较好的图片，1秒内返回结果，完美兼顾速度
    # ==========================
    group_no = _run_stage(
        crop_map=crop_map, crop_names=["center_band"],
        mode="fast", configs=fast_configs,
        debug_texts=debug_texts, stage_name="fast",
    )
    if group_no: return group_no, debug_texts

    # ==========================
    # 阶段 2：深度拯救 (Deep Pass)
    # 只有极端暗光、严重反光导致第一步识别失败，才动用算力和投票机制
    # ==========================
    group_no = _run_stage(
        crop_map=crop_map, crop_names=["center_band", "full"],
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