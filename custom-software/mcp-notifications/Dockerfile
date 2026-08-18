FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml .
COPY mcp_notifications/ ./mcp_notifications/

RUN pip install --no-cache-dir -e .

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "mcp_notifications"]
