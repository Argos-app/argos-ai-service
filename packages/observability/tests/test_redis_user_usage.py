"""Contadores efêmeros de chamadas e tokens por identidade autenticada."""

from __future__ import annotations

import asyncio
from uuid import UUID

from packages.observability.redis_user_usage import RedisUserUsageCounter


class _Pipeline:
    def __init__(self, client):
        self.client = client
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def __getattr__(self, command):
        def add(*args, **kwargs):
            self.commands.append((command, args, kwargs))

        return add

    async def execute(self):
        for command, args, kwargs in self.commands:
            getattr(self.client, command)(*args, **kwargs)


class _Redis:
    def __init__(self):
        self.hashes = {}
        self.sets = {}
        self.expirations = {}

    def pipeline(self, transaction=True):
        return _Pipeline(self)

    def hincrby(self, key, field, amount):
        values = self.hashes.setdefault(key, {})
        values[field] = int(values.get(field, 0)) + amount

    def hset(self, key, *, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def expire(self, key, ttl):
        self.expirations[key] = ttl

    async def eval(
        self, _script, numkeys, key, seen_key, observation_id, token_count, complete, ttl
    ):
        assert numkeys == 2
        seen = self.sets.setdefault(seen_key, set())
        if observation_id in seen:
            return 0
        seen.add(observation_id)
        self.hincrby(key, "model_calls", 1)
        if token_count == "unknown":
            self.hincrby(key, "unknown_token_calls", 1)
        else:
            self.hincrby(key, "tokens", int(token_count))
            if complete == "false":
                self.hincrby(key, "unknown_token_calls", 1)
        self.expire(key, int(ttl))
        self.expire(seen_key, int(ttl))
        return 1

    async def scan_iter(self, match):
        prefix = match.removesuffix("*")
        for key in self.hashes:
            if key.startswith(prefix):
                yield key

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def aclose(self):
        return None


def test_calls_and_reported_tokens_share_a_24_hour_user_key():
    async def run():
        redis = _Redis()
        usage = RedisUserUsageCounter(redis)
        user_id = UUID("7a1a86ec-0f2f-4a44-8db0-378fe3dad102")

        await usage.increment_call(user_id)
        await usage.record_model_usage(
            user_id,
            {"total_tokens": 15},
            observation_id="model-observation-1",
        )
        await usage.record_model_usage(
            user_id,
            {"total_tokens": 15},
            observation_id="model-observation-1",
        )
        await usage.record_model_usage(
            user_id,
            {"availability": "absent"},
            observation_id="model-observation-2",
        )
        await usage.record_model_usage(
            user_id,
            {"input_tokens": 6},
            observation_id="model-observation-3",
        )

        [row] = await usage.list_users()

        assert row == {
            "user_id": str(user_id),
            "name": None,
            "email": None,
            "calls": 1,
            "tokens": 21,
            "model_calls": 3,
            "unknown_token_calls": 2,
        }
        assert redis.expirations[usage.key_for(user_id)] == 86_400

    asyncio.run(run())


def test_identity_refresh_preserves_counters_and_survives_model_updates():
    async def run():
        redis = _Redis()
        usage = RedisUserUsageCounter(redis)
        user_id = UUID("7a1a86ec-0f2f-4a44-8db0-378fe3dad102")
        await usage.increment_call(user_id, name="José Antigo", email="antigo@example.test")
        await usage.record_model_usage(user_id, {"total_tokens": 10}, observation_id="model-1")
        await usage.increment_call(user_id, name="José Atual", email="atual@example.test")
        await usage.record_model_usage(user_id, {"total_tokens": 5}, observation_id="a2a-1")

        [row] = await usage.list_users()
        assert row == {
            "user_id": str(user_id),
            "name": "José Atual",
            "email": "atual@example.test",
            "calls": 2,
            "tokens": 15,
            "model_calls": 2,
            "unknown_token_calls": 0,
        }
        assert redis.expirations[usage.key_for(user_id)] == 86_400

    asyncio.run(run())


def test_ranking_decodes_identity_bytes_and_keeps_legacy_users_without_identity():
    async def run():
        redis = _Redis()
        usage = RedisUserUsageCounter(redis)
        identified = UUID("7a1a86ec-0f2f-4a44-8db0-378fe3dad102")
        legacy = UUID("7a1a86ec-0f2f-4a44-8db0-378fe3dad103")
        redis.hashes[usage.key_for(identified)] = {
            b"name": "José".encode(),
            b"email": b"jose@example.test",
            b"tokens": b"42",
        }
        redis.hashes[usage.key_for(legacy)] = {b"calls": b"3"}

        first, second = await usage.list_users()
        assert first["name"] == "José"
        assert first["email"] == "jose@example.test"
        assert first["tokens"] == 42
        assert second["name"] is None
        assert second["email"] is None
        assert second["calls"] == 3

    asyncio.run(run())
