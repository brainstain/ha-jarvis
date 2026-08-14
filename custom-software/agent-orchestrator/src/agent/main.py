"""FastAPI application entry point for the Agent Orchestrator."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from agent import __version__
from agent.api.routes import router as api_router
from agent.config import get_settings


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
    yield
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
