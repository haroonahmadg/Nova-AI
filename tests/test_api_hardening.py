import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    monkeypatch.setenv("UI_DB_PATH", str(tmp_path / "ui.sqlite"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "upload"))
    monkeypatch.setenv(
        "SESSION_SECRET",
        "test-only-session-secret-that-is-long-enough-123456",
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(tmp_path / "checkpoints.sqlite"))
    import database.db as database_module

    monkeypatch.setattr(database_module, "DB_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        database_module, "DB_PATH", str(tmp_path / "data" / "assistant.db")
    )
    import api.main as api_main

    importlib.reload(api_main)
    with TestClient(api_main.app) as client:
        yield client, api_main


def _auth_headers(client):
    response = client.post(
        "/auth/register",
        json={"email": "hardening@example.com", "password": "Password123"},
    )
    assert response.status_code == 200
    token = response.json()["token"]
    return {"Authorization": "Bearer " + token}


def test_speak_request_limits_text_length():
    from api.schemas import SpeakRequest

    assert len(SpeakRequest(text="x" * 5000).text) == 5000
    with pytest.raises(ValidationError):
        SpeakRequest(text="x" * 5001)


def test_thread_list_returns_server_error_when_checkpoint_store_fails(
    api_client, monkeypatch
):
    client, api_main = api_client
    monkeypatch.setattr(api_main, "verify_session_token", lambda _token: "test-user")

    def fail_listing(_user_id, *, raise_errors=False):
        raise OSError("database unavailable")

    monkeypatch.setattr(api_main, "get_all_saved_threads", fail_listing)
    response = client.get(
        "/threads", headers={"Authorization": "Bearer test-token"}
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "Could not load conversations right now."
    assert "database unavailable" not in response.text


def test_thread_delete_does_not_report_success_when_ownership_check_fails(
    api_client, monkeypatch
):
    client, api_main = api_client
    headers = _auth_headers(client)

    def fail_listing(_user_id, *, raise_errors=False):
        raise OSError("database unavailable")

    monkeypatch.setattr(api_main, "get_all_saved_threads", fail_listing)
    response = client.delete("/threads/thread-1", headers=headers)

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Could not verify the conversation before deleting it."
    )


def test_thread_history_failure_is_returned_as_server_error(api_client, monkeypatch):
    client, api_main = api_client
    headers = _auth_headers(client)

    def fail_history(**_kwargs):
        raise OSError("checkpoint unavailable")

    monkeypatch.setattr(api_main, "get_thread_history", fail_history)
    response = client.get("/threads/thread-1/messages", headers=headers)

    assert response.status_code == 500
    assert response.json()["detail"] == (
        "Could not load conversation history right now."
    )
    assert "checkpoint unavailable" not in response.text


def test_api_startup_initializes_long_term_memory_database(api_client):
    from database.db import get_memories, save_memory

    assert save_memory("startup_probe", "ready", "test-user")
    assert dict(get_memories("test-user"))["startup_probe"] == "ready"


def test_deleted_thread_history_is_not_returned_when_cleanup_fails(
    api_client, monkeypatch
):
    client, api_main = api_client
    headers = _auth_headers(client)
    user_id = api_main.verify_session_token(headers["Authorization"].removeprefix("Bearer "))

    from api.store import mark_thread_deleted

    mark_thread_deleted("thread-1", user_id)
    monkeypatch.setattr(
        api_main,
        "get_thread_history",
        lambda **_kwargs: pytest.fail("Deleted conversation history was read."),
    )
    monkeypatch.setattr(
        api_main,
        "get_all_saved_threads",
        lambda *_args, **_kwargs: pytest.fail("Deleted conversation was exported."),
    )
    monkeypatch.setattr(
        api_main,
        "remove_last_turn",
        lambda **_kwargs: pytest.fail("Deleted conversation was edited."),
    )
    monkeypatch.setattr(
        api_main,
        "stream_agent",
        lambda **_kwargs: pytest.fail("Deleted conversation was restarted."),
    )

    response = client.get("/threads/thread-1/messages", headers=headers)
    assert response.status_code == 404
    response = client.get("/threads/thread-1/export.md", headers=headers)
    assert response.status_code == 404
    response = client.post("/threads/thread-1/edit", headers=headers)
    assert response.status_code == 404
    response = client.post(
        "/threads/thread-1/messages/stream",
        headers=headers,
        json={"content": "This deleted conversation must not restart."},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Conversation not found."


def test_checkpoint_deletion_uses_user_namespaced_thread_id(api_client, monkeypatch):
    _, _ = api_client
    import api.store as store

    checkpoint_path = store.CHECKPOINT_DB_PATH
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.touch()
    deleted_ids = []

    class FakeCheckpointer:
        def delete_thread(self, thread_id):
            deleted_ids.append(thread_id)

    class FakeConnection:
        def close(self):
            pass

    monkeypatch.setattr(
        store,
        "create_checkpointer",
        lambda: (FakeCheckpointer(), FakeConnection()),
    )

    deleted, warning = store.delete_thread("thread-123", "user-456")
    assert deleted is True
    assert warning is None
    assert deleted_ids == ["user-456:thread-123"]


def test_upload_rejects_oversized_content_length_before_file_read(
    api_client, monkeypatch
):
    client, api_main = api_client
    monkeypatch.setattr(api_main, "verify_session_token", lambda _token: "test-user")
    headers = {"Authorization": "Bearer test-token"}
    max_bytes = int(
        (api_main.MAX_FILE_MB * api_main.MAX_UPLOAD_FILES + 1) * 1024 * 1024
    )
    headers["Content-Length"] = str(max_bytes + 1)

    response = client.post(
        "/files/upload",
        headers=headers,
        data={"thread_id": "thread-1"},
        files=[("files", ("small.txt", b"small", "text/plain"))],
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "The total upload size exceeds the allowed limit."


def test_oversized_file_is_read_only_to_limit_plus_one(api_client, monkeypatch):
    client, api_main = api_client
    monkeypatch.setattr(api_main, "verify_session_token", lambda _token: "test-user")
    monkeypatch.setattr(api_main, "MAX_FILE_MB", 4 / (1024 * 1024))

    read_limits = []
    original_read = api_main._read_limited_upload

    async def record_read_limit(upload, max_bytes):
        read_limits.append(max_bytes)
        return await original_read(upload, max_bytes)

    monkeypatch.setattr(api_main, "_read_limited_upload", record_read_limit)
    response = client.post(
        "/files/upload",
        headers={"Authorization": "Bearer test-token"},
        data={"thread_id": "thread-1"},
        files=[("files", ("large.txt", b"0123456789", "text/plain"))],
    )

    assert response.status_code == 200
    assert "exceeds maximum limit" in response.json()[0]["error"]
    assert read_limits == [4]


def test_chat_stream_rejects_oversized_json_before_parsing(api_client, monkeypatch):
    client, api_main = api_client
    monkeypatch.setattr(api_main, "verify_session_token", lambda _token: "test-user")
    monkeypatch.setattr(api_main, "_max_send_message_request_bytes", lambda: 16)

    response = client.post(
        "/threads/thread-1/messages/stream",
        headers={"Authorization": "Bearer test-token"},
        content=b'{"content":"this is too long"}',
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "The request body exceeds the allowed limit."


def test_chat_stream_preserves_validation_errors(api_client, monkeypatch):
    client, api_main = api_client
    monkeypatch.setattr(api_main, "verify_session_token", lambda _token: "test-user")

    response = client.post(
        "/threads/thread-1/messages/stream",
        headers={"Authorization": "Bearer test-token"},
        content=b"{invalid json",
    )

    assert response.status_code == 422


def test_oversized_audio_is_read_only_to_limit_plus_one(api_client, monkeypatch):
    client, api_main = api_client
    monkeypatch.setattr(api_main, "verify_session_token", lambda _token: "test-user")
    monkeypatch.setattr(api_main, "MAX_AUDIO_BYTES", 4)

    read_limits = []
    original_read = api_main._read_limited_upload

    async def record_read_limit(upload, max_bytes):
        read_limits.append(max_bytes)
        return await original_read(upload, max_bytes)

    monkeypatch.setattr(api_main, "_read_limited_upload", record_read_limit)
    response = client.post(
        "/voice/transcribe",
        headers={"Authorization": "Bearer test-token"},
        files={"audio": ("recording.webm", b"0123456789", "audio/webm")},
    )

    assert response.status_code == 413
    assert f"{4 / (1024 * 1024):g} MB limit" in response.json()["detail"]
    assert read_limits == [4]


def test_oversized_audio_request_is_rejected_before_multipart_parsing(
    api_client, monkeypatch
):
    client, api_main = api_client
    monkeypatch.setattr(api_main, "verify_session_token", lambda _token: "test-user")
    headers = {
        "Authorization": "Bearer test-token",
        "Content-Length": str(api_main.MAX_AUDIO_REQUEST_BYTES + 1),
        "Content-Type": "multipart/form-data; boundary=invalid",
    }

    response = client.post(
        "/voice/transcribe",
        headers=headers,
        content=b"not a multipart body",
    )

    assert response.status_code == 413
    assert response.json()["detail"] == (
        "The audio request exceeds the maximum allowed size."
    )


def test_chunked_oversized_audio_is_bounded_before_multipart_parsing(
    api_client, monkeypatch
):
    _, api_main = api_client
    app_called = False
    sent = []
    extend_calls = []

    class TrackingBuffer(bytearray):
        def extend(self, chunk):
            extend_calls.append(bytes(chunk))
            super().extend(chunk)

    monkeypatch.setattr(api_main, "bytearray", TrackingBuffer, raising=False)

    async def downstream(_scope, _receive, _send):
        nonlocal app_called
        app_called = True

    chunks = iter([
        {"type": "http.request", "body": b"123456", "more_body": False},
    ])

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    middleware = api_main.AudioRequestBodyLimitMiddleware(downstream, max_bytes=5)
    asyncio.run(middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/voice/transcribe",
            "headers": [],
        },
        receive,
        send,
    ))

    assert not app_called
    assert sent[0]["status"] == 413
    assert b"maximum allowed size" in sent[1]["body"]
    assert extend_calls == []


def test_chunked_oversized_file_upload_is_rejected_without_content_length(
    api_client,
):
    _, api_main = api_client
    app_called = False
    sent = []

    async def downstream(_scope, receive, _send):
        nonlocal app_called
        app_called = True
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if not message.get("more_body", False):
                return

    chunks = iter(
        [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
    )

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    middleware = api_main.UploadRequestBodyLimitMiddleware(
        downstream,
        max_bytes=5,
    )
    asyncio.run(
        middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/files/upload",
                "headers": [],
            },
            receive,
            send,
        )
    )

    assert app_called
    assert sent[0]["status"] == 413
    assert b"total upload size" in sent[1]["body"]
