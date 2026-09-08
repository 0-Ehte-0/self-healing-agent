FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY apps/demo-api/pyproject.toml /workspace/apps/demo-api/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e /workspace/apps/demo-api

# Copy application source code
COPY apps/demo-api/demo_api /workspace/apps/demo-api/demo_api

WORKDIR /workspace/apps/demo-api

EXPOSE 8000

CMD ["uvicorn", "demo_api.main:app", "--host", "0.0.0.0", "--port", "8000"]