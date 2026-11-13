import pytest
from pydantic_ai import models

# Tests never reach a real LLM: the agent's model is swapped for a scripted FunctionModel (see test_agent.Script).
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture
def anyio_backend():
    return "asyncio"
