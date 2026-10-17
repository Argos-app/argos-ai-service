"""Test runner defaults shared by every application and package."""

import asyncio
import sys

import pytest


@pytest.hookimpl
def pytest_asyncio_loop_factories(config, item):
    """Use a supported event loop factory on every CI and developer platform."""
    del config, item
    if sys.platform == "win32":
        return {"windows-selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}
