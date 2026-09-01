#!/usr/bin/env python3
"""Experimental Windows bridge for NuPhy Air75 V3 completion lighting.

The script is intentionally narrow: it only opens the Air75 V3 USB-C
configuration interface (VID 0x19F5, PID 0x1028, HID usage 1:0), uses the
documented S4 session handshake plus D5/D6 lighting commands, and never sends
firmware, reset, keymap, or factory-restore commands.

Install the dependency on Windows with:
    py -3.11 -m pip install hidapi

Examples:
    py -3.11 air75_v3_windows_bridge.py describe
    py -3.11 air75_v3_windows_bridge.py test
    py -3.11 air75_v3_windows_bridge.py listen --config air75_v3_bridge.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, Iterable

try:
    import hid  # type: ignore
except ImportError as exc:  # pragma: no cover - exercised on the target PC
    raise SystemExit(
        "缺少 hidapi，请先执行: py -3.11 -m pip install hidapi"
    ) from exc


VENDOR_ID = 0x19F5
PRODUCT_ID = 0x1028
USAGE_PAGE = 0x01
USAGE = 0x00
REPORT_SIZE = 64
MAX_PAYLOAD = REPORT_SIZE - 8
REPORT_ID = 0
HANDSHAKE = 0xEE
GET_FIRMWARE_INFO = 0xA1
GET_LIGHT_STATE = 0xD5
SET_LIGHT_STATE = 0xD6
WINDOWS_LIGHT_HANDLE = 1
STATIC_SIDE_MODE = 2
DEFAULT_HOLD_SECONDS = 4.0
MINIMUM_FIRMWARE = (1, 0, 14, 6)


def checksum(report: bytes | bytearray) -> int:
    return sum(report[4:REPORT_SIZE]) & 0xFF


def _frame(command: int, length: int, address: int, handle: int,
           payload: Iterable[int] = ()) -> bytes:
    report = bytearray(REPORT_SIZE)
    report[0] = 0x55
    report[1] = command
    report[4] = length & 0xFF
    report[5] = address & 0xFF
    report[6] = (address >> 8) & 0xFF
    report[7] = handle & 0xFF
    data = list(payload)
    if len(data) > MAX_PAYLOAD:
        raise ValueError("Air75 V3 payload exceeds 56 bytes")
    report[8:8 + len(data)] = bytes(data)
    report[3] = checksum(report)
    return bytes(report)


def _keyed_frame(command: int, length: int, address: int, handle: int,
                 payload: Iterable[int], key: int) -> bytes:
    payload_bytes = bytes(payload)
    plain = _frame(command, length, address, handle, payload_bytes)
    report = bytearray(plain)
    for index in range(4, 8):
        report[index] ^= key
    for index in range(8, 8 + len(payload_bytes)):
        report[index] ^= key
    report[3] = checksum(report)
    return bytes(report)


def _normalize_input(data: bytes | bytearray | list[int]) -> bytes:
    raw = bytes(data)
    # hidapi on Windows may include report ID 0, while some builds omit it.
    if len(raw) >= REPORT_SIZE + 1 and raw[0] == REPORT_ID:
        raw = raw[1:]
    if len(raw) < REPORT_SIZE:
        raise RuntimeError(f"Air75 V3 returned a short HID report ({len(raw)} bytes)")
    return raw[:REPORT_SIZE]


def _device_info(item: dict[str, Any]) -> str:
    return (
        f"VID={int(item.get('vendor_id', 0)):04X} "
        f"PID={int(item.get('product_id', 0)):04X} "
        f"usage={int(item.get('usage_page', 0)):04X}:{int(item.get('usage', 0)):02X} "
        f"product={item.get('product_string') or item.get('product') or '?'} "
        f"path={item.get('path', '?')}"
    )


def enumerate_targets() -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        if int(item.get("usage_page") or 0) != USAGE_PAGE:
            continue
        if int(item.get("usage") or 0) != USAGE:
            continue
        matches.append(item)
    return matches


class Air75V3:
    """Small, serialized D5/D6 client for one exact wired interface."""

    def __init__(self, timeout_ms: int = 1500) -> None:
        targets = enumerate_targets()
        if len(targets) != 1:
            details = "\n".join(_device_info(item) for item in targets) or "(none)"
            raise RuntimeError(
                "需要恰好一个 Air75 V3 USB 配置接口 "
                f"(VID:PID 19F5:1028, usage 01:00)，实际找到 {len(targets)} 个:\n{details}"
            )
        self.info = targets[0]
        self.timeout_ms = timeout_ms
        self.device = hid.device()
        self.device.open_path(self.info["path"])
        self.device.set_nonblocking(False)
        self._lock = threading.Lock()
        self._firmware_version: tuple[int, int, int, int] | None = None

    def close(self) -> None:
        try:
            self.device.close()
        except Exception:
            pass

    def __enter__(self) -> "Air75V3":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()

    def _write(self, report: bytes) -> None:
        if len(report) != REPORT_SIZE:
            raise ValueError("Air75 V3 report must be exactly 64 bytes")
        # Windows HIDAPI expects the report ID prefix for output reports.
        written = self.device.write(bytes((REPORT_ID,)) + report)
        if written != REPORT_SIZE + 1:
            raise RuntimeError(f"short HID write ({written} of {REPORT_SIZE + 1} bytes)")

    def _read_for(self, command: int) -> bytes:
        deadline = time.monotonic() + self.timeout_ms / 1000.0
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            data = self.device.read(REPORT_SIZE + 1, remaining)
            if not data:
                continue
            response = _normalize_input(data)
            if response[0] == 0xAA and response[1] == command:
                return response
        raise TimeoutError(f"等待 Air75 V3 命令 0x{command:02X} 响应超时")

    def _send_and_receive(self, report: bytes, command: int) -> bytes:
        self._write(report)
        return self._read_for(command)

    def _session_key(self) -> int:
        challenge = bytearray(secrets.token_bytes(MAX_PAYLOAD))
        if challenge[20] == 0:
            challenge[20] = 0xAA
        request = _frame(HANDSHAKE, 0, 0, 0, challenge)
        response = self._send_and_receive(request, HANDSHAKE)
        if response[3] != checksum(response):
            raise RuntimeError("Air75 V3 握手响应校验失败")
        return challenge[20]

    def transact(self, command: int, length: int, address: int = 0,
                 handle: int = 0, payload: Iterable[int] = ()) -> bytes:
        with self._lock:
            key = self._session_key()
            request = _keyed_frame(command, length, address, handle, payload, key)
            response = self._send_and_receive(request, command)
            if response[3] != checksum(response):
                raise RuntimeError(f"Air75 V3 命令 0x{command:02X} 响应校验失败")
            expected = bytes((length & 0xFF, address & 0xFF, (address >> 8) & 0xFF, handle & 0xFF))
            raw_header = response[4:8]
            if raw_header != expected and bytes(value ^ key for value in raw_header) != expected:
                raise RuntimeError(f"Air75 V3 命令 0x{command:02X} 路由字段不匹配")
            decoded = bytearray(response)
            decoded[4:8] = expected
            for index in range(8, 8 + length):
                decoded[index] ^= key
            return bytes(decoded)

    def read_light_state(self) -> bytes:
        response = self.transact(GET_LIGHT_STATE, 17, handle=WINDOWS_LIGHT_HANDLE)
        return response[8:25]

    def firmware_version(self) -> tuple[int, int, int, int]:
        if self._firmware_version is not None:
            return self._firmware_version
        response = self.transact(GET_FIRMWARE_INFO, 8)
        payload = response[8:16]
        if len(payload) < 5 or payload[3] != 0xAA:
            raise RuntimeError("无法识别 Air75 V3 官方固件版本")
        version = (payload[2], payload[1], payload[0], payload[4])
        self._firmware_version = version
        return version

    def require_supported_firmware(self) -> tuple[int, int, int, int]:
        version = self.firmware_version()
        if version < MINIMUM_FIRMWARE:
            installed = ".".join(map(str, version))
            minimum = ".".join(map(str, MINIMUM_FIRMWARE))
            raise RuntimeError(
                f"Air75 V3 官方固件 {installed} 低于最低要求 {minimum}；"
                "请先备份 NuPhyIO 配置并只使用 NuPhyIO 官方升级"
            )
        return version

    def write_light_state(self, state: bytes) -> bytes:
        if len(state) != 17:
            raise ValueError("Air75 V3 light state must be 17 bytes")
        response = self.transact(
            SET_LIGHT_STATE,
            17,
            handle=WINDOWS_LIGHT_HANDLE,
            payload=state,
        )
        echoed = response[8:25]
        if echoed != state:
            raise RuntimeError("Air75 V3 D6 未完整回显灯光状态")
        time.sleep(0.18)
        latest = b""
        for attempt in range(5):
            if attempt:
                time.sleep(0.12)
            latest = self.read_light_state()
            if self._matches_light_state(state, latest):
                return latest
        raise RuntimeError("Air75 V3 D5 回读与目标灯光状态不一致")

    @staticmethod
    def _matches_light_state(expected: bytes, actual: bytes) -> bool:
        if len(expected) != 17 or len(actual) != 17:
            return False
        rgb_offsets = {6, 7, 8, 14, 15, 16}
        return all(
            abs(expected[index] - actual[index]) <= 1
            if index in rgb_offsets else expected[index] == actual[index]
            for index in range(17)
        )

    def show_complete(self, hold_seconds: float = DEFAULT_HOLD_SECONDS) -> None:
        self.require_supported_firmware()
        original = self.read_light_state()
        desired = bytearray(original)
        # D5/D6 sidelight fields: mode, brightness, speed, RGB flag,
        # palette index, red, green, blue at offsets 9..16.
        desired[9] = STATIC_SIDE_MODE
        desired[10] = 100
        desired[12] = 0
        desired[13] = 0
        desired[14:17] = bytes((0, 255, 0))
        try:
            self.write_light_state(bytes(desired))
            time.sleep(max(0.0, hold_seconds))
        finally:
            try:
                self.write_light_state(original)
            except Exception as exc:
                print(f"警告：恢复 Air75 V3 原灯效失败: {exc}", file=sys.stderr)


def load_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取配置 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("桥接配置必须是 JSON 对象")
    return value


def _remote_tail_command(remote_file: str) -> str:
    # Quote the path before it reaches the remote POSIX shell. Preserve a
    # leading ~/ so the remote shell expands the remote user's home directory;
    # quoting the tilde itself would disable that expansion.
    if remote_file == "~":
        path_expression = '"$HOME"'
    elif remote_file.startswith("~/"):
        path_expression = '"$HOME"/' + shlex.quote(remote_file[2:])
    else:
        path_expression = shlex.quote(remote_file)
    return f"tail -n 0 -F -- {path_expression}"


def listen(config: dict[str, Any]) -> None:
    target = str(config.get("ssh_target", "")).strip()
    remote_file = str(config.get("remote_event_file", "~/.codex/codex-events.jsonl")).strip()
    if not target or "@" not in target:
        raise RuntimeError("配置 ssh_target，例如 user@example.com")
    ssh_options = config.get("ssh_options", [])
    if not isinstance(ssh_options, list) or not all(isinstance(item, str) for item in ssh_options):
        raise RuntimeError("ssh_options 必须是字符串数组")
    hold_seconds = float(config.get("complete_hold_seconds", DEFAULT_HOLD_SECONDS))
    retry_seconds = max(1.0, float(config.get("reconnect_seconds", 3.0)))

    while True:
        command = ["ssh", *ssh_options, target, _remote_tail_command(remote_file)]
        print(f"正在监听 {target}:{remote_file}", flush=True)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            # Inherit stderr so a noisy SSH diagnostic cannot fill a pipe and
            # deadlock the event stream.
            stderr=None,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        try:
            assert process.stdout is not None
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    print("忽略远端非 JSON 行", file=sys.stderr)
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") != "agent-turn-complete":
                    continue
                try:
                    with Air75V3() as keyboard:
                        keyboard.show_complete(hold_seconds)
                    print("Codex 完成：Air75 V3 已显示绿色完成灯效", flush=True)
                except Exception as exc:
                    print(f"键盘灯效失败: {exc}", file=sys.stderr, flush=True)
        except KeyboardInterrupt:
            process.terminate()
            return
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        print(f"SSH 监听断开，{retry_seconds:g} 秒后重连", file=sys.stderr, flush=True)
        time.sleep(retry_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="NuPhy Air75 V3 Windows Codex bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("describe", help="列出严格匹配的 Air75 V3 接口")
    subparsers.add_parser("test", help="绿色侧灯测试并恢复原灯效")
    listen_parser = subparsers.add_parser("listen", help="通过 SSH 监听远端 Codex 完成事件")
    listen_parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "describe":
        targets = enumerate_targets()
        print("\n".join(_device_info(item) for item in targets) or "未找到 Air75 V3 USB 配置接口")
        return 0 if len(targets) == 1 else 1
    if args.command == "test":
        with Air75V3() as keyboard:
            print(f"已连接: {_device_info(keyboard.info)}")
            version = keyboard.require_supported_firmware()
            print(f"官方固件: {'.'.join(map(str, version))}")
            keyboard.show_complete()
        print("测试完成，原灯效已尝试恢复")
        return 0
    listen(load_config(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
