"""Publishes this service's events to NATS JetStream as CloudEvents.

The event contract is likho-contracts/events/likho.vocabulary.updated.v1.schema.json.
A failed publish is logged and does not fail the change that caused it: every response of
this service carries the vocabulary version, so a consumer that missed the event still
notices that its copy is stale.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from likho_language.ids import new_id

log = logging.getLogger(__name__)

SOURCE = "likho-language"
VOCABULARY_UPDATED_TYPE = "likho.vocabulary.updated.v1"
VOCABULARY_UPDATED_SUBJECT = "likho.vocabulary.updated"


def vocabulary_updated(workspace_id: str, kind: str, version: int) -> dict[str, Any]:
    """The CloudEvent for a change to a workspace's glossary, spellings or policy."""
    return {
        "specversion": "1.0",
        "id": new_id("evt"),
        "source": SOURCE,
        "type": VOCABULARY_UPDATED_TYPE,
        "time": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "subject": workspace_id,
        "datacontenttype": "application/json",
        "data": {"workspace_id": workspace_id, "kind": kind, "version": version},
    }


class Publisher(Protocol):
    async def vocabulary_updated(self, workspace_id: str, kind: str, version: int) -> None: ...


class NullPublisher:
    """Used when no event bus is configured (unit tests, the seed command)."""

    async def vocabulary_updated(self, workspace_id: str, kind: str, version: int) -> None:
        return None


class NatsPublisher:
    def __init__(self, url: str) -> None:
        self._url = url
        self._nc: Any = None
        self._js: Any = None

    async def connect(self, timeout_seconds: float = 0.0) -> None:
        """Connects, trying again while NATS is not there yet, for `timeout_seconds` (0 = one try)."""
        import asyncio
        import logging

        import nats

        log = logging.getLogger(__name__)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        wait = 1.0
        while True:
            try:
                self._nc = await nats.connect(self._url, name=SOURCE, max_reconnect_attempts=-1, connect_timeout=5)
                self._js = self._nc.jetstream()
                return
            except Exception as error:
                if asyncio.get_running_loop().time() + wait > deadline:
                    raise
                log.warning("event bus not ready (%s); trying again in %.0f s", error, wait)
                await asyncio.sleep(wait)
                wait = min(wait * 2, 10.0)

    @property
    def connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

    @property
    def js(self) -> Any:
        """The JetStream context, for consumers; None while not connected."""
        return self._js

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()
            self._nc = None

    async def vocabulary_updated(self, workspace_id: str, kind: str, version: int) -> None:
        event = vocabulary_updated(workspace_id, kind, version)
        if self._js is None:
            log.warning("event bus not connected; %s for %s not published", event["type"], workspace_id)
            return
        try:
            # The event id is the message id, so a retried publish is stored once.
            await self._js.publish(
                VOCABULARY_UPDATED_SUBJECT,
                json.dumps(event, ensure_ascii=False).encode(),
                headers={"Nats-Msg-Id": event["id"]},
                timeout=3,
            )
        except Exception:
            log.exception("could not publish %s for %s (version %d)", event["type"], workspace_id, version)
