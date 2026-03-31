"""Shared test fixtures for ProtoNeo."""

import pytest

from protoneo.config.schema import ProtoNeoConfig


@pytest.fixture
def default_config():
    """A minimal ProtoNeoConfig for tests that need one."""
    return ProtoNeoConfig()


@pytest.fixture
def hpdc26_profile():
    """Load the HPDC 2026 conference profile."""
    from apps.paper_review.conference import load_profile
    return load_profile("hpdc26")


@pytest.fixture
def sc26_profile():
    """Load the SC 2026 conference profile."""
    from apps.paper_review.conference import load_profile
    return load_profile("sc26")
