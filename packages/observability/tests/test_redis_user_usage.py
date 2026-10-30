"""Contadores efêmeros de chamadas e tokens por identidade autenticada."""

from __future__ import annotations

import asyncio
from decimal import Decimal
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
        self, _script, numkeys, key, seen_key, observation_id, token_count, complete, ttl, cost
    ):
        assert numkeys == 2
        seen = self.sets.setdefault(seen_key, set())
        if observation_id in seen:
            return 0
        seen.add(observation_id)
        self.hincrby(key, "model_calls", 1)
        if cost != "unknown":
            values = self.hashes.setdefault(key, {})
            values["cost_usd"] = str(Decimal(values.get("cost_usd", "0")) + Decimal(cost))
            self.hincrby(key, "known_cost_calls", 1)
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
            "cost_usd": None,
            "known_cost_calls": 0,
            "unknown_cost_calls": 3,
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
            "cost_usd": None,
            "known_cost_calls": 0,
            "unknown_cost_calls": 2,
        }
        assert redis.expirations[usage.key_for(user_id)] == 86_400

    asyncio.run(run())


def test_ranking_handles_current_partial_usage_hash_without_identity_metadata():
    async def run():
        redis = _Redis()
        usage = RedisUserUsageCounter(redis)
        call_only = UUID("7a1a86ec-0f2f-4a44-8db0-378fe3dad103")
        model_only = UUID("7a1a86ec-0f2f-4a44-8db0-378fe3dad104")
        await usage.increment_call(call_only)
        await usage.increment_call(call_only)
        await usage.increment_call(call_only)
        await usage.record_model_usage(
            model_only, {"total_tokens": 42}, observation_id="model-only-observation"
        )

        rows = {row["user_id"]: row for row in await usage.list_users()}
        assert rows[str(call_only)] == {
            "user_id": str(call_only),
            "name": None,
            "email": None,
            "calls": 3,
            "tokens": 0,
            "model_calls": 0,
            "unknown_token_calls": 0,
            "cost_usd": None,
            "known_cost_calls": 0,
            "unknown_cost_calls": 0,
        }
        assert rows[str(model_only)] == {
            "user_id": str(model_only),
            "name": None,
            "email": None,
            "calls": 0,
            "tokens": 42,
            "model_calls": 1,
            "unknown_token_calls": 0,
            "cost_usd": None,
            "known_cost_calls": 0,
            "unknown_cost_calls": 1,
        }

    asyncio.run(run())


def test_costs_are_deduplicated_and_unknown_costs_are_visible():
    async def run():
        redis = _Redis()
        counter = RedisUserUsageCounter(redis)
        user = UUID("7a1a86ec-0f2f-4a44-8db0-378fe3dad102")
        billing = {"reported_cost": "0.0125", "currency": "USD", "cost_source": "provider"}
        await counter.record_model_usage(user, None, observation_id="one", billing=billing)
        await counter.record_model_usage(user, None, observation_id="one", billing=billing)
        await counter.record_model_usage(user, None, observation_id="two")
        [row] = await counter.list_users()
        assert Decimal(row["cost_usd"]) == Decimal("0.0125")
        assert row["known_cost_calls"] == 1
        assert row["unknown_cost_calls"] == 1

    asyncio.run(run())
