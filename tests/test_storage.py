from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
from pathlib import Path

import pytest

from utils.storage import (
    UserStorageLimitExceeded,
    legacy_flat_upload_roots,
    reserve_user_storage,
    user_upload_dir,
)


def _reserve_and_write(
    user_id: str,
    destination: Path,
    *,
    upload_dir: Path,
    generated_dir: Path,
    lock_dir: Path,
    max_bytes: int,
):
    try:
        with reserve_user_storage(
            user_id,
            upload_dir=upload_dir,
            generated_image_dir=generated_dir,
            lock_dir=lock_dir,
            max_bytes=max_bytes,
            requested_bytes=1,
        ):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"x")
    except UserStorageLimitExceeded:
        return False
    return True


def test_storage_quota_combines_uploads_and_generated_images_by_account(tmp_path):
    upload_dir = tmp_path / "uploads"
    generated_dir = tmp_path / "generated"
    lock_dir = tmp_path / "locks"
    user_upload = upload_dir / "user-a" / "thread"
    user_image = generated_dir / hashlib.sha256(b"user-a").hexdigest()
    user_upload.mkdir(parents=True)
    user_image.mkdir(parents=True)
    (user_upload / "document.txt").write_bytes(b"x")
    (user_image / "image.png").write_bytes(b"x")

    with pytest.raises(UserStorageLimitExceeded):
        with reserve_user_storage(
            "user-a",
            upload_dir=upload_dir,
            generated_image_dir=generated_dir,
            lock_dir=lock_dir,
            max_bytes=4096,
            requested_bytes=1,
        ):
            pytest.fail("The account should already be at its storage cap.")

    with reserve_user_storage(
        "user-b",
        upload_dir=upload_dir,
        generated_image_dir=generated_dir,
        lock_dir=lock_dir,
        max_bytes=4096,
        requested_bytes=1,
    ):
        pass


def test_storage_quota_check_and_write_are_atomic_across_threads(tmp_path):
    upload_dir = tmp_path / "uploads"
    generated_dir = tmp_path / "generated"
    lock_dir = tmp_path / "locks"

    def write(index: int) -> bool:
        return _reserve_and_write(
            "shared-user",
            upload_dir / "shared-user" / f"thread-{index}" / "file.txt",
            upload_dir=upload_dir,
            generated_dir=generated_dir,
            lock_dir=lock_dir,
            max_bytes=4096,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, (1, 2)))

    assert results.count(True) == 1
    assert results.count(False) == 1


def test_legacy_streamlit_uploads_are_counted_without_charging_api_accounts(
    tmp_path,
):
    upload_dir = tmp_path / "uploads"
    generated_dir = tmp_path / "generated"
    legacy_file = upload_dir / "legacy-thread" / "old.txt"
    api_file = upload_dir / "api-user" / "thread" / "document.txt"
    legacy_file.parent.mkdir(parents=True)
    api_file.parent.mkdir(parents=True)
    legacy_file.write_bytes(b"old")
    api_file.write_bytes(b"api")
    excluded = user_upload_dir(upload_dir, "local")
    legacy_roots = legacy_flat_upload_roots(upload_dir, excluded=excluded)

    with pytest.raises(UserStorageLimitExceeded):
        with reserve_user_storage(
            "local",
            upload_dir=upload_dir,
            generated_image_dir=generated_dir,
            lock_dir=tmp_path / "locks",
            max_bytes=4096,
            requested_bytes=1,
            additional_roots=legacy_roots,
        ):
            pytest.fail("Legacy local uploads must count toward the local quota.")

    with reserve_user_storage(
        "api-user",
        upload_dir=upload_dir,
        generated_image_dir=generated_dir,
        lock_dir=tmp_path / "locks",
        max_bytes=8192,
        requested_bytes=1,
    ):
        pass


def test_legacy_upload_roots_are_discovered_under_storage_lock(tmp_path):
    upload_dir = tmp_path / "uploads"
    generated_dir = tmp_path / "generated"
    lock_dir = tmp_path / "locks"
    legacy_file = upload_dir / "legacy-thread" / "first.txt"
    first_reserved = threading.Event()
    second_started = threading.Event()

    def first_upload() -> None:
        with reserve_user_storage(
            "local",
            upload_dir=upload_dir,
            generated_image_dir=generated_dir,
            lock_dir=lock_dir,
            max_bytes=4096,
            requested_bytes=1,
        ):
            first_reserved.set()
            legacy_file.parent.mkdir(parents=True)
            legacy_file.write_bytes(b"first")

    def second_upload() -> bool:
        second_started.set()
        try:
            with reserve_user_storage(
                "local",
                upload_dir=upload_dir,
                generated_image_dir=generated_dir,
                lock_dir=lock_dir,
                max_bytes=4096,
                requested_bytes=1,
                additional_roots=lambda: legacy_flat_upload_roots(
                    upload_dir,
                    excluded=user_upload_dir(upload_dir, "local"),
                ),
            ):
                return True
        except UserStorageLimitExceeded:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_upload)
        assert first_reserved.wait(timeout=2)
        second = executor.submit(second_upload)
        assert second_started.wait(timeout=2)
        first.result(timeout=2)
        assert second.result(timeout=2) is False
