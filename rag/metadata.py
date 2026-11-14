from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any


def load_metadata_registry(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Load and validate the chunk metadata registry shared by RAG operations."""

    if not os.path.exists(path):
        return []

    try:
        with open(path, "rb") as metadata_file:
            data = pickle.load(metadata_file)
    except Exception as exc:
        raise RuntimeError(f"Could not read RAG metadata registry: {exc}") from exc

    if not isinstance(data, list) or not all(
        isinstance(entry, dict) for entry in data
    ):
        raise RuntimeError("RAG metadata registry must contain a list of objects.")

    return data
