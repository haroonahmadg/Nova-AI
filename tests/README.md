# Tests

The repository uses pytest. Run the suite from the repository root:

```powershell
python -m pytest
```

For focused document-ingestion coverage, run:

```powershell
python -m pytest tests/test_rag.py
```

`conftest.py` assigns temporary storage paths per test and reloads imported
environment-dependent modules in place so they observe the current test
configuration without invalidating existing module references. Tests are
grouped by capability, including authentication/security, agent tools,
SQLite persistence, RAG and document/web citations, image/media, voice, API
hardening, and the Codex frontend workspace behavior.

Keep tests isolated from real user data and third-party services. Prefer
temporary paths and mocked HTTP/model calls; never require production
credentials for the normal test suite.
