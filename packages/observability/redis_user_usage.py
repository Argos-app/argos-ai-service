"""Contadores efêmeros de chamadas e tokens por usuário no Redis."""

from __future__ import annotations

from typing import Any
from uuid import UUID

_TTL_SECONDS = 24 * 60 * 60


class RedisUserUsageCounter:
    """Incrementa uso por usuário; cada incremento renova o TTL de 24 horas."""

    def __init__(self, client: Any, *, key_prefix: str = "argos:usage:users:v1") -> None:
        self._client = client
        self._key_prefix = key_prefix.rstrip(":")

    def key_for(self, user_id: str | UUID) -> str:
        return f"{self._key_prefix}:{UUID(str(user_id))}"

    async def increment_call(self, user_id: str | UUID) -> None:
        key = self.key_for(user_id)
        async with self._client.pipeline(transaction=True) as pipeline:
            pipeline.hincrby(key, "calls", 1)
            pipeline.expire(key, _TTL_SECONDS)
            await pipeline.execute()

    async def record_model_usage(
        self,
        user_id: str | UUID | None,
        usage: dict[str, Any] | None,
        *,
        observation_id: str,
    ) -> None:
        if user_id is None:
            return
        key = self.key_for(user_id)
        seen_key = f"{self._key_prefix}:_seen:{UUID(str(user_id))}"
        token_count, complete = _reported_token_count(usage)
        script = """
        if redis.call('SADD', KEYS[2], ARGV[1]) == 0 then return 0 end
        redis.call('EXPIRE', KEYS[2], ARGV[4])
        redis.call('HINCRBY', KEYS[1], 'model_calls', 1)
        if ARGV[2] == 'unknown' then
            redis.call('HINCRBY', KEYS[1], 'unknown_token_calls', 1)
        else
            redis.call('HINCRBY', KEYS[1], 'tokens', ARGV[2])
            if ARGV[3] == 'false' then
                redis.call('HINCRBY', KEYS[1], 'unknown_token_calls', 1)
            end
        end
        redis.call('EXPIRE', KEYS[1], ARGV[4])
        return 1
        """
        await self._client.eval(
            script,
            2,
            key,
            seen_key,
            observation_id,
            "unknown" if token_count is None else token_count,
            "true" if complete else "false",
            _TTL_SECONDS,
        )

    async def list_users(self) -> list[dict[str, int | str]]:
        rows: list[dict[str, int | str]] = []
        async for raw_key in self._client.scan_iter(match=f"{self._key_prefix}:*"):
            key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
            if ":_seen:" in key:
                continue
            raw = await self._client.hgetall(key)
            values = {
                (field.decode() if isinstance(field, bytes) else field): int(value)
                for field, value in raw.items()
            }
            rows.append(
                {
                    "user_id": key.removeprefix(f"{self._key_prefix}:"),
                    "calls": values.get("calls", 0),
                    "tokens": values.get("tokens", 0),
                    "model_calls": values.get("model_calls", 0),
                    "unknown_token_calls": values.get("unknown_token_calls", 0),
                }
            )
        rows.sort(key=lambda row: (int(row["tokens"]), int(row["calls"])), reverse=True)
        return rows

    async def close(self) -> None:
        await self._client.aclose()


def _reported_token_count(usage: dict[str, Any] | None) -> tuple[int | None, bool]:
    if not usage:
        return None, False
    total = usage.get("total_tokens")
    if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
        return total, True
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    valid_input = (
        isinstance(input_tokens, int)
        and not isinstance(input_tokens, bool)
        and input_tokens >= 0
    )
    valid_output = (
        isinstance(output_tokens, int)
        and not isinstance(output_tokens, bool)
        and output_tokens >= 0
    )
    known_counts = [
        value
        for value, valid in ((input_tokens, valid_input), (output_tokens, valid_output))
        if valid
    ]
    if known_counts:
        return sum(known_counts), valid_input and valid_output
    return None, False
