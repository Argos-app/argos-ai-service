"""Ponto de entrada do processo A2A."""

import asyncio

from argos_a2a_agent.bootstrap import run_server

if __name__ == "__main__":
    asyncio.run(run_server())
