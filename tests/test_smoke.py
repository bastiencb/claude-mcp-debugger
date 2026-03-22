"""Smoke tests: launch scripts, hit breakpoints, inspect variables, test exceptions."""

import asyncio
import textwrap
from pathlib import Path

import pytest

from mcp_debugger.session import DebugSession


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def sample_script(tmp_path: Path) -> Path:
    """Create a minimal Python script to debug."""
    script = tmp_path / "sample.py"
    script.write_text(textwrap.dedent("""\
        x = 1
        y = 2
        z = x + y
        print(z)
    """))
    return script


@pytest.mark.asyncio
async def test_launch_breakpoint_and_inspect(sample_script: Path):
    """Full lifecycle: launch → breakpoint → continue → inspect → stop."""
    session = DebugSession()

    try:
        info = await session.start(
            program=str(sample_script),
            stop_on_entry=True,
            port=15679,  # avoid conflict with a running debugger
        )
        assert info["program"] == str(sample_script)
        assert info["port"] == 15679

        # We stopped on entry (line 1: x = 1)
        stop = await session.client.wait_for_stop(timeout=5.0)
        assert stop.get("reason") in ("breakpoint", "entry", "step")

        # Set breakpoint on line 3 (z = x + y)
        bps = await session.client.set_breakpoints(str(sample_script), [3])
        assert any(bp.get("verified") for bp in bps)

        # Continue to breakpoint
        await session.client.continue_execution()
        stop = await session.client.wait_for_stop(timeout=5.0)
        assert stop.get("reason") == "breakpoint"

        # Inspect variables — x and y should be set
        variables = await session.client.get_all_variables()
        assert "x" in variables
        assert variables["x"]["value"] == "1"
        assert "y" in variables
        assert variables["y"]["value"] == "2"

        # Step over (z = x + y)
        await session.client.next_step()
        stop = await session.client.wait_for_stop(timeout=5.0)

        # Now z should exist
        variables = await session.client.get_all_variables()
        assert "z" in variables
        assert variables["z"]["value"] == "3"

        # Let the script finish (executes print(z))
        await session.client.continue_execution()
        await session.client.wait_for_stop(timeout=5.0)  # terminated event
        await asyncio.sleep(0.5)  # let output collection catch up

    finally:
        output = await session.stop()

    # The script prints "3"
    assert "3" in output


# ── Phase A tests ──────────────────────────────────────────────


@pytest.fixture
def exception_script(tmp_path: Path) -> Path:
    """Script that raises an exception."""
    script = tmp_path / "raise_error.py"
    script.write_text(textwrap.dedent("""\
        def divide(a, b):
            return a / b

        result = divide(10, 0)
    """))
    return script


@pytest.fixture
def expandable_script(tmp_path: Path) -> Path:
    """Script with complex data structures (dict, list)."""
    script = tmp_path / "data_structures.py"
    script.write_text(textwrap.dedent("""\
        data = {"name": "Alice", "scores": [90, 85, 92]}
        items = [1, 2, 3]
        x = 42
        print("done")
    """))
    return script


@pytest.mark.asyncio
async def test_exception_info(exception_script: Path):
    """When stopped on an exception, exception_info returns details."""
    session = DebugSession()

    try:
        await session.start(
            program=str(exception_script),
            stop_on_entry=False,
            port=15680,
        )

        # Set exception breakpoint on uncaught exceptions
        await session.client.set_exception_breakpoints(["uncaught"])

        # Continue — will hit the ZeroDivisionError
        stop = await session.client.wait_for_stop(timeout=10.0)
        assert stop.get("reason") == "exception"

        # Fetch exception info
        exc_info = await session.client.exception_info()
        assert "ZeroDivisionError" in exc_info.get("exceptionId", "")
        assert exc_info.get("description", "") != ""

    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_capabilities_stored(sample_script: Path):
    """After initialize, capabilities dict is populated."""
    session = DebugSession()

    try:
        await session.start(
            program=str(sample_script),
            stop_on_entry=True,
            port=15681,
        )
        await session.client.wait_for_stop(timeout=5.0)

        caps = session.capabilities
        assert isinstance(caps, dict)
        assert len(caps) > 0
        # debugpy should support conditional breakpoints
        assert caps.get("supportsConditionalBreakpoints") is True

    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_expandable_variables(expandable_script: Path):
    """Variables with sub-elements have variablesReference > 0."""
    session = DebugSession()

    try:
        await session.start(
            program=str(expandable_script),
            stop_on_entry=True,
            port=15682,
        )
        await session.client.wait_for_stop(timeout=5.0)

        # Set breakpoint on print("done") — all vars assigned by then
        await session.client.set_breakpoints(str(expandable_script), [4])
        await session.client.continue_execution()
        await session.client.wait_for_stop(timeout=5.0)

        # Get variables — data (dict) and items (list) should be expandable
        frames = await session.client.get_stack_trace()
        scopes = await session.client.get_scopes(frames[0]["id"])
        local_scope = next(s for s in scopes if "local" in s.get("name", "").lower())
        variables = await session.client.get_variables(local_scope["variablesReference"])

        var_dict = {v["name"]: v for v in variables}
        # dict and list should have variablesReference > 0
        assert var_dict["data"]["variablesReference"] > 0
        assert var_dict["items"]["variablesReference"] > 0
        # int should not be expandable
        assert var_dict["x"]["variablesReference"] == 0

        # Expand the dict variable
        data_ref = var_dict["data"]["variablesReference"]
        sub_vars = await session.client.get_variables(data_ref)
        sub_names = [v["name"] for v in sub_vars]
        assert "name" in sub_names or "'name'" in sub_names

    finally:
        await session.stop()


# ── Phase B tests ──────────────────────────────────────────────


@pytest.fixture
def long_running_script(tmp_path: Path) -> Path:
    """Script that runs for a while (for pause testing)."""
    script = tmp_path / "long_running.py"
    script.write_text(textwrap.dedent("""\
        import time
        for i in range(100):
            time.sleep(0.1)
        print("done")
    """))
    return script


@pytest.fixture
def cleanup_script(tmp_path: Path) -> Path:
    """Script that runs for a while (for terminate testing)."""
    script = tmp_path / "cleanup.py"
    script.write_text(textwrap.dedent("""\
        import time
        print("started")
        time.sleep(10)
        print("should not reach here")
    """))
    return script


@pytest.mark.asyncio
async def test_expand_variable(expandable_script: Path):
    """debug_expand_variable can drill into dict/list contents."""
    session = DebugSession()

    try:
        await session.start(
            program=str(expandable_script),
            stop_on_entry=True,
            port=15683,
        )
        await session.client.wait_for_stop(timeout=5.0)

        # Set breakpoint on print("done"), continue to it
        await session.client.set_breakpoints(str(expandable_script), [4])
        await session.client.continue_execution()
        await session.client.wait_for_stop(timeout=5.0)

        # Get the data variable's reference
        frames = await session.client.get_stack_trace()
        scopes = await session.client.get_scopes(frames[0]["id"])
        local_scope = next(s for s in scopes if "local" in s.get("name", "").lower())
        variables = await session.client.get_variables(local_scope["variablesReference"])

        data_var = next(v for v in variables if v["name"] == "data")
        data_ref = data_var["variablesReference"]
        assert data_ref > 0

        # Expand it
        sub_vars = await session.client.get_variables(data_ref)
        sub_dict = {v["name"]: v for v in sub_vars}
        # Should have 'name' and 'scores' keys (quoted in debugpy)
        assert any("name" in k for k in sub_dict)
        assert any("scores" in k for k in sub_dict)

    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_pause(long_running_script: Path):
    """debug_pause stops a running thread."""
    session = DebugSession()

    try:
        await session.start(
            program=str(long_running_script),
            stop_on_entry=False,
            port=15684,
        )

        # Let it run a bit, then pause
        await asyncio.sleep(0.5)
        await session.client.pause()
        stop = await session.client.wait_for_stop(timeout=5.0)
        assert stop.get("reason") == "pause"

    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_terminate(cleanup_script: Path):
    """debug_terminate gracefully stops the session without crashing."""
    session = DebugSession()

    try:
        await session.start(
            program=str(cleanup_script),
            stop_on_entry=False,
            port=15685,
        )

        # Let it start
        await asyncio.sleep(0.5)
        output = await session.terminate()
        # Session should be cleanly stopped
        assert not session.is_active
        # "should not reach here" should NOT appear (program was interrupted)
        assert "should not reach here" not in output

    finally:
        if session.is_active:
            await session.stop()


# ── Phase C tests ──────────────────────────────────────────────


@pytest.fixture
def loop_script(tmp_path: Path) -> Path:
    """Script with a loop (for hit condition testing)."""
    script = tmp_path / "loop.py"
    script.write_text(textwrap.dedent("""\
        total = 0
        for i in range(10):
            total += i
        print(total)
    """))
    return script


@pytest.fixture
def function_script(tmp_path: Path) -> Path:
    """Script with named functions."""
    script = tmp_path / "functions.py"
    script.write_text(textwrap.dedent("""\
        def greet(name):
            return f"Hello, {name}!"

        def add(a, b):
            return a + b

        msg = greet("World")
        result = add(3, 4)
        print(msg, result)
    """))
    return script


@pytest.mark.asyncio
async def test_hit_condition(loop_script: Path):
    """Breakpoint with hit condition fires on Nth hit."""
    session = DebugSession()

    try:
        await session.start(
            program=str(loop_script),
            stop_on_entry=True,
            port=15686,
        )
        await session.client.wait_for_stop(timeout=5.0)

        # Set breakpoint on line 3 (total += i) with hit condition "3"
        await session.client.set_breakpoints(
            str(loop_script), [3],
            hit_conditions={3: "3"},
        )
        await session.client.continue_execution()
        stop = await session.client.wait_for_stop(timeout=5.0)
        assert stop.get("reason") == "breakpoint"

        # At the 3rd hit, i should be 2 (0-indexed: i=0,1,2)
        variables = await session.client.get_all_variables()
        assert variables["i"]["value"] == "2"

    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_function_breakpoint(function_script: Path):
    """Function breakpoint fires when the function is called."""
    session = DebugSession()

    try:
        await session.start(
            program=str(function_script),
            stop_on_entry=True,
            port=15687,
        )
        await session.client.wait_for_stop(timeout=5.0)

        # Set function breakpoint on "add"
        bps = await session.client.set_function_breakpoints(["add"])
        assert any(bp.get("verified") for bp in bps)

        await session.client.continue_execution()
        stop = await session.client.wait_for_stop(timeout=5.0)
        assert "breakpoint" in stop.get("reason", "")

        # We should be inside the add function
        frames = await session.client.get_stack_trace()
        assert frames[0].get("name") == "add"

        variables = await session.client.get_all_variables()
        assert variables["a"]["value"] == "3"
        assert variables["b"]["value"] == "4"

    finally:
        await session.stop()


@pytest.mark.asyncio
async def test_set_variable(sample_script: Path):
    """Modify a variable mid-execution and verify the change."""
    session = DebugSession()

    try:
        await session.start(
            program=str(sample_script),
            stop_on_entry=True,
            port=15688,
        )
        await session.client.wait_for_stop(timeout=5.0)

        # Set breakpoint on line 3 (z = x + y), continue to it
        await session.client.set_breakpoints(str(sample_script), [3])
        await session.client.continue_execution()
        await session.client.wait_for_stop(timeout=5.0)

        # Modify x from 1 to 100
        frames = await session.client.get_stack_trace()
        scopes = await session.client.get_scopes(frames[0]["id"])
        local_scope = next(s for s in scopes if "local" in s.get("name", "").lower())

        result = await session.client.set_variable(
            local_scope["variablesReference"], "x", "100"
        )
        assert result.get("value") == "100"

        # Step over (z = x + y) — z should now be 100 + 2 = 102
        await session.client.next_step()
        await session.client.wait_for_stop(timeout=5.0)

        variables = await session.client.get_all_variables()
        assert variables["z"]["value"] == "102"

    finally:
        await session.stop()
