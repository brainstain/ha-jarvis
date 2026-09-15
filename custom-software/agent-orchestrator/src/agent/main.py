"""FastAPI application entry point for the Agent Orchestrator."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI
from prometheus_client import Gauge
from prometheus_fastapi_instrumentator import Instrumentator

from agent import __version__
from agent.api.openai_compat import router as openai_router
from agent.api.routes import router as api_router
from agent.api.routes import set_tools
from agent.api.websocket import ws_router
from agent.config import get_settings
from agent.integrations.ha import ha_router
from agent.mcp.registry import MCPToolRegistry

# Warmup gauge only; chat/tool metrics live in routes.py to avoid double-registration.
_model_warmup_seconds = Gauge(
    "agent_model_warmup_seconds",
    "Last model warm-up latency per model",
    ["model"],
)


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO))
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)
    log = structlog.get_logger(__name__)
    log.info(
        "orchestrator_starting",
        version=__version__,
        litellm=settings.litellm_base_url,
        qdrant=f"{settings.qdrant_host}:{settings.qdrant_port}",
    )

    # Discover MCP tools once, here, rather than lazily per request: connecting
    # up front means the first user request doesn't pay a subprocess spawn, and
    # the sessions are opened and closed by the same task.
    # strict=False — a broken MCP config degrades tool use, it does not stop
    # the service from booting and serving /health.
    mcp = MCPToolRegistry.from_config(settings.mcp_config_path, strict=False)
    try:
        await mcp.discover()
    except Exception as exc:  # noqa: BLE001 - startup must not hard-fail on MCP
        log.error("mcp_discovery_error", error=str(exc))
    set_tools(mcp)
    log.info("mcp_ready", tools=len(mcp.tool_names), failed=sorted(mcp.failed))

    # Warm up the LLM models so the first user request doesn't pay a cold-start
    # penalty. Failures are non-fatal — the request will load the model lazily.
    await _warmup_models(settings, log)

    yield

    set_tools(None)
    await mcp.close()
    log.info("orchestrator_stopping")


async def _warmup_models(settings, log) -> None:
    """Load each LLM and the embedding model into VRAM before serving traffic."""
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Chat models
        for model in list({settings.fast_model, settings.litellm_model}):
            t0 = time.monotonic()
            try:
                resp = await client.post(
                    f"{settings.litellm_base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                        "temperature": 0,
                    },
                )
                resp.raise_for_status()
                elapsed = time.monotonic() - t0
                _model_warmup_seconds.labels(model=model).set(elapsed)
                log.info("model_warmed", model=model, seconds=round(elapsed, 2))
            except Exception as exc:  # noqa: BLE001
                log.warning("model_warmup_failed", model=model, error=str(exc))

        # Embedding model — nomic-embed-text cold start adds 7-10s to first request
        t0 = time.monotonic()
        try:
            resp = await client.post(
                f"{settings.litellm_base_url}/embeddings",
                headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
                json={"model": settings.embeddings_model, "input": "warmup"},
            )
            resp.raise_for_status()
            elapsed = time.monotonic() - t0
            _model_warmup_seconds.labels(model=settings.embeddings_model).set(elapsed)
            log.info("model_warmed", model=settings.embeddings_model, seconds=round(elapsed, 2))
        except Exception as exc:  # noqa: BLE001
            log.warning("model_warmup_failed", model=settings.embeddings_model, error=str(exc))


app = FastAPI(
    title="Agent Orchestrator",
    version=__version__,
    description="LangGraph-based AI agent orchestrator for the offline homelab",
    lifespan=lifespan,
)

app.include_router(api_router)
app.include_router(openai_router)
app.include_router(ws_router)
app.include_router(ha_router)

if get_settings().enable_metrics:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
