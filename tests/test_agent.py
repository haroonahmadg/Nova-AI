import pytest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import END
from langgraph.prebuilt import tools_condition

from agent.graph import parse_input_messages


def test_parse_input_messages_valid():
    raw_messages = [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi there!"},
    ]

    parsed = parse_input_messages(raw_messages)
    assert len(parsed) == 2
    assert isinstance(parsed[0], HumanMessage)
    assert isinstance(parsed[1], AIMessage)
    assert parsed[0].content == "Hello!"


def test_parse_input_messages_scopes_attached_file_preview_to_user(
    tmp_path, monkeypatch
):
    from agent import graph

    file_path = tmp_path / "random_report.txt"
    file_path.write_text("report", encoding="utf-8")
    preview_calls = []
    monkeypatch.setattr(
        graph,
        "prepare_image_for_groq",
        lambda _image: (b"image", "image/jpeg"),
    )
    monkeypatch.setattr(
        graph,
        "get_document_preview",
        lambda filenames, *, k, user_id: preview_calls.append(
            (filenames, k, user_id)
        ) or [],
    )

    graph.parse_input_messages(
        [
            {
                "role": "user",
                "content": "Compare these",
                "images": [
                    {
                        "image_bytes": "eA==",
                        "image_type": "image/png",
                        "filename": "photo.png",
                    }
                ],
                "files": [
                    {"filename": "report.txt", "file_path": str(file_path)}
                ],
            }
        ],
        user_id="account-1",
    )

    assert preview_calls == [(["random_report.txt"], 6, "account-1")]


def test_image_preparation_rejects_excessive_pixel_count(monkeypatch):
    from PIL import Image
    from agent import graph

    image_data = BytesIO()
    Image.new("RGB", (4, 3)).save(image_data, format="PNG")
    monkeypatch.setattr(graph, "MAX_IMAGE_PIXELS", 11)

    with pytest.raises(ValueError, match="maximum of 11 pixels"):
        graph.prepare_image_for_groq(image_data.getvalue())


def test_image_only_conversation_is_included_in_thread_list(
    tmp_path, monkeypatch
):
    from agent import graph

    checkpoint_path = tmp_path / "checkpoints.sqlite"
    checkpoint_path.touch()
    monkeypatch.setattr(graph, "CHECKPOINT_DB_PATH", checkpoint_path)

    class FakeCheckpointer:
        def list(self, _config):
            return [
                SimpleNamespace(
                    config={
                        "configurable": {
                            "thread_id": "account-1:image-only-thread"
                        }
                    },
                    checkpoint={
                        "channel_values": {
                            "messages": [
                                HumanMessage(
                                    content=[{"type": "image", "url": "data:image/png;base64,AA=="}]
                                )
                            ]
                        }
                    },
                )
            ]

    class FakeConnection:
        def close(self):
            pass

    monkeypatch.setattr(
        graph,
        "create_checkpointer",
        lambda: (FakeCheckpointer(), FakeConnection()),
    )

    assert graph.get_all_saved_threads("account-1") == {
        "image-only-thread": "Image conversation"
    }


def test_thread_title_uses_first_prompt_after_greeting(tmp_path, monkeypatch):
    from agent import graph

    checkpoint_path = tmp_path / "checkpoints.sqlite"
    checkpoint_path.touch()
    monkeypatch.setattr(graph, "CHECKPOINT_DB_PATH", checkpoint_path)

    class FakeCheckpointer:
        def list(self, _config):
            return [
                SimpleNamespace(
                    config={
                        "configurable": {
                            "thread_id": "account-1:greeting-thread"
                        }
                    },
                    checkpoint={
                        "channel_values": {
                            "messages": [
                                HumanMessage(content="Hi"),
                                HumanMessage(
                                    content="Can you help me build a weather app?"
                                ),
                            ]
                        }
                    },
                ),
                SimpleNamespace(
                    config={
                        "configurable": {
                            "thread_id": "account-1:greeting-only-thread"
                        }
                    },
                    checkpoint={
                        "channel_values": {
                            "messages": [HumanMessage(content="Hello Nova!")]
                        }
                    },
                ),
            ]

    class FakeConnection:
        def close(self):
            pass

    monkeypatch.setattr(
        graph,
        "create_checkpointer",
        lambda: (FakeCheckpointer(), FakeConnection()),
    )

    assert graph.get_all_saved_threads("account-1") == {
        "greeting-thread": "Help me build a weather app?",
        "greeting-only-thread": "New chat",
    }


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Hi", "Hi"),
        ("Hello Nova!", "Nova!"),
        ("History of programming languages", "History of programming languages"),
        ("Hi, can you explain recursion?", "Recursion?"),
    ],
)
def test_thread_title_filler_matching_respects_word_boundaries(prompt, expected):
    from agent import graph

    assert graph._generate_thread_title(prompt) == expected


def test_parse_input_messages_invalid():
    with pytest.raises(ValueError, match="Messages cannot be empty"):
        parse_input_messages([])

    with pytest.raises(ValueError, match="No valid messages were provided"):
        parse_input_messages([{"role": "system", "content": ""}])


def test_parse_input_messages_uses_shared_image_limit(monkeypatch):
    from agent import graph

    monkeypatch.setattr(graph, "MAX_IMAGES_PER_REQUEST", 1)
    with pytest.raises(ValueError, match="maximum of 1 images"):
        graph.parse_input_messages(
            [
                {
                    "role": "user",
                    "content": "Compare these",
                    "images": [
                        {"image_bytes": "unused", "image_type": "image/png"},
                        {"image_bytes": "unused", "image_type": "image/png"},
                    ],
                }
            ]
        )


def test_tools_condition_ends_without_tool_calls():
    state = {"messages": [AIMessage(content="Final answer.")]}
    next_node = tools_condition(state)
    assert next_node == END


def test_tools_condition_routes_tool_calls():
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "calculator", "args": {"expression": "2+2"}, "id": "1"}],
    )
    state = {"messages": [msg]}
    next_node = tools_condition(state)
    assert next_node == "tools"


def test_system_prompt_requests_examples_balanced_analysis_and_real_sources():
    from agent.graph import build_system_prompt

    prompt = " ".join(build_system_prompt().lower().split())

    assert "concrete, real-world examples" in prompt
    assert "societal trade-offs" in prompt
    assert "never invent a publication" in prompt
    assert "single uncorroborated result" in prompt


def test_research_synthesis_prompt_requires_grounded_sources(monkeypatch):
    from agent import graph

    captured_messages = []

    class FakeModel:
        def invoke(self, messages):
            captured_messages.extend(messages)
            return AIMessage(content="Qualified, evidence-based summary.")

    monkeypatch.setattr(graph, "get_text_model_candidates", lambda: ["test-model"])
    monkeypatch.setattr(graph, "get_llm", lambda _model: FakeModel())

    result = graph.research_synthesize_node({"messages": []})

    system_prompt = captured_messages[0].content
    assert "never invent links" in system_prompt
    assert "disclose that" in system_prompt
    assert result["messages"][0].content == "Qualified, evidence-based summary."