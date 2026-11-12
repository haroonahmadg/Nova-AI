"""Configuration for the FastAPI backend.

Request limits are shared with the Streamlit frontend through
``utils.limits``. UI and upload paths are configured here without importing
``app.py``, whose Streamlit calls require an active Streamlit runtime.
"""

from __future__ import annotations

import os
from pathlib import Path

from utils.limits import (
    MAX_FILE_MB,
    MAX_IMAGE_MB,
    MAX_IMAGES_PER_REQUEST,
    MAX_PROMPT_CHARS,
    MAX_UPLOAD_FILES,
    _positive_int,
)

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Same on-disk locations app.py already uses, so both frontends see the
# same threads, pins, deletions, and uploaded files.
UI_DB_PATH = Path(
    os.getenv("UI_DB_PATH", str(DATA_DIR / "ui.sqlite"))
)

UPLOAD_DIR = Path(
    os.getenv("UPLOAD_DIR", str(DATA_DIR / "upload"))
)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
}

ALLOWED_FILE_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xlsx",
    ".xls",
}

# Comma-separated list of allowed frontend origins for CORS, e.g.
# "http://localhost:3000,http://localhost:3001,https://myapp.example.com"
_LOCAL_FRONTEND_ORIGINS = (
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
)
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGIN", ",".join(_LOCAL_FRONTEND_ORIGINS)
    ).split(",")
    if origin.strip()
]

GENERATED_IMAGE_DIR = DATA_DIR / "generated_images"
GENERATED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
# ============================================================
# ============================================================
# AUTH (Google sign-in)
# Google ID tokens are verified against GOOGLE_CLIENT_ID. App session
# tokens are signed with SESSION_SECRET; set a unique, random secret.
# SESSION_TTL_SECONDS defaults to 7 days.
# ============================================================

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
SESSION_TTL_SECONDS = _positive_int(
    "SESSION_TTL_SECONDS",
    str(7 * 24 * 60 * 60),
)





# ============================================================
# AUTH SETTINGS
#
# Every value can be overridden in .env; the defaults below apply
# when a variable is not set. auth.py, rate_limit.py and main.py
# read these names from here instead of hardcoding numbers, the
# same way GOOGLE_CLIENT_ID / SESSION_SECRET / SESSION_TTL_SECONDS
# already work.
# ============================================================

def _auth_env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read a whole-number setting from the environment, with validation."""

    raw = os.getenv(name)

    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a whole number.") from exc

    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")

    return value


# Input limits
EMAIL_MAX_LENGTH = _auth_env_int("EMAIL_MAX_LENGTH", 254)
NAME_MAX_LENGTH = _auth_env_int("NAME_MAX_LENGTH", 100)
PASSWORD_MIN_LENGTH = _auth_env_int("PASSWORD_MIN_LENGTH", 8)
PASSWORD_MAX_LENGTH = _auth_env_int("PASSWORD_MAX_LENGTH", 128)

if PASSWORD_MIN_LENGTH > PASSWORD_MAX_LENGTH:
    raise RuntimeError(
        "PASSWORD_MIN_LENGTH cannot be greater than PASSWORD_MAX_LENGTH."
    )

# Brute-force protection (see api/rate_limit.py)
LOGIN_MAX_ATTEMPTS_PER_EMAIL = _auth_env_int("LOGIN_MAX_ATTEMPTS_PER_EMAIL", 5)
LOGIN_MAX_ATTEMPTS_PER_IP = _auth_env_int("LOGIN_MAX_ATTEMPTS_PER_IP", 20)
LOGIN_WINDOW_SECONDS = _auth_env_int("LOGIN_WINDOW_SECONDS", 15 * 60)

REGISTER_MAX_PER_IP = _auth_env_int("REGISTER_MAX_PER_IP", 10)
REGISTER_WINDOW_SECONDS = _auth_env_int("REGISTER_WINDOW_SECONDS", 60 * 60)