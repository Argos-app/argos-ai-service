"""Readiness checks for local Kubernetes app pods (stdlib only)."""

from __future__ import annotations

import os
import socket
import sys
import urllib.error
import urllib.request


def tcp(host: str, port: int) -> None:
    with socket.create_connection((host, port), timeout=2):
        return


def http(url: str, headers: dict[str, str] | None = None) -> None:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=3) as response:
        if response.status >= 400:
            raise RuntimeError(f"HTTP status {response.status}: {url}")


def main(service: str) -> None:
    checks: dict[str, tuple[tuple[str, int], ...]] = {
        "main-api": (
            ("mongo", 27017),
            ("redis", 6379),
            ("mcp-data", 8000),
            ("mcp-prediction", 8000),
            ("mcp-email", 8000),
            ("a2a-agent", 8000),
        ),
        "mcp-data": (("mongo", 27017), ("postgres", 5432)),
        "mcp-prediction": (("postgres", 5432),),
        "mcp-email": (("mongo", 27017), ("postgres", 5432), ("mailpit", 1025)),
        "a2a-agent": (("mongo", 27017), ("postgres", 5432), ("redis", 6379), ("qdrant", 6333)),
    }
    for host, port in checks[service]:
        tcp(host, port)
    tcp("127.0.0.1", 8000)
    if service == "main-api":
        http("http://127.0.0.1:8000/health/ready")
    elif service == "a2a-agent":
        http(
            "http://127.0.0.1:8000/health/ready",
            {"Authorization": f"Bearer {os.environ['A2A_INTERNAL_SERVICE_TOKEN']}"},
        )


if __name__ == "__main__":
    try:
        main(sys.argv[1])
    except (KeyError, OSError, RuntimeError, urllib.error.URLError, IndexError) as exc:
        print(f"readiness check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
