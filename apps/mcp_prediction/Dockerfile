FROM python:3.14.3-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/src
ARG PIP_VERSION=26.2.1

WORKDIR /app

COPY apps/mcp_prediction/pylock.toml /app/pylock.toml
RUN python -m pip install --no-cache-dir --disable-pip-version-check "pip==${PIP_VERSION}" \
    && python -m pip install --no-cache-dir --disable-pip-version-check -r /app/pylock.toml

# Inclui os contratos e o código de segurança compartilhados neste artefato do serviço.
COPY apps/mcp_prediction/src/ /app/src/
COPY packages/contracts/ /app/packages/contracts/
COPY packages/security/ /app/packages/security/

RUN addgroup --system --gid 10001 argos \
    && adduser --system --uid 10001 --ingroup argos argos
USER argos:argos

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 --start-period=20s \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=2).close()"]
CMD ["python", "-m", "argos_mcp_prediction"]
