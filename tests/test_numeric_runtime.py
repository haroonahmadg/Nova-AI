import os

from utils.numeric_runtime import configure_numeric_thread_limits


def test_numeric_runtime_sets_default_openblas_thread_limit(monkeypatch):
    monkeypatch.delenv("OPENBLAS_NUM_THREADS", raising=False)

    configure_numeric_thread_limits()

    assert os.environ["OPENBLAS_NUM_THREADS"] == "1"


def test_numeric_runtime_preserves_explicit_openblas_setting(monkeypatch):
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "2")

    configure_numeric_thread_limits()

    assert os.environ["OPENBLAS_NUM_THREADS"] == "2"
