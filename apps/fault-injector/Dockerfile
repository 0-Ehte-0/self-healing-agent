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
COPY apps/fault-injector/pyproject.toml /workspace/apps/fault-injector/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e /workspace/apps/fault-injector

# Copy application source code
COPY apps/fault-injector/fault_injector /workspace/apps/fault-injector/fault_injector

WORKDIR /workspace/apps/fault-injector

EXPOSE 8000

CMD ["uvicorn", "fault_injector.main:app", "--host", "0.0.0.0", "--port", "8000"]