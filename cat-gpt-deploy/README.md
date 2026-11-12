# Deployment references

This directory contains deployment examples and the provider-specific guide.
The repository root `render.yaml` is the single Render Blueprint; keep
deployment settings there to avoid configuration drift.

- `DEPLOYMENT.md` — deployment walkthrough and free-tier storage notes.
- `../.env.example` — the single backend environment-variable template;
  keep configuration there to avoid duplicate templates drifting apart.
- `frontend.env.local.example` — local Next.js API URL template. Copy it to
  `forntend/.env.local` for local development.
- `../render.yaml` — the root Render Blueprint. It uses Docker, so the Python
  base version is selected by the root `Dockerfile`; `../runtime.txt` does not
  control this Docker deployment.

Keep credentials out of these templates. Configure real secrets in the local
ignored environment files or hosting platform's secret settings.
