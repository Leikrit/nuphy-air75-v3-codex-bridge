from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
SCRIPT = PROJECT_ROOT / "air75_v3_windows_bridge.py"


def _load_bridge():
    fake_hid = types.ModuleType("hid")
    fake_hid.enumerate = lambda _vendor, _product: []
    fake_hid.device = object
    sys.modules["hid"] = fake_hid
    spec = importlib.util.spec_from_file_location("air75_v3_windows_bridge", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_notifier():
    path = PROJECT_ROOT / "codex_notify_spool.py"
    spec = importlib.util.spec_from_file_location("codex_notify_spool", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_keyed_read_request_encrypts_only_routing_bytes() -> None:
    bridge = _load_bridge()
    report = bridge._keyed_frame(0xD5, 17, 0, 1, (), 0x5A)

    assert report[0:2] == bytes((0x55, 0xD5))
    assert report[4:8] == bytes((17 ^ 0x5A, 0x5A, 0x5A, 1 ^ 0x5A))
    assert report[8:] == bytes(56)
    assert report[3] == bridge.checksum(report)


def test_keyed_write_request_encrypts_payload() -> None:
    bridge = _load_bridge()
    payload = bytes(range(17))
    report = bridge._keyed_frame(0xD6, 17, 0, 1, payload, 0x33)

    assert report[8:25] == bytes(value ^ 0x33 for value in payload)
    assert report[25:] == bytes(39)


def test_light_state_match_allows_only_rgb_quantization() -> None:
    bridge = _load_bridge()
    expected = bytes(range(17))
    rgb_adjusted = bytearray(expected)
    rgb_adjusted[14] += 1
    assert bridge.Air75V3._matches_light_state(expected, bytes(rgb_adjusted))

    non_rgb_adjusted = bytearray(expected)
    non_rgb_adjusted[10] += 1
    assert not bridge.Air75V3._matches_light_state(expected, bytes(non_rgb_adjusted))


def test_air75_firmware_payload_order_and_minimum() -> None:
    bridge = _load_bridge()
    keyboard = object.__new__(bridge.Air75V3)
    keyboard._firmware_version = None
    keyboard.transact = lambda *_args, **_kwargs: (
        bytes(8) + bytes((0x0E, 0x00, 0x01, 0xAA, 0x06, 0x00, 0xBD, 0xBD))
    )

    assert keyboard.require_supported_firmware() == (1, 0, 14, 6)


def test_air75_old_firmware_is_rejected_before_lighting() -> None:
    bridge = _load_bridge()
    keyboard = object.__new__(bridge.Air75V3)
    keyboard._firmware_version = None
    keyboard.transact = lambda *_args, **_kwargs: (
        bytes(8) + bytes((0x0D, 0x00, 0x01, 0xAA, 0x06, 0x00, 0xBD, 0xBD))
    )

    try:
        keyboard.require_supported_firmware()
    except RuntimeError as exc:
        assert "1.0.13.6" in str(exc)
        assert "1.0.14.6" in str(exc)
    else:
        raise AssertionError("old Air75 V3 firmware was accepted")


def test_remote_tail_expands_remote_home_without_unquoted_path() -> None:
    bridge = _load_bridge()
    command = bridge._remote_tail_command("~/.codex/events file.jsonl")

    assert command == "tail -n 0 -F -- \"$HOME\"/'.codex/events file.jsonl'"


def test_remote_spool_excludes_assistant_content(tmp_path, monkeypatch) -> None:
    notifier = _load_notifier()
    spool = tmp_path / "keyboard-events.jsonl"
    monkeypatch.setenv("CODEX_EVENT_SPOOL", str(spool))

    notifier._append_marker({
        "type": "agent-turn-complete",
        "last-assistant-message": "private response",
    })

    event = json.loads(spool.read_text(encoding="utf-8"))
    assert event["type"] == "agent-turn-complete"
    assert set(event) == {"type", "timestamp"}
    assert "last-assistant-message" not in event
    assert "private response" not in spool.read_text(encoding="utf-8")
    assert spool.stat().st_mode & 0o777 == 0o600
