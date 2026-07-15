from research_agent.agent import build_research_agent
from research_agent.infrastructure.config import Settings


def test_agent_factory_builds_compiled_graph(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    workspace = tmp_path / "filesystem"
    workspace.mkdir()
    stale_skill_dir = workspace / "skills" / "paper-reading"
    stale_skill_dir.mkdir(parents=True)
    stale_skill_file = stale_skill_dir / "old-download-script.py"
    stale_skill_file.write_text("stale", encoding="utf-8")
    settings = Settings(
        model="openai:gpt-4.1-mini",
        data_dir=tmp_path,
        database_path=tmp_path / "agent.db",
        filesystem_root=workspace,
    )

    agent = build_research_agent(settings)

    assert hasattr(agent, "invoke")
    assert hasattr(agent, "stream")
    assert (workspace / "skills" / "research-protocol" / "SKILL.md").exists()
    assert not stale_skill_file.exists()
    assert (workspace / "papers").is_dir()
    assert (workspace / "memories" / "AGENTS.md").exists()
