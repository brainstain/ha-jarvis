FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install agent-orchestrator
# Build context is custom-software/ so all sibling packages are accessible
COPY agent-orchestrator/pyproject.toml .
COPY agent-orchestrator/src/ src/
RUN pip install --no-cache-dir .

# Install MCP server packages — they run as stdio subprocesses of the agent
COPY mcp-memory-scoped/ /tmp/mcp-memory-scoped/
COPY mcp-notifications/ /tmp/mcp-notifications/
COPY mcp-workflow-status/ /tmp/mcp-workflow-status/
COPY mcp-calendar/ /tmp/mcp-calendar/
RUN pip install --no-cache-dir \
    /tmp/mcp-memory-scoped \
    /tmp/mcp-notifications \
    /tmp/mcp-workflow-status \
    /tmp/mcp-calendar

# Copy config
COPY agent-orchestrator/config/ config/

# Create data directories
RUN mkdir -p /data/langgraph

# Non-root user
RUN useradd -m agent && chown -R agent:agent /app /data
USER agent

EXPOSE 8100

# Health check
HEALTHCHECK --interval=15s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8100/health || exit 1

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
