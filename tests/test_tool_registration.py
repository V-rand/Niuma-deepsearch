def test_sample_plugins_are_not_exposed_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        schema_names = [schema["function"]["name"] for schema in osys.list_tool_schemas()]

        assert "word_count" not in schema_names
    finally:
        import asyncio

        asyncio.run(osys.stop())


def test_enabled_tools_restricts_available_tool_schemas(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path), enabled_tools=["workspace_search", "web_read"])
    try:
        schema_names = [schema["function"]["name"] for schema in osys.list_tool_schemas()]

        assert set(schema_names) == {"web_read", "workspace_search"}
        assert len(schema_names) == 2
    finally:
        import asyncio

        asyncio.run(osys.stop())


def test_tool_registries_are_isolated_between_agent_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys_a = AgentOS(data_dir=str(tmp_path / "a"), enabled_tools=["workspace_search"])
    osys_b = AgentOS(data_dir=str(tmp_path / "b"), enabled_tools=["web_read"])
    try:
        names_a = [schema["function"]["name"] for schema in osys_a.list_tool_schemas()]
        names_b = [schema["function"]["name"] for schema in osys_b.list_tool_schemas()]

        assert names_a == ["workspace_search"]
        assert names_b == ["web_read"]
    finally:
        import asyncio

        asyncio.run(osys_a.stop())
        asyncio.run(osys_b.stop())


def test_array_parameters_declare_items(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        for schema in osys.list_tool_schemas():
            function = schema["function"]
            properties = function.get("parameters", {}).get("properties", {})
            for property_name, property_schema in properties.items():
                if property_schema.get("type") == "array":
                    assert "items" in property_schema, f"{function['name']}.{property_name}"
    finally:
        import asyncio

        asyncio.run(osys.stop())


def test_web_search_is_not_parallel_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")

    from agent_os import AgentOS

    osys = AgentOS(data_dir=str(tmp_path))
    try:
        entry = osys.tool_registry.get_entry("web_search")
        assert entry is not None
        assert entry.concurrency_safe is False
    finally:
        import asyncio

        asyncio.run(osys.stop())
