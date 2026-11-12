import pickle

import pytest
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_core.documents import Document

import rag.ingest as ingest


@pytest.fixture
def rag_store(tmp_path, monkeypatch):
    faiss_dir = tmp_path / "faiss"
    faiss_dir.mkdir()
    metadata_path = faiss_dir / "metadata.pkl"
    index_pickle_path = faiss_dir / "index.pkl"
    index_path = faiss_dir / "index.faiss"

    monkeypatch.setattr(ingest, "FAISS_DIR", str(faiss_dir))
    monkeypatch.setattr(ingest, "METADATA_PATH", str(metadata_path))
    monkeypatch.setattr(ingest, "INDEX_PATH", str(index_path))
    monkeypatch.setattr(ingest, "FAISS_LOCK_PATH", str(faiss_dir / ".faiss.lock"))

    yield metadata_path, index_pickle_path, index_path


def test_assign_legacy_user_updates_registry_and_faiss_documents(rag_store):
    metadata_path, index_pickle_path, index_path = rag_store
    legacy_document = Document(
        page_content="Legacy content",
        metadata={"source": "legacy.txt", "page": 0, "chunk_id": 0},
    )
    existing_document = Document(
        page_content="Already scoped content",
        metadata={
            "source": "scoped.txt",
            "page": 0,
            "chunk_id": 0,
            "user_id": "other-user",
        },
    )
    docstore = InMemoryDocstore(
        {"legacy-doc": legacy_document, "scoped-doc": existing_document}
    )
    index_to_docstore_id = {0: "legacy-doc", 1: "scoped-doc"}

    with metadata_path.open("wb") as file:
        pickle.dump(
            [
                {
                    "source": "legacy.txt",
                    "page": 0,
                    "chunk_id": 0,
                    "text": "Legacy content",
                },
                {
                    "source": "scoped.txt",
                    "page": 0,
                    "chunk_id": 0,
                    "text": "Already scoped content",
                    "user_id": "other-user",
                },
            ],
            file,
        )
    with index_pickle_path.open("wb") as file:
        pickle.dump((docstore, index_to_docstore_id), file)
    index_path.touch()

    assert ingest.assign_legacy_user_to_chunks("target-user") == 1

    with metadata_path.open("rb") as file:
        registry = pickle.load(file)
    with index_pickle_path.open("rb") as file:
        migrated_docstore, _ = pickle.load(file)

    assert registry[0]["user_id"] == "target-user"
    assert registry[1]["user_id"] == "other-user"
    assert migrated_docstore.search("legacy-doc").metadata["user_id"] == "target-user"
    assert migrated_docstore.search("scoped-doc").metadata["user_id"] == "other-user"
    assert ingest.assign_legacy_user_to_chunks("target-user") == 0


def test_assign_legacy_user_rejects_mismatched_faiss_index(rag_store):
    metadata_path, index_pickle_path, index_path = rag_store
    docstore = InMemoryDocstore(
        {
            "other-doc": Document(
                page_content="Different content",
                metadata={"source": "legacy.txt", "page": 0, "chunk_id": 0},
            )
        }
    )
    with metadata_path.open("wb") as file:
        pickle.dump(
            [
                {
                    "source": "legacy.txt",
                    "page": 0,
                    "chunk_id": 0,
                    "text": "Expected content",
                }
            ],
            file,
        )
    with index_pickle_path.open("wb") as file:
        pickle.dump((docstore, {0: "other-doc"}), file)
    index_path.touch()

    with pytest.raises(RuntimeError, match="do not match"):
        ingest.assign_legacy_user_to_chunks("target-user")

    with metadata_path.open("rb") as file:
        assert pickle.load(file)[0].get("user_id") is None


def test_assign_legacy_user_prefers_unassigned_duplicate_documents(rag_store):
    metadata_path, index_pickle_path, index_path = rag_store
    identity = {
        "source": "duplicate.txt",
        "page": 0,
        "chunk_id": 0,
    }
    docstore = InMemoryDocstore(
        {
            "assigned": Document(
                page_content="Repeated content",
                metadata={**identity, "user_id": "target-user"},
            ),
            "unassigned-1": Document(
                page_content="Repeated content",
                metadata=dict(identity),
            ),
            "unassigned-2": Document(
                page_content="Repeated content",
                metadata=dict(identity),
            ),
        }
    )
    with metadata_path.open("wb") as file:
        pickle.dump(
            [
                {**identity, "text": "Repeated content"},
                {**identity, "text": "Repeated content"},
            ],
            file,
        )
    with index_pickle_path.open("wb") as file:
        pickle.dump((docstore, {0: "assigned", 1: "unassigned-1", 2: "unassigned-2"}), file)
    index_path.touch()

    assert ingest.assign_legacy_user_to_chunks("target-user") == 2

    with index_pickle_path.open("rb") as file:
        migrated_docstore, _ = pickle.load(file)
    assert migrated_docstore.search("unassigned-1").metadata["user_id"] == "target-user"
    assert migrated_docstore.search("unassigned-2").metadata["user_id"] == "target-user"


def test_assign_legacy_user_rejects_invalid_user_id(rag_store):
    with pytest.raises(ValueError, match="user_id"):
        ingest.assign_legacy_user_to_chunks(" ")
