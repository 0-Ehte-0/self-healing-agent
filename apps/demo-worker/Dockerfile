FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY apps/demo-worker/pyproject.toml /workspace/apps/demo-worker/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e /workspace/apps/demo-worker

# Copy application source code
COPY apps/demo-worker/demo_worker /workspace/apps/demo-worker/demo_worker

WORKDIR /workspace/apps/demo-worker

EXPOSE 9102

CMD ["python", "-m", "demo_worker.main"]