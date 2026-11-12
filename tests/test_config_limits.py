import importlib

import pytest

import utils.limits as limits
from utils.limits import (
    MAX_SUPPORTED_IMAGES_PER_REQUEST,
    _image_count_limit,
    _positive_finite_float,
    _positive_int,
)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1", "invalid"])
def test_image_size_limit_requires_positive_finite_number(
    monkeypatch, value
):
    monkeypatch.setenv("TEST_IMAGE_LIMIT", value)

    with pytest.raises(RuntimeError, match="positive finite number"):
        _positive_finite_float("TEST_IMAGE_LIMIT", "10")


def test_image_count_limit_uses_vision_model_capacity(monkeypatch):
    monkeypatch.setenv(
        "MAX_IMAGES_PER_REQUEST",
        str(MAX_SUPPORTED_IMAGES_PER_REQUEST + 1),
    )

    with pytest.raises(RuntimeError, match="vision model limit"):
        _image_count_limit()


def test_image_count_limit_accepts_supported_configuration(monkeypatch):
    monkeypatch.setenv("MAX_IMAGES_PER_REQUEST", "2")

    assert _image_count_limit() == 2


@pytest.mark.parametrize("value", ["0", "-1", "invalid", ""])
def test_positive_integer_limit_rejects_invalid_values(value):
    with pytest.raises(RuntimeError, match="positive whole number"):
        _positive_int("TEST_INTEGER_LIMIT", value)


def test_shared_limits_are_validated_from_environment(monkeypatch):
    monkeypatch.setenv("MAX_FILE_MB", "12.5")
    monkeypatch.setenv("MAX_UPLOAD_FILES", "4")
    monkeypatch.setenv("MAX_PROMPT_CHARS", "12000")

    refreshed_limits = importlib.reload(limits)

    assert refreshed_limits.MAX_FILE_MB == 12.5
    assert refreshed_limits.MAX_UPLOAD_FILES == 4
    assert refreshed_limits.MAX_PROMPT_CHARS == 12000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MAX_UPLOAD_FILES", "0"),
        ("MAX_UPLOAD_FILES", "-1"),
        ("MAX_PROMPT_CHARS", "invalid"),
        ("MAX_PROMPT_CHARS", "0"),
    ],
)
def test_shared_integer_limits_reject_invalid_environment(
    monkeypatch, name, value
):
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="positive whole number"):
        importlib.reload(limits)


def test_api_serves_configured_prompt_limit(monkeypatch):
    from fastapi.testclient import TestClient

    import api.main as api_main

    monkeypatch.setattr(api_main, "MAX_PROMPT_CHARS", 12000)

    with TestClient(api_main.app) as client:
        response = client.get("/config/limits")

    assert response.status_code == 200
    assert response.json() == {"max_prompt_chars": 12000}


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
)
def test_api_allows_local_frontend_origins_by_default(monkeypatch, origin):
    from fastapi.testclient import TestClient

    import api.config as api_config
    import api.main as api_main

    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    importlib.reload(api_config)
    api_main = importlib.reload(api_main)

    with TestClient(api_main.app) as client:
        response = client.get(
            "/config/limits",
            headers={"Origin": origin},
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
