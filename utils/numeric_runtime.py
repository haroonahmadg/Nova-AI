"""Runtime safeguards for numerical libraries that initialize at import time."""

from __future__ import annotations

import os


def configure_numeric_thread_limits() -> None:
    """Avoid OpenBLAS allocating per-thread resources across all host CPUs."""

    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
