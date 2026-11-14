from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent import graph


def test_graph_persists_conversation_summary_in_checkpoint(monkeypatch):
    monkeypatch.setattr(
        graph,
        "_get_max_tokens",
        lambda _name, default: 20 if _name == "GROQ_HISTORY_MAX_TOKENS" else int(default),
    )
    monkeypatch.setattr(graph, "get_text_model_candidates", lambda: ["model"])

    class FakeRunnable:
        def bind(self, **_kwargs):
            return self

        def bind_tools(self, _tools):
            return self

        def invoke(self, messages):
            if "You maintain concise" in messages[0].content:
                return AIMessage(content="Earlier topic summary.")
            return AIMessage(content="Current answer.")

    monkeypatch.setattr(graph, "get_llm", lambda _model: FakeRunnable())
    compiled_graph = graph.build_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "summary-persistence-test"}}

    compiled_graph.invoke(
        {
            "messages": [
                HumanMessage(content="old topic " * 100),
                AIMessage(content="old answer " * 100),
                HumanMessage(content="current question"),
            ],
            "research_mode": False,
        },
        config=config,
    )

    saved_state = compiled_graph.get_state(config).values
    assert saved_state["conversation_summary"] == "Earlier topic summary."
    assert saved_state["summarized_message_count"] == 2
    assert saved_state["messages"][-1].content == "Current answer."


def test_prepare_context_summarizes_older_turn_and_keeps_latest(
    monkeypatch,
):
    monkeypatch.setattr(
        graph,
        "_get_max_tokens",
        lambda _name, _default: 20,
    )
    summary_requests = []

    class FakeRunnable:
        def bind(self, **_kwargs):
            return self

        def invoke(self, messages):
            summary_requests.extend(messages)
            return AIMessage(content="The user discussed an older topic.")

    monkeypatch.setattr(graph, "get_text_model_candidates", lambda: ["model"])
    monkeypatch.setattr(graph, "get_llm", lambda _model: FakeRunnable())

    old_turn = [
        HumanMessage(content="old topic " * 100),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "web_search",
                    "args": {"query": "old topic"},
                    "id": "call-old",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="old search result " * 100, tool_call_id="call-old"),
        AIMessage(content="old answer " * 100),
    ]
    latest_turn = [
        HumanMessage(content="current question"),
        AIMessage(content="current answer"),
    ]
    state = {"messages": old_turn + latest_turn}

    updates = graph.prepare_conversation_context(state)

    assert updates == {
        "conversation_summary": "The user discussed an older topic.",
        "summarized_message_count": len(old_turn),
    }
    summary_input = summary_requests[-1].content
    assert "old topic" in summary_input
    assert "old search result" in summary_input
    assert "current question" not in summary_input

    context = graph._get_conversation_context_messages({**state, **updates})
    assert context[0].content.endswith("The user discussed an older topic.")
    assert context[1:] == latest_turn


def test_prepare_context_updates_rolling_summary_incrementally(monkeypatch):
    monkeypatch.setattr(
        graph,
        "_get_max_tokens",
        lambda _name, _default: 20,
    )
    summary_requests = []

    class FakeRunnable:
        def bind(self, **_kwargs):
            return self

        def invoke(self, messages):
            summary_requests.extend(messages)
            return AIMessage(content="Updated summary.")

    monkeypatch.setattr(graph, "get_text_model_candidates", lambda: ["model"])
    monkeypatch.setattr(graph, "get_llm", lambda _model: FakeRunnable())

    first_turn = [
        HumanMessage(content="first subject " * 100),
        AIMessage(content="first answer " * 100),
    ]
    second_turn = [
        HumanMessage(content="second subject " * 100),
        AIMessage(content="second answer " * 100),
    ]
    latest_turn = [HumanMessage(content="current question")]

    updates = graph.prepare_conversation_context(
        {
            "messages": first_turn + second_turn + latest_turn,
            "conversation_summary": "Earlier summary.",
            "summarized_message_count": len(first_turn),
        }
    )

    summary_input = summary_requests[-1].content
    assert "Earlier summary." in summary_input
    assert "second subject" in summary_input
    assert "first subject" not in summary_input
    assert "current question" not in summary_input
    assert updates["summarized_message_count"] == len(first_turn + second_turn)


def test_prepare_context_does_not_summarize_the_only_current_turn(monkeypatch):
    monkeypatch.setattr(
        graph,
        "_get_max_tokens",
        lambda _name, _default: 1,
    )
    monkeypatch.setattr(
        graph,
        "get_text_model_candidates",
        lambda: pytest.fail("A current turn should not be summarized."),
    )
    messages = [HumanMessage(content="important current request " * 100)]

    assert graph.prepare_conversation_context({"messages": messages}) == {}
    assert graph._get_conversation_context_messages({"messages": messages}) == messages


def test_llm_enables_provider_token_streaming(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    graph.get_llm.cache_clear()
    options = {}

    def fake_chat_groq(**kwargs):
        options.update(kwargs)
        return object()

    monkeypatch.setattr(graph, "ChatGroq", fake_chat_groq)

    try:
        graph.get_llm("streaming-model")
    finally:
        graph.get_llm.cache_clear()

    assert options["streaming"] is True


def test_model_candidates_use_configured_primary_for_legacy_deployments(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "custom/primary")
    monkeypatch.delenv("GROQ_MODELS", raising=False)

    assert graph.get_text_model_candidates() == ["custom/primary"]


def test_model_candidates_require_configuration(monkeypatch):
    monkeypatch.delenv("GROQ_MODELS", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="GROQ_MODELS is not configured"):
        graph.get_text_model_candidates()


def test_model_candidates_validate_count_and_deduplicate(monkeypatch):
    monkeypatch.setenv("GROQ_MODELS", "model-a, model-b, model-a")
    assert graph.get_text_model_candidates() == ["model-a", "model-b"]

    monkeypatch.setenv("GROQ_MODELS", "a,b,c,d,e,f,g")
    with pytest.raises(RuntimeError, match="at most 6"):
        graph.get_text_model_candidates()


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("429 rate_limit_exceeded"),
        RuntimeError("model_not_found: model does not exist"),
    ],
)
def test_assistant_switches_to_next_model(failure, monkeypatch):
    monkeypatch.setattr(graph, "build_system_prompt", lambda: "system")
    monkeypatch.setattr(graph, "get_text_model_candidates", lambda: ["model-a", "model-b"])
    monkeypatch.setattr(graph, "_get_max_tokens", lambda _name, default: int(default))
    monkeypatch.setattr(graph, "emit_tool_event", lambda *_args, **_kwargs: None)

    selected_models = []

    class FakeRunnable:
        def __init__(self, model):
            self.model = model

        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            selected_models.append(self.model)
            if self.model == "model-a":
                raise failure
            return SimpleNamespace(content="fallback answer")

    monkeypatch.setattr(graph, "get_llm", FakeRunnable)
    monkeypatch.setattr(graph, "recover_tool_call", lambda *_args: None)

    result = graph.assistant_node({"messages": []})
    assert selected_models == ["model-a", "model-b"]
    assert result["messages"][0].content == "fallback answer"


def test_vision_model_does_not_use_text_fallbacks(monkeypatch):
    monkeypatch.setattr(graph, "build_system_prompt", lambda: "system")
    monkeypatch.setattr(graph, "get_vision_model_name", lambda: "vision-model")
    monkeypatch.setattr(graph, "get_text_model_candidates", lambda: ["text-a", "text-b"])
    monkeypatch.setattr(graph, "_get_max_tokens", lambda _name, default: int(default))
    monkeypatch.setattr(graph, "emit_tool_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(graph.time, "sleep", lambda _seconds: None)
    selected_models = []

    class FakeRunnable:
        def __init__(self, model):
            self.model = model

        def bind_tools(self, _tools):
            return self

        def invoke(self, _messages):
            selected_models.append(self.model)
            raise RuntimeError("429 rate_limit_exceeded")

    monkeypatch.setattr(graph, "get_llm", FakeRunnable)
    monkeypatch.setattr(graph, "recover_tool_call", lambda *_args: None)

    result = graph.assistant_node(
        {"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}])]}
    )
    assert selected_models == ["vision-model"]
    assert "rate limit" in result["messages"][0].content.lower()
