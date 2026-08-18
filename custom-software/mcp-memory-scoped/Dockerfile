FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
COPY mcp_memory_scoped/ ./mcp_memory_scoped/

RUN pip install --no-cache-dir -e .

# Runs as stdio server — invoked as a subprocess by the orchestrator
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "mcp_memory_scoped"]
