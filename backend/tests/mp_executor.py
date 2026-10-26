"""Importable callbacks used by spawn-based multiprocessing tests."""

import os
from pathlib import Path


def append_task(task):
    marker = Path(task["marker"])
    with marker.open("a", encoding="utf-8") as stream:
        stream.write(f"{os.getpid()}:{task['job_id']}\n")


def raise_task(task):
    raise RuntimeError(f"injected failure for {task['job_id']}")
