"""Ponto de entrada do processo A2A."""

import asyncio
import os

from argos_a2a_agent.bootstrap import load_runtime_dependencies, run_server
from argos_a2a_agent.settings import Settings


async def main() -> None:
    settings = Settings.from_env()
    settings.validate_runtime()
    dependencies = await load_runtime_dependencies(os.getenv("A2A_RUNTIME_FACTORY"))
    await run_server(settings, dependencies=dependencies)


if __name__ == "__main__":
    asyncio.run(main())
