"""Failure artifacts for terminal runs that stop before a normal result exists."""

from __future__ import annotations

import json
from pathlib import Path

from ..io import atomic_write_text
from ..trace import TraceRecorder


_TERMINAL_RUN_ARTIFACTS = (
    "result.json",
    "trace.jsonl",
    "execution-error.json",
)


def prepare_terminal_run_output(output_dir: str | Path) -> Path:
    """Remove files from an earlier attempt before reusing an output directory."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for filename in _TERMINAL_RUN_ARTIFACTS:
        (destination / filename).unlink(missing_ok=True)
    return destination


def persist_terminal_run_failure(
    *,
    output_dir: str | Path,
    trace: TraceRecorder,
    case_id: str,
    model_label: str,
    error: Exception,
) -> tuple[Path, Path]:
    """Write one inspectable failure document and the trace collected so far."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    trace_path = destination / "trace.jsonl"
    error_path = destination / "execution-error.json"
    failure = {
        "status": "screening_execution_failed",
        "phase": "screening_workflow",
        "case_id": case_id,
        "model": model_label,
        "error": {
            "type": type(error).__name__,
            "message": str(error),
        },
    }
    trace.record(
        cycle=max((item.cycle for item in trace.events), default=0),
        actor="screening_workflow",
        event="execution_failed",
        input_refs=[case_id],
        output=failure,
    )
    atomic_write_text(
        error_path,
        json.dumps(failure, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace.write_jsonl(trace_path)
    return error_path, trace_path


__all__ = ["persist_terminal_run_failure", "prepare_terminal_run_output"]
