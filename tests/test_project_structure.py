from pathlib import Path


def test_project_structure() -> None:
    """Confirms the project skeleton was created correctly."""
    root = Path(__file__).resolve().parent.parent
    assert (root / "src/config.py").exists()
    assert (root / "src/data_collection/database_manager.py").exists()
    assert (root / "src/council/council_orchestrator.py").exists()
    assert (root / "src/agents/insight_reporter.py").exists()
    assert (root / ".claude/skills/prompt-optimizer/SKILL.md").exists()
    assert (root / ".claude/skills/multi-agent-patterns/SKILL.md").exists()
    assert (root / ".claude/skills/write-judge-prompt/SKILL.md").exists()
    assert (root / ".claude/agents/council-orchestrator.md").exists()
