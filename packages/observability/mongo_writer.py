"""Writer Mongo assíncrono com fila limitada, retry e drenagem limitada."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import deque
from dataclasses import dataclass
from time import monotonic
from typing import Any

from pymongo.errors import (
    AutoReconnect,
    ConnectionFailure,
    NotPrimaryError,
    WTimeoutError,
)

from packages.observability.context import current_user_id
from packages.observability.models import Observation
from packages.observability.ports import SubmissionResult
from packages.observability.redis_user_usage import RedisUserUsageCounter
from packages.observability.sanitization import sanitize_observation

from .mongo_repository import MongoObservationRepository, ObservationConflictError

_LOGGER = logging.getLogger(__name__)
_TRANSIENT_MONGO_ERRORS = (AutoReconnect, ConnectionFailure, NotPrimaryError, WTimeoutError)


@dataclass(frozen=True, slots=True)
class _QueuedDocument:
    document: dict[str, Any]
    size_bytes: int
    user_id: str | None


class MongoObservationWriter:
    """Mantém somente itens limitados em memória; nunca grava spool local."""

    def __init__(
        self,
        repository: MongoObservationRepository,
        *,
        user_usage_counter: RedisUserUsageCounter | None = None,
        max_items: int = 2_048,
        max_bytes: int = 8 * 1024 * 1024,
        max_document_bytes: int = 16 * 1024,
        batch_size: int = 100,
        batch_interval_ms: int = 250,
        max_attempts: int = 3,
        retry_base_delay_ms: int = 50,
        shutdown_timeout_ms: int = 2_000,
        report_failure=None,
    ) -> None:
        if (
            min(
                max_items,
                max_bytes,
                max_document_bytes,
                batch_size,
                batch_interval_ms,
                max_attempts,
                shutdown_timeout_ms,
            )
            < 1
            or retry_base_delay_ms < 0
        ):
            raise ValueError("limites de writer inválidos")
        if batch_size > max_items or max_document_bytes > max_bytes:
            raise ValueError("limites de lote ou documento excedem a fila")

        self._repository = repository
        self._user_usage_counter = user_usage_counter
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._max_document_bytes = max_document_bytes
        self._batch_size = batch_size
        self._batch_interval_seconds = batch_interval_ms / 1000
        self._max_attempts = max_attempts
        self._retry_base_delay_seconds = retry_base_delay_ms / 1000
        self._shutdown_timeout_seconds = shutdown_timeout_ms / 1000
        self._report_failure = report_failure or self._log_failure
        self._queue: deque[_QueuedDocument] = deque()
        self._available = asyncio.Event()
        self._pending_items = 0
        self._pending_bytes = 0
        self._started = False
        self._closing = False
        self._closed = False
        self._worker_task: asyncio.Task[None] | None = None
        self._index_task: asyncio.Task[None] | None = None
        self._last_report_at: dict[str, float] = {}

    async def start(self) -> None:
        if self._started or self._closed:
            return
        self._started = True
        self._index_task = asyncio.create_task(self._ensure_indexes())
        self._worker_task = asyncio.create_task(self._run())

    def try_submit(self, observation: Observation) -> SubmissionResult:
        if not self._started or self._closing or self._closed:
            return SubmissionResult.NOT_RUNNING
        try:
            document, size_bytes = self._repository.document_for(sanitize_observation(observation))
        except Exception:
            return SubmissionResult.INVALID_DOCUMENT
        if size_bytes > self._max_document_bytes:
            return SubmissionResult.DOCUMENT_TOO_LARGE
        if (
            self._pending_items >= self._max_items
            or self._pending_bytes + size_bytes > self._max_bytes
        ):
            return SubmissionResult.QUEUE_FULL

        current_user = current_user_id()
        self._queue.append(
            _QueuedDocument(
                document=document,
                size_bytes=size_bytes,
                user_id=str(current_user) if current_user is not None else None,
            )
        )
        self._pending_items += 1
        self._pending_bytes += size_bytes
        self._available.set()
        return SubmissionResult.ACCEPTED

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        self._available.set()
        tasks = [task for task in (self._worker_task, self._index_task) if task is not None]
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.gather(*tasks)),
                    timeout=self._shutdown_timeout_seconds,
                )
            except TimeoutError:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self._drop_queued()
                self._report("shutdown_pending")
        self._closed = True

    async def _ensure_indexes(self) -> None:
        try:
            await self._repository.ensure_indexes()
        except Exception:
            self._report("index_creation_failed")

    async def _run(self) -> None:
        while True:
            batch = await self._take_batch()
            if not batch:
                if self._closing:
                    return
                continue
            try:
                await self._flush_batch(batch)
            finally:
                self._release(batch)

    async def _take_batch(self) -> list[_QueuedDocument]:
        while not self._queue:
            if self._closing:
                return []
            self._available.clear()
            if self._queue:
                break
            await self._available.wait()

        batch = [self._queue.popleft()]
        deadline = asyncio.get_running_loop().time() + self._batch_interval_seconds
        while len(batch) < self._batch_size:
            while self._queue and len(batch) < self._batch_size:
                batch.append(self._queue.popleft())
            if self._closing or len(batch) >= self._batch_size:
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            self._available.clear()
            if self._queue:
                continue
            try:
                await asyncio.wait_for(self._available.wait(), timeout=remaining)
            except TimeoutError:
                break
        return batch

    async def _flush_batch(self, batch: list[_QueuedDocument]) -> None:
        groups: dict[str, list[_QueuedDocument]] = {}
        for item in batch:
            groups.setdefault(item.document["_id"], []).append(item)
        await asyncio.gather(*(self._write_group(items) for items in groups.values()))

    async def _write_group(self, items: list[_QueuedDocument]) -> None:
        for item in items:
            await self._write_with_retry(item.document, item.user_id)

    async def _write_with_retry(self, document: dict[str, Any], user_id: str | None) -> None:
        if self._user_usage_counter is not None:
            try:
                if (
                    document.get("operation_kind") in {"model", "embedding"}
                    and document.get("status") != "started"
                ):
                    await self._user_usage_counter.record_model_usage(
                        user_id,
                        document.get("usage"),
                        observation_id=document.get("_id"),
                    )
            except Exception:
                self._report("redis_user_usage_write_failed")
        for attempt in range(1, self._max_attempts + 1):
            try:
                await self._repository.write_document(document)
                return
            except ObservationConflictError:
                self._report("document_conflict")
                return
            except _TRANSIENT_MONGO_ERRORS:
                if attempt == self._max_attempts:
                    self._report("mongo_unavailable")
                    return
                backoff = self._retry_base_delay_seconds * (2 ** (attempt - 1))
                await asyncio.sleep(random.uniform(backoff * 0.75, backoff * 1.25))
            except Exception:
                self._report("mongo_write_failed")
                return

    def _release(self, batch: list[_QueuedDocument]) -> None:
        self._pending_items -= len(batch)
        self._pending_bytes -= sum(item.size_bytes for item in batch)

    def _drop_queued(self) -> None:
        dropped = list(self._queue)
        self._queue.clear()
        if dropped:
            self._release(dropped)

    def _report(self, code: str) -> None:
        now = monotonic()
        if now - self._last_report_at.get(code, 0) < 30:
            return
        self._last_report_at[code] = now
        try:
            self._report_failure(code)
        except Exception:
            return

    @staticmethod
    def _log_failure(code: str) -> None:
        _LOGGER.warning("falha no writer de observabilidade", extra={"failure_code": code})
