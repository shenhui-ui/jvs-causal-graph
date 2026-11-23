"""Tests for the JVS MCPServer tools and resources."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from jvs.mcp.server import create_mcp_server


def test_mcp_server_tools_and_resources(tmp_path: Path, sample_graph_db: Path):
    server = create_mcp_server(sample_graph_db, read_only=True)

    async def _exercise():
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert {
            "causal_search",
            "causal_why",
            "causal_timeline",
            "causal_ask",
            "causal_ingest_event",
            "causal_link_events",
            "causal_graph_stats",
        }.issubset(names)

        search_res = await server.call_tool("causal_search", {"query": "扩容连接池", "k": 3})
        search_payload = json.loads(search_res.content[0].text)
        assert search_payload["count"] >= 1

        why_res = await server.call_tool("causal_why", {"event_id": "E1", "max_hops": 3})
        why_payload = json.loads(why_res.content[0].text)
        assert why_payload["traversal"]["ok"] is True

        ask_res = await server.call_tool(
            "causal_ask", {"question": "为什么扩容连接池？", "use_llm": False}
        )
        ask_payload = json.loads(ask_res.content[0].text)
        assert ask_payload["pipeline_ok"] is True

        # Read-only server safely rejects write calls
        write_res = await server.call_tool(
            "causal_ingest_event",
            {
                "event_id": "E999",
                "subject": "s",
                "action": "a",
                "object_name": "o",
            },
        )
        assert json.loads(write_res.content[0].text)["ok"] is False

        summary_contents = await server.read_resource("jvs://graph/summary")
        summary_data = json.loads(summary_contents[0].content)
        assert summary_data["storage"]["events"] == 7

    asyncio.run(_exercise())

    # Writable server supports ingest + link
    writable_db = tmp_path / "mcp_write.db"
    w_server = create_mcp_server(writable_db, read_only=False)

    async def _exercise_write():
        await w_server.call_tool(
            "causal_ingest_event",
            {
                "event_id": "EW1",
                "ts": "2026-09-01",
                "subject": "部署",
                "action": "触发",
                "object_name": "回滚",
            },
        )
        await w_server.call_tool(
            "causal_ingest_event",
            {
                "event_id": "EW2",
                "ts": "2026-09-02",
                "subject": "部署",
                "action": "修复",
                "object_name": "配置",
            },
        )
        res = await w_server.call_tool(
            "causal_link_events",
            {
                "edge_id": "AW1",
                "from_event": "EW1",
                "to_event": "EW2",
                "confidence": 0.9,
            },
        )
        assert json.loads(res.content[0].text)["stats"]["inserted"] == 1

    asyncio.run(_exercise_write())
