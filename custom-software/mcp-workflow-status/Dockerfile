FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
COPY mcp_workflow_status/ ./mcp_workflow_status/

RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "mcp_workflow_status"]
