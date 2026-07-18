from research_agent.demo import run_offline_demo


def test_offline_demo_completes_the_current_writing_workflow(tmp_path) -> None:
    result = run_offline_demo(tmp_path / "demo.db")

    assert result["project"]["stage"] == "COMPLETED"
    assert result["premature_completion_blocked"] is True
    artifact_kinds = [artifact["kind"] for artifact in result["artifacts"]]
    assert artifact_kinds[-4:] == [
        "ReviewOutline",
        "SectionDraft",
        "NarrativeReview",
        "FactCheckReport",
    ]
