#!/usr/bin/env python3
"""Write an anonymous Codex lifecycle marker to a local JSONL spool.

Codex invokes the configured ``notify`` command with one JSON event argument.
This helper performs no network requests and stores only the event type and a
Unix timestamp. It deliberately omits prompts, responses, commands, tool
output, paths, hostnames, session IDs, and credentials.

Set ``CODEX_EVENT_SPOOL`` to an absolute path in the environment file loaded by
the Codex process. The Windows Air75 bridge can follow that file over SSH.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_EVENT_TYPE = "agent-turn-complete"
SPOOL_ENV = "CODEX_EVENT_SPOOL"
LOG_ENV = "CODEX_EVENT_LOG"


def _log(message: str) -> None:
    print(f"codex-spool: {message}", file=sys.stderr)
    log_value = os.environ.get(LOG_ENV, "").strip()
    if not log_value:
        return
    try:
        path = Path(log_value).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {message}\n")
    except OSError:
        pass


def _load_codex_env() -> None:
    """Load only spool-related variables without executing the env file."""
    if os.environ.get(SPOOL_ENV):
        return
    codex_home = os.environ.get("CODEX_HOME", "").strip()
    env_path = Path(codex_home or (Path.home() / ".codex")) / "env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line.startswith("export "):
            continue
        name, separator, raw_value = line[7:].strip().partition("=")
        if not separator or name not in (SPOOL_ENV, LOG_ENV) or os.environ.get(name):
            continue
        try:
            values = shlex.split(raw_value, comments=True, posix=True)
        except ValueError:
            continue
        if len(values) == 1:
            os.environ[name] = values[0]


def _event_type(event: dict[str, Any]) -> str:
    value = event.get("type", event.get("event_type", DEFAULT_EVENT_TYPE))
    return str(value).strip() or DEFAULT_EVENT_TYPE


def _append_marker(event: dict[str, Any]) -> None:
    spool_value = os.environ.get(SPOOL_ENV, "").strip()
    if not spool_value:
        _log(f"{SPOOL_ENV} is not set; event skipped")
        return

    path = Path(spool_value).expanduser()
    marker = {"type": _event_type(event), "timestamp": int(time.time())}
    line = (json.dumps(marker, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            remaining = memoryview(line)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("short write to Codex event spool")
                remaining = remaining[written:]
        finally:
            os.close(descriptor)
        path.chmod(0o600)
    except OSError as exc:
        _log(f"event skipped: {exc}")


def main() -> int:
    if len(sys.argv) < 2:
        _log("missing event JSON argument")
        return 0
    try:
        event = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        _log(f"invalid event JSON: {exc}")
        return 0
    if not isinstance(event, dict):
        _log("event JSON is not an object")
        return 0

    _load_codex_env()
    _append_marker(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
