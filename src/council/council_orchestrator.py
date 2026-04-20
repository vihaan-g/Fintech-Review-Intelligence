"""Coordinates the multi-stage product-intelligence council."""

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

_LABELS = ["Response A", "Response B", "Response C"]
_OPENROUTER_MODELS_ENDPOINT = "https://openrouter.ai/api/v1/models"
_PREFLIGHT_TIMEOUT = 15.0


@dataclass
class CouncilResult:
    """Complete output of a council run."""

    stage1_responses: dict[str, MemberResponse]
    anonymization_map: dict[str, str]
    stage2_gap_analysis: str
    stage3_synthesis: str
    total_duration_ms: int
    generated_at: str
    analytical_frame: str = ""
    stage2a_contrarian_pass: str = ""
    stage2b_evidence_audits: dict[str, MemberResponse] | None = None
    stage2c_audit_synthesis: str = ""


class CouncilOrchestrator:
    """Coordinates the revised OpenRouter-only council flow."""

    STAGE1_PROMPT: str = """You are a senior product analyst specializing in Indian consumer fintech.

ANALYTICAL FRAME
{analytical_frame}

FINDINGS SUMMARY
{findings_summary}

Write 3-5 evidence-grounded insights.

For each insight use exactly this structure:
**Insight [N]: [Title]**
- **App(s):** [specific app names]
- **Data signal:** [specific metric, quote, or asymmetry from the findings]
- **Hypothesis:** [1-2 sentences explaining why this pattern may exist]
- **Implication:** [specific PM action or decision impact]

Rules:
1. Every claim must be grounded in the findings.
2. Prefer structural asymmetries across apps over generic complaints.
3. Do not restate the dashboard; surface what matters most.
4. Stay tightly tied to Indian fintech product behavior, trust, onboarding, support, and payment flows.
"""

    STAGE2A_PROMPT: str = """You are the Contrarian Chairman of an Indian fintech product-intelligence council.

ANALYTICAL FRAME
{analytical_frame}

FINDINGS SUMMARY
{findings_summary}

ANONYMIZED STAGE 1 OUTPUTS
{labeled_responses}

Your task is an independent contrarian pass.

Identify:
1. the strongest supported convergence
2. the most important weakly-supported leap
3. the most important missed tension or asymmetry

Use exactly this structure:
## Confirmed Signals
- [signal + why it is well-supported]

## Weak Leaps
- [claim that outruns evidence + why]

## Missing Tensions
- [important unresolved tension or blind spot]

Keep it under 250 words and quote or reference evidence directly.
"""

    STAGE2B_PROMPT: str = """You are conducting an anonymized evidence audit of specialist council outputs about Indian fintech Play Store reviews.

ANALYTICAL FRAME
{analytical_frame}

FINDINGS SUMMARY
{findings_summary}

ANONYMIZED STAGE 1 OUTPUTS
{labeled_responses}

Audit only the evidence quality of these responses.

Use exactly this structure:
## Supported Claims
- [response label + specific claim that is well-grounded]

## Evidence Gaps
- [response label + claim that is weakly supported or over-extended]

## Missing Evidence
- [important data angle the group did not use]

Rules:
1. Do not rank authors.
2. Focus on evidence traceability, not writing quality.
3. Quote labels explicitly: Response A/B/C.
4. Keep it under 250 words.
"""

    STAGE2C_PROMPT: str = """You are the chairman synthesizing the council's audit phase.

ANALYTICAL FRAME
{analytical_frame}

STAGE 2A CONTRARIAN PASS
{stage2a_contrarian_pass}

STAGE 2B SPECIALIST EVIDENCE AUDITS
{stage2b_evidence_audits}

Produce one audit synthesis using exactly this structure:
## High Confidence
- [claims that survived audit]

## Evidence Risks
- [claims that remain weak or overstated]

## What The Final Report Should Prioritize
- [highest-value themes to lead with]

Keep it under 300 words.
"""

    STAGE3_PROMPT: str = """You are the chairman of an Indian fintech product-intelligence council.

ANALYTICAL FRAME
{analytical_frame}

STAGE 1 SPECIALIST OUTPUTS
{stage1_outputs}

STAGE 2 AUDIT SYNTHESIS
{stage2_gap_analysis}

Produce the final report using exactly this structure:

## Key Findings
List 3-5 findings. For each use:
**Finding [N]: [Title]**
- **Insight:** [specific claim with numbers where available]
- **Evidence base:** [which specialist views and audit signals support it]
- **Confidence:** HIGH | MEDIUM | LOW
- **Why hypothesis:** [brief mechanism]

## App-Specific Signals
One paragraph each for Groww, Jupiter, CRED, PhonePe, and Paytm.

## Cross-App Pattern
One category-level pattern grounded in at least two apps.

Rules:
1. Lead with the findings that most change PM priorities.
2. Exclude claims the audit marked as weak unless clearly caveated.
3. Keep every claim evidence-grounded and PM-useful.
4. Total length: 500-700 words.
"""

    ROLE_MANDATES: dict[str, str] = {
        "anthropic/claude-opus-4.7": (
            "ROLE MANDATE — FIRST PRINCIPLES ANALYST\n\n"
            "Reason from structural causes, not complaint summaries. Ask what the data reveals about trust, regulation, incentives, and operating-model tradeoffs in Indian fintech."
        ),
        "deepseek/deepseek-r1": (
            "ROLE MANDATE — OUTSIDER ANALYST\n\n"
            "Use only what the data literally shows. Do not rely on domain assumptions. Surface what looks strange, inconsistent, or counterintuitive from the evidence alone."
        ),
        "qwen/qwen3.6-plus": (
            "ROLE MANDATE — EXPANSIONIST ANALYST\n\n"
            "Look for upside, unmet jobs-to-be-done, and opportunity signals hidden inside complaints, trust gaps, and cross-app differences."
        ),
    }

    ROLE_NAMES: dict[str, str] = {
        "anthropic/claude-opus-4.7": "First Principles",
        "deepseek/deepseek-r1": "Outsider",
        "qwen/qwen3.6-plus": "Expansionist",
        "google/gemini-3.1-pro-preview": "Chairman (Contrarian)",
    }

    def __init__(
        self,
        members: list[CouncilMember],
        chairman: CouncilMember,
        config: Config,
        db: DatabaseManager | None = None,
        seed: int | None = None,
    ) -> None:
        self.members = members
        self.chairman = chairman
        self.config = config
        self._db = db
        self._seed = seed

    @property
    def specialist_members(self) -> list[CouncilMember]:
        """Return non-chairman specialist members only."""
        return [member for member in self.members if member.model_id != self.chairman.model_id]

    @classmethod
    def default(
        cls,
        config: Config,
        db: DatabaseManager | None = None,
    ) -> "CouncilOrchestrator":
        """Factory for the standard four-model council via OpenRouter."""
        chairman = CouncilMember(
            name="Gemini 3.1 Pro Preview (Chairman)",
            provider="openrouter",
            model_id="google/gemini-3.1-pro-preview",
            config=config,
        )
        members = [
            chairman,
            CouncilMember(
                name="Claude Opus 4.7",
                provider="openrouter",
                model_id="anthropic/claude-opus-4.7",
                config=config,
            ),
            CouncilMember(
                name="DeepSeek R1",
                provider="openrouter",
                model_id="deepseek/deepseek-r1",
                config=config,
            ),
            CouncilMember(
                name="Qwen 3.6 Plus",
                provider="openrouter",
                model_id="qwen/qwen3.6-plus",
                config=config,
            ),
        ]
        return cls(members=members, chairman=chairman, config=config, db=db)

    async def run(self, findings_summary: str) -> CouncilResult:
        """Execute the revised multi-stage council."""
        pipeline_start = time.monotonic()
        generated_at = datetime.now(timezone.utc).isoformat()

        analytical_frame = self._load_cached_text("council_stage0_frame", "frame")
        if not analytical_frame:
            analytical_frame = await self._stage0_frame_question(findings_summary)
            if not analytical_frame:
                raise RuntimeError("Stage 0 failed: chairman returned empty analytical frame. Re-run to retry.")
            self._save_text_checkpoint("council_stage0_frame", analytical_frame, "frame")

        await self._preflight_openrouter_models()

        stage1_responses = await self._run_stage1(findings_summary, analytical_frame)
        if not stage1_responses:
            raise RuntimeError("Stage 1 failed: no specialist outputs available.")

        shuffled_stage1 = list(stage1_responses.values())
        rng = random.Random(self._seed)
        rng.shuffle(shuffled_stage1)
        anonymization_map = {
            label: response.member_name
            for label, response in zip(_LABELS, shuffled_stage1)
        }
        labeled_responses = self._build_labeled_responses(shuffled_stage1)

        stage2a = self._load_cached_text("council_stage2a_contrarian", "text")
        if not stage2a:
            stage2a_response = await self.chairman.generate(
                self.STAGE2A_PROMPT.format(
                    analytical_frame=analytical_frame,
                    findings_summary=findings_summary,
                    labeled_responses=labeled_responses,
                )
            )
            stage2a = stage2a_response.clean_response.strip()
            if stage2a:
                self._save_text_checkpoint("council_stage2a_contrarian", stage2a, "text")
            else:
                stage2a = "[Stage 2a contrarian pass unavailable.]"

        stage2b_evidence_audits = await self._run_stage2b(findings_summary, analytical_frame, labeled_responses)
        if not stage2b_evidence_audits:
            raise RuntimeError("Stage 2b failed: no specialist evidence audits available.")

        stage2c = self._load_cached_text("council_stage2c_audit_synthesis", "text")
        if not stage2c:
            stage2c_response = await self.chairman.generate(
                self.STAGE2C_PROMPT.format(
                    analytical_frame=analytical_frame,
                    stage2a_contrarian_pass=stage2a,
                    stage2b_evidence_audits=self._format_member_responses(stage2b_evidence_audits),
                )
            )
            stage2c = stage2c_response.clean_response.strip()
            if not stage2c:
                stage2c = "[Stage 2 audit synthesis unavailable.]"
            self._save_text_checkpoint("council_stage2c_audit_synthesis", stage2c, "text")
            self._save_text_checkpoint("council_stage2_audit", stage2c, "text")

        stage3_synthesis = self._load_cached_text("council_stage3_final", "text")
        if not stage3_synthesis:
            stage3_response = await self.chairman.generate(
                self.STAGE3_PROMPT.format(
                    analytical_frame=analytical_frame,
                    stage1_outputs=self._format_member_responses(stage1_responses),
                    stage2_gap_analysis=stage2c,
                )
            )
            stage3_synthesis = stage3_response.clean_response.strip()
            if len(stage3_synthesis) < 100:
                raise RuntimeError(
                    f"Stage 3 synthesis is too short ({len(stage3_synthesis)} chars). "
                    "The chairman model may have returned an empty or blocked response."
                )
            self._save_text_checkpoint("council_stage3_final", stage3_synthesis, "text")

        total_duration_ms = int((time.monotonic() - pipeline_start) * 1000)
        result = CouncilResult(
            stage1_responses=stage1_responses,
            anonymization_map=anonymization_map,
            stage2_gap_analysis=stage2c,
            stage3_synthesis=stage3_synthesis,
            total_duration_ms=total_duration_ms,
            generated_at=generated_at,
            analytical_frame=analytical_frame,
            stage2a_contrarian_pass=stage2a,
            stage2b_evidence_audits=stage2b_evidence_audits,
            stage2c_audit_synthesis=stage2c,
        )
        self._save_result(result)
        return result

    def run_sync(self, findings_summary: str) -> CouncilResult:
        """Synchronous wrapper for ``run``."""
        return asyncio.run(self.run(findings_summary))

    async def _run_stage1(
        self,
        findings_summary: str,
        analytical_frame: str,
    ) -> dict[str, MemberResponse]:
        """Run or load the three specialist Stage 1 outputs."""
        responses_by_id: dict[str, MemberResponse] = {}
        members_to_run: list[CouncilMember] = []
        for member in self.specialist_members:
            cached = self._load_cached_member_response(self._stage1_cache_key(member.model_id), member)
            if cached is not None:
                responses_by_id[member.model_id] = cached
            else:
                members_to_run.append(member)

        if members_to_run:
            gathered = await asyncio.gather(
                *[
                    member.generate(self._build_stage1_prompt_for_member(member, findings_summary, analytical_frame))
                    for member in members_to_run
                ],
                return_exceptions=True,
            )
            for member, item in zip(members_to_run, gathered):
                if isinstance(item, BaseException):
                    if isinstance(item, httpx.HTTPStatusError):
                        status_code = item.response.status_code
                        if 400 <= status_code < 500 and status_code != 429:
                            raise RuntimeError(
                                f"Stage 1 member {self._member_label(member)} returned fatal HTTP {status_code} "
                                f"(model: {member.model_id}). Check API key and model ID. Council aborted."
                            ) from item
                    logger.warning(
                        "Stage 1 member %s raised %s — recording empty response",
                        self._member_label(member),
                        item,
                    )
                    responses_by_id[member.model_id] = MemberResponse(
                        member_name=member.name,
                        model_id=member.model_id,
                        raw_response=f"[error] {item}",
                        clean_response="",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        duration_ms=0,
                    )
                else:
                    responses_by_id[member.model_id] = item
                    self._checkpoint_member_response(self._stage1_cache_key(member.model_id), item)

        stage1_responses = {
            self._member_label(member): responses_by_id[member.model_id]
            for member in self.specialist_members
            if responses_by_id.get(member.model_id) and responses_by_id[member.model_id].clean_response.strip()
        }
        if stage1_responses:
            self._save_stage1_outputs(stage1_responses)
        return stage1_responses

    async def _run_stage2b(
        self,
        findings_summary: str,
        analytical_frame: str,
        labeled_responses: str,
    ) -> dict[str, MemberResponse]:
        """Run or load specialist evidence audits."""
        audits_by_id: dict[str, MemberResponse] = {}
        members_to_run: list[CouncilMember] = []
        for member in self.specialist_members:
            cache_key = self._stage2b_cache_key(member.model_id)
            cached = self._load_cached_member_response(cache_key, member)
            if cached is not None:
                audits_by_id[member.model_id] = cached
            else:
                members_to_run.append(member)

        if members_to_run:
            gathered = await asyncio.gather(
                *[
                    member.generate(
                        self.STAGE2B_PROMPT.format(
                            analytical_frame=analytical_frame,
                            findings_summary=findings_summary,
                            labeled_responses=labeled_responses,
                        )
                    )
                    for member in members_to_run
                ],
                return_exceptions=True,
            )
            for member, item in zip(members_to_run, gathered):
                if isinstance(item, BaseException):
                    logger.warning(
                        "Stage 2b member %s raised %s — skipping this audit on this run",
                        self._member_label(member),
                        item,
                    )
                    continue
                if item.clean_response.strip():
                    audits_by_id[member.model_id] = item
                    self._checkpoint_member_response(self._stage2b_cache_key(member.model_id), item)

        return {
            self._member_label(member): audits_by_id[member.model_id]
            for member in self.specialist_members
            if member.model_id in audits_by_id and audits_by_id[member.model_id].clean_response.strip()
        }

    async def _preflight_openrouter_models(self) -> None:
        """Verify all configured council model IDs exist in OpenRouter's catalog."""
        async with httpx.AsyncClient(timeout=_PREFLIGHT_TIMEOUT) as client:
            try:
                response = await client.get(
                    _OPENROUTER_MODELS_ENDPOINT,
                    headers={"Authorization": f"Bearer {self.config.openrouter_api_key}"},
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(
                    f"OpenRouter models preflight failed — HTTP {exc.response.status_code}. "
                    "Check OPENROUTER_API_KEY."
                ) from exc
            except httpx.TransportError as exc:
                raise RuntimeError(
                    f"OpenRouter models preflight failed — {type(exc).__name__}: {exc}."
                ) from exc

        data = response.json().get("data", [])
        available_ids = {item["id"] for item in data if isinstance(item, dict) and item.get("id")}
        missing = [
            f"{member.name} ({member.model_id})"
            for member in self.members
            if member.model_id not in available_ids
        ]
        if missing:
            raise RuntimeError(
                "OpenRouter preflight failed — model not found in catalog: "
                + ", ".join(missing)
                + "."
            )

    async def _stage0_frame_question(self, findings_text: str) -> str:
        """Chairman produces a short analytical frame for the session."""
        frame_prompt = (
            "You are the chairman of an Indian fintech product-intelligence council.\n\n"
            "Read the findings summary below and produce one sharp analytical frame, no more than 100 words, "
            "capturing the most important question this council should answer.\n\n"
            f"FINDINGS SUMMARY\n{findings_text}\n\n"
            "Analytical frame:"
        )
        response = await self.chairman.generate(frame_prompt)
        return response.clean_response.strip()

    def _build_stage1_prompt_for_member(
        self,
        member: CouncilMember,
        findings_summary: str,
        analytical_frame: str,
    ) -> str:
        """Build a cache-stable Stage 1 prompt for one specialist."""
        return "\n\n".join(
            [
                self.ROLE_MANDATES.get(member.model_id, ""),
                self.STAGE1_PROMPT.format(
                    analytical_frame=analytical_frame,
                    findings_summary=findings_summary,
                ),
            ]
        ).strip()

    def _build_labeled_responses(self, responses: list[MemberResponse]) -> str:
        """Return anonymized A/B/C sections for specialist outputs."""
        return "\n".join(
            f"--- {label} ---\n{response.clean_response}"
            for label, response in zip(_LABELS, responses)
        )

    def _format_member_responses(self, responses: dict[str, MemberResponse]) -> str:
        """Format named responses into a prompt-friendly block."""
        return "\n".join(
            f"=== {name} ===\n{response.clean_response}\n"
            for name, response in responses.items()
        )

    def _member_label(self, member: CouncilMember) -> str:
        """Return a display label including the configured role name."""
        role = self.ROLE_NAMES.get(member.model_id)
        return f"{member.name} [{role}]" if role else member.name

    @staticmethod
    def _sanitize_model_key(model_id: str) -> str:
        """Make a model ID safe for use as a checkpoint key."""
        return model_id.replace("/", "_").replace(":", "_")

    def _stage1_cache_key(self, model_id: str) -> str:
        """Return the per-member Stage 1 checkpoint key."""
        return f"council_stage1_{self._sanitize_model_key(model_id)}"

    def _stage2b_cache_key(self, model_id: str) -> str:
        """Return the per-member Stage 2b checkpoint key."""
        return f"council_stage2b_{self._sanitize_model_key(model_id)}"

    def _load_cached_text(self, phase: str, field: str) -> str:
        """Load a simple text checkpoint field from pipeline_state."""
        if self._db is None:
            return ""
        try:
            state = self._db.get_phase_state(phase)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read %s from pipeline_state: %s", phase, exc)
            return ""
        if state and state.get("status") == "complete":
            return str((state.get("metadata") or {}).get(field, ""))
        return ""

    def _save_text_checkpoint(self, phase: str, text: str, field: str) -> None:
        """Persist a simple text checkpoint to pipeline_state."""
        if self._db is None:
            return
        self._db.save_phase_state(phase, "complete", {field: text})

    def _load_cached_member_response(
        self,
        phase: str,
        member: CouncilMember,
    ) -> MemberResponse | None:
        """Load a cached member response or return ``None``."""
        if self._db is None:
            return None
        try:
            state = self._db.get_phase_state(phase)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read %s: %s", phase, exc)
            return None
        if not (state and state.get("status") == "complete"):
            return None
        metadata = state.get("metadata") or {}
        clean = str(metadata.get("clean_response", ""))
        if not clean.strip():
            return None
        return MemberResponse(
            member_name=str(metadata.get("member_name", member.name)),
            model_id=str(metadata.get("model_id", member.model_id)),
            raw_response=str(metadata.get("raw_response", "")),
            clean_response=clean,
            timestamp=str(metadata.get("timestamp", datetime.now(timezone.utc).isoformat())),
            duration_ms=int(metadata.get("duration_ms", 0)),
        )

    def _checkpoint_member_response(self, phase: str, response: MemberResponse) -> None:
        """Persist a successful member response checkpoint."""
        if self._db is None:
            return
        self._db.save_phase_state(phase, "complete", dataclasses.asdict(response))

    def _save_stage1_outputs(self, responses: dict[str, MemberResponse]) -> None:
        """Persist an aggregate Stage 1 checkpoint for compatibility."""
        if self._db is None:
            return
        self._db.save_phase_state(
            "council_stage1_outputs",
            "complete",
            {name: dataclasses.asdict(response) for name, response in responses.items()},
        )

    def _save_result(self, result: CouncilResult) -> None:
        """Serialize CouncilResult to outputs/council_result.json."""
        os.makedirs("outputs", exist_ok=True)
        raw: dict = dataclasses.asdict(result)  # type: ignore[assignment]

        for response_dict in raw.get("stage1_responses", {}).values():
            response_dict["role"] = self.ROLE_NAMES.get(response_dict.get("model_id", ""), "")
        for response_dict in (raw.get("stage2b_evidence_audits") or {}).values():
            response_dict["role"] = self.ROLE_NAMES.get(response_dict.get("model_id", ""), "")

        output_path = os.path.join("outputs", "council_result.json")
        try:
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(raw, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            logger.error("Failed to save council result to %s: %s", output_path, exc)
            raise RuntimeError(
                f"Could not write {output_path}: {exc}. Check that outputs/ is writable."
            ) from exc
