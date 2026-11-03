"""Entrada de processo do MCP de predição."""

import asyncio

from argos_mcp_prediction.bootstrap import run_server


def main() -> None:
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
