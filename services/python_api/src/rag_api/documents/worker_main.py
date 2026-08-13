"""Backward-compatible entry point for the unified ingestion worker."""

from rag_api.ingestion.worker_main import main, run


if __name__ == "__main__":
    main()
