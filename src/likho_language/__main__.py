"""Starts the service:  python -m likho_language   (or the `likho-language` command)."""

import asyncio
import contextlib
import json
import logging
import signal
import sys
from datetime import UTC, datetime

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from likho.language.v1 import language_pb2, language_pb2_grpc
from sqlalchemy import text

from likho_language import __version__
from likho_language.db import make_engine, make_sessions, upgrade
from likho_language.events import NatsPublisher
from likho_language.grpc_server import LanguageServicer
from likho_language.health import start_health_server
from likho_language.settings import Settings
from likho_language.vocabulary import VocabularyStore

log = logging.getLogger("likho_language")

SERVICE_NAME = language_pb2.DESCRIPTOR.services_by_name["LanguageService"].full_name


class JsonFormatter(logging.Formatter):
    """One JSON object per log line."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)


async def serve(settings: Settings, stop: asyncio.Event | None = None) -> None:
    """Run until `stop` is set (or the process is told to stop)."""
    stop = stop or asyncio.Event()

    if settings.migrate_on_start:
        await asyncio.to_thread(upgrade, settings.database_url)
        log.info("database is up to date")

    engine = make_engine(settings.database_url)
    store = VocabularyStore(make_sessions(engine), ttl_seconds=settings.vocabulary_ttl_seconds)

    publisher = NatsPublisher(settings.nats_url)
    try:
        await publisher.connect()
        log.info("connected to the event bus at %s", settings.nats_url)
    except Exception:
        # The service still answers; a change is then visible through the version in each response.
        log.exception("event bus not reachable at %s; changes will not be announced", settings.nats_url)

    server = grpc.aio.server(options=[("grpc.max_receive_message_length", 8 * 1024 * 1024)])
    language_pb2_grpc.add_LanguageServiceServicer_to_server(LanguageServicer(store, publisher), server)
    health_servicer = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    await health_servicer.set(SERVICE_NAME, health_pb2.HealthCheckResponse.SERVING)
    await health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    server.add_insecure_port(f"[::]:{settings.grpc_port}")
    await server.start()

    async def ready() -> bool:
        async with engine.connect() as connection:
            await connection.execute(text("select 1"))
        return True

    http = await start_health_server(settings.http_port, ready)
    log.info("likho-language %s: gRPC on %d, health on %d", __version__, settings.grpc_port, settings.http_port)

    await stop.wait()

    log.info("stopping: finishing calls in flight")
    await health_servicer.set(SERVICE_NAME, health_pb2.HealthCheckResponse.NOT_SERVING)
    await server.stop(grace=10)
    http.close()
    await http.wait_closed()
    await publisher.close()
    await engine.dispose()


async def _run() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        # Windows has no loop.add_signal_handler; signal.signal works for Ctrl+C there.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
            continue
        signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
    await serve(settings, stop)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
