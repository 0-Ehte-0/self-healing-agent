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

# Install control-api
COPY apps/control-api/pyproject.toml /workspace/apps/control-api/
RUN pip install --no-cache-dir -e /workspace/apps/control-api

# Copy control-api code and alembic files
COPY apps/control-api/app /workspace/apps/control-api/app
COPY apps/control-api/alembic.ini /workspace/apps/control-api/alembic.ini

WORKDIR /workspace/apps/control-api

EXPOSE 8000

CMD ["python", "-m", "app.db.bootstrap"]
