"""Coordinates the 3-stage Karpathy-adapted LLM council."""
import logging
from typing import Any

from src.council.council_member import CouncilMember

logger = logging.getLogger(__name__)


class CouncilOrchestrator:
    """Coordinates a 4-model LLM council through 3 stages (Karpathy-adapted).

    Council members:
      - Gemini 2.5 Flash (chairman) — Google AI Studio free tier
      - DeepSeek R1 — OpenRouter :free (RL-trained reasoning)
      - Qwen3-235B-A22B — OpenRouter :free (Alibaba MoE)
      - Llama 4 Maverick — OpenRouter :free (Meta Western MoE)

    Stage 1: All 4 models generate insights in parallel (asyncio.gather)
    Stage 2: All 4 review each other with identities anonymized (A/B/C/D)
    Stage 3: Chairman synthesizes Stage 1 + Stage 2 gap analysis
    """

    def __init__(self, members: list[CouncilMember], chairman: CouncilMember) -> None:
        """Initialize with council members and the chairman.

        Args:
            members: List of CouncilMember instances (all 4 models).
            chairman: CouncilMember instance for the Gemini chairman.
        """
        self.members = members
        self.chairman = chairman

    def run(self, findings_summary: dict[str, Any]) -> dict[str, Any]:
        """Execute all 3 council stages and return the final synthesis dict."""
        raise NotImplementedError
