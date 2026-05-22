from __future__ import annotations


def test_reconstruct_restores_reasoning_content_for_deepseek_tool_turns():
    from agent_os.kernel.helpers import reconstruct_messages_from_db

    rows = [
        {
            "kind": "chat",
            "role": "assistant",
            "content": "",
            "metadata": {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": "{}"},
                    }
                ],
                "reasoning_content": "I need to search, then continue from this thought.",
            },
        },
        {
            "kind": "tool",
            "role": "tool",
            "content": "tool result",
            "metadata": {"tool_call_id": "call_1"},
        },
    ]

    messages = reconstruct_messages_from_db(rows, need_reasoning_roundtrip=True)

    assert messages[0]["reasoning_content"] == "I need to search, then continue from this thought."
    assert messages[0]["tool_calls"][0]["id"] == "call_1"


def test_reconstruct_omits_reasoning_content_when_provider_does_not_need_it():
    from agent_os.kernel.helpers import reconstruct_messages_from_db

    rows = [
        {
            "kind": "chat",
            "role": "assistant",
            "content": "",
            "metadata": {
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": "{}"},
                    }
                ],
                "reasoning_content": "provider-specific private reasoning",
            },
        },
        {
            "kind": "tool",
            "role": "tool",
            "content": "tool result",
            "metadata": {"tool_call_id": "call_1"},
        },
    ]

    messages = reconstruct_messages_from_db(rows, need_reasoning_roundtrip=False)

    assert "reasoning_content" not in messages[0]


def test_reconstruct_drops_reasoning_content_when_tool_call_is_orphaned():
    from agent_os.kernel.helpers import reconstruct_messages_from_db

    rows = [
        {
            "kind": "chat",
            "role": "assistant",
            "content": "",
            "metadata": {
                "tool_calls": [
                    {
                        "id": "call_missing",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": "{}"},
                    }
                ],
                "reasoning_content": "This reasoning belonged to a tool-call turn.",
            },
        }
    ]

    messages = reconstruct_messages_from_db(rows, need_reasoning_roundtrip=True)

    assert "tool_calls" not in messages[0]
    assert "reasoning_content" not in messages[0]


def test_reconstruct_deepseek_roundtrip_tolerates_system_messages():
    from agent_os.kernel.helpers import reconstruct_messages_from_db

    rows = [
        {
            "kind": "system",
            "role": "system",
            "content": "stable prompt",
            "metadata": {"reasoning_content": "should not be restored"},
        }
    ]

    messages = reconstruct_messages_from_db(rows, need_reasoning_roundtrip=True)

    assert messages == [{"role": "system", "content": "stable prompt"}]


def test_normalize_messages_for_cache_preserves_reasoning_content():
    from agent_os.kernel.helpers import normalize_messages_for_cache

    messages = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "keep this for DeepSeek tool-call continuation",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"b": 2, "a": 1}'},
                }
            ],
        }
    ]

    normalize_messages_for_cache(messages)

    assert messages[0]["reasoning_content"] == "keep this for DeepSeek tool-call continuation"
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == '{"a":1,"b":2}'
