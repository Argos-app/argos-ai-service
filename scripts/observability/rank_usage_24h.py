"""Lista o uso temporário de chamadas e tokens por usuário no Redis."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from packages.observability.redis_user_usage import RedisUserUsageCounter


def _rank_users(users: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_calls = sorted(users, key=lambda row: (row["calls"], row["tokens"]), reverse=True)
    by_tokens = sorted(users, key=lambda row: (row["tokens"], row["calls"]), reverse=True)
    return {"by_calls": by_calls, "by_tokens": by_tokens}


async def _main() -> None:
    redis_url = os.getenv("OBSERVABILITY_REDIS_URL")
    if not redis_url:
        raise SystemExit("Configure OBSERVABILITY_REDIS_URL para consultar o uso no Redis.")
    from redis.asyncio import Redis

    client = Redis.from_url(redis_url, decode_responses=False, socket_connect_timeout=5)
    counter = RedisUserUsageCounter(
        client,
        key_prefix=os.getenv("USER_USAGE_REDIS_KEY_PREFIX", "argos:usage:users:v1"),
    )
    try:
        users = await counter.list_users()
    finally:
        await counter.close()

    print(
        json.dumps(
            {
                "source": "redis",
                "key_ttl_seconds": 86_400,
                "ttl_behavior": "renovado a cada chamada ou atualização de tokens",
                "completeness": "best_effort_incomplete",
                "limitations": [
                    "Contagens são efêmeras e desaparecem 24 horas após a última atualização.",
                    "Falhas de Redis ou da fila de observabilidade podem perder incrementos.",
                    "Tokens ausentes do provider são contabilizados separadamente "
                    "como desconhecidos.",
                    "Tokens são somados somente uma vez por observação de modelo no Redis.",
                ],
                "rankings": _rank_users(users),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(_main())
