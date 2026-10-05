"""How often each glossary term and spelling is heard.

The workers publish every transcript line as `likho.transcription.segment.v1` on the NATS subject
`likho.live.segment` (stream LIKHO_LIVE). This module takes those lines with a durable consumer,
finds the workspace's terms and spellings in them, and counts them. A spelling also keeps the last
few lines it was applied to, as before/after examples.

Counts are "lines heard": a recording transcribed twice is heard twice. A line redelivered after a
crash may be counted twice as well; the counts guide a person, they are not an audit.
"""

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from nats.errors import TimeoutError as NatsTimeoutError
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy

log = logging.getLogger(__name__)

SEGMENT_SUBJECT = "likho.live.segment"
SEGMENT_STREAM = "LIKHO_LIVE"

# A word character for the boundary check: letters and digits of any script, plus the Devanagari
# block (its vowel signs are combining marks, which \w does not cover).
_WORD = r"[\wऀ-ॿ]"


class Matcher:
    """Finds which entries occur in a line, each as a whole word or phrase.

    Every entry is looked for on its own, so "Triphala" and "Triphala Churna" are both found in
    "Triphala Churna lijiye". Latin text matches regardless of case; inner whitespace is normalised
    on both sides.
    """

    def __init__(self, entries: Iterable[tuple[str, str]]) -> None:
        self._patterns: list[tuple[str, re.Pattern[str]]] = []
        for entry_id, text in entries:
            text = " ".join(text.split())
            if text:
                self._patterns.append(
                    (entry_id, re.compile(rf"(?<!{_WORD}){re.escape(text)}(?!{_WORD})", re.IGNORECASE))
                )

    def __len__(self) -> int:
        return len(self._patterns)

    def find(self, line: str) -> set[str]:
        """The ids of the entries found in the line."""
        if not self._patterns or not line:
            return set()
        line = " ".join(line.split())
        return {entry_id for entry_id, pattern in self._patterns if pattern.search(line)}


def heard_at(event: dict[str, Any]) -> datetime:
    """When the line was heard: the event's time, or now."""
    value = event.get("time")
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


class SegmentConsumer:
    """Takes transcript lines from the bus and counts the vocabulary in them."""

    def __init__(self, js: Any, store: Any, durable: str, start: str, metrics: Any = None) -> None:
        self._js = js
        self._store = store
        self._durable = durable
        self._start = start
        self._metrics = metrics
        self._subscription: Any = None

    async def start(self) -> None:
        config = ConsumerConfig(
            durable_name=self._durable,
            ack_policy=AckPolicy.EXPLICIT,
            ack_wait=30,
            max_deliver=5,
            deliver_policy=DeliverPolicy.NEW if self._start == "new" else DeliverPolicy.ALL,
            filter_subject=SEGMENT_SUBJECT,
        )
        self._subscription = await self._js.pull_subscribe(
            SEGMENT_SUBJECT, durable=self._durable, stream=SEGMENT_STREAM, config=config
        )
        log.info("counting lines from %s as %s", SEGMENT_SUBJECT, self._durable)

    async def run(self, stop: asyncio.Event) -> None:
        """Fetch and count lines until stop is set."""
        while not stop.is_set():
            try:
                messages = await self._subscription.fetch(50, timeout=1)
            except (NatsTimeoutError, TimeoutError):
                continue
            except Exception:
                log.exception("could not fetch lines; trying again")
                await asyncio.sleep(1)
                continue
            for message in messages:
                await self.handle(message)
        with contextlib.suppress(Exception):
            await self._subscription.unsubscribe()

    def _count(self, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.events_handled.add(1, {"subject": SEGMENT_SUBJECT, "outcome": outcome})

    async def handle(self, message: Any) -> None:
        try:
            event = json.loads(message.data)
            data = event["data"]
            segment = data["segment"]
            workspace_id = str(data.get("workspace_id") or "")
            recording_id = str(data["recording_id"])
            index = int(segment["index"])
            text_script = str(segment.get("text_script") or "")
            text_roman = str(segment.get("text_roman") or "")
        except (ValueError, KeyError, TypeError) as error:
            log.error("dropping a message that is not a line: %s", error)
            self._count("dropped")
            await message.term()
            return
        if not workspace_id:
            # A worker older than contracts 0.9 does not say which workspace the line belongs to.
            self._count("skipped")
            await message.ack()
            return
        try:
            terms, spellings = await self._store.count_line(
                workspace_id, recording_id, index, text_script, text_roman, heard_at(event)
            )
        except Exception:
            log.exception("could not count line %d of %s; trying again", index, recording_id)
            self._count("retry")
            await message.nak(delay=2)
            return
        if self._metrics is not None:
            self._metrics.lines.add(1, {"outcome": "hit" if terms or spellings else "miss"})
        self._count("ok")
        await message.ack()
