from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from filelock import FileLock


_MIN_FILE_ALLOCATION_BYTES = 4096


class UserStorageLimitExceeded(RuntimeError):
    """The user's persisted uploads and generated images exceed their quota."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        limit_mb = limit_bytes / (1024 * 1024)
        super().__init__(
            f"Your account storage limit of {limit_mb:g} MB would be exceeded."
        )


def safe_storage_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", value or "").strip("_")[:100]
    return cleaned or "unknown"


def user_upload_dir(upload_dir: Path, user_id: str) -> Path:
    return upload_dir / safe_storage_component(user_id)


def legacy_flat_upload_roots(upload_dir: Path, *, excluded: Path) -> tuple[Path, ...]:
    """Find pre-account-scoped Streamlit upload folders without charging API users."""

    if not upload_dir.exists():
        return ()

    return tuple(
        directory
        for directory in upload_dir.iterdir()
        if directory != excluded
        and not directory.is_symlink()
        and directory.is_dir()
        and any(
            item.is_file() and not item.is_symlink()
            for item in directory.iterdir()
        )
    )


def _account_storage_usage(roots: tuple[Path, ...]) -> int:
    total = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            total += max(path.stat().st_size, _MIN_FILE_ALLOCATION_BYTES)
    return total


@contextmanager
def reserve_user_storage(
    user_id: str,
    *,
    upload_dir: Path,
    generated_image_dir: Path,
    lock_dir: Path,
    max_bytes: int,
    requested_bytes: int,
    additional_roots: tuple[Path, ...] | Callable[[], tuple[Path, ...]] = (),
) -> Iterator[None]:
    """Serialize a user's storage check and write across worker processes."""

    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("A valid user_id is required.")
    if max_bytes <= 0 or requested_bytes < 0:
        raise ValueError("Storage limits must be positive and requested bytes non-negative.")

    user_key = hashlib.sha256(user_id.strip().encode("utf-8")).hexdigest()
    lock_path = lock_dir / f"{user_key}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(str(lock_path), timeout=30):
        extra_roots = (
            additional_roots()
            if callable(additional_roots)
            else additional_roots
        )
        roots = (
            user_upload_dir(upload_dir, user_id),
            generated_image_dir / user_key,
            *extra_roots,
        )
        current_bytes = _account_storage_usage(roots)
        allocated_bytes = (
            max(requested_bytes, _MIN_FILE_ALLOCATION_BYTES)
            if requested_bytes
            else 0
        )
        if current_bytes + allocated_bytes > max_bytes:
            raise UserStorageLimitExceeded(max_bytes)
        yield
