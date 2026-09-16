from uuid import uuid4

from atlas.agent import Decision, run_agent
from atlas.tools import ToolResult


async def test_agent_hard_step_limit():
    async def planner(state):
        return Decision(
            tool="search_documents", arguments={"question": "test " + str(state["steps"])}
        )

    async def executor(*args):
        return ToolResult(message="result")

    result = await run_agent(uuid4(), "test", max_steps=3, planner=planner, executor=executor)
    assert result["stop_reason"] == "step_limit"
    assert result["steps"] == 3


async def test_agent_handles_tool_failure():
    async def planner(state):
        return Decision(tool="search_documents", arguments={"question": "test"})

    async def executor(*args):
        raise RuntimeError("forced tool failure")

    result = await run_agent(uuid4(), "test", planner=planner, executor=executor)
    assert result["stop_reason"] == "tool_error"
    assert result["errors"] == 2


async def test_agent_malformed_model_output():
    async def planner(state):
        return Decision.model_validate_json("not-json")

    result = await run_agent(uuid4(), "test", planner=planner)
    assert result["stop_reason"] == "model_output_error"
    assert result["steps"] == 2


async def test_agent_combines_tools():
    async def planner(state):
        if state["steps"] == 0:
            return Decision(tool="search_documents", arguments={"question": "test"})
        if state["steps"] == 1:
            return Decision(tool="query_structured_data", arguments={"name": "checkout"})
        return Decision(tool="finish")

    async def executor(tenant, name, args):
        return ToolResult(sources=[{"id": name, "title": "Fixture", "content": "fixture"}])

    result = await run_agent(uuid4(), "test", planner=planner, executor=executor)
    assert len(result["sources"]) == 2
    assert result["stop_reason"] == ""


async def test_agent_stops_repeated_successful_tool_calls():
    async def planner(state):
        return Decision(tool="search_documents", arguments={"question": "repeat"})

    calls = []

    async def executor(*args):
        calls.append(args)
        return ToolResult(message="found")

    result = await run_agent(uuid4(), "repeat", planner=planner, executor=executor)
    assert result["stop_reason"] == "duplicate_tool_call" and len(calls) == 1
