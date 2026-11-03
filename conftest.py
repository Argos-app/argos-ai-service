"""Configurações do executor de testes compartilhadas por aplicações e pacotes."""

import asyncio
import sys

import pytest


@pytest.hookimpl
def pytest_asyncio_loop_factories(config, item):
    """Usa uma fábrica de loop de eventos compatível na CI e no desenvolvimento."""
    del config, item
    if sys.platform == "win32":
        return {"windows-selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}
