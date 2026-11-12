import importlib
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def image_api(monkeypatch):
    tmp_path = Path(tempfile.mkdtemp(prefix="ai-images-"))
    for key in (
        "Gemini_API_Key",
        "GEMINI_API_KEY",
        "Cloudflare_key",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_ACCOUNT_ID",
        "HF_API_KEY",
        "HF_API_TOKEN",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("UI_DB_PATH", str(tmp_path / "ui.sqlite"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "upload"))
    monkeypatch.setenv(
        "SESSION_SECRET",
        "test-only-session-secret-that-is-long-enough-123456",
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.sqlite"))

    import agent.tools as agent_tools
    import api.main as api_main

    monkeypatch.setattr(agent_tools, "GENERATED_IMAGE_DIR", tmp_path / "generated")
    importlib.reload(api_main)
    with TestClient(api_main.app) as client:
        yield client, api_main, agent_tools


def _create_account(client: TestClient, email: str) -> str:
    response = client.post(
        "/auth/register",
        json={"email": email, "password": "Password123", "name": "Image User"},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _provider_response(url: str, payload: dict, status_code: int = 200):
    import httpx

    class FakeResponse:
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            if status_code >= 400:
                request = httpx.Request("POST", url)
                response = httpx.Response(status_code, request=request, json=payload)
                raise httpx.HTTPStatusError(
                    f"HTTP {status_code}",
                    request=request,
                    response=response,
                )

        def json(self):
            return payload

    return FakeResponse()


def test_image_library_is_account_scoped(image_api):
    client, api_main, agent_tools = image_api
    first_token = _create_account(client, "first@example.com")
    second_token = _create_account(client, "second@example.com")
    first_user = api_main.verify_session_token(first_token)
    second_user = api_main.verify_session_token(second_token)
    first_name = f"{uuid.uuid4().hex[:16]}.png"
    second_name = f"{uuid.uuid4().hex[:16]}.png"
    first_path = agent_tools.get_generated_image_dir(first_user, create=True) / first_name
    second_path = agent_tools.get_generated_image_dir(second_user, create=True) / second_name
    first_path.write_bytes(b"first-image")
    second_path.write_bytes(b"second-image")

    first_headers = {"Authorization": f"Bearer {first_token}"}
    second_headers = {"Authorization": f"Bearer {second_token}"}

    first_library = client.get("/images", headers=first_headers)
    second_library = client.get("/images", headers=second_headers)
    assert first_library.status_code == 200
    assert [image["filename"] for image in first_library.json()] == [first_name]
    assert [image["filename"] for image in second_library.json()] == [second_name]

    assert client.get(f"/generated-images/{first_name}", headers=first_headers).content == b"first-image"
    assert client.get(f"/generated-images/{first_name}", headers=second_headers).status_code == 404
    assert client.get(f"/generated-images/{first_name}").status_code == 401


def test_library_includes_account_uploaded_files_and_media_is_private(image_api):
    client, api_main, _ = image_api
    token = _create_account(client, "library@example.com")
    other_token = _create_account(client, "other-library@example.com")
    user_id = api_main.verify_session_token(token)
    folder = "thread-folder"
    user_folder = api_main._user_upload_dir(user_id) / folder
    user_folder.mkdir(parents=True)
    filename = "document_123456.txt"
    (user_folder / filename).write_text("account document", encoding="utf-8")

    response = client.get(
        "/library/media",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()[0]["filename"] == filename
    assert response.json()[0]["folder"] == folder

    item = client.get(
        f"/library/media/{folder}/{filename}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert item.status_code == 200
    assert item.text == "account document"
    assert client.get(
        f"/library/media/{folder}/{filename}",
        headers={"Authorization": f"Bearer {other_token}"},
    ).status_code == 404


def test_image_generation_uses_authenticated_account(image_api, monkeypatch):
    client, api_main, agent_tools = image_api
    token = _create_account(client, "generate@example.com")
    user_id = api_main.verify_session_token(token)
    filename = f"{uuid.uuid4().hex[:16]}.png"
    calls = []

    def fake_generate(args):
        calls.append(agent_tools.active_user_id.get())
        image_dir = agent_tools.get_generated_image_dir(
            agent_tools.active_user_id.get(), create=True
        )
        (image_dir / filename).write_bytes(b"generated-image")
        return f"{agent_tools.GENERATED_IMAGE_MARKER}{filename}"

    monkeypatch.setattr(api_main, "generate_image", SimpleNamespace(invoke=fake_generate))
    response = client.post(
        "/images/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": "A cat in a garden"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["filename"] == filename
    assert calls == [user_id]


def test_generate_image_uses_gemini_and_saves_valid_image(image_api, monkeypatch):
    _, _, agent_tools = image_api
    import base64
    from io import BytesIO
    from PIL import Image

    monkeypatch.setenv("Gemini_API_Key", "test-gemini-key")
    user_token = agent_tools.active_user_id.set("free-provider-user")
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2), color="red").save(image_bytes, format="PNG")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(("post", url, kwargs))
        return _provider_response(
            url,
            {"output_image": {"data": base64.b64encode(image_bytes.getvalue()).decode()}},
        )

    monkeypatch.setattr(agent_tools.httpx, "post", fake_post)
    monkeypatch.setattr(agent_tools, "emit_tool_event", lambda *args, **kwargs: None)
    try:
        result = agent_tools.generate_image.func("A cat in a garden")
    finally:
        agent_tools.active_user_id.reset(user_token)

    assert result.startswith(agent_tools.GENERATED_IMAGE_MARKER)
    assert calls[0][1] == agent_tools.GEMINI_IMAGE_URL
    assert calls[0][2]["headers"]["x-goog-api-key"] == "test-gemini-key"
    assert calls[0][2]["json"]["model"] == agent_tools.GEMINI_IMAGE_MODEL
    assert calls[0][2]["json"]["input"][0]["text"] == "A cat in a garden"
    image_path = (
        agent_tools.get_generated_image_dir("free-provider-user")
        / f"{result.removeprefix(agent_tools.GENERATED_IMAGE_MARKER)}"
    )
    with Image.open(image_path) as saved:
        assert saved.size == (2, 2)


@pytest.mark.parametrize(
    ("gemini_status_code", "gemini_payload"),
    [
        (401, {}),
        (403, {}),
        (429, {}),
        (500, {}),
        (
            400,
            {
                "error": {
                    "status": "API_KEY_INVALID",
                    "message": "API key not valid.",
                }
            },
        ),
    ],
)
def test_generate_image_falls_back_to_cloudflare_for_retryable_gemini_errors(
    image_api, monkeypatch, gemini_status_code, gemini_payload
):
    _, _, agent_tools = image_api
    import base64
    from io import BytesIO
    from PIL import Image

    account_id = "a" * 32
    monkeypatch.setenv("Gemini_API_Key", "test-gemini-key")
    monkeypatch.setenv("Cloudflare_key", "test-cloudflare-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", account_id)
    user_token = agent_tools.active_user_id.set("fallback-image-user")
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2), color="blue").save(image_bytes, format="PNG")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if url == agent_tools.GEMINI_IMAGE_URL:
            return _provider_response(
                url, gemini_payload, status_code=gemini_status_code
            )
        return _provider_response(
            url,
            {
                "success": True,
                "result": {"image": base64.b64encode(image_bytes.getvalue()).decode()},
            },
        )

    monkeypatch.setattr(agent_tools.httpx, "post", fake_post)
    monkeypatch.setattr(agent_tools, "emit_tool_event", lambda *args, **kwargs: None)
    try:
        result = agent_tools.generate_image.func("A blue cat")
    finally:
        agent_tools.active_user_id.reset(user_token)

    assert result.startswith(agent_tools.GENERATED_IMAGE_MARKER)
    assert [call[0] for call in calls] == [
        agent_tools.GEMINI_IMAGE_URL,
        agent_tools.CLOUDFLARE_IMAGE_URL.format(
            account_id=account_id,
            model=agent_tools.CLOUDFLARE_IMAGE_MODEL,
        ),
    ]
    assert calls[1][1]["headers"]["Authorization"] == "Bearer test-cloudflare-token"
    assert calls[1][1]["json"] == {"prompt": "A blue cat"}
    image_path = (
        agent_tools.get_generated_image_dir("fallback-image-user")
        / result.removeprefix(agent_tools.GENERATED_IMAGE_MARKER)
    )
    with Image.open(image_path) as saved:
        assert saved.size == (2, 2)


def test_generate_image_does_not_fallback_for_gemini_bad_request(
    image_api, monkeypatch
):
    _, _, agent_tools = image_api

    monkeypatch.setenv("Gemini_API_Key", "test-gemini-key")
    monkeypatch.setenv("Cloudflare_key", "test-cloudflare-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "d" * 32)
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _provider_response(url, {}, status_code=400)

    monkeypatch.setattr(agent_tools.httpx, "post", fake_post)
    monkeypatch.setattr(agent_tools, "emit_tool_event", lambda *args, **kwargs: None)
    user_token = agent_tools.active_user_id.set("bad-request-user")
    try:
        result = agent_tools.generate_image.func("A cat")
    finally:
        agent_tools.active_user_id.reset(user_token)

    assert calls == [agent_tools.GEMINI_IMAGE_URL]
    assert result == "Image generation error: image service returned status 400."


def test_generate_image_reports_missing_cloudflare_account_id(
    image_api, monkeypatch
):
    _, _, agent_tools = image_api
    monkeypatch.setenv("Gemini_API_Key", "test-gemini-key")
    monkeypatch.setenv("Cloudflare_key", "test-cloudflare-token")
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(
        agent_tools.httpx,
        "post",
        lambda url, **kwargs: _provider_response(url, {}, status_code=429),
    )
    monkeypatch.setattr(agent_tools, "emit_tool_event", lambda *args, **kwargs: None)
    user_token = agent_tools.active_user_id.set("missing-account-id-user")
    try:
        result = agent_tools.generate_image.func("A cat")
    finally:
        agent_tools.active_user_id.reset(user_token)

    assert "requires CLOUDFLARE_ACCOUNT_ID" in result


def test_generate_image_returns_plain_message_when_both_providers_fail(
    image_api, monkeypatch
):
    _, _, agent_tools = image_api

    account_id = "b" * 32
    monkeypatch.setenv("Gemini_API_Key", "test-gemini-key")
    monkeypatch.setenv("Cloudflare_key", "test-cloudflare-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", account_id)
    user_token = agent_tools.active_user_id.set("both-providers-fail-user")
    requested_urls = []

    def fake_post(url, **kwargs):
        requested_urls.append(url)
        return _provider_response(url, {}, status_code=429 if len(requested_urls) == 1 else 503)

    monkeypatch.setattr(agent_tools.httpx, "post", fake_post)
    monkeypatch.setattr(agent_tools, "emit_tool_event", lambda *args, **kwargs: None)

    try:
        result = agent_tools.generate_image.func("A cat")
    finally:
        agent_tools.active_user_id.reset(user_token)

    assert requested_urls == [
        agent_tools.GEMINI_IMAGE_URL,
        agent_tools.CLOUDFLARE_IMAGE_URL.format(
            account_id=account_id,
            model=agent_tools.CLOUDFLARE_IMAGE_MODEL,
        ),
    ]
    assert result == "Image generation error: image service returned status 503."


def test_generate_image_uses_cloudflare_when_gemini_key_is_missing(
    image_api, monkeypatch
):
    _, _, agent_tools = image_api
    import base64
    from io import BytesIO
    from PIL import Image

    account_id = "c" * 32
    monkeypatch.setenv("Cloudflare_key", "test-cloudflare-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", account_id)
    user_token = agent_tools.active_user_id.set("cloudflare-only-user")
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2), color="green").save(image_bytes, format="JPEG")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _provider_response(
            url,
            {
                "success": True,
                "result": {"image": base64.b64encode(image_bytes.getvalue()).decode()},
            },
        )

    monkeypatch.setattr(agent_tools.httpx, "post", fake_post)
    monkeypatch.setattr(agent_tools, "emit_tool_event", lambda *args, **kwargs: None)
    try:
        result = agent_tools.generate_image.func("A green cat")
    finally:
        agent_tools.active_user_id.reset(user_token)

    assert result.startswith(agent_tools.GENERATED_IMAGE_MARKER)
    assert len(calls) == 1
    assert calls[0][0] == agent_tools.CLOUDFLARE_IMAGE_URL.format(
        account_id=account_id,
        model=agent_tools.CLOUDFLARE_IMAGE_MODEL,
    )
    assert calls[0][1]["json"] == {"prompt": "A green cat"}
    saved_path = (
        agent_tools.get_generated_image_dir("cloudflare-only-user")
        / result.removeprefix(agent_tools.GENERATED_IMAGE_MARKER)
    )
    with Image.open(saved_path) as saved:
        assert saved.size == (2, 2)


def test_generate_image_without_provider_keys_returns_configuration_error(
    image_api, monkeypatch
):
    _, _, agent_tools = image_api
    monkeypatch.setattr(
        agent_tools.httpx,
        "post",
        lambda *args, **kwargs: pytest.fail("HTTP request should not be made without a key"),
    )
    monkeypatch.setattr(agent_tools, "emit_tool_event", lambda *args, **kwargs: None)

    user_token = agent_tools.active_user_id.set("no-provider-user")
    try:
        result = agent_tools.generate_image.func("A cat")
    finally:
        agent_tools.active_user_id.reset(user_token)

    assert result == (
        "Image generation error: RuntimeError: Image generation is not configured. "
        "Set Gemini_API_Key or configure Cloudflare Workers AI."
    )


@pytest.mark.parametrize(
    ("prompt", "expected_status"),
    [("   ", 400), ("x" * 501, 422)],
)
def test_image_generation_rejects_invalid_prompt(image_api, prompt, expected_status):
    client, _, _ = image_api
    token = _create_account(client, f"invalid-{len(prompt)}@example.com")
    response = client.post(
        "/images/generate",
        headers={"Authorization": f"Bearer {token}"},
        json={"prompt": prompt},
    )
    assert response.status_code == expected_status
