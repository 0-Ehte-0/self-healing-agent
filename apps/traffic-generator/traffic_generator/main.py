import asyncio
import logging
import random
import sys

import httpx
from traffic_generator.config import get_settings

logging.basicConfig(level=logging.INFO, format='{"time": "%(asctime)s", "message": "%(message)s"}')
logger = logging.getLogger("traffic-generator")

settings = get_settings()


async def run_traffic_loop() -> None:
    rng = random.Random(settings.random_seed)
    delay = 1.0 / max(settings.rate_rps, 0.1)
    logger.info(f"Starting traffic to {settings.target_api_url} at {settings.rate_rps} RPS (seed={settings.random_seed})")

    async with httpx.AsyncClient(base_url=settings.target_api_url, timeout=5.0) as client:
        while True:
            try:
                payload_val = f"job-val-{rng.randint(1000, 9999)}"
                resp = await client.post("/jobs", json={"payload": payload_val})
                if resp.status_code >= 400:
                    logger.warning(f"Request failed: status={resp.status_code}")
            except Exception as e:
                logger.error(f"Request connection error: {e}")

            jitter = rng.uniform(-0.1 * delay, 0.1 * delay)
            await asyncio.sleep(max(0.001, delay + jitter))


def main() -> None:
    try:
        asyncio.run(run_traffic_loop())
    except KeyboardInterrupt:
        logger.info("Traffic generator stopped.")


if __name__ == "__main__":
    main()
