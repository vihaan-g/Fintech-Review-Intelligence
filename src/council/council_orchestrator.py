"""Coordinates the 3-stage Karpathy-adapted LLM council."""
import asyncio
import dataclasses
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from src.config import Config
from src.council.council_member import CouncilMember, MemberResponse

logger = logging.getLogger(__name__)

_LABELS = ["Response A", "Response B", "Response C", "Response D"]


@dataclass
class CouncilResult:
    """Complete output of a council run."""

    stage1_responses: dict[str, MemberResponse]   # member_name → response
    anonymization_map: dict[str, str]              # "Response A" → member_name
    stage2_gap_analysis: str                       # anonymized review output
    stage3_synthesis: str                          # chairman final report
    total_duration_ms: int
    generated_at: str                              # ISO timestamp


class CouncilOrchestrator:
    """Coordinates a 4-model LLM council through 3 stages (Karpathy-adapted).

    Council members:
      - Gemini 3 Flash Preview (chairman) — Google AI Studio free tier
      - DeepSeek R1 — OpenRouter :free (RL-trained reasoning)
      - Qwen3-235B-A22B — OpenRouter :free (Alibaba MoE)
      - Llama 4 Maverick — OpenRouter :free (Meta Western MoE)

    Stage 1: All 4 models generate insights in parallel (asyncio.gather)
    Stage 2: All 4 review each other with identities anonymized (A/B/C/D)
    Stage 3: Chairman synthesizes Stage 1 + Stage 2 gap analysis
    """

    # -----------------------------------------------------------------------
    # Optimized prompts (6-phase optimizer applied to each)
    # -----------------------------------------------------------------------

    STAGE1_PROMPT: str = """You are a senior product analyst specializing in Indian consumer fintech.

Below are findings from analyzing Play Store reviews across four apps:
Fi Money, Jupiter, CRED, and PhonePe.

--- FINDINGS ---
{findings_summary}
--- END FINDINGS ---

Generate exactly 3–5 product insights. Use this structure for each:

**Insight [N]: [One-line title]**
- **App(s):** [Name the specific app(s)]
- **Data signal:** [Quote or reference a specific metric or pattern from the findings above]
- **Hypothesis:** [1–2 sentences: mechanistic explanation for WHY this pattern exists — name a cause, not just a restatement of the pattern]
- **Implication:** [What a PM should do differently knowing this — be specific about the action]

Rules:
1. Do not anchor on the most prominent number in the data. Push to the second or third-order pattern — what does the surface metric imply about user psychology or product-market gaps?
2. Every hypothesis must propose a cause (competitor behavior, product design choice, user mental model, regulatory constraint, or market dynamic).
3. Do not write generic insights (e.g., "users want better support", "UX needs improvement"). If an insight could apply to any fintech app globally, discard it and replace it with something specific to India or to this app.
4. Each insight must be traceable to a specific data point in the findings above — do not infer beyond the evidence.
5. Cross-app comparisons are more valuable than single-app observations when the comparison reveals a structural asymmetry.

If the findings do not support 5 non-obvious insights, write 3 strong ones rather than 5 weak ones."""

    STAGE2_PROMPT: str = """Four analysts independently reviewed the same Indian fintech Play Store dataset.
Their responses are labeled A through D. You do not know which analyst produced which response, and you must not speculate about authorship.

{labeled_responses}

Your task is gap-finding, not ranking. Do not declare any response superior.

For each item you identify, use exactly this structure:

**Item [N]**
Responses: [list which labels, e.g. "A, C"]
Quote: "[Exact quote from the response(s) — do not paraphrase]"
Assessment: [1–2 sentences: what this tells the chairman about confidence or blind spots, before committing to a category]
Category: HIGH CONFIDENCE | UNIQUE SIGNAL | CONTRADICTION

Category definitions:

**HIGH CONFIDENCE** — The same substantive insight appears independently in 2 or more responses. Minor wording differences are acceptable; the underlying claim must be the same.

**UNIQUE SIGNAL** — An insight appears in exactly one response and was missed by all others. Flag only if it is specific and data-grounded — not vague or unverifiable.

**CONTRADICTION** — Two or more responses make directly conflicting claims about the same app or metric. Quote both sides in the Quote field.

Output rules:
- Quote exactly. Never paraphrase when quoting.
- Identify 2–4 HIGH CONFIDENCE items, up to 3 UNIQUE SIGNAL items, and any CONTRADICTIONs (0 is acceptable).
- Do not surface generic observations (e.g., "all responses mention UX issues") — only specific, data-grounded insights.
- The Assessment field must appear before Category on every item — write your reasoning before committing to a label.
- Total response: under 450 words."""

    STAGE3_PROMPT: str = """You are the chairman of a 4-model LLM council synthesizing a final product intelligence report on Indian fintech Play Store reviews (Fi Money, Jupiter, CRED, PhonePe).

--- STAGE 1: Independent findings from all 4 analysts ---
{stage1_outputs}
--- END STAGE 1 ---

--- STAGE 2: Gap analysis (convergence, unique signals, contradictions) ---
{stage2_gap_analysis}
--- END STAGE 2 ---

Produce a final report using exactly this structure. Total length: 500–700 words.

## Key Findings

List 3–5 findings. For each:

**Finding [N]: [One-line title]**
- **Insight:** [Specific claim with numbers where available]
- **Evidence base:** [Which models surfaced this; e.g. "DeepSeek R1 + Qwen3 independently; Stage 2 HIGH CONFIDENCE"]
- **Confidence:** HIGH | MEDIUM | LOW
  - HIGH = 2+ models surfaced this independently
  - MEDIUM = 1 model + consistent with data patterns in Stage 1
  - LOW = 1 model, data is ambiguous
- **Why hypothesis:** [One sentence: root cause mechanism]

Lead with the finding that would most change a PM's near-term roadmap priorities. Do not include findings that contradict all Stage 1 outputs or that outrun the evidence in the data.

## App-Specific Signals

One paragraph per app. Each paragraph must name one specific metric or pattern from Stage 1 and state one concrete implication for that app's product team. Do not repeat findings already covered in Key Findings — this section adds app-specific color not captured above.

Apps: Fi Money | Jupiter | CRED | PhonePe

## Cross-App Pattern

One finding that applies to the Indian fintech category broadly — not to any single app. Must be grounded in data from at least 2 apps. State why this pattern is structurally likely to persist.

---

Quality bar: A PM at CRED or PhonePe should find this report useful without having seen the underlying data. Every claim must be traceable to Stage 1 or Stage 2 evidence."""

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def __init__(
        self,
        members: list[CouncilMember],
        chairman: CouncilMember,
        config: Config,
        seed: int | None = None,
    ) -> None:
        """Initialise orchestrator with injected members and chairman.

        Args:
            members:  All 4 council members (including chairman as a member).
            chairman: The Gemini chairman — also participates in Stage 1.
            config:   Validated Config instance.
            seed:     Optional RNG seed for Stage 2 shuffle (None = random).
        """
        assert len(members) == 4, (
            f"Council requires exactly 4 members, got {len(members)}"
        )
        self.members = members
        self.chairman = chairman
        self.config = config
        self._seed = seed

    @classmethod
    def default(cls, config: Config) -> "CouncilOrchestrator":
        """Factory: instantiate with the standard 4-model council.

        Chairman: Gemini 3 Flash Preview
        Members:
          - Gemini 3 Flash Preview (provider='gemini',     model_id='gemini-3-flash-preview')
          - DeepSeek R1            (provider='openrouter', model_id='deepseek/deepseek-r1:free')
          - Qwen3-235B-A22B        (provider='openrouter', model_id='qwen/qwen3-235b-a22b:free')
          - Llama 4 Maverick       (provider='openrouter', model_id='meta-llama/llama-4-maverick:free')

        Chairman is Gemini — also participates as a council member in Stage 1.
        The same CouncilMember instance is used for both roles.
        """
        chairman = CouncilMember(
            name="Gemini 3 Flash (Chairman)",
            provider="gemini",
            model_id="gemini-3-flash-preview",
            config=config,
        )
        members: list[CouncilMember] = [
            chairman,
            CouncilMember(
                name="DeepSeek R1",
                provider="openrouter",
                model_id="deepseek/deepseek-r1:free",
                config=config,
            ),
            CouncilMember(
                name="Qwen3-235B-A22B",
                provider="openrouter",
                model_id="qwen/qwen3-235b-a22b:free",
                config=config,
            ),
            CouncilMember(
                name="Llama 4 Maverick",
                provider="openrouter",
                model_id="meta-llama/llama-4-maverick:free",
                config=config,
            ),
        ]
        return cls(members=members, chairman=chairman, config=config)

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    async def run(self, findings_summary: str) -> CouncilResult:
        """Execute the full 3-stage council. Returns CouncilResult.

        Stage 1 — Parallel independent insights:
            Builds Stage 1 prompt with findings_summary injected.
            Uses asyncio.gather() to call all 4 members simultaneously.

        Stage 2 — Anonymized gap-finding review:
            Shuffles Stage 1 responses and assigns labels A/B/C/D.
            Calls ONLY the chairman for the gap analysis.

        Stage 3 — Chairman synthesis:
            Calls chairman with Stage 1 outputs + Stage 2 gap analysis.
        """
        pipeline_start = time.monotonic()
        generated_at = datetime.now(timezone.utc).isoformat()

        # -------------------------------------------------------------------
        # Stage 1 — parallel independent insight generation
        # -------------------------------------------------------------------
        stage1_prompt = self._build_stage1_prompt(findings_summary)
        t1_start = time.monotonic()
        gathered = await asyncio.gather(
            *[member.generate(stage1_prompt) for member in self.members],
            return_exceptions=True,
        )
        stage1_list: list[MemberResponse] = []
        for member, item in zip(self.members, gathered):
            if isinstance(item, BaseException):
                logger.warning(
                    "Stage 1 member %s raised %s — recording empty response",
                    member.name, item,
                )
                stage1_list.append(MemberResponse(
                    member_name=member.name,
                    model_id=member.model_id,
                    raw_response=f"[error] {item}",
                    clean_response="",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    duration_ms=0,
                ))
            else:
                stage1_list.append(item)
        t1_ms = int((time.monotonic() - t1_start) * 1000)
        logger.info(
            "Council Stage 1 complete — %d responses in %dms",
            len(stage1_list),
            t1_ms,
        )
        stage1_responses: dict[str, MemberResponse] = {
            r.member_name: r for r in stage1_list
        }

        # Guard: Stage 2+ is meaningless if nobody returned useful text.
        if not any(r.clean_response.strip() for r in stage1_list):
            # H9: persist raw stage1 outputs before raising so there is something
            # to debug from even when the run fails here.
            os.makedirs("outputs", exist_ok=True)
            stage1_raw_path = os.path.join("outputs", "council_stage1_raw.json")
            try:
                with open(stage1_raw_path, "w", encoding="utf-8") as fh:
                    json.dump(
                        [dataclasses.asdict(r) for r in stage1_list],
                        fh, indent=2, ensure_ascii=False,
                    )
                logger.info("Stage 1 raw responses saved to %s for debugging", stage1_raw_path)
            except OSError as exc:
                logger.warning("Could not save stage1 raw debug output: %s", exc)
            raise RuntimeError(
                "All Stage 1 members returned empty responses. "
                "Check API keys, network connectivity, and OpenRouter / "
                "Gemini quota. Council aborted — nothing to synthesise."
            )

        # -------------------------------------------------------------------
        # Stage 2 — anonymized gap-finding review (chairman only)
        # -------------------------------------------------------------------
        shuffled = list(stage1_list)
        rng = random.Random(self._seed)
        rng.shuffle(shuffled)
        anonymization_map: dict[str, str] = {
            label: resp.member_name
            for label, resp in zip(_LABELS, shuffled)
        }
        stage2_prompt = self._build_stage2_prompt(shuffled, _LABELS)

        t2_start = time.monotonic()
        stage2_resp = await self.chairman.generate(stage2_prompt)
        t2_ms = int((time.monotonic() - t2_start) * 1000)
        logger.info("Council Stage 2 complete — gap analysis in %dms", t2_ms)
        stage2_gap_analysis = stage2_resp.clean_response

        # -------------------------------------------------------------------
        # Stage 3 — chairman synthesis
        # -------------------------------------------------------------------
        stage3_prompt = self._build_stage3_prompt(stage1_responses, stage2_gap_analysis)
        t3_start = time.monotonic()
        stage3_resp = await self.chairman.generate(stage3_prompt)
        t3_ms = int((time.monotonic() - t3_start) * 1000)
        logger.info("Council Stage 3 complete — synthesis in %dms", t3_ms)

        synthesis = stage3_resp.clean_response
        # H10: validate synthesis length here, inside run(), before marking council
        # complete — prevents InsightReporter from crashing after 2 hours of
        # classification have already completed.
        if len(synthesis.strip()) < 100:
            raise RuntimeError(
                f"Stage 3 synthesis is too short ({len(synthesis.strip())} chars). "
                "The chairman model may have returned an empty or blocked response. "
                "Check the chairman model ID and Gemini quota."
            )

        total_duration_ms = int((time.monotonic() - pipeline_start) * 1000)
        result = CouncilResult(
            stage1_responses=stage1_responses,
            anonymization_map=anonymization_map,
            stage2_gap_analysis=stage2_gap_analysis,
            stage3_synthesis=synthesis,
            total_duration_ms=total_duration_ms,
            generated_at=generated_at,
        )
        self._save_result(result)
        return result

    def run_sync(self, findings_summary: str) -> CouncilResult:
        """Synchronous wrapper for run(). Use from non-async contexts.

        Implementation: asyncio.run(self.run(findings_summary))
        """
        return asyncio.run(self.run(findings_summary))

    # -----------------------------------------------------------------------
    # Prompt builders
    # -----------------------------------------------------------------------

    def _build_stage1_prompt(self, findings_summary: str) -> str:
        """Inject findings_summary into STAGE1_PROMPT."""
        return self.STAGE1_PROMPT.format(findings_summary=findings_summary)

    def _build_stage2_prompt(
        self,
        responses: list[MemberResponse],
        labels: list[str],
    ) -> str:
        """Build anonymized Stage 2 prompt.

        Assigns labels (Response A/B/C/D) to the provided (shuffled) responses.
        Uses clean_response (think tags stripped) not raw_response.
        """
        sections = ""
        for label, resp in zip(labels, responses):
            sections += f"\n--- {label} ---\n{resp.clean_response}\n"
        return self.STAGE2_PROMPT.format(labeled_responses=sections)

    def _build_stage3_prompt(
        self,
        stage1_responses: dict[str, MemberResponse],
        stage2_output: str,
    ) -> str:
        """Build Stage 3 chairman synthesis prompt with all context."""
        stage1_text = ""
        for name, resp in stage1_responses.items():
            stage1_text += f"\n=== {name} ===\n{resp.clean_response}\n"
        return self.STAGE3_PROMPT.format(
            stage1_outputs=stage1_text,
            stage2_gap_analysis=stage2_output,
        )

    # -----------------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------------

    def _save_result(self, result: CouncilResult) -> None:
        """Serialize CouncilResult to outputs/council_result.json.

        MemberResponse objects are converted via dataclasses.asdict()
        before json.dumps(). Ensures outputs/ directory exists.
        """
        os.makedirs("outputs", exist_ok=True)

        # Convert to plain dict — MemberResponse dataclasses nested inside
        raw: dict = dataclasses.asdict(result)  # type: ignore[assignment]

        output_path = os.path.join("outputs", "council_result.json")
        try:
            with open(output_path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
            logger.info("Council result saved to %s", output_path)
        except OSError as exc:
            logger.error("Failed to save council result: %s", exc)

        # H8: pipeline_state.json is never read — canonical state lives in the DB.
        # Removed the dead JSON state write.
