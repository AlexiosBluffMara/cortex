FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    CORTEX_WORKER_MODE=fake \
    CORTEX_WORKER_PROVIDER=docker

WORKDIR /app

COPY cloud/tribe_worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY cloud /app/cloud
COPY cortex /app/cortex
COPY pyproject.toml README.md /app/

EXPOSE 7860
CMD ["python", "-m", "uvicorn", "cloud.tribe_worker.app:app", "--host", "0.0.0.0", "--port", "7860"]

