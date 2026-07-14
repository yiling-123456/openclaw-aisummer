"""图片 OCR 模块（Day10+ 扩展）。

为 `read` 工具提供图片 → 文字能力。
DeepSeek API 不支持多模态视觉输入，因此对图片文件自动走 OCR 路径，
把识别的文字返回给模型，对调用方完全透明。

设计要点：
  - 图片检测基于文件扩展名（轻量、无侵入）
  - OCR 依赖 Pillow + pytesseract，缺失时给出清晰的安装指引
  - 中英文混合识别（chi_sim + eng）
  - 大图自动缩放（防 OOM 和超时）
  - 降级优雅：没装依赖时报错，不崩溃
"""
from __future__ import annotations
import os
import sys

# ── 图片格式白名单 ─────────────────────────────────────────────────────
_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp",
}

# ── OCR 相关（延迟导入，允许依赖缺失时降级） ────────────────────────────
_HAVE_OCR = False
_OCR_IMPORT_ERROR: str | None = None

try:
    from PIL import Image
    import pytesseract
    _HAVE_OCR = True
except ImportError as e:
    _OCR_IMPORT_ERROR = str(e)


# ── 公开 API ──────────────────────────────────────────────────────────

def is_image_file(path: str) -> bool:
    """通过扩展名判断文件是否为图片（不打开文件）。"""
    ext = os.path.splitext(path)[1].lower()
    return ext in _IMAGE_EXTENSIONS


def ocr_image(path: str, lang: str = "chi_sim+eng", max_pixels: int = 2000) -> str:
    """对图片执行 OCR，返回识别的文字。

    Args:
        path: 图片文件路径。
        lang: Tesseract 语言包（默认中英文混合）。
        max_pixels: 最长边缩放阈值（px），超过则等比缩放，防 OOM。

    Returns:
        识别到的文本内容。无文字时返回 "[OCR 未检测到文字]"。
        依赖缺失时返回错误指引。
    """
    # ── 依赖检查 ──
    if not _HAVE_OCR:
        return _build_dependency_error()

    if not os.path.isfile(path):
        return f"[OCR 错误] 文件不存在：{path}"

    try:
        img = Image.open(path)
    except Exception as e:
        return f"[OCR 错误] 无法打开图片：{e}"

    # ── 大图缩放 ──
    original_size = img.size
    img = _maybe_resize(img, max_pixels)

    # ── 执行 OCR ──
    try:
        text = pytesseract.image_to_string(img, lang=lang).strip()
    except Exception as e:
        return f"[OCR 错误] 识别失败：{e}"

    if not text:
        return "[OCR 未检测到文字]"

    # 附上图片基本信息（方便模型了解来源）
    info_parts = [
        f"文件：{os.path.basename(path)}",
        f"尺寸：{original_size[0]}×{original_size[1]}",
    ]
    if original_size != img.size:
        info_parts.append(f"（已从 {original_size[0]}×{original_size[1]} 缩放至 {img.size[0]}×{img.size[1]}）")

    return (
        f"── 以下为图片 OCR 识别结果 ──\n"
        f"{'  '.join(info_parts)}\n"
        f"──\n"
        f"{text}\n"
        f"── OCR 结束 ──"
    )


# ── 内部函数 ──────────────────────────────────────────────────────────

def _maybe_resize(img, max_pixels: int) -> object:
    """如果图片最长边超过 max_pixels，等比缩小。"""
    w, h = img.size
    if max(w, h) <= max_pixels:
        return img

    if w >= h:
        new_w = max_pixels
        new_h = int(h * max_pixels / w)
    else:
        new_h = max_pixels
        new_w = int(w * max_pixels / h)

    return img.resize((new_w, new_h), Image.LANCZOS)


def _build_dependency_error() -> str:
    """依赖缺失时给出清晰的安装指引。"""
    lines = [
        "[OCR 不可用] 缺少依赖。",
        "",
        "请安装以下组件：",
    ]

    # Python 包
    missing_pkgs = []
    try:
        import PIL  # noqa: F401
    except ImportError:
        missing_pkgs.append("Pillow")
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        missing_pkgs.append("pytesseract")

    if missing_pkgs:
        lines.append(f"  1. Python 包：pip install {' '.join(missing_pkgs)}")

    # Tesseract 引擎
    import shutil
    if shutil.which("tesseract") is None:
        lines.append("  2. Tesseract OCR 引擎：")
        if sys.platform == "win32":
            lines.append("     https://github.com/UB-Mannheim/tesseract/wiki 下载安装")
        elif sys.platform == "darwin":
            lines.append("     brew install tesseract tesseract-lang")
        else:
            lines.append("     sudo apt install tesseract-ocr tesseract-ocr-chi-sim")
            lines.append("     或：sudo yum install tesseract tesseract-langpack-chi-sim")
        lines.append("")
        lines.append("     验证安装：tesseract --list-langs  # 应列出 chi_sim（中文）和 eng（英文）")

    lines.append("")
    lines.append("安装完成后重启 agent 即可使用 OCR 功能。")

    return "\n".join(lines)
