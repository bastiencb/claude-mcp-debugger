"""Smoke tests for Java debugging via JDT LS + java-debug plugin."""

import asyncio
import shutil

import pytest

from mcp_debugger.session import DebugSession

_SKIP_NO_JAVA = pytest.mark.skipif(
    shutil.which("java") is None,
    reason="Java (JDK) not found in PATH",
)

SAMPLE_JAVA = """\
import java.util.List;
import java.util.ArrayList;

public class Main {
    public static void main(String[] args) {
        List<String> names = new ArrayList<>();
        names.add("Alice");
        names.add("Bob");

        int total = names.size();
        String first = names.get(0);
        System.out.println("Total: " + total + ", First: " + first);
    }
}
"""


@pytest.fixture
def sample_java(tmp_path):
    """Create a simple Java source file."""
    f = tmp_path / "Main.java"
    f.write_text(SAMPLE_JAVA)
    return str(f)


async def _launch_java(program, cwd, port, stop_on_entry=True):
    """Helper to launch a Java debug session."""
    session = DebugSession()
    await session.start(
        program=program,
        cwd=cwd,
        stop_on_entry=stop_on_entry,
        port=port,
        language="java",
    )
    if stop_on_entry:
        await session.client.wait_for_stop(timeout=20.0)
    return session


@_SKIP_NO_JAVA
@pytest.mark.asyncio
async def test_java_launch_and_variables(sample_java, tmp_path):
    """Launch a Java program, hit a breakpoint, inspect variables."""
    session = await _launch_java(sample_java, str(tmp_path), 15950)
    try:
        # Set breakpoint at line 11 (int total = names.size())
        bps = await session.client.set_breakpoints(sample_java, [11])
        assert any(b.get("verified") for b in bps), f"Breakpoint not verified: {bps}"

        # Continue to breakpoint
        await session.client.continue_execution()
        stop = await session.client.wait_for_stop(timeout=20.0)
        assert stop.get("reason") in ("breakpoint", "step", "entry")

        # Inspect variables
        frames = await session.client.get_stack_trace()
        assert frames, "No stack frames"

        scopes = await session.client.get_scopes(frames[0]["id"])
        assert scopes, "No scopes"

        all_vars = {}
        for scope in scopes:
            ref = scope.get("variablesReference", 0)
            if ref:
                variables = await session.client.get_variables(ref)
                for v in variables:
                    all_vars[v["name"]] = v

        assert "names" in all_vars, f"Expected 'names' in variables, got: {list(all_vars.keys())}"

    finally:
        await session.stop()


@_SKIP_NO_JAVA
@pytest.mark.asyncio
async def test_java_step_over(sample_java, tmp_path):
    """Step over lines in a Java program."""
    session = await _launch_java(sample_java, str(tmp_path), 15951)
    try:
        # Get initial line
        frames = await session.client.get_stack_trace()
        line1 = frames[0].get("line")

        # Step over
        await session.client.next_step()
        await session.client.wait_for_stop(timeout=10.0)

        frames2 = await session.client.get_stack_trace()
        line2 = frames2[0].get("line")

        assert line2 > line1, f"Expected line to advance: {line1} -> {line2}"

    finally:
        await session.stop()
