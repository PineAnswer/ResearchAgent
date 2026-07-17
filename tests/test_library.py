import pytest

from research_agent.application.library_service import LibraryService
from research_agent.infrastructure.sqlite_repository import SqliteResearchRepository


def test_library_deduplicates_doi_and_keeps_project_status_separate(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    service = LibraryService(repository)
    first_project = repository.create_project("first", "question one")
    second_project = repository.create_project("second", "question two")

    first = service.add_project_paper(
        first_project.project_id,
        {
            "paper_id": "W123",
            "title": "A useful paper",
            "authors": ["Ada Author"],
            "year": 2025,
            "doi": "https://doi.org/10.1000/Example",
            "source": "OpenAlex",
        },
        status="included",
    )
    second = service.add_project_paper(
        second_project.project_id,
        {
            "title": "A useful paper",
            "doi": "doi:10.1000/example",
            "abstract": "A richer abstract.",
            "source": "Crossref",
        },
        status="excluded",
    )

    assert first["paper"]["library_id"] == second["paper"]["library_id"]
    papers = service.list_papers()
    assert len(papers) == 1
    assert papers[0]["abstract"] == "A richer abstract."
    assert papers[0]["project_count"] == 2
    assert papers[0]["project_statuses"] == ["excluded", "included"]


def test_project_sync_indexes_candidates_but_only_saves_included_papers(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    service = LibraryService(repository)
    project = repository.create_project("topic", "question")
    repository.save_artifact(
        project.project_id,
        "CandidateSetSnapshot",
        {
            "candidates": [
                {"paper_id": "W1", "title": "Included", "source": "OpenAlex"},
                {"paper_id": "W2", "title": "Candidate", "source": "OpenAlex"},
            ],
            "agent_included_paper_ids": ["W1"],
            "agent_uncertain_paper_ids": ["W2"],
        },
    )
    repository.save_artifact(
        project.project_id,
        "ScreeningDecision",
        {
            "included_paper_ids": ["W1"],
            "excluded_paper_ids": ["W2"],
            "reasons": ["manual review"],
        },
    )

    linked = service.sync_project(project.project_id)

    assert len(linked) == 2
    assert {item["relation"]["status"] for item in linked} == {"included", "excluded"}
    assert [paper["title"] for paper in service.list_papers()] == ["Included"]


def test_agent_recommendation_does_not_auto_save_before_human_screening(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    service = LibraryService(repository)
    project = repository.create_project("topic", "question")
    repository.save_artifact(
        project.project_id,
        "CandidateSetSnapshot",
        {
            "candidates": [
                {"paper_id": "W1", "title": "Recommended", "source": "OpenAlex"},
            ],
            "agent_included_paper_ids": ["W1"],
        },
    )

    linked = service.sync_project(project.project_id)

    assert linked[0]["relation"]["status"] == "included"
    assert linked[0]["paper"]["saved"] is False
    assert service.list_papers() == []


def test_library_import_export_supports_bibtex_and_ris(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    service = LibraryService(repository)

    imported = service.import_records(
        """@article{demo,
  title = {Evidence Systems},
  author = {Ada Author and Bo Writer},
  year = {2024},
  doi = {10.1000/demo}
}
""",
        "bibtex",
        ["evidence"],
    )

    assert len(imported) == 1
    assert imported[0].tags == ["evidence"]
    ris = service.export_records("ris")
    assert "TI  - Evidence Systems" in ris
    assert "DO  - 10.1000/demo" in ris

    reimported = service.import_records(ris, "ris")
    assert reimported[0].library_id == imported[0].library_id
    assert len(service.list_papers()) == 1

    other = service.upsert_paper({"title": "Other paper", "year": 2022})
    selected_export = service.export_records(
        "bibtex", library_ids=[other.library_id]
    )
    assert "Other paper" in selected_export
    assert "Evidence Systems" not in selected_export


def test_library_collections_smart_views_and_bulk_actions(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    service = LibraryService(repository)
    first = service.upsert_paper({"title": "First paper", "year": 2024})
    second = service.upsert_paper({"title": "Second paper", "year": 2025})
    collection = service.create_collection("Thesis")

    service.bulk_update(
        [first.library_id, second.library_id],
        "add_collection",
        collection.collection_id,
    )
    service.bulk_update([first.library_id], "star")
    service.bulk_update([first.library_id], "add_tags", ["method", "important"])
    service.bulk_update([second.library_id], "archive")

    assert [item["title"] for item in service.list_papers(view="starred")] == [
        "First paper"
    ]
    assert service.list_papers(collection_id=collection.collection_id)[0][
        "collection_ids"
    ] == [collection.collection_id]
    assert service.list_papers(view="trash")[0]["title"] == "Second paper"
    assert service.library_overview()["counts"]["trash"] == 1

    service.bulk_update([second.library_id], "restore")
    assert service.library_overview()["counts"]["all"] == 2


def test_library_collections_form_a_three_level_acyclic_tree(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    service = LibraryService(repository)
    root = service.create_collection("Thesis")
    chapter = service.create_collection("Chapter 1", root.collection_id)
    topic = service.create_collection("Methods", chapter.collection_id)

    with pytest.raises(ValueError, match="at most three levels"):
        service.create_collection("Too deep", topic.collection_id)

    with pytest.raises(ValueError, match="below its descendant"):
        service.update_collection(
            root.collection_id,
            name=root.name,
            parent_id=topic.collection_id,
        )

    overview = service.library_overview()
    parents = {
        item["name"]: item["parent_id"] for item in overview["collections"]
    }
    assert parents == {
        "Chapter 1": root.collection_id,
        "Methods": chapter.collection_id,
        "Thesis": None,
    }


def test_library_notes_attachments_merge_and_comparison(tmp_path) -> None:
    repository = SqliteResearchRepository(tmp_path / "test.db")
    service = LibraryService(repository)
    project = repository.create_project("evidence", "What works?")
    primary = service.upsert_paper(
        {
            "paper_id": "W1",
            "title": "Evidence System Evaluation",
            "authors": ["Ada"],
            "abstract": "The evaluation improves traceability.",
        }
    )
    duplicate = service.upsert_paper(
        {
            "paper_id": "W2",
            "title": "Evidence-System Evaluation: A Study",
            "authors": ["Ada"],
        }
    )
    other = service.upsert_paper(
        {"paper_id": "W3", "title": "Baseline Study", "abstract": "A baseline."}
    )
    note = service.add_note(duplicate.library_id, "Keep the robustness result.")
    attachment = service.add_attachment(
        duplicate.library_id,
        name="paper.pdf",
        url="https://example.test/paper.pdf",
    )
    service.add_project_paper(
        project.project_id,
        primary.model_dump(mode="json"),
        status="included",
    )
    repository.save_artifact(
        project.project_id,
        "PaperCard",
        {
            "paper_id": "W1",
            "title": primary.title,
            "research_question": "What works?",
            "methods": ["controlled evaluation"],
            "datasets": ["benchmark"],
            "findings": [
                {
                    "evidence_id": "E1",
                    "paper_id": "W1",
                    "claim": "Traceability improved.",
                    "quote": "Measured improvement.",
                }
            ],
            "limitations": ["single benchmark"],
        },
    )

    merged = service.merge_papers(primary.library_id, duplicate.library_id)
    detail = service.get_paper(merged.library_id)

    assert detail["notes"][0]["note_id"] == note.note_id
    assert detail["attachments"][0]["attachment_id"] == attachment.attachment_id
    comparison = service.compare_papers([primary.library_id, other.library_id])
    assert comparison["rows"][0]["methods"] == ["controlled evaluation"]
    answer = service.answer_library_question(
        [primary.library_id, other.library_id],
        "Which study mentions traceability?",
    )
    assert merged.title in answer["answer"]
