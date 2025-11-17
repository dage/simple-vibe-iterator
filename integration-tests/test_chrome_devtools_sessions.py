"""Session manager tests for Chrome DevTools."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.chrome_devtools_service import ChromeDevToolsSessionManager


class _FakeService:
    _counter = 0

    def __init__(self) -> None:
        self.instance_id = _FakeService._counter
        _FakeService._counter += 1
        self.closed = False

    async def aclose(self) -> None:
        # Simulate async cleanup latency
        await asyncio.sleep(0)
        self.closed = True


@pytest.mark.asyncio
async def test_session_manager_allocates_one_service_per_agent() -> None:
    manager = ChromeDevToolsSessionManager(factory=_FakeService)

    agent_one = await manager.get_session("agent-one")
    agent_two = await manager.get_session("agent-two")

    assert agent_one is not agent_two
    assert agent_one.instance_id == 0
    assert agent_two.instance_id == 1

    # Same agent should receive the cached instance.
    again = await manager.get_session("agent-one")
    assert again is agent_one

    # Releasing frees the instance and closes it.
    await manager.release_session("agent-one")
    assert agent_one.closed is True

    replacement = await manager.get_session("agent-one")
    assert replacement is not agent_one
    assert replacement.instance_id == 2

    # Cleanup remaining session to avoid warnings.
    await manager.release_session("agent-two")
    await manager.release_session("agent-one")
