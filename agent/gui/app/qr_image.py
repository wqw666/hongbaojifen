"""二维码图片：优先读 NapCat 缓存 png，否则根据 URL 生成。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image


def load_qrcode_image(png_path: Path | None, url: str = "", size: int = 260) -> Image.Image | None:
    if png_path and png_path.is_file() and png_path.stat().st_size > 100:
        try:
            img = Image.open(png_path).convert("RGB")
            img.thumbnail((size, size), Image.Resampling.LANCZOS)
            return img
        except Exception:
            pass
    url = (url or "").strip()
    if not url:
        return None
    try:
        import qrcode

        qr = qrcode.QRCode(box_size=4, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        return img
    except Exception:
        return None


def pil_to_ctk(pil_img: Image.Image, size: int = 260):
    import customtkinter as ctk

    return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(size, size))
