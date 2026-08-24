"""Command-line entry point for the transactional-outbox publisher."""

from __future__ import annotations

import asyncio
import logging

from rag_api.config import Settings
from rag_api.db.session import create_database_engine, create_session_factory
from rag_api.kafka.runtime import create_outbox_publisher


async def _run() -> None:
    settings = Settings()
    engine = create_database_engine(settings)
    try:
        publisher = create_outbox_publisher(
            settings,
            session_factory=create_session_factory(engine),
        )
        await publisher.run_forever()
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == "__main__":
    main()
