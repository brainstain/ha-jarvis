"""Shared fixtures: endpoint config, latency budgets, reachability gating,
and the perf-report hooks that fire at the end of the session.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from endpoints import Budgets, Endpoints, get_budgets, get_endpoints
from perf import PERF, PerfRecorder

REPORTS_DIR = Path(__file__).parent / "reports"


# ──────────────────────────────────────────────────────────────────────
# Config fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def endpoints() -> Endpoints:
    return get_endpoints()


@pytest.fixture(scope="session")
def budgets() -> Budgets:
    return get_budgets()


@pytest.fixture(scope="session")
def perf() -> PerfRecorder:
    return PERF


# ──────────────────────────────────────────────────────────────────────
# Reachability — probe once per session, skip tests for whatever is down
# instead of failing (or hanging) on every test that touches it.
# ──────────────────────────────────────────────────────────────────────


def _probe_http(url: str, timeout: float = 3.0) -> bool:
    try:
        httpx.get(url, timeout=timeout)
        return True
    except httpx.HTTPError:
        return False


def _probe_tcp(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def reachability(endpoints: Endpoints) -> dict[str, bool]:
    checks: dict[str, bool] = {
        "litellm": _probe_http(f"{endpoints.litellm_url}/health"),
        "qdrant": _probe_http(f"{endpoints.qdrant_url}/readyz"),
        "orchestrator": _probe_http(f"{endpoints.orchestrator_url}/health"),
        "open_webui": _probe_http(endpoints.open_webui_url),
        "ollama_agent": _probe_http(f"{endpoints.ollama_agent_url}/api/tags"),
        "ollama_inference": _probe_http(f"{endpoints.ollama_inference_url}/api/tags"),
        "speechbrain": _probe_http(f"{endpoints.speechbrain_url}/health"),
        "wyoming_whisper": _probe_tcp(endpoints.wyoming_whisper_host, endpoints.wyoming_whisper_port),
        "wyoming_piper": _probe_tcp(endpoints.wyoming_piper_host, endpoints.wyoming_piper_port),
        "wyoming_wakeword": _probe_tcp(endpoints.wyoming_wakeword_host, endpoints.wyoming_wakeword_port),
        "home_assistant": bool(endpoints.ha_token) and _probe_http(f"{endpoints.ha_url}/api/"),
    }
    return checks


def require(reachability: dict[str, bool], *names: str) -> None:
    """Skip the current test if any named backing service is unreachable.

    Call this as the first line of a test, not a fixture, so the skip
    reason is attributed to the test that needed the dependency.
    """
    missing = [n for n in names if not reachability.get(n)]
    if missing:
        pytest.skip(
            f"unreachable, skipping: {', '.join(missing)} "
            "(start the stack / set the matching endpoint env vars)"
        )


# ──────────────────────────────────────────────────────────────────────
# Perf report — written once, at the very end of the run.
# ──────────────────────────────────────────────────────────────────────


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    stats = PERF.stats()
    if not stats:
        return

    REPORTS_DIR.mkdir(exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exit_status": exitstatus,
        "operations": stats,
    }

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (REPORTS_DIR / f"perf-{ts}.json").write_text(json.dumps(payload, indent=2, sort_keys=True))
    (REPORTS_DIR / "latest.json").write_text(json.dumps(payload, indent=2, sort_keys=True))


def pytest_terminal_summary(terminalreporter, exitstatus: int, config) -> None:
    stats = PERF.stats()
    if not stats:
        return

    terminalreporter.write_sep("=", "response-time summary (seconds)")
    header = f"{'operation':<38}{'n':>4}{'min':>8}{'mean':>8}{'p50':>8}{'p95':>8}{'max':>8}"
    terminalreporter.write_line(header)
    terminalreporter.write_line("-" * len(header))
    for name in sorted(stats):
        s = stats[name]
        terminalreporter.write_line(
            f"{name:<38}{s['count']:>4}{s['min']:>8.2f}{s['mean']:>8.2f}"
            f"{s['p50']:>8.2f}{s['p95']:>8.2f}{s['max']:>8.2f}"
        )
    terminalreporter.write_line(f"\nfull report: {REPORTS_DIR / 'latest.json'}")
