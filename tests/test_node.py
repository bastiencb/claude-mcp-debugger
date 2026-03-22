"""Smoke tests for Node.js debugging via js-debug."""

import asyncio
import shutil
import textwrap
from pathlib import Path

import pytest

from mcp_debugger.session import DebugSession

# Skip all tests if node is not available
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None,
    reason="Node.js not found in PATH",
)


@pytest.fixture
def sample_js(tmp_path: Path) -> Path:
    """Create a minimal JavaScript script to debug."""
    script = tmp_path / "sample.js"
    script.write_text(textwrap.dedent("""\
        let x = 1;
        let y = 2;
        let z = x + y;
        console.log(z);
    """))
    return script


@pytest.fixture
def function_js(tmp_path: Path) -> Path:
    """Script with a function for step_into testing."""
    script = tmp_path / "functions.js"
    script.write_text(textwrap.dedent("""\
        function add(a, b) {
            return a + b;
        }

        let result = add(3, 4);
        console.log(result);
    """))
    return script


@pytest.mark.asyncio
async def test_node_launch_and_inspect(sample_js: Path):
    """Launch a JS program, set breakpoint, inspect variables."""
    session = DebugSession()

    try:
        info = await session.start(
            program=str(sample_js),
            stop_on_entry=True,
            port=15750,
            language="node",
        )
        assert info["program"] == str(sample_js)
        assert info["port"] == 15750

        # We should be stopped at entry
        stop = await session.client.wait_for_stop(timeout=10.0)
        assert stop.get("reason") in ("breakpoint", "entry", "step", "pause")

        # Set breakpoint on line 3 (z = x + y)
        bps = await session.client.set_breakpoints(str(sample_js), [3])
        assert any(bp.get("verified") for bp in bps)

        # Continue to breakpoint
        await session.client.continue_execution()
        stop = await session.client.wait_for_stop(timeout=10.0)
        assert stop.get("reason") == "breakpoint"

        # Inspect variables — x and y should be set
        variables = await session.client.get_all_variables()
        assert "x" in variables
        assert variables["x"]["value"] == "1"
        assert "y" in variables
        assert variables["y"]["value"] == "2"

    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_node_step_and_evaluate(sample_js: Path):
    """Step over and evaluate expressions in Node.js."""
    session = DebugSession()

    try:
        info = await session.start(
            program=str(sample_js),
            stop_on_entry=True,
            port=15751,
            language="node",
        )

        stop = await session.client.wait_for_stop(timeout=10.0)

        # Set breakpoint on line 3, continue to it
        await session.client.set_breakpoints(str(sample_js), [3])
        await session.client.continue_execution()
        await session.client.wait_for_stop(timeout=10.0)

        # Step over (z = x + y)
        await session.client.next_step()
        stop = await session.client.wait_for_stop(timeout=10.0)

        # z should now be 3
        variables = await session.client.get_all_variables()
        assert "z" in variables
        assert variables["z"]["value"] == "3"

        # Evaluate an expression
        result = await session.client.evaluate("x + y * 10")
        assert result.get("result") == "21"

    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_node_function_step_into(function_js: Path):
    """Step into a function call in Node.js."""
    session = DebugSession()

    try:
        await session.start(
            program=str(function_js),
            stop_on_entry=True,
            port=15752,
            language="node",
        )
        await session.client.wait_for_stop(timeout=10.0)

        # Set breakpoint on line 5 (let result = add(3, 4))
        await session.client.set_breakpoints(str(function_js), [5])
        await session.client.continue_execution()
        await session.client.wait_for_stop(timeout=10.0)

        # Step into the add function
        await session.client.step_in()
        stop = await session.client.wait_for_stop(timeout=10.0)

        # We should be inside the add function
        frames = await session.client.get_stack_trace()
        assert frames[0].get("name") == "add"

        # Check function parameters
        variables = await session.client.get_all_variables()
        assert "a" in variables
        assert variables["a"]["value"] == "3"
        assert "b" in variables
        assert variables["b"]["value"] == "4"

    finally:
        await session.stop()
