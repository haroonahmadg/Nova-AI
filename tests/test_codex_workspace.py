from types import SimpleNamespace

from langchain_core.messages import HumanMessage

import pytest
from agent import graph


def test_codex_conversation_checkpoints_are_not_listed_as_chats(monkeypatch, tmp_path):
    monkeypatch.setattr(graph, "CHECKPOINT_DB_PATH", tmp_path / "checkpoints.sqlite")
    graph.CHECKPOINT_DB_PATH.touch()

    checkpoints = [
        SimpleNamespace(
            config={"configurable": {"thread_id": "user-1:codex-workspace"}},
            checkpoint={
                "channel_values": {
                    "messages": [HumanMessage(content="Explain this code")]
                }
            },
        ),
        SimpleNamespace(
            config={"configurable": {"thread_id": "user-1:codex-6f5b16d7"}},
            checkpoint={
                "channel_values": {
                    "messages": [HumanMessage(content="Refactor this function")]
                }
            },
        ),
        SimpleNamespace(
            config={"configurable": {"thread_id": "user-1:regular-chat"}},
            checkpoint={
                "channel_values": {
                    "messages": [HumanMessage(content="Help with this task")]
                }
            },
        ),
        SimpleNamespace(
            config={"configurable": {"thread_id": "user-2:regular-chat"}},
            checkpoint={
                "channel_values": {
                    "messages": [HumanMessage(content="Another account's chat")]
                }
            },
        ),
    ]

    class FakeCheckpointer:
        def list(self, _config):
            return checkpoints

    class FakeConnection:
        def close(self):
            pass

    monkeypatch.setattr(
        graph,
        "create_checkpointer",
        lambda: (FakeCheckpointer(), FakeConnection()),
    )

    assert graph.get_all_saved_threads("user-1") == {
        "regular-chat": "Help with this task"
    }


def test_thread_history_propagates_checkpoint_read_errors(monkeypatch, tmp_path):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    checkpoint_path.touch()
    monkeypatch.setattr(graph, "CHECKPOINT_DB_PATH", checkpoint_path)

    class FakeCheckpointer:
        def get_tuple(self, _config):
            raise OSError("checkpoint unavailable")

    class FakeConnection:
        closed = False

        def close(self):
            self.closed = True

    connection = FakeConnection()
    monkeypatch.setattr(
        graph,
        "create_checkpointer",
        lambda: (FakeCheckpointer(), connection),
    )

    with pytest.raises(OSError, match="checkpoint unavailable"):
        graph.get_thread_history("user-1", "thread-1")

    assert connection.closed
