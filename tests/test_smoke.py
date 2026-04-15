def test_project_structure():
    """Confirms the project skeleton was created correctly."""
    import os
    assert os.path.exists("src/config.py")
    assert os.path.exists("src/data_collection/database_manager.py")
    assert os.path.exists("src/council/council_orchestrator.py")
    assert os.path.exists(".claude/skills/prompt-optimizer/SKILL.md")
    assert os.path.exists(".claude/skills/multi-agent-patterns/SKILL.md")
    assert os.path.exists(".claude/agents/council-orchestrator.md")
