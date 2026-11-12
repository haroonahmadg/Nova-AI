import hashlib
import os
import pytest
import rag.ingest as ingest_module
from langchain_core.embeddings import Embeddings
from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
import rag.search as search_module
from rag.ingest import (
    ingest_file,
    ingest_multiple_files,
    search_documents,
    list_indexed_documents,
    delete_document,
    delete_indexed_source,
    FAISS_DIR,
)


class _TestEmbeddings(Embeddings):
    def _embed(self, text):
        vector = [0.0] * 128
        for token in text.lower().split():
            index = int.from_bytes(
                hashlib.sha256(token.encode("utf-8")).digest()[:4],
                "big",
            ) % len(vector)
            vector[index] += 1.0
        return vector

    def embed_documents(self, texts):
        return [self._embed(text) for text in texts]

    def embed_query(self, text):
        return self._embed(text)


@pytest.fixture(autouse=True)
def setup_rag_dir(tmp_path, monkeypatch):
    test_faiss_dir = str(tmp_path / "faiss")
    monkeypatch.setattr("rag.ingest.FAISS_DIR", test_faiss_dir)
    monkeypatch.setattr(
        "rag.ingest.FAISS_LOCK_PATH",
        os.path.join(test_faiss_dir, ".faiss.lock"),
    )
    monkeypatch.setattr("rag.ingest.INDEX_PATH", os.path.join(test_faiss_dir, "index.faiss"))
    monkeypatch.setattr("rag.ingest.METADATA_PATH", os.path.join(test_faiss_dir, "metadata.pkl"))
    monkeypatch.setattr(
        ingest_module,
        "HuggingFaceEmbeddings",
        lambda **_: _TestEmbeddings(),
    )
    ingest_module.get_embeddings.cache_clear()
    yield
    ingest_module.get_embeddings.cache_clear()


def test_ingest_and_search_single_file(tmp_path):
    sample_file = tmp_path / "data" / "knowledge.txt"
    sample_file.parent.mkdir(parents=True, exist_ok=True)
    sample_file.write_text("Project Alpha code name is Omega-99. System port is 9090.")

    chunks = ingest_file(str(sample_file))
    assert chunks > 0

    results = search_documents("What is the system port?", k=1)
    assert len(results) > 0
    assert "9090" in results[0].page_content


def test_ingest_multiple_files(tmp_path):
    dir_path = tmp_path / "data"
    dir_path.mkdir(parents=True, exist_ok=True)

    file1 = dir_path / "doc1.txt"
    file2 = dir_path / "doc2.txt"

    file1.write_text("Python is a dynamic programming language.")
    file2.write_text("SQLite is an embedded relational database.")

    summary = ingest_multiple_files([str(file1), str(file2)])
    assert len(summary["successful"]) == 2
    assert len(summary["failed"]) == 0

    indexed_docs = list_indexed_documents()
    assert len(indexed_docs) == 2


def test_delete_document_and_rebuild(tmp_path):
    dir_path = tmp_path / "data"
    dir_path.mkdir(parents=True, exist_ok=True)

    file1 = dir_path / "keep.txt"
    file2 = dir_path / "remove.txt"

    file1.write_text("Keep this document content in FAISS index.")
    file2.write_text("Remove this document content from index completely.")

    ingest_multiple_files([str(file1), str(file2)])

    removed = delete_document("remove.txt")
    assert removed is True

    indexed = list_indexed_documents()
    sources = [item["source"] for item in indexed]

    assert "keep.txt" in sources
    assert "remove.txt" not in sources


@pytest.mark.parametrize(
    ("delete_source", "source_to_remove"),
    [
        (delete_document, "remove.txt"),
        (delete_indexed_source, "https://example.com/remove"),
    ],
)
def test_rebuilding_index_preserves_user_metadata(
    delete_source, source_to_remove, monkeypatch
):
    faiss_dir = ingest_module.FAISS_DIR
    os.makedirs(faiss_dir, exist_ok=True)
    embeddings = _TestEmbeddings()
    documents = [
        Document(
            page_content="Alice's retained document",
            metadata={
                "source": "keep.txt",
                "page": 0,
                "chunk_id": 0,
                "user_id": "alice",
            },
        ),
        Document(
            page_content="Bob's document to remove",
            metadata={
                "source": source_to_remove,
                "page": 0,
                "chunk_id": 0,
                "user_id": "bob",
            },
        ),
    ]
    FAISS.from_documents(documents, embeddings).save_local(faiss_dir)
    ingest_module._save_metadata_registry(
        [
            {
                **document.metadata,
                "text": document.page_content,
            }
            for document in documents
        ]
    )
    monkeypatch.setattr(
        ingest_module,
        "_get_embeddings_with_progress",
        lambda **_kwargs: embeddings,
    )
    monkeypatch.setattr(search_module, "FAISS_DIR", faiss_dir)
    monkeypatch.setattr(
        search_module,
        "HuggingFaceEmbeddings",
        lambda **_kwargs: embeddings,
    )

    assert delete_source(source_to_remove) is True

    results = search_module.search_pdf(
        "Alice retained",
        k=1,
        source_filter=["keep.txt"],
        user_id="alice",
    )
    assert len(results) == 1
    assert results[0]["metadata"]["user_id"] == "alice"


def test_index_update_rolls_back_all_files_when_metadata_registration_fails(
    monkeypatch,
):
    faiss_dir = ingest_module.FAISS_DIR
    os.makedirs(faiss_dir, exist_ok=True)
    index_path = ingest_module.INDEX_PATH
    index_pickle_path = os.path.join(faiss_dir, "index.pkl")
    metadata_path = ingest_module.METADATA_PATH
    original_contents = {
        index_path: b"original-index",
        index_pickle_path: b"original-docstore",
        metadata_path: b"original-metadata",
    }
    for path, contents in original_contents.items():
        with open(path, "wb") as file:
            file.write(contents)

    def write_partial_index(*_args, **_kwargs):
        with open(index_path, "wb") as file:
            file.write(b"updated-index")
        with open(index_pickle_path, "wb") as file:
            file.write(b"updated-docstore")

    def fail_metadata_registration(*_args, **_kwargs):
        with open(metadata_path, "wb") as file:
            file.write(b"partial-metadata")
        raise OSError("metadata write failed")

    monkeypatch.setattr(ingest_module, "_save_chunks_to_faiss", write_partial_index)
    monkeypatch.setattr(
        ingest_module,
        "_register_chunks",
        fail_metadata_registration,
    )

    with pytest.raises(OSError, match="metadata write failed"):
        ingest_module._index_chunks_locked("sample.txt", [], _TestEmbeddings())

    for path, contents in original_contents.items():
        with open(path, "rb") as file:
            assert file.read() == contents