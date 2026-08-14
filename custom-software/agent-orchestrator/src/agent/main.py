"""FastAPI application entry point for the Agent Orchestrator."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from agent import __version__
from agent.api.routes import router as api_router
from agent.api.routes import set_tools
from agent.config import get_settings
from agent.mcp.registry import MCPToolRegistry


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

    yield

    set_tools(None)
    await mcp.close()
    log.info("orchestrator_stopping")


app = FastAPI(
    title="Agent Orchestrator",
    version=__version__,
    description="LangGraph-based AI agent orchestrator for the offline homelab",
    lifespan=lifespan,
)

app.include_router(api_router)

if get_settings().enable_metrics:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
