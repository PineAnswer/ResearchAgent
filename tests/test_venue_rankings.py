from __future__ import annotations

import json
from email.message import Message
from pathlib import Path

import research_agent.tools.literature_tools as literature_module

from research_agent.api.schemas import CreateConversationRequest
from research_agent.infrastructure.venue_rankings import VenueRankingIndex
from research_agent.tools.literature_tools import build_literature_tools


class FakeJsonResponse:
    def __init__(self, payload: dict):
        self.payload = payload
        self.headers = Message()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


def test_seed_covers_requested_venue_sets_and_fast_alias_lookup(tmp_path: Path) -> None:
    index = VenueRankingIndex(tmp_path / "rankings.db")

    assert index.stats() == {
        "venues": 330,
        "ccf_a": 95,
        "q1": 98,
        "q2": 63,
        "nature_portfolio": 90,
    }
    cvpr = index.lookup(
        "Proceedings of the IEEE/CVF Computer Vision and Pattern Recognition Conference",
        "conference",
    )
    assert cvpr is not None
    assert cvpr["acronym"] == "CVPR"
    assert cvpr["ccf_rank"] == "A"
    assert cvpr["ccf_year"] == 2026

    tpami = index.lookup("TPAMI", "journal")
    assert tpami is not None
    assert tpami["sci_quartile"] == "Q1"
    assert tpami["impact_factor"] == 18.6
    assert tpami["impact_factor_year"] == 2024

    nature = index.lookup("Nature Machine Intelligence", "journal")
    assert nature is not None
    assert nature["nature_portfolio"] is True
    assert nature["impact_factor"] == 29.8


def test_generic_venue_names_do_not_match_longer_high_quality_titles(
    tmp_path: Path,
) -> None:
    index = VenueRankingIndex(tmp_path / "rankings.db")

    assert index.lookup("Information", "journal") is None
    assert index.lookup("Electronics", "journal") is None


def test_unsupported_source_type_is_normalized_without_dropping_candidate(
    tmp_path: Path,
) -> None:
    index = VenueRankingIndex(tmp_path / "rankings.db")

    candidate = index.enrich_candidate(
        {
            "paper_id": "W-repository",
            "title": "Repository copy",
            "source": "OpenAlex",
            "venue": "Unranked Repository",
            "venue_type": "repository",
        }
    )

    assert candidate["venue_type"] is None
    assert candidate["venue"] == "Unranked Repository"
    assert candidate["nature_portfolio"] is False


def test_openalex_hard_filters_year_and_quality_then_enriches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    requests = []
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "title": "Recent top venue paper",
                "authorships": [],
                "publication_year": 2025,
                "doi": "https://doi.org/10.1/recent",
                "primary_location": {
                    "source": {
                        "display_name": (
                            "IEEE Transactions on Pattern Analysis and Machine Intelligence"
                        ),
                        "type": "journal",
                    }
                },
                "best_oa_location": {},
                "abstract_inverted_index": {},
            },
            {
                "id": "https://openalex.org/W2",
                "title": "Old paper",
                "authorships": [],
                "publication_year": 2021,
                "doi": "https://doi.org/10.1/old",
                "primary_location": {
                    "source": {
                        "display_name": (
                            "IEEE Transactions on Pattern Analysis and Machine Intelligence"
                        ),
                        "type": "journal",
                    }
                },
                "best_oa_location": {},
                "abstract_inverted_index": {},
            },
            {
                "id": "https://openalex.org/W3",
                "title": "Recent unranked paper",
                "authorships": [],
                "publication_year": 2025,
                "doi": "https://doi.org/10.1/unranked",
                "primary_location": {
                    "source": {"display_name": "Unknown Venue", "type": "journal"}
                },
                "best_oa_location": {},
                "abstract_inverted_index": {},
            },
        ]
    }

    def fake_urlopen(request, timeout):
        requests.append(request)
        return FakeJsonResponse(payload)

    monkeypatch.setattr(literature_module, "urlopen", fake_urlopen)
    index = VenueRankingIndex(tmp_path / "rankings.db")
    tools = {
        item.name: item
        for item in build_literature_tools(tmp_path, venue_index=index)
    }
    result = json.loads(
        tools["search_openalex"].invoke(
            {
                "query": "computer vision",
                "limit": 10,
                "year_from": 2024,
                "year_to": 2026,
                "quality_venues_only": True,
            }
        )
    )

    assert len(result) == 1
    assert result[0]["paper_id"].endswith("/W1")
    assert result[0]["sci_quartile"] == "Q1"
    assert result[0]["ccf_rank"] == "A"
    assert "IF 18.6" in result[0]["venue_rating_explanation"]
    assert "from_publication_date%3A2024-01-01" in requests[0].full_url
    assert "to_publication_date%3A2026-12-31" in requests[0].full_url


def test_search_request_defaults_and_year_validation() -> None:
    request = CreateConversationRequest(topic="topic", research_question="question")
    assert request.year_from == 2024
    assert request.year_to == 2026
    assert request.quality_venues_only is False
    assert request.prefer_library is False

    try:
        CreateConversationRequest(
            topic="topic",
            research_question="question",
            year_from=2026,
            year_to=2024,
        )
    except ValueError as exc:
        assert "year_from cannot be greater" in str(exc)
    else:
        raise AssertionError("invalid year range was accepted")


def test_frontend_exposes_year_quality_and_venue_rating_controls() -> None:
    frontend = Path("src/research_agent/api/frontend")
    html = (frontend / "index.html").read_text(encoding="utf-8")
    script = (frontend / "app.js").read_text(encoding="utf-8")

    assert 'id="initialYearFrom"' in html
    assert 'id="initialYearTo"' in html
    assert 'id="initialQualityVenuesOnly"' in html
    assert 'id="initialPreferLibrary"' in html
    assert "prefer_library: elements.initialPreferLibrary.checked" in script
    assert "仅 CCF-A、一区和 Nature 子刊" in html
    assert "candidate.venue_rating_explanation" in script
    assert "candidate.impact_factor" in script
