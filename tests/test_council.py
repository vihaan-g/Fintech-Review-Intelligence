import asyncio

import httpx
import pytest

from src.config import Config
from src.council.council_member import CouncilMember, MemberResponse
from src.council.council_orchestrator import CouncilOrchestrator
from src.data_collection.database_manager import DatabaseManager


def test_council_member_strips_think_tags(llm_env) -> None:
    """CouncilMember._strip_think_tags() removes think blocks correctly."""
    member = CouncilMember(
        name="Test",
        provider="gemini",
        model_id="gemini-2.5-flash-lite",
        config=Config.from_env(),
    )
    raw = "<think>some reasoning here</think>Actual insight about CRED."
    result = member._strip_think_tags(raw)
    assert "think" not in result
    assert "Actual insight about CRED." in result


def test_council_member_strips_multiline_think_tags(llm_env) -> None:
    """_strip_think_tags() handles multiline think blocks."""
    member = CouncilMember("Test", "gemini", "gemini-2.5-flash-lite", Config.from_env())
    raw = "<think>\nline 1\nline 2\n</think>\nFinal answer."
    assert member._strip_think_tags(raw).strip() == "Final answer."


def test_council_orchestrator_default_has_four_members(llm_env) -> None:
    """CouncilOrchestrator.default() creates a council with 4 members."""
    orchestrator = CouncilOrchestrator.default(Config.from_env())
    assert len(orchestrator.members) == 4


def test_council_orchestrator_anonymization_map(llm_env) -> None:
    """_build_stage2_prompt() includes all expected anonymized response labels."""
    orchestrator = CouncilOrchestrator.default(Config.from_env())
    fake_responses = [
        MemberResponse(
            member_name=f"Member{i}",
            model_id=f"model-{i}",
            raw_response=f"insight {i}",
            clean_response=f"insight {i}",
            timestamp="2026-04-16T00:00:00",
            duration_ms=100,
        )
        for i in range(4)
    ]
    labels = ["Response A", "Response B", "Response C", "Response D"]
    prompt = orchestrator._build_stage2_prompt(fake_responses, labels)
    for label in labels:
        assert label in prompt


def test_stage1_responses_checkpointed_before_stage2(llm_env) -> None:
    """Stage 1 member outputs are checkpointed before Stage 2 starts."""
    config = Config.from_env()
    keys_before_stage2: dict = {}

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.save_phase_state("council_stage0_frame", "complete", {"frame": "Test frame."})
        orchestrator = CouncilOrchestrator.default(config, db)

        async def mock_generate(prompt: str) -> MemberResponse:
            return MemberResponse(
                member_name="test",
                model_id="test-model",
                raw_response="insight",
                clean_response="insight",
                timestamp="2026-04-20T00:00:00",
                duration_ms=50,
            )

        for member in orchestrator.members:
            member.generate = mock_generate

        async def noop_preflight() -> None:
            return None

        orchestrator._preflight_openrouter_models = noop_preflight  # type: ignore[method-assign]
        chairman_calls = [0]

        async def intercept_chairman(prompt: str) -> MemberResponse:
            chairman_calls[0] += 1
            if chairman_calls[0] == 1:
                return MemberResponse(
                    member_name="chairman",
                    model_id="gemini-3.1-pro-preview",
                    raw_response="insight",
                    clean_response="insight",
                    timestamp="2026-04-20T00:00:00",
                    duration_ms=50,
                )
            openrouter_members = [member for member in orchestrator.members if member.provider == "openrouter"]
            for member in openrouter_members:
                key = orchestrator._stage1_cache_key(member.model_id)
                keys_before_stage2[key] = db.get_phase_state(key)
            raise RuntimeError("intercepted_stage2_for_test")

        orchestrator.chairman.generate = intercept_chairman

        with pytest.raises(RuntimeError, match="intercepted_stage2_for_test"):
            asyncio.run(orchestrator.run("test findings"))

    assert keys_before_stage2
    for key, state in keys_before_stage2.items():
        assert state is not None
        assert state.get("status") == "complete"


def test_stage1_skipped_when_all_member_keys_cached(
    llm_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage 1 gather is skipped when all member responses are cached."""
    import src.council.council_orchestrator as co_module

    config = Config.from_env()
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.save_phase_state("council_stage0_frame", "complete", {"frame": "Cached frame."})
        orchestrator = CouncilOrchestrator.default(config, db)

        for member in orchestrator.members:
            key = orchestrator._stage1_cache_key(member.model_id)
            db.save_phase_state(
                key,
                "complete",
                {
                    "member_name": member.name,
                    "model_id": member.model_id,
                    "raw_response": f"cached insight from {member.name}",
                    "clean_response": f"cached insight from {member.name}",
                    "timestamp": "2026-04-20T00:00:00",
                    "duration_ms": 100,
                },
            )

        gather_called = [False]
        original_gather = asyncio.gather

        async def tracking_gather(*coroutines, **kwargs):
            gather_called[0] = True
            return await original_gather(*coroutines, **kwargs)

        monkeypatch.setattr(co_module.asyncio, "gather", tracking_gather)

        async def noop_preflight() -> None:
            return None

        orchestrator._preflight_openrouter_models = noop_preflight  # type: ignore[method-assign]

        async def stop_at_stage2(prompt: str) -> MemberResponse:
            raise RuntimeError("stage2_intercepted_for_test")

        orchestrator.chairman.generate = stop_at_stage2

        with pytest.raises(RuntimeError, match="stage2_intercepted_for_test"):
            asyncio.run(orchestrator.run("test findings"))

    assert not gather_called[0]


def test_council_orchestrator_chairman_model_id(llm_env) -> None:
    """CouncilOrchestrator.default() chairman uses gemini-3.1-pro-preview."""
    orchestrator = CouncilOrchestrator.default(Config.from_env())
    assert orchestrator.chairman.model_id == "gemini-3.1-pro-preview"


def test_role_mandates_coverage() -> None:
    """ROLE_MANDATES contains a key for each member model ID and no chairman."""
    member_ids = {
        "anthropic/claude-opus-4.7",
        "deepseek/deepseek-r1",
        "qwen/qwen3.6-plus",
    }
    chairman_id = "gemini-3.1-pro-preview"
    assert set(CouncilOrchestrator.ROLE_MANDATES.keys()) == member_ids
    assert chairman_id not in CouncilOrchestrator.ROLE_MANDATES


def test_council_result_has_analytical_frame_field() -> None:
    """CouncilResult.analytical_frame defaults to empty string."""
    from src.council.council_orchestrator import CouncilResult

    result = CouncilResult(
        stage1_responses={},
        anonymization_map={},
        stage2_gap_analysis="gap analysis text",
        stage3_synthesis="x" * 100,
        total_duration_ms=1000,
        generated_at="2026-04-20T00:00:00",
    )
    assert result.analytical_frame == ""


def test_bug6_stage0_receives_full_findings_text(llm_env) -> None:
    """Stage 0 prompt must not truncate findings_text."""
    orchestrator = CouncilOrchestrator.default(Config.from_env())
    long_text = "X" * 5000
    frame_prompt_parts: list[str] = []

    async def capture_generate(prompt: str) -> MemberResponse:
        frame_prompt_parts.append(prompt)
        return MemberResponse(
            member_name="chairman",
            model_id="test",
            raw_response="analytical frame",
            clean_response="analytical frame",
            timestamp="2026-04-20T00:00:00",
            duration_ms=100,
        )

    original = orchestrator.chairman.generate
    orchestrator.chairman.generate = capture_generate
    asyncio.run(orchestrator._stage0_frame_question(long_text))
    orchestrator.chairman.generate = original
    assert frame_prompt_parts
    assert long_text in frame_prompt_parts[0]


def test_bug7_fatal_4xx_in_stage1_raises(llm_env) -> None:
    """A fatal HTTP 4xx from a Stage 1 member must raise."""
    orchestrator = CouncilOrchestrator.default(Config.from_env())

    class FakeResponse:
        status_code = 401
        text = "Unauthorized"

    ok_response = MemberResponse(
        member_name="ok",
        model_id="ok-model",
        raw_response="insight text",
        clean_response="insight text",
        timestamp="2026-04-20T00:00:00",
        duration_ms=100,
    )
    fatal_exc = httpx.HTTPStatusError(
        "401 Unauthorized",
        request=None,  # type: ignore[arg-type]
        response=FakeResponse(),  # type: ignore[arg-type]
    )
    gathered = [fatal_exc, ok_response, ok_response, ok_response]

    with pytest.raises(RuntimeError, match="fatal HTTP 401"):
        for member, item in zip(orchestrator.members, gathered):
            if isinstance(item, BaseException) and isinstance(item, httpx.HTTPStatusError):
                status_code = item.response.status_code
                if 400 <= status_code < 500 and status_code != 429:
                    raise RuntimeError(
                        f"Stage 1 member {orchestrator._member_label(member)} returned "
                        f"fatal HTTP {status_code} (model: {member.model_id}). "
                        "Check API key and model ID. Council aborted."
                    ) from item


def test_preflight_passes_for_paid_model(
    llm_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight passes for paid models; it only checks catalog existence."""
    import src.council.council_orchestrator as co_module

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        orchestrator = CouncilOrchestrator.default(Config.from_env(), db)

    openrouter_ids = [member.model_id for member in orchestrator.members if member.provider == "openrouter"]
    catalog = {
        "data": [
            {"id": model_id, "pricing": {"prompt": "0.0015", "completion": "0.002"}}
            for model_id in openrouter_ids
        ]
    }

    class MockResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return catalog

    class MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            return MockResponse()

    monkeypatch.setattr(co_module.httpx, "AsyncClient", lambda **kwargs: MockClient())
    asyncio.run(orchestrator._preflight_openrouter_models())


def test_stage0_fail_fast_raises_before_stage1(llm_env) -> None:
    """Empty chairman frame raises RuntimeError before Stage 1 fires."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        orchestrator = CouncilOrchestrator.default(Config.from_env(), db)

        async def empty_chairman(prompt: str) -> MemberResponse:
            return MemberResponse(
                member_name="chairman",
                model_id="test",
                raw_response="",
                clean_response="",
                timestamp="2026-04-20T00:00:00",
                duration_ms=0,
            )

        orchestrator.chairman.generate = empty_chairman
        with pytest.raises(RuntimeError, match="Stage 0 failed"):
            asyncio.run(orchestrator.run("test findings"))


def test_stage0_frame_checkpointed_before_stage1(
    llm_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage 0 frame is persisted before Stage 1 fires."""
    import src.council.council_orchestrator as co_module

    test_frame = "Specific analytical frame for checkpoint test."
    checkpoint_at_gather = [None]

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        orchestrator = CouncilOrchestrator.default(Config.from_env(), db)

        async def mock_chairman(prompt: str) -> MemberResponse:
            return MemberResponse(
                member_name="chairman",
                model_id="test",
                raw_response=test_frame,
                clean_response=test_frame,
                timestamp="2026-04-20T00:00:00",
                duration_ms=0,
            )

        orchestrator.chairman.generate = mock_chairman

        async def noop_preflight() -> None:
            return None

        orchestrator._preflight_openrouter_models = noop_preflight  # type: ignore[method-assign]

        async def capturing_gather(*coroutines, **kwargs):
            checkpoint_at_gather[0] = db.get_phase_state("council_stage0_frame")
            for coroutine in coroutines:
                coroutine.close()
            raise RuntimeError("gather_intercepted_for_test")

        monkeypatch.setattr(co_module.asyncio, "gather", capturing_gather)

        with pytest.raises(RuntimeError, match="gather_intercepted_for_test"):
            asyncio.run(orchestrator.run("test findings"))

    state = checkpoint_at_gather[0]
    assert state is not None
    assert state.get("status") == "complete"
    assert state.get("metadata", {}).get("frame") == test_frame


def test_stage0_skipped_when_frame_cached(
    llm_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stage 0 is skipped when the frame is already cached."""
    import src.council.council_orchestrator as co_module

    cached_frame = "Cached frame from a previous run."
    stage0_generate_called = [False]

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.save_phase_state("council_stage0_frame", "complete", {"frame": cached_frame})
        orchestrator = CouncilOrchestrator.default(Config.from_env(), db)

        async def chairman_generate(prompt: str) -> MemberResponse:
            stage0_generate_called[0] = True
            return MemberResponse(
                member_name="chairman",
                model_id="test",
                raw_response="new frame",
                clean_response="new frame",
                timestamp="2026-04-20T00:00:00",
                duration_ms=0,
            )

        orchestrator.chairman.generate = chairman_generate

        async def noop_preflight() -> None:
            return None

        orchestrator._preflight_openrouter_models = noop_preflight  # type: ignore[method-assign]

        async def early_exit(*coroutines, **kwargs):
            for coroutine in coroutines:
                coroutine.close()
            raise RuntimeError("stage1_early_exit_for_test")

        monkeypatch.setattr(co_module.asyncio, "gather", early_exit)

        with pytest.raises(RuntimeError, match="stage1_early_exit_for_test"):
            asyncio.run(orchestrator.run("test findings"))

    assert not stage0_generate_called[0]
