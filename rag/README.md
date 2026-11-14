# Retrieval-augmented generation

This package ingests documents and URLs, splits and embeds content, stores
vectors in a FAISS index, and retrieves user-scoped context for agent
responses.

- File uploads support PDF, Word, text/Markdown, CSV, JSON, Excel (`.xls`,
  `.xlsx`), images, and Python source where applicable. Excel ingestion reads
  each worksheet into searchable text.
- `ingest.py` handles document loading, chunking, index updates, metadata,
  source listing, and deletion.
- `search.py` provides similarity search and document-preview retrieval.
- Runtime index and metadata files are stored below `data/faiss/` by default.

Use the user ID consistently when ingesting, searching, previewing, or
deleting content. The FAISS index and metadata registry are generated runtime
data and may contain user documents; preserve them during source cleanup.
Document citations are emitted with the source filename, page, and a short
preview. These are separate from web citations emitted by
`agent.tools.web_search`; do not treat search-result URLs as RAG document
metadata.

See `tests/test_rag.py` and `tests/test_rag_citations.py` for focused coverage.
Project setup and the full test command are in the [root README](../README.md).
