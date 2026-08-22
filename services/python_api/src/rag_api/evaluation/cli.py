"""Command-line entry point for deterministic evaluation replay reports."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from .io import load_records, write_run_artifacts
from .metrics import evaluate_records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 JSONL 中间结果生成分阶段 RAG 评估报告"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--run-id")
    arguments = parser.parse_args()

    baseline = None
    if arguments.baseline is not None:
        with arguments.baseline.open("r", encoding="utf-8") as source:
            baseline = json.load(source)
        if not isinstance(baseline, dict):
            parser.error("baseline must contain a JSON object")

    run_id = arguments.run_id or datetime.now(timezone.utc).strftime(
        "eval-%Y%m%dT%H%M%SZ"
    )
    summary = evaluate_records(load_records(arguments.input))
    summary_path, report_path = write_run_artifacts(
        arguments.output_dir,
        run_id=run_id,
        summary=summary,
        baseline=baseline,
        source_sha256=sha256(arguments.input.read_bytes()).hexdigest(),
    )
    print(summary_path)
    print(report_path)


if __name__ == "__main__":
    main()
