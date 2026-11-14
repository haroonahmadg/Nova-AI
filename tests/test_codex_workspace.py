from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

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


def test_thread_history_restores_research_mode_on_latest_user_turn(
    monkeypatch, tmp_path
):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    checkpoint_path.touch()
    monkeypatch.setattr(graph, "CHECKPOINT_DB_PATH", checkpoint_path)

    class FakeCheckpointer:
        def get_tuple(self, _config):
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [
                            HumanMessage(content="Earlier question"),
                            AIMessage(content="Earlier answer"),
                            HumanMessage(content="Latest question"),
                            AIMessage(content="Latest answer"),
                        ],
                        "research_mode": True,
                    }
                }
            )

    class FakeConnection:
        def close(self):
            pass

    monkeypatch.setattr(
        graph,
        "create_checkpointer",
        lambda: (FakeCheckpointer(), FakeConnection()),
    )

    history = graph.get_thread_history("user-1", "thread-1")

    assert history == [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
        {
            "role": "user",
            "content": "Latest question",
            "research_mode": True,
        },
        {"role": "assistant", "content": "Latest answer"},
    ]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ([{"type": "image", "url": "data:image/png;base64,AA=="}], "[Image attached]"),
        (
            [
                {"type": "text", "text": "What is in this picture?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
            "What is in this picture?\n[Image attached]",
        ),
    ],
)
def test_thread_history_keeps_image_turns_without_exposing_image_data(
    monkeypatch, tmp_path, content, expected
):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    checkpoint_path.touch()
    monkeypatch.setattr(graph, "CHECKPOINT_DB_PATH", checkpoint_path)

    class FakeCheckpointer:
        def get_tuple(self, _config):
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [
                            HumanMessage(content=content),
                            AIMessage(content="A cat."),
                        ]
                    }
                }
            )

    class FakeConnection:
        def close(self):
            pass

    monkeypatch.setattr(
        graph,
        "create_checkpointer",
        lambda: (FakeCheckpointer(), FakeConnection()),
    )

    history = graph.get_thread_history("user-1", "thread-1")

    assert history[0] == {"role": "user", "content": expected}
    assert "base64" not in history[0]["content"]


def test_thread_history_restores_generated_images_for_the_owner(
    monkeypatch, tmp_path
):
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    checkpoint_path.touch()
    image_dir = tmp_path / "generated-images"
    image_dir.mkdir()
    filename = "0123456789abcdef.png"
    (image_dir / filename).write_bytes(b"image")
    monkeypatch.setattr(graph, "CHECKPOINT_DB_PATH", checkpoint_path)
    monkeypatch.setattr(graph, "get_generated_image_dir", lambda _user_id: image_dir)

    class FakeCheckpointer:
        def get_tuple(self, _config):
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [
                            ToolMessage(
                                content=f"{graph.GENERATED_IMAGE_MARKER}{filename}",
                                tool_call_id="call-1",
                            )
                        ]
                    }
                }
            )

    class FakeConnection:
        def close(self):
            pass

    monkeypatch.setattr(
        graph,
        "create_checkpointer",
        lambda: (FakeCheckpointer(), FakeConnection()),
    )

    assert graph.get_thread_history("user-1", "thread-1") == [
        {
            "role": "assistant",
            "content": "[Generated image]",
            "image_url": f"/generated-images/{filename}",
        }
    ]
