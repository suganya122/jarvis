"""Downscale and encode captures: one image for the model, one small preview for the UI."""

import base64
import io

from PIL import Image

from contracts.model import ImageData

PREVIEW_EDGE = 480
MODEL_QUALITY = 85
PREVIEW_QUALITY = 70


def fit(image: Image.Image, max_edge: int) -> tuple[Image.Image, float]:
    """Image no larger than max_edge on its longest side, and source px per output px."""
    longest = max(image.width, image.height)
    if longest <= max_edge:
        return image, 1.0
    scale = longest / max_edge
    size = (max(1, round(image.width / scale)), max(1, round(image.height / scale)))
    return image.resize(size, Image.Resampling.LANCZOS), scale


def encode(image: Image.Image, quality: int) -> ImageData:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True)
    return ImageData(
        media_type="image/jpeg",
        data_b64=base64.b64encode(buffer.getvalue()).decode(),
        width=image.width,
        height=image.height,
    )


def model_and_preview(image: Image.Image, max_edge: int) -> tuple[ImageData, ImageData, float]:
    model_image, scale = fit(image, max_edge)
    preview, _ = fit(model_image, PREVIEW_EDGE)
    return encode(model_image, MODEL_QUALITY), encode(preview, PREVIEW_QUALITY), scale


def data_url(image: ImageData) -> str:
    return f"data:{image.media_type};base64,{image.data_b64}"
