"""Ponto de entrada do processo A2A."""

import asyncio

from argos_a2a_agent.bootstrap import run_server
from argos_a2a_agent.settings import Settings


async def main() -> None:
    settings = Settings.from_env()
    settings.validate_runtime(require_providers=settings.model_mode != "simulated")
    await run_server(settings)


if __name__ == "__main__":
    asyncio.run(main())
