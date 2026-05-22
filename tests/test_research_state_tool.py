import pytest
import json


@pytest.mark.asyncio
async def test_research_state_guides_reasoning_before_search():
    from agent_os.tools.registry import set_session_context
    from agent_os.tools.research import handle_research_state

    set_session_context(work_dir="", session_id="research-state-1")

    started = await handle_research_state(
        operation="start",
        question_model={
            "answer_type": "entity",
            "hard_constraints": ["associative clue"],
            "output_fields": ["answer"],
        },
    )
    assert started.success

    focused = await handle_research_state(
        operation="focus_constraint",
        active_constraint="associative clue",
        expected_gain="decide whether candidate background resolves the clue",
    )
    assert focused.success

    guidance = await handle_research_state(operation="next_action")
    data = guidance.data

    assert data["control"]["must_inventory_known_facts"] is True
    assert data["control"]["answer_allowed"] is False
    assert data["next_action"] == "inventory_known_facts"

    inventoried = await handle_research_state(
        operation="inventory_known_facts",
        candidate="Fleming",
        known_facts=["Scottish scientist", "discovered penicillin"],
        reasoning_paths=["Scottish -> Highlands -> green mountains"],
    )
    assert inventoried.success

    guidance = await handle_research_state(operation="next_action")
    assert guidance.data["control"]["must_inventory_known_facts"] is False
    assert guidance.data["next_action"] in {"reason_from_known_facts", "discriminating_search"}
    assert "action_card" in guidance.data
    assert guidance.data["action_card"]["active_constraint"] == "associative clue"
    assert "allowed_next_tools" in guidance.data["action_card"]


@pytest.mark.asyncio
async def test_research_state_counts_no_progress_and_failed_pivots():
    from agent_os.tools.registry import set_session_context
    from agent_os.tools.research import handle_research_state

    set_session_context(work_dir="", session_id="research-state-2")
    await handle_research_state(operation="start", question_model={"answer_type": "entity"})
    await handle_research_state(operation="focus_constraint", active_constraint="hard clue")

    for _ in range(3):
        await handle_research_state(operation="round_update", progress=False)

    guidance = await handle_research_state(operation="next_action")
    assert guidance.data["control"]["must_pivot"] is True
    assert guidance.data["state"]["failed_pivots"] == 1

    await handle_research_state(operation="pivot", pivot_strategy="change frame")
    for _ in range(3):
        await handle_research_state(operation="round_update", progress=False)

    guidance = await handle_research_state(operation="next_action")
    assert guidance.data["control"]["must_stop_or_answer_uncertain"] is True
    assert guidance.data["state"]["failed_pivots"] == 2


def test_research_state_tool_is_registered(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        schema_names = [schema["function"]["name"] for schema in osys.list_tool_schemas()]
        assert "research_state" in schema_names
    finally:
        import asyncio

        asyncio.run(osys.stop())


@pytest.mark.asyncio
async def test_research_state_persists_to_session_research_file(tmp_path):
    from agent_os.tools.registry import set_session_context
    from agent_os.tools import research
    from agent_os.tools.research import handle_research_state

    session_id = "research-state-persist"
    set_session_context(work_dir=str(tmp_path), session_id=session_id)

    await handle_research_state(
        operation="start",
        question_model={"answer_type": "entity", "hard_constraints": ["constraint"]},
    )
    await handle_research_state(operation="focus_constraint", active_constraint="constraint")

    state_path = tmp_path / "research" / "research_state.json"
    assert state_path.exists()
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["active_constraint"] == "constraint"

    research._states.pop(session_id, None)
    restored = await handle_research_state(operation="next_action")
    assert restored.data["state"]["active_constraint"] == "constraint"


def test_research_guardrail_blocks_after_four_blind_retrieval_rounds():
    from agent_os.kernel.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    messages = []
    search = [{"name": "web_search", "arguments": {"query": "first"}}]
    read = [{"name": "web_read", "arguments": {"url": "https://example.com"}}]
    with_state = [{"name": "research_state", "arguments": {"operation": "next_action"}}]

    blind_rounds = 0
    for call in [search, read, search]:
        blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
            call,
            consecutive_blind_search_rounds=blind_rounds,
            messages=messages,
        )
        assert blocked is False
        assert reminder == ""
        if blind_rounds != 2:
            assert hint == ""

    blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
        read,
        consecutive_blind_search_rounds=blind_rounds,
        messages=messages,
    )
    assert blocked is True
    assert "research_state" in reminder
    assert hint == ""
    assert blind_rounds == 3
    assert messages[-1]["role"] == "user"
    assert "Runtime research guardrail" in messages[-1]["content"]

    blocked, _, blind_rounds, hint = loop._research_search_guardrail(
        with_state,
        consecutive_blind_search_rounds=blind_rounds,
        messages=messages,
    )
    assert blocked is False
    assert blind_rounds == 0
    assert hint == ""


def test_research_guardrail_counts_structured_retrieval_tools():
    from agent_os.kernel.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    messages = []
    calls = [
        [{"name": "arxiv_search", "arguments": {"query": "first"}}],
        [{"name": "openalex_works", "arguments": {"title": "first"}}],
        [{"name": "pubmed_search", "arguments": {"query": "first"}}],
        [{"name": "opencitations_search", "arguments": {"doi": "10.123/test"}}],
    ]

    blind_rounds = 0
    for index, call in enumerate(calls, start=1):
        blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
            call,
            consecutive_blind_search_rounds=blind_rounds,
            messages=messages,
        )
        assert blind_rounds == min(index, 3)
        if index == 2:
            assert blocked is False
            assert "research_state" in hint
        elif index == 4:
            assert blocked is True
            assert "another retrieval round" in reminder
        else:
            assert blocked is False


def test_research_guardrail_uses_registry_toolset_for_plugin_retrieval_tools():
    from agent_os.kernel.agent_loop import AgentLoop

    class Tools:
        def get_entry(self, name):
            if name == "custom_literature_search":
                return type("Entry", (), {"toolset": "retrieval"})()
            if name == "disabled_search":
                return None
            return None

    loop = AgentLoop.__new__(AgentLoop)
    loop.tools = Tools()
    messages = []

    blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
        [{"name": "custom_literature_search", "arguments": {"query": "first"}}],
        consecutive_blind_search_rounds=0,
        messages=messages,
    )
    assert blocked is False
    assert reminder == ""
    assert hint == ""
    assert blind_rounds == 1

    blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
        [{"name": "disabled_search", "arguments": {"query": "first"}}],
        consecutive_blind_search_rounds=blind_rounds,
        messages=messages,
    )
    assert blocked is False
    assert reminder == ""
    assert hint == ""
    assert blind_rounds == 1


def test_research_guardrail_parallel_retrieval_batch_counts_as_one_round():
    from agent_os.kernel.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    messages = []
    batch = [
        {"name": "arxiv_search", "arguments": {"query": "first"}},
        {"name": "openalex_works", "arguments": {"title": "first"}},
        {"name": "crossref_search", "arguments": {"query": "first"}},
    ]

    blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
        batch,
        consecutive_blind_search_rounds=0,
        messages=messages,
    )

    assert blocked is False
    assert reminder == ""
    assert hint == ""
    assert blind_rounds == 1


def test_research_guardrail_does_not_count_workspace_search_as_blind_external_retrieval():
    from agent_os.kernel.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    messages = []
    call = [{"name": "workspace_search", "arguments": {"query": "first"}}]

    blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
        call,
        consecutive_blind_search_rounds=3,
        messages=messages,
    )

    assert blocked is False
    assert reminder == ""
    assert hint == ""
    assert blind_rounds == 3


@pytest.mark.asyncio
async def test_research_state_analyzes_associative_constraint_with_action_card():
    from agent_os.tools.registry import set_session_context
    from agent_os.tools.research import handle_research_state

    set_session_context(work_dir="", session_id="research-state-3")
    await handle_research_state(operation="start", question_model={"answer_type": "entity"})
    await handle_research_state(
        operation="focus_constraint",
        active_constraint="name evokes someone living in green mountains",
    )

    empty = await handle_research_state(
        operation="analyze_constraint",
        active_constraint="name evokes someone living in green mountains",
        candidate="Alexander Fleming",
        constraint_type="associative",
    )
    assert empty.data["control"]["must_inventory_known_facts"] is True

    analyzed = await handle_research_state(
        operation="analyze_constraint",
        active_constraint="name evokes someone living in green mountains",
        candidate="Alexander Fleming",
        constraint_type="associative",
        known_facts=["Scottish scientist", "surname Fleming"],
    )

    card = analyzed.data["action_card"]
    assert analyzed.data["next_action"] == "reason_from_known_facts"
    assert analyzed.data["constraint_analysis"]["constraint_type"] == "associative"
    assert analyzed.data["control"]["reasoning_preferred"] is False
    assert "nationality / geography" in card["reasoning_lenses"]
    assert card["search_needed"] == "only_for_verification"
    assert card["search_policy"] == "associative_prefer_reasoning_after_first_failed_match"
    assert "web_search" in card["blocked_next_tools"]


@pytest.mark.asyncio
async def test_associative_no_progress_prefers_reasoning_without_hard_block():
    from agent_os.tools.registry import set_session_context
    from agent_os.tools.research import handle_research_state

    set_session_context(work_dir="", session_id="research-state-4")
    await handle_research_state(operation="start", question_model={"answer_type": "entity"})
    await handle_research_state(
        operation="focus_constraint",
        active_constraint="name reminds of a mountain resident",
    )
    await handle_research_state(
        operation="inventory_known_facts",
        candidate="Fleming",
        known_facts=["Scottish scientist"],
        reasoning_paths=["Scottish -> Highlands"],
    )
    await handle_research_state(operation="round_update", progress=False)

    guidance = await handle_research_state(operation="next_action")
    assert guidance.data["next_action"] == "reason_from_known_facts"
    assert guidance.data["control"]["reasoning_preferred"] is True


def test_research_guardrail_hints_before_hard_block():
    from agent_os.kernel.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    messages = []
    search = [{"name": "web_search", "arguments": {"query": "first"}}]

    blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
        search,
        consecutive_blind_search_rounds=0,
        messages=messages,
    )
    assert blocked is False
    assert reminder == ""
    assert hint == ""
    assert blind_rounds == 1

    blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
        search,
        consecutive_blind_search_rounds=blind_rounds,
        messages=messages,
    )
    assert blocked is False
    assert reminder == ""
    assert "research_state" in hint
    assert blind_rounds == 2


def test_research_guardrail_blocks_mixed_state_and_retrieval_batch():
    from agent_os.kernel.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    messages = []
    mixed = [
        {"name": "web_search", "arguments": {"query": "first"}},
        {"name": "research_state", "arguments": {"operation": "next_action"}},
    ]

    blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
        mixed,
        consecutive_blind_search_rounds=0,
        messages=messages,
    )

    assert blocked is True
    assert "Call research_state separately" in reminder
    assert blind_rounds == 0
    assert hint == ""


def test_research_guardrail_blocks_mixed_state_and_structured_retrieval_batch():
    from agent_os.kernel.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    messages = []
    mixed = [
        {"name": "openalex_works", "arguments": {"title": "first"}},
        {"name": "research_state", "arguments": {"operation": "next_action"}},
    ]

    blocked, reminder, blind_rounds, hint = loop._research_search_guardrail(
        mixed,
        consecutive_blind_search_rounds=0,
        messages=messages,
    )

    assert blocked is True
    assert "Call research_state separately" in reminder
    assert blind_rounds == 0
    assert hint == ""


@pytest.mark.asyncio
async def test_structured_retrieval_tool_results_are_archived(tmp_path):
    from agent_os.kernel.agent_loop import AgentLoop
    from agent_os.tools.registry import ToolResult

    class Sessions:
        async def get(self, session_id):
            return type("Session", (), {"work_dir": str(tmp_path)})()

    class WorkspaceMemory:
        def __init__(self):
            self.saved = None

        async def upsert_artifact(self, session_id, *, path, content, artifact_type, title, summary, metadata):
            self.saved = {
                "session_id": session_id,
                "path": path,
                "content": content,
                "artifact_type": artifact_type,
                "title": title,
                "summary": summary,
                "metadata": metadata,
            }
            return {"id": "artifact"}

    loop = AgentLoop.__new__(AgentLoop)
    loop.workspace_memory = WorkspaceMemory()
    loop.sessions = Sessions()

    archived_path = await loop._archive_external_tool_result(
        "s1",
        "openalex_works",
        {"title": "test paper"},
        ToolResult.ok(data={
            "results": [{
                "title": "Test Paper",
                "url": "https://example.test/paper",
                "authors": ["A. Researcher"],
                "content": "Abstract text.",
            }],
            "count": 1,
        }),
    )

    assert archived_path
    assert archived_path.startswith("raw_search/openalex_works/")
    assert loop.workspace_memory.saved["artifact_type"] == "external_retrieval"
    assert "Test Paper" in loop.workspace_memory.saved["content"]
    assert loop.workspace_memory.saved["metadata"]["lineage"]["source_urls"] == ["https://example.test/paper"]


@pytest.mark.asyncio
async def test_plugin_retrieval_tool_results_are_archived_by_toolset(tmp_path):
    from agent_os.kernel.agent_loop import AgentLoop
    from agent_os.tools.registry import ToolResult

    class Tools:
        def get_entry(self, name):
            if name == "custom_literature_search":
                return type("Entry", (), {"toolset": "retrieval"})()
            return None

    class WorkspaceMemory:
        def __init__(self):
            self.saved = None

        async def upsert_artifact(self, session_id, *, path, content, artifact_type, title, summary, metadata):
            self.saved = {"path": path, "content": content, "metadata": metadata}
            return {"id": "artifact"}

    loop = AgentLoop.__new__(AgentLoop)
    loop.tools = Tools()
    loop.workspace_memory = WorkspaceMemory()

    archived_path = await loop._archive_external_tool_result(
        "s1",
        "custom_literature_search",
        {"query": "test"},
        ToolResult.ok(data={"query": "test", "results": [{"title": "Plugin Result", "url": "https://example.test"}]}),
    )

    assert archived_path
    assert archived_path.startswith("raw_search/custom_literature_search/")
    assert "Plugin Result" in loop.workspace_memory.saved["content"]


def test_constraint_reasoning_skill_exists():
    from pathlib import Path

    skill = Path("skills/research/constraint_reasoning/SKILL.md")
    text = skill.read_text(encoding="utf-8")

    assert "associative" in text
    assert "linguistic" in text
    assert "geographic" in text


def test_prune_orphaned_tool_messages_removes_incomplete_middle_group():
    from agent_os.kernel.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    msgs = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1", "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "tc1", "content": "{}"},
        {"role": "assistant", "content": "ok"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "tc2", "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "user", "content": "q2"},
    ]

    pruned = loop._prune_orphaned_tool_messages(msgs)
    assert [m["role"] for m in pruned] == ["user", "assistant", "tool", "assistant", "user"]
    assert all(
        not (
            m.get("role") == "assistant"
            and any(tc.get("id") == "tc2" for tc in (m.get("tool_calls") or []))
        )
        for m in pruned
    )


def test_prune_orphaned_tool_messages_drops_orphan_tools_and_keeps_complete_groups():
    from agent_os.kernel.agent_loop import AgentLoop

    loop = AgentLoop.__new__(AgentLoop)
    msgs = [
        {"role": "tool", "tool_call_id": "orphan", "content": "{}"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "tc3", "function": {"name": "web_search", "arguments": "{}"}},
            {"id": "tc4", "function": {"name": "web_read", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "tc3", "content": "{}"},
        {"role": "tool", "tool_call_id": "tc4", "content": "{}"},
        {"role": "assistant", "content": "done"},
    ]

    pruned = loop._prune_orphaned_tool_messages(msgs)
    assert [m["role"] for m in pruned] == ["assistant", "tool", "tool", "assistant"]
