import pytest

from app import state


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "PATH", tmp_path / "agent.sqlite3")
    state.initialize()


@pytest.fixture(autouse=True)
def no_roster(monkeypatch):
    from app import agent

    async def empty():
        return []
    monkeypatch.setattr(agent, "student_roster", empty)
