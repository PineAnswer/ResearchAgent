from research_agent.application.candidate_ranking import rank_candidates


def _candidate(paper_id: str, title: str, **extra):
    return {
        "paper_id": paper_id,
        "title": title,
        "abstract": "retrieval benchmark evaluation method",
        "source": "OpenAlex",
        "sources": ["OpenAlex"],
        "year": 2024,
        **extra,
    }


def test_openalex_only_high_impact_is_not_penalized_for_missing_channels() -> None:
    [paper] = rank_candidates(
        [
            _candidate(
                "P1",
                "Retrieval benchmark",
                citation_counts={"OpenAlex": 500},
                citation_percentiles={"OpenAlex": 0.95},
                recent_citation_velocities={"OpenAlex": 60},
            )
        ],
        ["retrieval benchmark"],
    )

    assert paper["impact_score"] > 90
    assert 0 < paper["impact_confidence"] < 70
    assert any("重加权" in item for item in paper["impact_explanation"])


def test_many_channels_do_not_beat_a_more_influential_single_channel_paper() -> None:
    ranked = rank_candidates(
        [
            _candidate(
                "HIGH",
                "High impact retrieval",
                citation_percentiles={"OpenAlex": 0.96},
            ),
            _candidate(
                "LOW",
                "Low impact retrieval",
                source="OpenAlex + Crossref + Semantic Scholar",
                sources=["OpenAlex", "Crossref", "Semantic Scholar"],
                citation_percentiles={
                    "OpenAlex": 0.30,
                    "Semantic Scholar": 0.32,
                    "Crossref": 0.28,
                },
            ),
        ],
        ["retrieval"],
    )

    assert [item["paper_id"] for item in ranked] == ["HIGH", "LOW"]
    assert ranked[0]["impact_score"] == 96


def test_missing_impact_and_authority_reweight_to_available_components() -> None:
    [paper] = rank_candidates(
        [_candidate("P1", "Exact retrieval benchmark")],
        ["exact retrieval benchmark"],
    )

    assert paper["impact_score"] is None
    assert paper["authority_score"] is None
    assert 0 <= paper["composite_score"] <= 100
    assert paper["composite_score"] > 80


def test_invalid_extreme_metrics_are_clamped_or_treated_as_missing() -> None:
    [paper] = rank_candidates(
        [
            _candidate(
                "P1",
                "Retrieval",
                citation_counts={"OpenAlex": -50},
                citation_percentiles={
                    "OpenAlex": 140,
                    "Semantic Scholar": float("nan"),
                },
                recent_citation_velocities={"OpenAlex": float("inf")},
            )
        ],
        ["retrieval"],
    )

    assert paper["impact_score"] == 100
    assert 0 <= paper["impact_confidence"] <= 100


def test_retracted_candidate_keeps_impact_but_is_demoted() -> None:
    ranked = rank_candidates(
        [
            _candidate(
                "RETRACTED",
                "Retrieval benchmark",
                citation_percentiles={"OpenAlex": 0.99},
                is_retracted=True,
            ),
            _candidate(
                "SAFE",
                "Retrieval benchmark study",
                citation_percentiles={"OpenAlex": 0.60},
            ),
        ],
        ["retrieval benchmark"],
    )

    assert ranked[0]["paper_id"] == "SAFE"
    retracted = ranked[1]
    assert retracted["impact_score"] == 99
    assert retracted["composite_score"] == 5


def test_agent_decision_groups_before_score() -> None:
    ranked = rank_candidates(
        [
            _candidate(
                "UNCERTAIN",
                "Retrieval benchmark",
                citation_percentiles={"OpenAlex": 0.99},
                agent_decision="uncertain",
            ),
            _candidate(
                "INCLUDE",
                "Retrieval method",
                citation_percentiles={"OpenAlex": 0.40},
                agent_decision="include",
            ),
        ],
        ["retrieval benchmark"],
    )

    assert [item["paper_id"] for item in ranked] == ["INCLUDE", "UNCERTAIN"]


def test_empty_candidates_and_empty_query_are_stable() -> None:
    assert rank_candidates([], ["query"]) == []
    first = rank_candidates([_candidate("P1", "Paper")], [""])
    second = rank_candidates([_candidate("P1", "Paper")], [""])
    assert first == second
