import os
import pytest
from pathlib import Path
from utils.security import (
    sanitize_path,
    validate_file_security,
    sanitize_user_input,
    MAX_FILE_SIZE_BYTES,
)


def test_sanitize_user_input():
    raw_text = "  Hello World!\x00 "
    cleaned = sanitize_user_input(raw_text)
    assert cleaned == "Hello World!"


def test_sanitize_path_valid(tmp_path):
    safe_dir = tmp_path / "data"
    safe_dir.mkdir()
    target_file = safe_dir / "sample.txt"
    target_file.write_text("Test content")

    resolved = sanitize_path(str(target_file), base_dir=str(safe_dir))
    assert resolved == target_file.resolve()


def test_sanitize_path_traversal_blocked(tmp_path):
    safe_dir = tmp_path / "data"
    safe_dir.mkdir()
    dangerous_path = safe_dir / ".." / ".." / "etc" / "passwd"

    with pytest.raises(PermissionError):
        sanitize_path(str(dangerous_path), base_dir=str(safe_dir))


def test_validate_file_security_invalid_extension(tmp_path):
    bad_file = tmp_path / "malicious.exe"
    bad_file.write_text("binary content")

    with pytest.raises(ValueError, match="Unsupported file format"):
        validate_file_security(str(bad_file))


def test_validate_file_security_empty_file(tmp_path):
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("")

    with pytest.raises(ValueError, match="File is empty"):
        validate_file_security(str(empty_file))


def test_validate_file_security_oversized_file(tmp_path):
    large_file = tmp_path / "large.txt"
    with open(large_file, "wb") as f:
        f.seek(MAX_FILE_SIZE_BYTES + 100)
        f.write(b"0")

    with pytest.raises(ValueError, match="exceeds maximum limit"):
        validate_file_security(str(large_file))


def test_validate_file_security_accepts_files_larger_than_legacy_limit(
    tmp_path, monkeypatch
):
    import utils.security as security

    monkeypatch.setattr(security, "MAX_FILE_SIZE_MB", 25.0)
    monkeypatch.setattr(
        security,
        "MAX_FILE_SIZE_BYTES",
        int(25 * 1024 * 1024),
    )
    large_file = tmp_path / "large.txt"
    with open(large_file, "wb") as file:
        file.truncate(11 * 1024 * 1024)

    assert validate_file_security(str(large_file))


def test_file_size_limit_uses_configured_environment_value(tmp_path, monkeypatch):
    import importlib
    import utils.limits as limits
    import utils.security as security

    original_value = os.environ.get("MAX_FILE_MB")
    monkeypatch.setenv("MAX_FILE_MB", "12.5")
    importlib.reload(limits)
    security = importlib.reload(security)

    assert security.MAX_FILE_SIZE_MB == 12.5
    assert security.MAX_FILE_SIZE_BYTES == int(12.5 * 1024 * 1024)

    if original_value is None:
        monkeypatch.delenv("MAX_FILE_MB")
    else:
        monkeypatch.setenv("MAX_FILE_MB", original_value)
    importlib.reload(limits)
    importlib.reload(security)