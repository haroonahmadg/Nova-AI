# Maintenance scripts

Scripts in this directory are run manually and are not part of normal
application startup.

`migrate_existing_data.py` assigns older unscoped application data to one
selected user. It updates SQLite records and RAG metadata in place. Back up all
relevant databases and `data/` before running it, and verify the target user
and resulting records afterward. RAG migration updates both the metadata
registry and the FAISS document metadata; it stops with an error if those
stores do not match or the FAISS index is missing.

The script requires the repository's Python dependencies and a configured
local environment. It uses the same `DATA_DIR`, `UI_DB_PATH`, and
`CHECKPOINT_DB_PATH` settings as the application, so set these before running
the migration if the data is not in the default `data/` directory. See the
[root README](../README.md) for setup.
