# AGENTS.md — Cat GPT project guidance

> Code wins over comments. This repo is the live implementation for Cat GPT.
> See `README.md` for current architecture, setup, and deployment guidance.

## Project purpose

Cat GPT is a portfolio chat assistant built on FastAPI, LangGraph, FAISS-backed RAG, and a Next.js frontend. The assistant's system prompt identifies it as "Nova AI". The app uses per-user isolation for threads, uploads, RAG indexes, and memory.

## Current architecture

- Backend: `api/main.py` owns the FastAPI routes, auth, streaming, uploads, generated images, and voice endpoints.
- Agent: `agent/graph.py` is the LangGraph orchestrator. It keeps `research_mode` in `AgentState` and routes to research plan/synthesis when enabled.
- Tools: `agent/tools.py` is the tool inventory; the app emits custom activity/citation events with `emit_tool_event()`.
- RAG: `rag/ingest.py` and `rag/search.py` maintain user-scoped FAISS metadata and retrieval previews.
- Frontend: `forntend/` is the primary Next.js App Router UI. It renders streaming chat, citations, markdown, export, and the separate Codex workspace.
- Alternate UI: root `app.py` is a Streamlit interface retained for compatibility; it shares some backend and local data code.

## Runtime contract

SSE event types currently emitted by the app are:
- `text` — `{"text": "..."}`
- `image` — `{"url": "/generated-images/<file>"}`
- `activity` — `{"message": "..."}`
- `plan` — `{"steps": [str, ...]}`
- `citations` — `{"items": [...]}`; document items contain `file`, `page`,
  and `preview`, while web items contain `type: "web"`, `title`, and a
  validated HTTP(S) `url`.
- `done` — `{}`
- `error` — `{"message": "..."}`

Clients must ignore unknown event types. Existing event shapes stay stable; new event types are additive only.
Keep web citations sourced from actual search results; never synthesize source
names or links from model output. Preserve document citation compatibility.

## Feature status

Implemented and in the codebase:
- F1 markdown + syntax highlighting
- F3 export / print-to-PDF
- F4 citations + sources panel
- F5 voice input / Whisper transcription
- Research-mode graph routing with `research_mode` state and plan/synthesis
  nodes; the chat input's Think toggle sends this option.

## Repo rules

- Keep per-user isolation everywhere.
- Keep the event contract additive, not breaking.
- Keep LangGraph orchestration in the agent layer; do not hand-roll routing in the API.
- Preserve backward compatibility for old checkpoints when adding state fields.
- Focus on surgical fixes and existing project patterns.
