"""
Research cognition scaffold tools.

These tools do not perform retrieval. They externalize short-answer research
state so the model has a visible control surface before it reaches for search.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .registry import ToolResult, get_session_id, get_session_work_dir, get_tool_registry

_DESC_DIR = Path(__file__).resolve().parent / "descriptions"
_states: dict[str, dict[str, Any]] = {}


def _load_desc(name: str) -> str:
    path = _DESC_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def _sid() -> str:
    return get_session_id() or "__default__"


def _empty_state() -> dict[str, Any]:
    return {
        "question_model": {},
        "active_constraint": "",
        "active_constraint_type": "",
        "expected_gain": "",
        "candidates": {},
        "evidence": [],
        "known_fact_inventory": {},
        "reasoning_paths": {},
        "no_progress_rounds": 0,
        "failed_pivots": 0,
        "pivot_history": [],
        "last_progress": {},
    }


def _state_path() -> Path | None:
    work_dir = get_session_work_dir()
    if not work_dir:
        return None
    return Path(work_dir) / "research" / "research_state.json"


def _load_state_from_file() -> dict[str, Any] | None:
    path = _state_path()
    if path is None or not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(loaded, dict):
        return None
    state = _empty_state()
    for key in state:
        if key in loaded:
            state[key] = loaded[key]
    return state


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _state() -> dict[str, Any]:
    sid = _sid()
    if sid not in _states:
        _states[sid] = _load_state_from_file() or _empty_state()
    return _states[sid]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_model": deepcopy(state["question_model"]),
        "active_constraint": state["active_constraint"],
        "active_constraint_type": state.get("active_constraint_type", ""),
        "expected_gain": state["expected_gain"],
        "candidates": deepcopy(state["candidates"]),
        "evidence_count": len(state["evidence"]),
        "known_fact_inventory": deepcopy(state["known_fact_inventory"]),
        "reasoning_paths": deepcopy(state["reasoning_paths"]),
        "no_progress_rounds": state["no_progress_rounds"],
        "failed_pivots": state["failed_pivots"],
        "last_progress": deepcopy(state["last_progress"]),
    }


_LENSES: dict[str, list[str]] = {
    "associative": [
        "literal wording",
        "translation / Chinese characters",
        "etymology / surname origin",
        "nationality / geography",
        "biography / profession",
        "cultural archetype",
        "domain metaphor",
    ],
    "linguistic": [
        "original language wording",
        "translation variants",
        "Chinese characters",
        "pronunciation / homophone",
        "etymology",
        "domain-specific meaning",
    ],
    "geographic": [
        "birthplace / residence / registered origin",
        "region hierarchy",
        "nearby landmarks",
        "historical geography",
        "cultural region",
    ],
    "temporal": [
        "event date",
        "approval / launch / production / publication distinction",
        "calendar system",
        "source publication date",
    ],
    "factual": [
        "official source",
        "structured reference",
        "primary source",
        "independent corroboration",
    ],
    "relational": [
        "role membership",
        "cast / team / organization relation",
        "alias / translation",
        "source-specific wording",
    ],
    "causal": [
        "mechanism",
        "necessary condition",
        "sufficient condition",
        "alternative explanation",
        "counterexample",
    ],
}


def _infer_constraint_type(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ("evoke", "remind", "associate", "联想", "想到", "让人想起")):
        return "associative"
    if any(token in lowered for token in ("name", "word", "character", "translation", "etymology", "名字", "汉字", "词源", "译名")):
        return "linguistic"
    if any(token in lowered for token in ("where", "near", "adjacent", "mountain", "river", "birthplace", "地点", "靠近", "山", "河", "出生")):
        return "geographic"
    if any(token in lowered for token in ("when", "year", "date", "launched", "approved", "年份", "时间", "投产", "获批", "上市")):
        return "temporal"
    if any(token in lowered for token in ("cause", "because", "mechanism", "导致", "原因", "机制")):
        return "causal"
    return "factual"


def _action_card(
    state: dict[str, Any],
    action: str,
    *,
    constraint_type: str | None = None,
    candidate: str = "",
    known_facts: list[str] | None = None,
) -> dict[str, Any]:
    active_constraint = state["active_constraint"]
    ctype = constraint_type or state.get("active_constraint_type") or _infer_constraint_type(active_constraint)
    lenses = _LENSES.get(ctype, _LENSES["factual"])
    facts = _as_list(known_facts)
    search_needed = "yes"
    allowed = ["research_state"]
    blocked: list[str] = []
    required_output = "Update research_state with progress before the next retrieval round."
    why = "Keep the next action tied to the active constraint instead of collecting more information broadly."

    if action == "inventory_known_facts":
        required_output = "List known facts and 2-5 possible reasoning paths for the active constraint."
        allowed = ["research_state"]
        blocked = ["web_search", "web_read"]
        search_needed = "after_inventory"
        why = "The active constraint has not been grounded in known facts yet."
    elif action == "reason_from_known_facts":
        required_output = "Write 2-5 reasoning chains, mark which support/exclude the candidate, then decide whether search is only needed for verification."
        allowed = ["research_state", "wikipedia_lookup"]
        blocked = ["web_search"]
        search_needed = "only_for_verification" if facts or any(state["known_fact_inventory"].values()) else "after_inventory"
        why = "Known facts or reasoning paths may resolve the active constraint without another broad search."
        if ctype == "associative":
            required_output = (
                "Write 2 competing interpretations, then prefer the one with fewer assumptions / shorter chain "
                "unless explicit counter-evidence exists."
            )
    elif action == "pivot":
        required_output = "Change query family or frame; do not repeat the same search wording."
        allowed = ["research_state", "web_search", "wikipedia_lookup"]
        search_needed = "yes_after_pivot"
        why = "The current search family has stopped producing progress."
    elif action == "answer_with_uncertainty":
        required_output = "Stop searching; answer with the best candidate and explicit uncertainty or say evidence is insufficient."
        allowed = ["research_state"]
        blocked = ["web_search", "web_read"]
        search_needed = "no"
        why = "Two failed pivots mean more retrieval is unlikely to improve the answer this turn."
    elif action == "answer_allowed":
        required_output = "Provide the final short answer with compact justification."
        allowed = []
        blocked = ["web_search", "web_read"]
        search_needed = "no"
        why = "A winner is available and competing candidates have been handled."

    return {
        "active_constraint": active_constraint,
        "candidate": candidate,
        "constraint_type": ctype,
        "why_this_action": why,
        "required_output": required_output,
        "reasoning_lenses": lenses,
        "allowed_next_tools": allowed,
        "blocked_next_tools": blocked,
        "search_needed": search_needed,
        "search_policy": (
            "associative_prefer_reasoning_after_first_failed_match"
            if ctype == "associative"
            else "normal"
        ),
    }


def _next_action(state: dict[str, Any]) -> dict[str, Any]:
    candidates = state["candidates"]
    has_active_constraint = bool(state["active_constraint"])
    has_known_facts = bool(state["known_fact_inventory"])
    has_reasoning_paths = any(state["reasoning_paths"].values())
    winners = [
        name for name, item in candidates.items()
        if item.get("status") == "winner"
    ]
    has_rejected = any(item.get("status") == "rejected" for item in candidates.values())
    active_type = state.get("active_constraint_type") or _infer_constraint_type(state["active_constraint"])
    answer_allowed = bool(winners and (has_rejected or len(candidates) <= 1))
    must_inventory = has_active_constraint and not has_known_facts
    must_pivot = state["failed_pivots"] >= 1 and not answer_allowed
    must_stop = state["failed_pivots"] >= 2 and not answer_allowed
    reasoning_preferred = bool(
        active_type == "associative"
        and state["no_progress_rounds"] >= 1
        and (has_known_facts or has_reasoning_paths)
    )

    if answer_allowed:
        action = "answer_allowed"
    elif must_stop:
        action = "answer_with_uncertainty"
    elif not has_active_constraint:
        action = "focus_constraint"
    elif must_inventory:
        action = "inventory_known_facts"
    elif reasoning_preferred:
        action = "reason_from_known_facts"
    elif has_reasoning_paths:
        action = "reason_from_known_facts"
    elif must_pivot:
        action = "pivot"
    else:
        action = "discriminating_search"

    result = {
        "next_action": action,
        "control": {
            "answer_allowed": answer_allowed,
            "must_inventory_known_facts": must_inventory,
            "must_pivot": must_pivot,
            "must_stop_or_answer_uncertain": must_stop,
            "reasoning_preferred": reasoning_preferred,
        },
        "state": _public_state(state),
        "action_card": _action_card(state, action),
    }
    return result


async def handle_research_state(
    operation: str,
    question_model: dict[str, Any] | None = None,
    active_constraint: str = "",
    expected_gain: str = "",
    candidate: str = "",
    matched: list[str] | None = None,
    failed: list[str] | None = None,
    missing: list[str] | None = None,
    status: str = "",
    claim: str = "",
    source: str = "",
    constraint: str = "",
    verdict: str = "",
    reliability: str = "",
    known_facts: list[str] | None = None,
    reasoning_paths: list[str] | None = None,
    progress: bool | None = None,
    progress_note: str = "",
    pivot_strategy: str = "",
    constraint_type: str = "",
    **kw: Any,
) -> ToolResult:
    op = str(operation or "").strip()
    if not op:
        return ToolResult.fail("operation is required")

    state = _state()

    if op == "reset":
        _states[_sid()] = _empty_state()
        _save_state(_states[_sid()])
        return ToolResult.ok(data=_next_action(_states[_sid()]))

    if op == "start":
        state.clear()
        state.update(_empty_state())
        state["question_model"] = dict(question_model or {})
        _save_state(state)
        return ToolResult.ok(data=_next_action(state))

    if op == "focus_constraint":
        state["active_constraint"] = active_constraint.strip()
        state["active_constraint_type"] = _infer_constraint_type(state["active_constraint"])
        state["expected_gain"] = expected_gain.strip()
        _save_state(state)
        return ToolResult.ok(data=_next_action(state))

    if op == "inventory_known_facts":
        name = candidate.strip() or "__general__"
        state["known_fact_inventory"][name] = _as_list(known_facts)
        state["reasoning_paths"][name] = _as_list(reasoning_paths)
        _save_state(state)
        return ToolResult.ok(data=_next_action(state))

    if op == "update_candidate":
        name = candidate.strip()
        if not name:
            return ToolResult.fail("candidate is required for update_candidate")
        state["candidates"][name] = {
            "matched": _as_list(matched),
            "failed": _as_list(failed),
            "missing": _as_list(missing),
            "status": status.strip() or "active",
        }
        _save_state(state)
        return ToolResult.ok(data=_next_action(state))

    if op == "add_evidence":
        if not claim.strip():
            return ToolResult.fail("claim is required for add_evidence")
        state["evidence"].append({
            "claim": claim.strip(),
            "source": source.strip(),
            "constraint": constraint.strip() or state["active_constraint"],
            "verdict": verdict.strip() or "unclear",
            "reliability": reliability.strip() or "medium",
        })
        _save_state(state)
        return ToolResult.ok(data=_next_action(state))

    if op == "round_update":
        made_progress = bool(progress)
        state["last_progress"] = {
            "progress": made_progress,
            "note": progress_note.strip(),
        }
        if made_progress:
            state["no_progress_rounds"] = 0
        else:
            state["no_progress_rounds"] += 1
            if state["no_progress_rounds"] >= 3:
                state["failed_pivots"] += 1
                state["no_progress_rounds"] = 0
        _save_state(state)
        return ToolResult.ok(data=_next_action(state))

    if op == "pivot":
        state["pivot_history"].append(pivot_strategy.strip() or "change query family or frame")
        state["active_constraint"] = ""
        state["active_constraint_type"] = ""
        state["expected_gain"] = ""
        state["known_fact_inventory"] = {}
        state["reasoning_paths"] = {}
        _save_state(state)
        return ToolResult.ok(data=_next_action(state))

    if op == "analyze_constraint":
        constraint_text = active_constraint.strip() or state["active_constraint"]
        if constraint_text:
            state["active_constraint"] = constraint_text
        ctype = constraint_type.strip() or _infer_constraint_type(constraint_text)
        state["active_constraint_type"] = ctype
        facts = _as_list(known_facts)
        if facts:
            name = candidate.strip() or "__general__"
            state["known_fact_inventory"][name] = facts
        result = _next_action(state)
        if facts and result["next_action"] == "discriminating_search":
            result["next_action"] = "reason_from_known_facts"
        card = _action_card(
            state,
            result["next_action"],
            constraint_type=ctype,
            candidate=candidate.strip(),
            known_facts=facts,
        )
        result["action_card"] = card
        result["constraint_analysis"] = {
            "constraint": constraint_text,
            "constraint_type": ctype,
            "reasoning_lenses": card["reasoning_lenses"],
            "required_reasoning_chains": 2,
            "search_needed": card["search_needed"],
            "suggested_search_if_needed": (
                f"{candidate} {constraint_text}" if candidate and card["search_needed"] != "no" else ""
            ),
        }
        if not facts:
            result["control"]["must_inventory_known_facts"] = True
            result["next_action"] = "inventory_known_facts"
            result["action_card"] = _action_card(state, "inventory_known_facts", constraint_type=ctype, candidate=candidate.strip())
        _save_state(state)
        return ToolResult.ok(data=result)

    if op == "next_action":
        return ToolResult.ok(data=_next_action(state))

    return ToolResult.fail(f"unknown operation: {operation}")


def register_research_tools(r) -> None:
    r.register("research_state", "reasoning", {
        "name": "research_state",
        "description": _load_desc("research_state"),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "start", "focus_constraint", "inventory_known_facts",
                        "update_candidate", "add_evidence", "round_update",
                        "pivot", "next_action", "analyze_constraint", "reset",
                    ],
                    "description": "State operation to perform.",
                },
                "question_model": {"type": "object", "description": "Answer type, hard constraints, soft clues, ambiguities, output fields."},
                "active_constraint": {"type": "string", "description": "The single constraint currently being solved."},
                "expected_gain": {"type": "string", "description": "What this next step is expected to change or decide."},
                "candidate": {"type": "string", "description": "Candidate answer name."},
                "matched": {"type": "array", "items": {"type": "string"}, "description": "Hard constraints matched by this candidate."},
                "failed": {"type": "array", "items": {"type": "string"}, "description": "Hard constraints failed by this candidate."},
                "missing": {"type": "array", "items": {"type": "string"}, "description": "Hard constraints still missing for this candidate."},
                "status": {"type": "string", "enum": ["active", "rejected", "winner", ""], "description": "Candidate status."},
                "claim": {"type": "string", "description": "Evidence claim."},
                "source": {"type": "string", "description": "Evidence source or URL."},
                "constraint": {"type": "string", "description": "Constraint supported/excluded by the evidence."},
                "verdict": {"type": "string", "enum": ["supports", "excludes", "unclear", ""], "description": "Evidence verdict."},
                "reliability": {"type": "string", "enum": ["high", "medium", "low", ""], "description": "Evidence reliability."},
                "known_facts": {"type": "array", "items": {"type": "string"}, "description": "Facts already known about the candidate/constraint before searching."},
                "reasoning_paths": {"type": "array", "items": {"type": "string"}, "description": "Reasoning paths from known facts to the active constraint."},
                "progress": {"type": "boolean", "description": "Whether the last round made progress."},
                "progress_note": {"type": "string", "description": "What changed in the last round."},
                "pivot_strategy": {"type": "string", "description": "New frame/query family after a failed pivot."},
                "constraint_type": {"type": "string", "enum": ["factual", "temporal", "geographic", "linguistic", "associative", "relational", "causal", ""], "description": "Optional constraint type for analyze_constraint."},
            },
            "required": ["operation"],
        },
    }, handle_research_state, read_only=False)
