"""Test runner defaults shared by every application and package."""

import asyncio
import sys

import pytest


@pytest.hookimpl
def pytest_asyncio_loop_factories(config, item):
    """Use the selector loop on Windows for local async tests."""
    del config, item
    if sys.platform == "win32":
        return {"windows-selector": asyncio.SelectorEventLoop}
    return None
