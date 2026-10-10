FROM python:3.14.3-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/src

WORKDIR /app

COPY apps/mcp_prediction/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    -r /app/requirements.txt

# Keep the internal contracts/security code inside this service artifact.
COPY apps/mcp_prediction/src/ /app/src/
COPY packages/contracts/ /app/packages/contracts/
COPY packages/security/ /app/packages/security/

RUN addgroup --system --gid 10001 argos \
    && adduser --system --uid 10001 --ingroup argos argos
USER argos:argos

EXPOSE 8000
CMD ["python", "-m", "argos_mcp_prediction"]
