import asyncio

import httpx
import pytest

from research_agent.api.app import create_app
from research_agent.domain.models import ResearchNote
from research_agent.infrastructure.config import Settings
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository


def test_research_library_similarity_full_text_and_relations(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "agent.db")
    first, first_project = repository.create_conversation(
        "AIOps anomaly detection",
        "How can small language models detect anomalies in operations logs?",
    )
    second, second_project = repository.create_conversation(
        "AIOps log anomaly detection",
        "How can compact language models identify anomalies in operations logs?",
    )
    _, unrelated_project = repository.create_conversation(
        "Visual geolocation",
        "Which benchmark measures visual geographic localization?",
    )
    repository.save_artifact(
        first_project.project_id,
        "SynthesisReport",
        {"conclusion": "可观测性驱动蒸馏能够降低日志异常检测成本"},
    )
    repository.save_research_note(
        ResearchNote(
            note_id="note-cross-project",
            project_id=second_project.project_id,
            content="跨域迁移仍然需要人工校准告警阈值",
        )
    )

    similar = repository.find_similar_research(
        first_project.project_id,
        threshold=0.2,
    )[0]["matches"]
    assert similar[0]["project_id"] == second_project.project_id
    assert unrelated_project.project_id not in {item["project_id"] for item in similar}

    artifact_results = repository.search_research_library("可观测性驱动蒸馏")
    assert artifact_results[0]["project_id"] == first_project.project_id
    assert artifact_results[0]["matches"][0]["source"] == "artifact"
    note_results = repository.search_research_library("人工校准告警阈值")
    assert note_results[0]["project_id"] == second_project.project_id
    assert note_results[0]["matches"][0]["source"] == "note"

    relation = repository.create_research_relation(
        first_project.project_id,
        second_project.project_id,
        "沿用异常定义并缩小模型规模",
    )
    assert relation.parent_project_id == first_project.project_id
    assert relation.child_project_id == second_project.project_id
    assert relation.note == "沿用异常定义并缩小模型规模"
    assert repository.list_research_relations(second_project.project_id)[
        0
    ].relation_id == relation.relation_id
    with pytest.raises(ValueError, match="research_relation_cycle"):
        repository.create_research_relation(
            second_project.project_id,
            first_project.project_id,
        )
    repository.delete_research_relation(relation.relation_id)
    assert repository.list_research_relations() == []


def test_research_library_api_exposes_search_similarity_and_relations(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    filesystem_root = tmp_path / "filesystem"
    filesystem_root.mkdir()
    app = create_app(
        Settings(
            model="openai:gpt-4.1-mini",
            data_dir=tmp_path,
            database_path=tmp_path / "agent.db",
            filesystem_root=filesystem_root,
            enable_fallback=True,
        )
    )
    first, first_project = app.state.supervisor.service.create_conversation(
        "AIOps anomaly detection",
        "Detect anomalies in operations logs",
    )
    _, second_project = app.state.supervisor.service.create_conversation(
        "AIOps log anomaly detection",
        "Identify anomalies in operations logs",
    )

    async def exercise_api():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            search = await client.get(
                "/api/research-library/search",
                params={"q": "operations logs"},
            )
            similar = await client.get(
                "/api/research-library/similarities",
                params={"project_id": first_project.project_id, "threshold": 0.1},
            )
            created = await client.post(
                "/api/research-relations",
                json={
                    "parent_project_id": first_project.project_id,
                    "child_project_id": second_project.project_id,
                    "note": "沿用异常定义并缩小模型规模",
                },
            )
            listing = await client.get("/api/research-relations")
            deleted = await client.delete(
                f"/api/research-relations/{created.json()['data']['relation_id']}"
            )
            return search, similar, created, listing, deleted

    search, similar, created, listing, deleted = asyncio.run(exercise_api())
    assert search.status_code == 200
    assert search.json()["data"][0]["project_id"] == first_project.project_id
    assert similar.status_code == 200
    assert similar.json()["data"][0]["matches"][0]["project_id"] == second_project.project_id
    assert created.status_code == 200
    assert listing.json()["data"][0]["parent_title"] == first.title
    assert deleted.status_code == 200
