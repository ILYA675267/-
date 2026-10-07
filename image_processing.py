"""Обработка фото товара: убрать фон и поставить товар по центру на твой фон."""

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps
from rembg import new_session, remove

# Модель для удаления фона. Скачается сама при первом запуске (~170 МБ).
_session = None


def _get_session():
    global _session
    if _session is None:
        _session = new_session("u2net")
    return _session


# --- Настройка (можно менять) ---
# Какую часть фона может занять товар: 0.68 = 68% ширины/высоты.
# Больше — товар крупнее, но может залезть на человечка в правом нижнем углу.
PRODUCT_SIZE = 0.68


def _cut_out(photo: Image.Image) -> Image.Image:
    """Вырезает товар: всё вокруг становится прозрачным, пустые края обрезаются."""
    cut = remove(photo, session=_get_session())
    box = cut.getbbox()
    return cut.crop(box) if box else cut


def make_product_photo(photo_bytes: bytes, background_path: Path) -> bytes:
    """Главная функция: принимает фото (байты), возвращает готовое фото JPG (байты)."""
    photo = ImageOps.exif_transpose(Image.open(BytesIO(photo_bytes))).convert("RGB")
    product = _cut_out(photo)

    canvas = Image.open(background_path).convert("RGBA")

    # Вписываем товар в фон, сохраняя пропорции, и ставим ровно по центру
    scale = min(canvas.width * PRODUCT_SIZE / product.width,
                canvas.height * PRODUCT_SIZE / product.height)
    product = product.resize((max(1, int(product.width * scale)),
                              max(1, int(product.height * scale))), Image.LANCZOS)
    x = (canvas.width - product.width) // 2
    y = (canvas.height - product.height) // 2
    canvas.alpha_composite(product, (x, y))

    out = BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=95)
    return out.getvalue()
