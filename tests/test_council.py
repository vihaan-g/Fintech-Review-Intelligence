import dataclasses
import asyncio

from src.config import Config
from src.council.council_member import CouncilMember, MemberResponse
from src.council.council_orchestrator import CouncilOrchestrator, CouncilResult
from src.data_collection.database_manager import DatabaseManager


def _response(member_name: str, model_id: str, text: str) -> MemberResponse:
    return MemberResponse(
        member_name=member_name,
        model_id=model_id,
        raw_response=text,
        clean_response=text,
        timestamp="2026-04-20T00:00:00",
        duration_ms=25,
    )


def _stage1_text(label: str) -> str:
    return (
        f"**Insight 1: {label} angle one**\n"
        "- **App(s):** Jupiter\n"
        "- **Data signal:** Specific metric from the findings.\n"
        "- **Hypothesis:** A detailed explanation of the mechanism behind this pattern.\n"
        "- **Implication:** A specific PM action for next week.\n\n"
        f"**Insight 2: {label} angle two**\n"
        "- **App(s):** CRED\n"
        "- **Data signal:** Another concrete metric from the findings.\n"
        "- **Hypothesis:** Another detailed explanation grounded in the data.\n"
        "- **Implication:** Another specific action with clear execution value.\n"
    )


def _stage0_frame_text() -> str:
    return (
        "Jupiter's trust failures and CRED's reward backlash suggest Indian fintech users forgive"
        " reversible friction but punish irreversible financial harm. How should fintech apps redesign"
        " product flows so support effort is not wasted on structurally unresolvable failures?"
    )


def test_council_member_strips_think_tags(llm_env) -> None:
    """CouncilMember strips provider reasoning tags from visible output."""
    member = CouncilMember(
        name="Test",
        provider="openrouter",
        model_id="google/gemini-3.1-pro-preview",
        config=Config.from_env(),
    )
    raw = "<think>hidden reasoning</think>Actual answer"
    assert member._strip_think_tags(raw) == "Actual answer"


def test_council_orchestrator_default_has_openrouter_only_members(llm_env) -> None:
    """Default council roster keeps four models and routes all through OpenRouter."""
    orchestrator = CouncilOrchestrator.default(Config.from_env())
    assert len(orchestrator.members) == 4
    assert all(member.provider == "openrouter" for member in orchestrator.members)


def test_council_orchestrator_default_chairman_model_id(llm_env) -> None:
    """Chairman model ID is preserved under its OpenRouter form."""
    orchestrator = CouncilOrchestrator.default(Config.from_env())
    assert orchestrator.chairman.model_id == "google/gemini-3.1-pro-preview"


def test_specialist_members_exclude_chairman(llm_env) -> None:
    """Stage 1 specialists should exclude the chairman."""
    orchestrator = CouncilOrchestrator.default(Config.from_env())
    specialists = orchestrator.specialist_members
    assert len(specialists) == 3
    assert all(member.model_id != orchestrator.chairman.model_id for member in specialists)


def test_role_mandates_cover_only_specialists() -> None:
    """ROLE_MANDATES contains specialist models only."""
    assert set(CouncilOrchestrator.ROLE_MANDATES.keys()) == {
        "anthropic/claude-opus-4.7",
        "deepseek/deepseek-r1",
        "qwen/qwen3.6-plus",
    }
    assert "google/gemini-3.1-pro-preview" not in CouncilOrchestrator.ROLE_MANDATES


def test_stage0_frame_guard_rejects_placeholder_text() -> None:
    """Stage 0 frame guard should reject placeholder or non-question outputs."""
    assert not CouncilOrchestrator._is_stage0_frame_usable("frame")
    assert not CouncilOrchestrator._is_stage0_frame_usable("Analytical frame")
    assert not CouncilOrchestrator._is_stage0_frame_usable("- Jupiter has problems")
    assert CouncilOrchestrator._is_stage0_frame_usable(
        "Jupiter's support responsiveness coexists with severe trust collapse while PhonePe and Paytm sustain strong ratings on lighter-touch service. How should Indian fintechs handle irreversible trust failures without relying on support theater?"
    )
    assert CouncilOrchestrator._is_stage0_frame_usable(
        "Jupiter's support responsiveness coexists with severe trust collapse while PhonePe and Paytm sustain strong ratings on lighter-touch service, forcing a trade-off between compliance intensity and perceived trust during onboarding and support."
    )


def test_build_labeled_responses_uses_three_labels(llm_env) -> None:
    """Anonymized specialist labels should be A/B/C only."""
    orchestrator = CouncilOrchestrator.default(Config.from_env())
    prompt_block = orchestrator._build_labeled_responses(
        [
            _response("one", "m1", "insight 1"),
            _response("two", "m2", "insight 2"),
            _response("three", "m3", "insight 3"),
        ]
    )
    assert "Response A" in prompt_block
    assert "Response B" in prompt_block
    assert "Response C" in prompt_block
    assert "Response D" not in prompt_block


def test_stage1_outputs_checkpoint_saved(llm_env) -> None:
    """Aggregate Stage 1 outputs should be persisted under the compatibility key."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        orchestrator = CouncilOrchestrator.default(Config.from_env(), db)
        orchestrator._save_stage1_outputs(
            {
                "Claude Opus 4.7 [First Principles]": _response(
                    "Claude Opus 4.7",
                    "anthropic/claude-opus-4.7",
                    "insight",
                )
            }
        )
        state = db.get_phase_state("council_stage1_outputs")
        assert state is not None
        assert state["status"] == "complete"


def test_council_result_has_new_audit_fields() -> None:
    """CouncilResult exposes additive fields for the revised audit stages."""
    result = CouncilResult(
        stage1_responses={},
        anonymization_map={},
        stage2_gap_analysis="audit alias",
        stage3_synthesis="x" * 120,
        total_duration_ms=100,
        generated_at="2026-04-20T00:00:00",
    )
    assert result.stage2a_contrarian_pass == ""
    assert result.stage2c_audit_synthesis == ""
    assert result.stage2b_evidence_audits is None


def test_stage1_response_usable_guard_rejects_truncated_text() -> None:
    """Stage 1 guard should reject clearly truncated specialist outputs."""
    assert not CouncilOrchestrator._is_stage1_response_usable("**Insight 1: cut off")
    assert CouncilOrchestrator._is_stage1_response_usable(_stage1_text("usable"))


def test_run_excludes_chairman_from_stage1_and_runs_stage2b_specialists(llm_env) -> None:
    """The revised flow should call 3 specialists in Stage 1 and 3 in Stage 2b."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        orchestrator = CouncilOrchestrator.default(Config.from_env(), db)

        stage1_calls: list[str] = []
        stage2b_calls: list[str] = []
        chairman_calls: list[str] = []

        async def noop_preflight() -> None:
            return None

        orchestrator._preflight_openrouter_models = noop_preflight  # type: ignore[method-assign]

        for member in orchestrator.specialist_members:
            async def specialist_generate(prompt: str, member=member, **kwargs) -> MemberResponse:
                if "evidence audit" in prompt.lower():
                    stage2b_calls.append(member.model_id)
                    return _response(member.name, member.model_id, f"audit {member.name}")
                stage1_calls.append(member.model_id)
                return _response(member.name, member.model_id, _stage1_text(member.name))

            member.generate_with_options = specialist_generate  # type: ignore[method-assign]

        async def chairman_generate(prompt: str, **kwargs) -> MemberResponse:
            chairman_calls.append(prompt)
            prompt_lower = prompt.lower()
            if prompt_lower.endswith("analytical frame:"):
                return _response(orchestrator.chairman.name, orchestrator.chairman.model_id, _stage0_frame_text())
            if "independent contrarian pass" in prompt_lower:
                return _response(orchestrator.chairman.name, orchestrator.chairman.model_id, "contrarian")
            if "audit phase" in prompt_lower:
                return _response(orchestrator.chairman.name, orchestrator.chairman.model_id, "audit synthesis")
            return _response(orchestrator.chairman.name, orchestrator.chairman.model_id, "X" * 140)

        orchestrator.chairman.generate_with_options = chairman_generate  # type: ignore[method-assign]
        result = asyncio.run(orchestrator.run("findings text"))

    assert len(stage1_calls) == 3
    assert len(stage2b_calls) == 3
    assert all(model_id != orchestrator.chairman.model_id for model_id in stage1_calls)
    assert result.stage2_gap_analysis == result.stage2c_audit_synthesis
    assert result.stage2b_evidence_audits is not None
    assert len(result.stage2b_evidence_audits) == 3
    assert len(chairman_calls) == 4


def test_partial_stage2b_resume_uses_cached_audits(llm_env) -> None:
    """Cached Stage 2b audits should be reused and only missing ones rerun."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        orchestrator = CouncilOrchestrator.default(Config.from_env(), db)
        first_specialist = orchestrator.specialist_members[0]
        db.save_phase_state(
            orchestrator._stage2b_cache_key(first_specialist.model_id),
            "complete",
            dataclasses.asdict(_response(first_specialist.name, first_specialist.model_id, "cached audit")),
        )

        rerun_calls: list[str] = []

        async def noop_preflight() -> None:
            return None

        orchestrator._preflight_openrouter_models = noop_preflight  # type: ignore[method-assign]

        async def chairman_generate(prompt: str, **kwargs) -> MemberResponse:
            prompt_lower = prompt.lower()
            if prompt_lower.endswith("analytical frame:"):
                return _response(orchestrator.chairman.name, orchestrator.chairman.model_id, _stage0_frame_text())
            if "independent contrarian pass" in prompt_lower:
                return _response(orchestrator.chairman.name, orchestrator.chairman.model_id, "contrarian")
            if "audit phase" in prompt_lower:
                return _response(orchestrator.chairman.name, orchestrator.chairman.model_id, "audit synthesis")
            return _response(orchestrator.chairman.name, orchestrator.chairman.model_id, "X" * 140)

        orchestrator.chairman.generate_with_options = chairman_generate  # type: ignore[method-assign]

        for member in orchestrator.specialist_members:
            async def specialist_generate(prompt: str, member=member, **kwargs) -> MemberResponse:
                if "evidence audit" in prompt.lower():
                    rerun_calls.append(member.model_id)
                    return _response(member.name, member.model_id, "fresh audit")
                return _response(member.name, member.model_id, _stage1_text(member.name))

            member.generate_with_options = specialist_generate  # type: ignore[method-assign]

        result = asyncio.run(orchestrator.run("findings text"))

    assert result.stage2b_evidence_audits is not None
    assert first_specialist.model_id not in rerun_calls
    assert len(rerun_calls) == 2
