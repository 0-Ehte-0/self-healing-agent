FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY apps/traffic-generator/pyproject.toml /workspace/apps/traffic-generator/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e /workspace/apps/traffic-generator

# Copy application source code
COPY apps/traffic-generator/traffic_generator /workspace/apps/traffic-generator/traffic_generator

WORKDIR /workspace/apps/traffic-generator

CMD ["python", "-m", "traffic_generator.main"]