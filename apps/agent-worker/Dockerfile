FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install shared packages
COPY packages/shared-models /workspace/packages/shared-models/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e /workspace/packages/shared-models

# Install diagnosis and action-catalog
COPY packages/diagnosis /workspace/packages/diagnosis/
RUN pip install --no-cache-dir -e /workspace/packages/diagnosis

COPY packages/action-catalog /workspace/packages/action-catalog/
RUN pip install --no-cache-dir -e /workspace/packages/action-catalog

# Install agent-core
COPY packages/agent-core /workspace/packages/agent-core/
RUN pip install --no-cache-dir -e /workspace/packages/agent-core

# Install control-api (for DB models and repositories)
COPY apps/control-api /workspace/apps/control-api/
RUN pip install --no-cache-dir -e /workspace/apps/control-api

# Install agent-worker
COPY apps/agent-worker /workspace/apps/agent-worker/
RUN pip install --no-cache-dir -e /workspace/apps/agent-worker

WORKDIR /workspace/apps/agent-worker

CMD ["python", "-m", "agent_worker.main"]
