FROM python:3.14.3-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/src

WORKDIR /app

COPY apps/mcp_data/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    -r /app/requirements.txt

COPY apps/mcp_data/src/ /app/src/
COPY packages/contracts/ /app/packages/contracts/
COPY packages/security/ /app/packages/security/

RUN addgroup --system --gid 10001 argos \
    && adduser --system --uid 10001 --ingroup argos argos
USER argos:argos

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 --start-period=20s \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=2).close()"]
CMD ["python", "-m", "argos_mcp_data"]
