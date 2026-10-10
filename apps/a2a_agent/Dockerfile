FROM python:3.14.3-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app:/app/src:/opt/argos/a2a-adapters

WORKDIR /app

COPY apps/a2a_agent/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    -r /app/requirements.txt

# O artefato inclui apenas este serviço e os contratos/segurança compartilhados.
COPY apps/a2a_agent/src/ /app/src/
COPY packages/contracts/ /app/packages/contracts/
COPY packages/security/ /app/packages/security/

RUN mkdir -p /var/lib/argos/a2a/state \
    && addgroup --system --gid 10001 argos \
    && adduser --system --uid 10001 --ingroup argos argos \
    && chown -R argos:argos /var/lib/argos/a2a/state

USER argos:argos

EXPOSE 8000
CMD ["python", "-m", "argos_a2a_agent"]
