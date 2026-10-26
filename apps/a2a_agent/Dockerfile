FROM python:3.14.3-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/src
ARG PIP_VERSION=26.2.1

WORKDIR /app

COPY apps/a2a_agent/pylock.toml /app/pylock.toml
RUN python -m pip install --no-cache-dir --disable-pip-version-check "pip==${PIP_VERSION}" \
    && python -m pip install --no-cache-dir --disable-pip-version-check -r /app/pylock.toml

# O artefato inclui apenas este serviço e os contratos/segurança compartilhados.
COPY apps/a2a_agent/src/ /app/src/
COPY apps/a2a_agent/data/documents/ /var/lib/argos/a2a/documents/
COPY packages/contracts/ /app/packages/contracts/
COPY packages/security/ /app/packages/security/
COPY packages/observability/ /app/packages/observability/
COPY packages/model_options.py /app/packages/model_options.py

RUN mkdir -p /var/lib/argos/a2a/state \
    && addgroup --system --gid 10001 argos \
    && adduser --system --uid 10001 --ingroup argos argos \
    && chown -R argos:argos /var/lib/argos/a2a/state

USER argos:argos

EXPOSE 8000
CMD ["python", "-m", "argos_a2a_agent"]
