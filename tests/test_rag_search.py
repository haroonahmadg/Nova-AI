from types import SimpleNamespace

import pytest

import rag.search as rag_search
import rag.ingest as rag_ingest


def test_search_pdf_expands_candidates_until_user_documents_are_found(
    tmp_path, monkeypatch
):
    faiss_dir = tmp_path / "faiss"
    faiss_dir.mkdir()
    (faiss_dir / "index.faiss").touch()
    (faiss_dir / "index.pkl").touch()
    monkeypatch.setattr(rag_search, "FAISS_DIR", str(faiss_dir))
    monkeypatch.setattr(rag_search, "HuggingFaceEmbeddings", lambda **_: object())

    documents = [
        SimpleNamespace(
            page_content=f"Other user's document {index}",
            metadata={"source": f"other-{index}.txt", "user_id": "other-user"},
        )
        for index in range(160)
    ]
    documents.append(
        SimpleNamespace(
            page_content="Target user's matching document",
            metadata={"source": "target.txt", "user_id": "target-user"},
        )
    )

    class FakeVectorStore:
        index = SimpleNamespace(ntotal=len(documents))

        def __init__(self):
            self.requested_counts = []

        def similarity_search(self, _query, k):
            self.requested_counts.append(k)
            return documents[:k]

    vectorstore = FakeVectorStore()
    monkeypatch.setattr(
        rag_search.FAISS,
        "load_local",
        lambda *args, **kwargs: vectorstore,
    )

    results = rag_search.search_pdf("target document", k=1, user_id="target-user")

    assert len(results) == 1
    assert results[0]["content"] == "Target user's matching document"
    assert vectorstore.requested_counts[-1] == len(documents)
    assert len(vectorstore.requested_counts) > 1


def test_search_pdf_returns_empty_when_index_has_no_vectors(tmp_path, monkeypatch):
    faiss_dir = tmp_path / "faiss"
    faiss_dir.mkdir()
    (faiss_dir / "index.faiss").touch()
    (faiss_dir / "index.pkl").touch()
    monkeypatch.setattr(rag_search, "FAISS_DIR", str(faiss_dir))
    monkeypatch.setattr(rag_search, "HuggingFaceEmbeddings", lambda **_: object())

    class EmptyVectorStore:
        index = SimpleNamespace(ntotal=0)

        def similarity_search(self, *_args, **_kwargs):
            raise AssertionError("An empty FAISS index must not be searched.")

    monkeypatch.setattr(
        rag_search.FAISS,
        "load_local",
        lambda *args, **kwargs: EmptyVectorStore(),
    )

    assert rag_search.search_pdf("query", user_id="target-user") == []


@pytest.mark.parametrize(
    "metadata_bytes",
    [b"not a pickle", None],
)
def test_preview_and_ingestion_share_metadata_validation(
    tmp_path, monkeypatch, metadata_bytes
):
    import pickle

    metadata_path = tmp_path / "metadata.pkl"
    with metadata_path.open("wb") as metadata_file:
        if metadata_bytes is None:
            pickle.dump({"unexpected": "mapping"}, metadata_file)
        else:
            metadata_file.write(metadata_bytes)

    monkeypatch.setattr(rag_search, "METADATA_PATH", str(metadata_path))
    monkeypatch.setattr(rag_ingest, "METADATA_PATH", str(metadata_path))

    expected_message = (
        "list of objects" if metadata_bytes is None else "Could not read RAG metadata registry"
    )
    with pytest.raises(RuntimeError, match=expected_message):
        rag_ingest._load_metadata_registry()
    with pytest.raises(RuntimeError, match=expected_message):
        rag_search.get_document_preview(["document.txt"])
