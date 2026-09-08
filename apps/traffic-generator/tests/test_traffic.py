import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from traffic_generator.main import run_traffic_loop


@pytest.mark.asyncio
async def test_traffic_generator_resilience():
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post.side_effect = Exception("Simulated connection drop")

    with patch("httpx.AsyncClient", return_value=mock_client), \
         patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
        try:
            await run_traffic_loop()
        except asyncio.CancelledError:
            pass

        assert mock_client.post.call_count >= 1
