from __future__ import annotations

import math
import os


MAX_SUPPORTED_IMAGES_PER_REQUEST = 3


def _positive_finite_float(name: str, default: str) -> float:
    raw_value = os.getenv(name, default)
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive finite number.") from exc

    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a positive finite number.")
    return value


def _positive_int(name: str, default: str) -> int:
    raw_value = os.getenv(name, default)
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive whole number.") from exc

    if value <= 0:
        raise RuntimeError(f"{name} must be a positive whole number.")
    return value


def _image_count_limit() -> int:
    value = _positive_int("MAX_IMAGES_PER_REQUEST", "3")
    if value > MAX_SUPPORTED_IMAGES_PER_REQUEST:
        raise RuntimeError(
            "MAX_IMAGES_PER_REQUEST cannot exceed "
            f"{MAX_SUPPORTED_IMAGES_PER_REQUEST}, the vision model limit."
        )
    return value


MAX_IMAGE_MB = _positive_finite_float("MAX_IMAGE_MB", "10")
MAX_FILE_MB = _positive_finite_float("MAX_FILE_MB", "25")
MAX_UPLOAD_FILES = _positive_int("MAX_UPLOAD_FILES", "10")
MAX_PROMPT_CHARS = _positive_int("MAX_PROMPT_CHARS", "8000")
MAX_IMAGES_PER_REQUEST = _image_count_limit()
MAX_USER_STORAGE_MB = _positive_int("MAX_USER_STORAGE_MB", "1024")
