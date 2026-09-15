"""Minimal Wyoming protocol client — just enough to prove a voice service is alive.

The Wyoming protocol (used by whisper/piper/openWakeWord) is newline-delimited
JSON header events over a plain TCP socket. A header does *not* embed its
payload inline: `data_length` (if present) says how many bytes of follow-on
JSON to read for the event's `data`, and `payload_length` (if present) says
how many raw bytes follow after that. Every Wyoming service answers a
`describe` event with an `info` event listing what it can do (models loaded,
languages, etc.) — that's a real protocol round trip, not just "the port
accepts a connection," without us needing to ship an audio sample through
the test suite.
"""

from __future__ import annotations

import json
import socket

DESCRIBE_EVENT = b'{"type": "describe", "data": {}}\n'


def describe(host: str, port: int, timeout: float = 3.0) -> dict:
    """Send a `describe` event and return the decoded `info` response, with
    its `data` (if any) resolved from the follow-on `data_length` bytes.

    Raises OSError/socket.timeout on connection failure and ValueError if the
    service doesn't answer with a well-formed Wyoming event.
    """
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(DESCRIBE_EVENT)

        with sock.makefile("rb") as f:
            line = f.readline()
            if not line:
                raise ValueError(f"no response from wyoming service at {host}:{port}")

            event = json.loads(line.decode("utf-8"))
            if event.get("type") != "info":
                raise ValueError(f"expected 'info' event, got {event.get('type')!r}: {event}")

            data_length = event.get("data_length")
            if data_length:
                event["data"] = json.loads(f.read(data_length).decode("utf-8"))

        return event
