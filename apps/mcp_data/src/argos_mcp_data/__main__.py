"""Ponto de entrada do serviço MCP de dados."""

import asyncio

from argos_mcp_data.bootstrap import run_server

if __name__ == "__main__":
    asyncio.run(run_server())
