import os

# Unit tests must not create background network exporters during collection.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
