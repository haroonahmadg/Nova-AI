import importlib
import os
import sys

import pytest

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.pop("REDIS_URL", None)

_ENVIRONMENT_MODULES = (
    "utils.limits",
    "utils.security",
    "database.db",
    "rag.paths",
    "rag.search",
    "rag.ingest",
    "agent.tools",
    "agent.graph",
    "agent.voice",
    "api.config",
    "api.store",
    "api.auth",
    "api.rate_limit",
    "api.streaming",
    "api.main",
)


@pytest.fixture(autouse=True)
def reset_test_state(monkeypatch, tmp_path_factory):
    # Give each test isolated storage and refresh imported configuration in place.
    test_dir = tmp_path_factory.mktemp("catgpt-test")
    data_dir = test_dir / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("UI_DB_PATH", str(test_dir / "ui.sqlite"))
    monkeypatch.setenv("UPLOAD_DIR", str(test_dir / "upload"))
    monkeypatch.setenv(
        "SESSION_SECRET",
        "test-only-session-secret-that-is-long-enough-123456",
    )
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("CHECKPOINT_DB_PATH", str(test_dir / "checkpoints.sqlite"))

    for module_name in _ENVIRONMENT_MODULES:
        module = sys.modules.get(module_name)
        if module is not None:
            importlib.reload(module)

    yield
