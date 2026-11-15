# Agent

This package contains the LangGraph assistant and its model-facing tools.

- `graph.py` defines the agent state, model setup, tool registration, graph
  routing, streaming/run helpers, and checkpoint integration.
- `tools.py` implements callable assistant tools and their activity events.
- `llm_recovery.py` handles supported model/tool-call recovery paths.
- `voice.py` contains speech-related agent integrations.
- `quiz.py` contains quiz functionality.

The API passes authenticated user and thread context into the graph. Preserve
that namespacing and user isolation when changing agent state or checkpoint
configuration. The API streams assistant tokens as they arrive and normalizes
tool activity to a generic status before sending it to the frontend. Coordinate
stream changes with the API and frontend client.

The system prompt asks for clear structure and useful real-world examples,
balanced discussion of relevant trade-offs, and explicit uncertainty for
unsupported or consequential claims. Research synthesis must use retrieved
evidence and must not invent citations. `web_search` emits source citations
only for deduplicated results with valid HTTP(S) URLs; `rag_search` emits
document citations with source and page metadata.

Research-mode routing remains implemented in the graph and defaults off when
the request does not enable it. The Next.js chat input's Think toggle enables
the research plan and synthesis flow.

See the [project guide](../README.md) for setup and tests.

The email tool uses Resend. Configure `RESEND_API_KEY` and
`RESEND_FROM_EMAIL` on the backend; the sender address must use a domain
verified in the same Resend account. The tool sends only to the authenticated
user's verified Google address. For restricted testing, set
`RESEND_TEST_MODE=true` to use `onboarding@resend.dev`; Resend only allows
that sender to deliver to the email address that owns the Resend account.
