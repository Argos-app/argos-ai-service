FROM python:3.14.3-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY apps/main_api/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    -r /app/requirements.txt

COPY apps/main_api/app/ /app/app/
COPY packages/contracts/ /app/packages/contracts/
COPY packages/security/ /app/packages/security/
COPY packages/observability/ /app/packages/observability/

RUN addgroup --system --gid 10001 argos \
    && adduser --system --uid 10001 --ingroup argos argos
USER argos:argos

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
