"""Coordinates the 4-stage Karpathy-adapted LLM council."""

import asyncio
import dataclasses
import json
import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from src.config import Config
from src.council.council_member import CouncilMember, MemberResponse
from src.data_collection.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

_LABELS = ["Response A", "Response B", "Response C", "Response D"]


@dataclass
class CouncilResult:
    """Complete output of a council run."""

    stage1_responses: dict[str, MemberResponse]  # "Name [Role]" → response
    anonymization_map: dict[str, str]  # "Response A" → member_name
    stage2_gap_analysis: str  # anonymized review output
    stage3_synthesis: str  # chairman final report
    total_duration_ms: int
    generated_at: str  # ISO timestamp
    analytical_frame: str = ""  # Stage 0 output from chairman


class CouncilOrchestrator:
    """Coordinates a 4-model LLM council through 4 stages (Karpathy-adapted).

    Council members:
      - Gemini 3.1 Pro Preview (Contrarian Chairman) — Google AI Studio Tier 1 paid
      - DeepSeek R1 — OpenRouter :free [First Principles analyst]
      - Qwen3-235B-A22B — OpenRouter :free [Outsider analyst]
      - Llama 4 Maverick — OpenRouter :free [Expansionist analyst]

    Stage 0: Chairman frames the analytical question (≤100 words)
    Stage 1: All 4 models generate insights in parallel, each with a role mandate
    Stage 2: Chairman as Contrarian — Three Tensions gap analysis (A/B/C/D)
    Stage 3: Chairman synthesizes Stage 1 outputs + Stage 2 gap analysis
    """

    # -----------------------------------------------------------------------
    # Optimized prompts (6-phase optimizer applied to each)
    # -----------------------------------------------------------------------

    STAGE1_PROMPT: str = """You are a senior product analyst specializing in Indian consumer fintech.

Below are findings from analyzing Play Store reviews across five apps:
Groww, Jupiter, CRED, PhonePe, and Paytm.

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
- Total response: under 450 words.

CONTRARIAN FRAMING

You are operating as the Contrarian in this phase. Your job is not just to find
gaps — it is to find where the analytical lenses explicitly clash. Look for
these three named tensions:

TENSION 1 — OUTSIDER vs DOMAIN EXPERTS
Where does the Outsider's surface-level reading contradict what the First
Principles or Expansionist analysts assumed or implied? Flag any case where
"what the data literally shows" conflicts with "what an insider would conclude."

TENSION 2 — EXPANSIONIST vs FIRST PRINCIPLES
Where does the Expansionist's upside signal contradict the First Principles
analyst's structural diagnosis? If one identifies an opportunity and the other
identifies a structural constraint that would prevent it, name that tension.

TENSION 3 — CONSENSUS vs EVIDENCE
Where do two or more analysts agree on a conclusion but the data (numbers,
distributions, verbatim patterns) does not clearly support it? Flag
overconfident consensus that outruns the evidence.

For each tension found, add a TENSION block in your output using this exact
format:

TENSION [N]
Type: [OUTSIDER_VS_EXPERTS | EXPANSIONIST_VS_FIRST_PRINCIPLES | CONSENSUS_VS_EVIDENCE]
Analysts: [role labels in tension, e.g. "Outsider, First Principles"]
Summary: [one sentence naming the tension]
Assessment: [1-2 sentences on what this tension reveals about the data]"""

    STAGE3_PROMPT: str = """You are the chairman of a 4-model LLM council synthesizing a final product intelligence report on Indian fintech Play Store reviews (Groww, Jupiter, CRED, PhonePe, Paytm).

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

Apps: Groww | Jupiter | CRED | PhonePe | Paytm

## Cross-App Pattern

One finding that applies to the Indian fintech category broadly — not to any single app. Must be grounded in data from at least 2 apps. State why this pattern is structurally likely to persist.

---

Quality bar: A PM at CRED or PhonePe should find this report useful without having seen the underlying data. Every claim must be traceable to Stage 1 or Stage 2 evidence."""

    # -----------------------------------------------------------------------
    # Cognitive role mandates — Stage 1 only
    # Each non-chairman member receives a different analytical lens.
    # Chairman has no Stage 1 mandate; its Contrarian role activates in Stage 2.
    # -----------------------------------------------------------------------

    ROLE_MANDATES: dict[str, str] = {
        "deepseek/deepseek-r1:free": (
            "ROLE MANDATE — FIRST PRINCIPLES ANALYST\n\n"
            "Your analytical entry point is first principles reasoning. Do not describe what\n"
            "the data shows on the surface. Do not list complaint categories. Instead, ask:\n"
            "what does this data fundamentally reveal about the structural problems in Indian\n"
            "fintech? What business model tensions, regulatory constraints, or user trust\n"
            "dynamics explain WHY these patterns exist — not just WHAT they are?\n\n"
            "Ignore conventional product analysis. Reason from the ground up. A surface\n"
            "observation like \"Jupiter has poor support\" is not a finding. A first-principles\n"
            "finding is: \"Jupiter's support collapse is the predictable result of a neo-bank\n"
            "trying to compete on feature velocity while cutting the operational cost that\n"
            "customer trust actually requires.\""
        ),
        "qwen/qwen3-235b-a22b:free": (
            "ROLE MANDATE — OUTSIDER ANALYST\n\n"
            "Your analytical entry point is radical surface-level observation. You have no\n"
            "prior knowledge of Indian fintech, CRED, Jupiter, Groww, Paytm, or PhonePe.\n"
            "Do not use domain assumptions or insider knowledge. React only to what is\n"
            "literally present in the data — the numbers, distributions, and verbatim\n"
            "patterns in front of you.\n\n"
            "Describe what is surprising or anomalous when viewed with completely fresh eyes.\n"
            "If a pattern seems obvious to a domain expert, that is precisely when you should\n"
            "question it. Your most valuable output is: \"From the data alone, without\n"
            "assumptions, what is actually strange here that everyone else would normalise?\""
        ),
        "meta-llama/llama-4-maverick:free": (
            "ROLE MANDATE — EXPANSIONIST ANALYST\n\n"
            "Your analytical entry point is upside and adjacent signal. Do not focus on\n"
            "problems, complaints, or failures. Look for what everyone else is missing.\n\n"
            "Where is user frustration actually revealing an unmet job-to-be-done that no app\n"
            "is solving well? What do the high-rated reviews across all five apps reveal about\n"
            "what users genuinely value? What does the competitive pattern — which apps are\n"
            "gaining trust, which are losing it — suggest about where Indian fintech is\n"
            "heading? What opportunity is hiding in the data that looks like a complaint but\n"
            "is actually a signal?\n\n"
            "Focus on signals, possibilities, and undervalued patterns — not problems."
        ),
    }

    ROLE_NAMES: dict[str, str] = {
        "deepseek/deepseek-r1:free": "First Principles",
        "qwen/qwen3-235b-a22b:free": "Outsider",
        "meta-llama/llama-4-maverick:free": "Expansionist",
        "gemini-3.1-pro-preview": "Chairman (Contrarian)",
    }

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def __init__(
        self,
        members: list[CouncilMember],
        chairman: CouncilMember,
        config: Config,
        db: DatabaseManager,
        seed: int | None = None,
    ) -> None:
        """Initialise orchestrator with injected members and chairman.

        Args:
            members:  All 4 council members (including chairman as a member).
            chairman: The Gemini chairman — also participates in Stage 1.
            config:   Validated Config instance.
            db:       Open DatabaseManager — used for Stage 0 checkpointing.
            seed:     Optional RNG seed for Stage 2 shuffle (None = random).
        """
        assert (
            len(members) == 4
        ), f"Council requires exactly 4 members, got {len(members)}"
        self.members = members
        self.chairman = chairman
        self.config = config
        self._db = db
        self._seed = seed

    @classmethod
    def default(cls, config: Config, db: DatabaseManager) -> "CouncilOrchestrator":
        """Factory: instantiate with the standard 4-model council.

        Chairman: Gemini 3.1 Pro Preview
        Members:
          - Gemini 3.1 Pro Preview (provider='gemini',     model_id='gemini-3.1-pro-preview')
          - DeepSeek R1            (provider='openrouter', model_id='deepseek/deepseek-r1:free')
          - Qwen3-235B             (provider='openrouter', model_id='qwen/qwen3-235b-a22b:free')
          - Llama 4 Maverick       (provider='openrouter', model_id='meta-llama/llama-4-maverick:free')

        Chairman is Gemini — also participates as a council member in Stage 1.
        The same CouncilMember instance is used for both roles.
        """
        chairman = CouncilMember(
            name="Gemini 3.1 Pro (Chairman)",
            provider="gemini",
            model_id="gemini-3.1-pro-preview",
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
                name="Qwen3-235B",
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
        return cls(members=members, chairman=chairman, config=config, db=db)

    # -----------------------------------------------------------------------
    # Public interface
    # -----------------------------------------------------------------------

    async def run(self, findings_summary: str) -> CouncilResult:
        """Execute the full 4-stage council. Returns CouncilResult.

        Stage 0 — Chairman frames the analytical question:
            Reads findings_summary and returns a ≤100-word analytical frame
            that is prepended to every member's Stage 1 prompt.

        Stage 1 — Parallel independent insights:
            Each member receives: frame + their role mandate + base prompt.
            Uses asyncio.gather() to call all 4 members simultaneously.

        Stage 2 — Anonymized gap-finding review (Contrarian):
            Shuffles Stage 1 responses and assigns labels A/B/C/D.
            Calls ONLY the chairman for gap + tension analysis.

        Stage 3 — Chairman synthesis:
            Calls chairman with Stage 1 outputs + Stage 2 gap analysis.
        """
        pipeline_start = time.monotonic()
        generated_at = datetime.now(timezone.utc).isoformat()

        # -------------------------------------------------------------------
        # Stage 0 — chairman frames the analytical question.
        # Checkpointed in pipeline_state("council_stage0_frame") so reruns
        # skip Stage 0 and use the cached frame without re-billing the chairman.
        # Fatal API errors propagate — empty content halts the pipeline.
        # -------------------------------------------------------------------
        analytical_frame = self._load_cached_stage0_frame()
        if analytical_frame:
            logger.info("Stage 0: resuming from cached analytical frame (Stage 0 skipped)")
        else:
            analytical_frame = await self._stage0_frame_question(findings_summary)
            if not analytical_frame:
                raise RuntimeError(
                    "Stage 0 returned empty content from the chairman model "
                    "(gemini-3.1-pro-preview). The model may have been safety-filtered, "
                    "rate-limited, or returned no candidates. Check the Gemini API key, "
                    "quota, and model ID. Council aborted — Stage 1 requires an analytical frame."
                )
            self._db.save_phase_state(
                "council_stage0_frame", "complete", {"frame": analytical_frame}
            )

        # -------------------------------------------------------------------
        # Stage 1 — parallel independent insight generation
        # -------------------------------------------------------------------
        t1_start = time.monotonic()
        gathered = await asyncio.gather(
            *[
                member.generate(
                    self._build_stage1_prompt_for_member(
                        member, findings_summary, analytical_frame
                    )
                )
                for member in self.members
            ],
            return_exceptions=True,
        )
        stage1_list: list[MemberResponse] = []
        for member, item in zip(self.members, gathered):
            if isinstance(item, BaseException):
                # Bug 7: fatal 4xx errors (auth failure, bad model ID) must halt
                # Stage 1, not silently become empty slots. Transient errors
                # (network, exhausted 429) still produce empty slots as before.
                if isinstance(item, httpx.HTTPStatusError):
                    status_code = item.response.status_code
                    if 400 <= status_code < 500 and status_code != 429:
                        raise RuntimeError(
                            f"Stage 1 member {self._member_label(member)} returned fatal "
                            f"HTTP {status_code} (model: {member.model_id}). "
                            "Check API key and model ID. Council aborted."
                        ) from item
                logger.warning(
                    "Stage 1 member %s raised %s — recording empty response",
                    self._member_label(member),
                    item,
                )
                stage1_list.append(
                    MemberResponse(
                        member_name=member.name,
                        model_id=member.model_id,
                        raw_response=f"[error] {item}",
                        clean_response="",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        duration_ms=0,
                    )
                )
            else:
                stage1_list.append(item)
        t1_ms = int((time.monotonic() - t1_start) * 1000)
        logger.info(
            "Council Stage 1 complete — %d responses in %dms",
            len(stage1_list),
            t1_ms,
        )
        stage1_responses: dict[str, MemberResponse] = {
            self._member_label(member): resp
            for member, resp in zip(self.members, stage1_list)
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
                        fh,
                        indent=2,
                        ensure_ascii=False,
                    )
                logger.info(
                    "Stage 1 raw responses saved to %s for debugging", stage1_raw_path
                )
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
            label: resp.member_name for label, resp in zip(_LABELS, shuffled)
        }
        stage2_prompt = self._build_stage2_prompt(shuffled, _LABELS)

        t2_start = time.monotonic()
        stage2_resp = await self.chairman.generate(stage2_prompt)
        t2_ms = int((time.monotonic() - t2_start) * 1000)
        logger.info("Council Stage 2 complete — gap analysis in %dms", t2_ms)
        stage2_gap_analysis = stage2_resp.clean_response
        if not stage2_gap_analysis.strip():
            # Not fatal — stage 3 can still synthesise from stage 1 alone,
            # but the user should know the gap analysis is missing so they
            # can judge whether to re-run.
            logger.warning(
                "Stage 2 gap analysis is empty — chairman may have been "
                "safety-filtered or rate-limited. Stage 3 will proceed with "
                "stage 1 responses only. Check council_stage1_raw.json and "
                "re-run if a cleaner synthesis is needed."
            )
            stage2_gap_analysis = (
                "[Stage 2 gap analysis unavailable — chairman returned empty. "
                "Stage 3 synthesis relies on Stage 1 responses only.]"
            )

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
            analytical_frame=analytical_frame,
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

    def _load_cached_stage0_frame(self) -> str:
        """Return the Stage 0 frame from pipeline_state, or '' if not yet checkpointed.

        Returns empty string on DB errors so Stage 0 re-runs rather than halting.
        """
        try:
            state = self._db.get_phase_state("council_stage0_frame")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not read council_stage0_frame from pipeline_state: %s — will re-run Stage 0",
                exc,
            )
            return ""
        if state and state.get("status") == "complete":
            frame: str = (state.get("metadata") or {}).get("frame", "")
            if frame:
                logger.info(
                    "Stage 0 cache hit — frame saved at %s", state.get("updated_at", "unknown")
                )
            return frame
        return ""

    def _member_label(self, member: CouncilMember) -> str:
        """Return display label: 'Name [Role]' if ROLE_NAMES entry exists, else 'Name'."""
        role = self.ROLE_NAMES.get(member.model_id)
        return f"{member.name} [{role}]" if role else member.name

    def _build_stage1_prompt(self, findings_summary: str) -> str:
        """Inject findings_summary into STAGE1_PROMPT."""
        return self.STAGE1_PROMPT.format(findings_summary=findings_summary)

    async def _stage0_frame_question(self, findings_text: str) -> str:
        """Chairman produces a focused 100-word analytical frame from the data.

        Turns 'here is data' into 'here is the specific question to answer.'
        This frame is prepended to every member's Stage 1 prompt.

        Args:
            findings_text: Structured findings summary passed to the council.

        Returns:
            Analytical frame string, 100 words max.
        """
        # Bug 6: pass full findings_text — truncating to 4,000 chars cut off
        # classification enrichment appended at the end of structured_text.
        logger.info("Stage 0 input: %d chars", len(findings_text))
        frame_prompt = (
            "You are about to convene an analytical council on Indian fintech user sentiment "
            "across five apps: PhonePe, CRED, Jupiter, Groww, and Paytm.\n\n"
            "Read the following data summary carefully. Then produce a single focused "
            "analytical question or frame — 100 words maximum — that captures the most "
            "important thing this council should determine. Be specific. Name the apps, "
            "patterns, and tensions that matter most. Do not list multiple questions. "
            "One sharp, specific analytical target only.\n\n"
            f"Data:\n{findings_text}\n\n"
            "Analytical frame (100 words max):"
        )
        response = await self.chairman.generate(frame_prompt)
        frame = response.clean_response.strip()
        logger.info(
            "=== STAGE 0 ANALYTICAL FRAME ===\n%s\n================================",
            frame,
        )
        return frame

    def _build_stage1_prompt_for_member(
        self, member: CouncilMember, findings_summary: str, analytical_frame: str = ""
    ) -> str:
        """Build Stage 1 prompt: analytical frame + role mandate + base prompt.

        Order: frame (if any) → mandate (if any) → base STAGE1_PROMPT.
        Chairman has no ROLE_MANDATES entry; its Contrarian role activates in Stage 2.
        If analytical_frame is empty (Stage 0 failed), that section is omitted cleanly.
        """
        base = self._build_stage1_prompt(findings_summary)
        parts: list[str] = []
        if analytical_frame:
            parts.append(f"ANALYTICAL FRAME FOR THIS SESSION:\n{analytical_frame}")
        mandate = self.ROLE_MANDATES.get(member.model_id)
        if mandate:
            parts.append(mandate)
        parts.append(base)
        return "\n\n".join(parts)

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

        # Enrich each stage1_responses entry with a role field from ROLE_NAMES
        for resp_dict in raw.get("stage1_responses", {}).values():
            resp_dict["role"] = self.ROLE_NAMES.get(resp_dict.get("model_id", ""), "")

        output_path = os.path.join("outputs", "council_result.json")
        try:
            with open(output_path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
            logger.info("Council result saved to %s", output_path)
        except OSError as exc:
            # Do not swallow — the report phase depends on this file, and 2h of
            # council work is about to be lost. Re-raise with a message that
            # tells the user exactly where to look.
            logger.error(
                "Failed to save council result to %s: %s. "
                "Council output is in memory but could not be persisted — "
                "check filesystem space and permissions on outputs/.",
                output_path,
                exc,
            )
            raise RuntimeError(
                f"Could not write {output_path}: {exc}. "
                "Council ran successfully but the result was not persisted. "
                "Check that outputs/ is writable and has disk space, then re-run."
            ) from exc

        # H8: pipeline_state.json is never read — canonical state lives in the DB.
        # Removed the dead JSON state write.
