from pathlib import Path

import pytest

from research_agent.agent import build_research_agent
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.workspace import (
    WorkspaceBootstrapper,
    validate_skill_content,
)


EXPECTED_SKILLS = {
    "research-protocol",
    "literature-search",
    "paper-reading",
    "research-synthesis",
    "evidence-review",
}


def test_workspace_bootstrapper_loads_every_skill_content(tmp_path) -> None:
    assets = WorkspaceBootstrapper(tmp_path / "filesystem").prepare()

    assert set(assets.skill_contents) == EXPECTED_SKILLS
    assert all(content.startswith("---\nname:") for content in assets.skill_contents.values())
    assert "禁止为了继续流程而跳过前置产物" in assets.skill_contents[
        "research-protocol"
    ]


def test_skill_frontmatter_must_match_its_directory() -> None:
    content = "---\nname: paper-reading\ndescription: test\n---\n"

    with pytest.raises(ValueError, match="does not match directory"):
        validate_skill_content(
            "literature-search",
            content,
            Path("literature-search/SKILL.md"),
        )


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
    for skill_name in EXPECTED_SKILLS:
        assert (workspace / "skills" / skill_name / "SKILL.md").exists()
    assert not stale_skill_file.exists()
    assert (workspace / "papers").is_dir()
    assert (workspace / "memories" / "AGENTS.md").exists()
