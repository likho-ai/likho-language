"""Fixtures for the integration tests: a real service on free ports against the likho-infra stack.

Start the stack first:  likho-infra> bash scripts/up.sh   (or .\\stack.ps1 up)
Without it these tests are skipped locally; with LIKHO_REQUIRE_STACK=1 (set in CI) they fail instead.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass

import grpc
import pytest
import pytest_asyncio
from likho.language.v1 import language_pb2_grpc

from likho_language.__main__ import serve
from likho_language.db import make_engine, make_sessions
from likho_language.ids import new_id
from likho_language.settings import Settings
from likho_language.vocabulary import VocabularyStore


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@dataclass
class Service:
    settings: Settings
    stub: language_pb2_grpc.LanguageServiceStub
    channel: grpc.aio.Channel
    store: VocabularyStore  # a second connection to the same database, for set-up and clean-up


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def service() -> AsyncIterator[Service]:
    settings = Settings(
        grpc_port=_free_port(),
        http_port=_free_port(),
        vocabulary_ttl_seconds=0,  # every call sees the latest version, so tests need no sleeps
        segment_durable="test-" + new_id("lang")[-12:].lower(),  # this run's own consumer of the lines
        segment_start="new",
        log_level="WARNING",
    )
    # postgresql+asyncpg://user:pass@host:port/db  and  nats://host:port
    db_host, db_port = settings.database_url.rsplit("@", 1)[1].split("/", 1)[0].split(":")
    nats_host, nats_port = settings.nats_url.split("//", 1)[1].split(":")
    missing = [
        name
        for name, host, port in (("PostgreSQL", db_host, db_port), ("NATS", nats_host, nats_port))
        if not _reachable(host, int(port))
    ]
    if missing:
        message = f"{' and '.join(missing)} not reachable; start the likho-infra stack"
        if os.environ.get("LIKHO_REQUIRE_STACK") == "1":
            pytest.fail(message)
        pytest.skip(message)

    stop = asyncio.Event()
    task = asyncio.create_task(serve(settings, stop))
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{settings.grpc_port}")
    try:
        await asyncio.wait_for(channel.channel_ready(), timeout=30)
    except TimeoutError:
        stop.set()
        await task  # surfaces the start-up error
        raise

    engine = make_engine(settings.database_url)
    yield Service(
        settings, language_pb2_grpc.LanguageServiceStub(channel), channel, VocabularyStore(make_sessions(engine))
    )

    await channel.close()
    await engine.dispose()
    stop.set()
    await asyncio.wait_for(task, timeout=30)
    # The durable consumer would otherwise stay on the server, holding this run's lines.
    import nats

    connection = await nats.connect(settings.nats_url)
    try:
        await connection.jetstream().delete_consumer("LIKHO_LIVE", settings.segment_durable)
    except Exception:
        pass
    finally:
        await connection.close()


@pytest_asyncio.fixture(loop_scope="session")
async def workspace(service: Service) -> AsyncIterator[str]:
    """A workspace of its own for each test, removed afterwards."""
    workspace_id = new_id("wsp")
    yield workspace_id
    await service.store.delete_workspace(workspace_id)
