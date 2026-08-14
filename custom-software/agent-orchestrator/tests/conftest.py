"""Shared pytest fixtures."""

import pytest

from agent.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        litellm_base_url="http://litellm:4000/v1",
        qdrant_host="qdrant",
        max_iterations=5,
        token_budget=1000,
    )
