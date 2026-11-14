# API

`main.py` defines the FastAPI application, lifecycle, authentication
dependencies, and HTTP routes. It is the deployed backend entry point:

```powershell
python -m uvicorn api.main:app --reload
```

Key modules:

- `auth.py` — login/session token and identity verification helpers.
- `config.py` — environment-derived paths, origins, and request limits.
- `schemas.py` — Pydantic request and response models.
- `store.py` — user records and user-scoped thread/UI metadata in SQLite.
- `streaming.py` — conversion of graph updates and custom events to SSE.
- `rate_limit.py` — authentication request throttling.

The API coordinates agent calls, RAG, persistence, uploads, generated media,
and voice services. Authenticate and authorize server-side, keep user IDs
attached to persistence and retrieval operations, and preserve the SSE event
contract when modifying endpoints.

`GET /health` and `GET /config/limits` are public. The limits endpoint
currently returns `{"max_prompt_chars": <positive integer>}` so the frontend
can validate against server configuration; request validation on the API
remains authoritative. Chat SSE streams assistant text incrementally and can
include generic activity, research-plan, image, citation, done, and error
events. Tool-specific activity labels and IDs are not sent to the client.
The configured `MAX_UPLOAD_FILES` limit applies both to multipart uploads and
to file references attached to a chat request.
`MAX_USER_STORAGE_MB` caps the combined persisted uploaded files and generated
images for each account (default: 1024 MB). Quota checks are serialized per
account using a lock file under `DATA_DIR`; deployments sharing media storage
across workers should share that directory as well.
Citation items distinguish uploaded documents (`file`, `page`, `preview`) from
web results (`type`, `title`, `url`); only actual, validated search-result
URLs should be emitted. Message history may include an `image_url` for an
authenticated, user-scoped generated image, and a `research_mode` flag on the
latest user turn for retry behavior; uploaded image bytes are never included
in history responses.
Set `REDIS_URL` to a shared Redis instance when running multiple workers or
servers; without it, authentication attempt limits are process-local. Behind
a reverse proxy, configure Uvicorn's proxy headers and trusted proxy IPs so
per-IP limits use the client address rather than the proxy address.

Configuration values and local startup steps are in the repository
[README](../README.md) and [`.env.example`](../.env.example).
