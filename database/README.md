# Database

`db.py` contains the SQLite helpers used for thread/message, calendar-event,
and long-term memory persistence, including schema initialization and
compatibility migrations. Calendar events are scoped to a user. Legacy event
rows without a known owner remain unassigned until explicitly migrated. The
default database is `data/assistant.db`, under `DATA_DIR` when that setting is
configured.

The API also maintains account and UI metadata through `api/store.py`, and
LangGraph keeps checkpoint state in its configured checkpoint database.
These stores have different responsibilities; do not assume that deleting
one database resets all application state. The UI database path can be
overridden with `UI_DB_PATH`; the checkpoint database can be overridden with
`CHECKPOINT_DB_PATH`.

Local databases and their SQLite WAL sidecars are runtime data. Do not delete
or replace them as a routine source cleanup. Back up the relevant files before
running a migration or manually editing database state.

See the [project guide](../README.md) for local setup and test commands.
