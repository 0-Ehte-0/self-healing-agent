import asyncio
import logging
from collections.abc import Callable
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class NodeClassification(StrEnum):
    SAFE_READ = "SAFE_READ"
    MUTATING = "MUTATING"
    COORDINATION = "COORDINATION"


NODE_CLASSIFICATIONS: dict[str, NodeClassification] = {
    "collect_evidence": NodeClassification.SAFE_READ,
    "diagnose": NodeClassification.COORDINATION,
    "plan": NodeClassification.COORDINATION,
    "evaluate_policy": NodeClassification.COORDINATION,
    "wait_for_approval": NodeClassification.COORDINATION,
    "execute": NodeClassification.MUTATING,
    "verify": NodeClassification.SAFE_READ,
    "escalate": NodeClassification.COORDINATION,
    "resolve": NodeClassification.COORDINATION,
}


class NodeRetryPolicy:
    """Enforces bounded exponential retries strictly on SAFE_READ nodes; ZERO retries on MUTATING nodes.

    Per Section 6 Step 11: Use bounded node timeouts and retry only safe reads automatically.
    Side-effect retries belong to M1-F's execution protocol.
    """

    def __init__(self, max_read_retries: int = 3, initial_delay: float = 0.05):
        self.max_read_retries = max_read_retries
        self.initial_delay = initial_delay

    async def execute_with_policy(
        self, node_name: str, func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        classification = NODE_CLASSIFICATIONS.get(node_name, NodeClassification.MUTATING)

        if classification != NodeClassification.SAFE_READ:
            # Side-effect / mutating nodes have zero automatic internal retries
            return await func(*args, **kwargs)

        # Safe read node: bounded retries with exponential backoff
        last_exc: Exception | None = None
        for attempt in range(1, self.max_read_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    f"Safe read node '{node_name}' failed on attempt {attempt}/{self.max_read_retries}: {exc}"
                )
                if attempt < self.max_read_retries:
                    await asyncio.sleep(self.initial_delay * (2 ** (attempt - 1)))
        raise last_exc  # type: ignore[misc]
