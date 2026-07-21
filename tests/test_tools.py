import json
from io import BytesIO
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import research_agent.tools.literature_tools as literature_module
from pypdf import PdfWriter
from research_agent.tools.literature_tools import build_literature_tools


class FakeJsonResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeBinaryResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.payload


def test_pdf_tool_returns_error_for_paths_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_pdf = tmp_path / "outside.pdf"
    outside_pdf.write_bytes(b"not a real pdf")

    tools = {item.name: item for item in build_literature_tools(workspace)}
    result = json.loads(
        tools["extract_pdf_text"].invoke(
            {"pdf_path": str(outside_pdf), "max_pages": 1}
        )
    )

    assert result["available"] is False
    assert result["error_code"] == "path_outside_workspace"


def test_pdf_tool_maps_virtual_paths_to_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "papers").mkdir(parents=True)
    tools = {item.name: item for item in build_literature_tools(workspace)}

    result = json.loads(
        tools["extract_pdf_text"].invoke(
            {"pdf_path": "/papers/missing.pdf", "max_pages": 1}
        )
    )

    assert result["available"] is False
    assert result["error_code"] == "pdf_not_found"
    assert "must stay inside" not in result["error"]


def test_openalex_request_uses_configured_api_key(tmp_path: Path, monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeJsonResponse({"results": []})

    monkeypatch.setattr(literature_module, "urlopen", fake_urlopen)
    tools = {
        item.name: item
        for item in build_literature_tools(
            tmp_path,
            openalex_api_key="test-openalex-key",
            contact_email="researcher@example.com",
        )
    }

    result = json.loads(tools["search_openalex"].invoke({"query": "AIOps", "limit": 5}))

    assert result == []
    assert len(requests) == 1
    request = requests[0][0]
    assert "api_key=test-openalex-key" in request.full_url
    assert "mailto=researcher%40example.com" in request.full_url
    assert "mailto:researcher@example.com" in request.headers["User-agent"]


def test_openalex_429_retries_then_returns_recoverable_error(
    tmp_path: Path, monkeypatch
) -> None:
    calls = 0
    sleeps = []
    headers = Message()
    headers["Retry-After"] = "0"
    headers["X-RateLimit-Remaining"] = "0"

    def rate_limited(_request, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(
            "https://api.openalex.org/works",
            429,
            "Too Many Requests",
            headers,
            None,
        )

    monkeypatch.setattr(literature_module, "urlopen", rate_limited)
    monkeypatch.setattr(literature_module.time, "sleep", sleeps.append)
    tools = {
        item.name: item
        for item in build_literature_tools(
            tmp_path,
            max_retries=2,
            backoff_seconds=0.01,
            max_retry_wait_seconds=1,
        )
    }

    result = json.loads(tools["search_openalex"].invoke({"query": "AIOps", "limit": 5}))

    assert calls == 3
    assert sleeps == [0.0, 0.0]
    assert result["ok"] is False
    assert result["error_code"] == "rate_limited"
    assert result["attempts"] == 3
    assert result["rate_limit"]["X-RateLimit-Remaining"] == "0"


def test_openalex_retry_can_recover(tmp_path: Path, monkeypatch) -> None:
    headers = Message()
    headers["Retry-After"] = "0"
    responses = [
        HTTPError(
            "https://api.openalex.org/works",
            429,
            "Too Many Requests",
            headers,
            None,
        ),
        FakeJsonResponse({"results": []}),
    ]

    def fake_urlopen(_request, timeout):
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(literature_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(literature_module.time, "sleep", lambda _seconds: None)
    tools = {
        item.name: item
        for item in build_literature_tools(tmp_path, max_retries=1)
    }

    result = json.loads(tools["search_openalex"].invoke({"query": "AIOps", "limit": 5}))

    assert result == []
    assert responses == []


def test_openalex_returns_abstract_and_open_pdf_url(tmp_path: Path, monkeypatch) -> None:
    payload = {
        "results": [
            {
                "id": "https://openalex.org/W1",
                "title": "Paper",
                "authorships": [],
                "publication_year": 2025,
                "doi": "https://doi.org/10.1/example",
                "primary_location": {},
                "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
                "abstract_inverted_index": {"Open": [0], "evidence": [1]},
            }
        ]
    }
    monkeypatch.setattr(
        literature_module,
        "urlopen",
        lambda _request, timeout: FakeJsonResponse(payload),
    )
    tools = {item.name: item for item in build_literature_tools(tmp_path)}

    result = json.loads(tools["search_openalex"].invoke({"query": "AIOps", "limit": 5}))

    assert result[0]["abstract"] == "Open evidence"
    assert result[0]["url"] == "https://example.org/paper.pdf"


def test_fetch_paper_text_downloads_arxiv_pdf(tmp_path: Path, monkeypatch) -> None:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    monkeypatch.setattr(
        literature_module,
        "urlopen",
        lambda _request, timeout: FakeBinaryResponse(buffer.getvalue()),
    )
    tools = {item.name: item for item in build_literature_tools(tmp_path)}

    result = json.loads(
        tools["fetch_paper_text"].invoke(
            {
                "paper_id": "https://openalex.org/W1",
                "doi": "10.48550/arxiv.2406.11213",
                "url": "http://arxiv.org/abs/2406.11213",
                "max_pages": 1,
            }
        )
    )

    assert result["available"] is True
    assert result["local_pdf_path"].startswith("/papers/")
    assert len(result["pages"]) == 1


def test_fetch_paper_text_accepts_trusted_arxiv_pdf_path_without_doi(
    tmp_path: Path, monkeypatch
) -> None:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    requested_urls = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        return FakeBinaryResponse(buffer.getvalue())

    monkeypatch.setattr(literature_module, "urlopen", fake_urlopen)
    tools = {item.name: item for item in build_literature_tools(tmp_path)}

    result = json.loads(
        tools["fetch_paper_text"].invoke(
            {
                "paper_id": "W7140346934",
                "doi": "",
                "url": "https://arxiv.org/pdf/2603.21522",
                "max_pages": 1,
            }
        )
    )

    assert result["available"] is True
    assert requested_urls == ["https://arxiv.org/pdf/2603.21522.pdf"]


def test_fetch_paper_text_resolves_doi_linked_arxiv_preprint(
    tmp_path: Path, monkeypatch
) -> None:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    requested_urls = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if request.full_url.startswith("https://api.openalex.org/"):
            return FakeJsonResponse({"best_oa_location": None, "locations": []})
        if request.full_url.startswith("https://api.semanticscholar.org/"):
            return FakeJsonResponse(
                {
                    "openAccessPdf": {"url": "https://arxiv.org/pdf/2502.16601"},
                    "externalIds": {"ArXiv": "2502.16601"},
                }
            )
        if request.full_url == "https://arxiv.org/pdf/2502.16601":
            return FakeBinaryResponse(buffer.getvalue())
        raise AssertionError(request.full_url)

    monkeypatch.setattr(literature_module, "urlopen", fake_urlopen)
    tools = {item.name: item for item in build_literature_tools(tmp_path)}

    result = json.loads(
        tools["fetch_paper_text"].invoke(
            {
                "paper_id": "10.1109/TPAMI.2025.3629287",
                "doi": "10.1109/TPAMI.2025.3629287",
                "url": "https://doi.org/10.1109/TPAMI.2025.3629287",
                "max_pages": 1,
            }
        )
    )

    assert result["available"] is True
    assert result["source_url"] == "https://arxiv.org/pdf/2502.16601"
    assert any("api.semanticscholar.org" in item for item in requested_urls)


def test_fetch_paper_text_uses_mdpi_resource_fallback_after_403(
    tmp_path: Path, monkeypatch
) -> None:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    direct_url = "https://www.mdpi.com/2078-2489/16/2/87/pdf?version=2"
    requested_urls = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if request.full_url.startswith("https://api.openalex.org/"):
            location = {
                "pdf_url": direct_url,
                "source": {"display_name": "Information"},
            }
            return FakeJsonResponse(
                {"best_oa_location": location, "locations": [location]}
            )
        if request.full_url == direct_url:
            raise HTTPError(direct_url, 403, "Forbidden", Message(), None)
        if request.full_url.startswith("https://mdpi-res.com/d_attachment/information/"):
            return FakeBinaryResponse(buffer.getvalue())
        raise AssertionError(request.full_url)

    monkeypatch.setattr(literature_module, "urlopen", fake_urlopen)
    tools = {item.name: item for item in build_literature_tools(tmp_path)}

    result = json.loads(
        tools["fetch_paper_text"].invoke(
            {
                "paper_id": "W4406756422",
                "doi": "10.3390/info16020087",
                "url": direct_url,
                "max_pages": 1,
            }
        )
    )

    assert result["available"] is True
    assert result["source_url"].startswith(
        "https://mdpi-res.com/d_attachment/information/"
    )
    assert direct_url in requested_urls


def test_fetch_paper_text_rejects_guessed_arxiv_url_for_non_arxiv_doi(
    tmp_path: Path, monkeypatch
) -> None:
    requested_urls = []

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        return FakeJsonResponse({"best_oa_location": None, "locations": []})

    monkeypatch.setattr(literature_module, "urlopen", fake_urlopen)
    tools = {item.name: item for item in build_literature_tools(tmp_path)}

    result = json.loads(
        tools["fetch_paper_text"].invoke(
            {
                "paper_id": "https://openalex.org/W123",
                "doi": "10.1145/example",
                "url": "https://arxiv.org/pdf/2501.00000.pdf",
            }
        )
    )

    assert result["available"] is False
    assert all("arxiv.org" not in item for item in requested_urls)
    assert requested_urls[0].startswith("https://api.openalex.org/works/W123")


def test_fetch_paper_text_reuses_cached_pdf_without_network(
    tmp_path: Path, monkeypatch
) -> None:
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(buffer)
    calls = 0

    def fake_urlopen(_request, timeout):
        nonlocal calls
        calls += 1
        return FakeBinaryResponse(buffer.getvalue())

    monkeypatch.setattr(literature_module, "urlopen", fake_urlopen)
    tools = {item.name: item for item in build_literature_tools(tmp_path)}
    inputs = {
        "paper_id": "https://openalex.org/W1",
        "doi": "10.48550/arxiv.2406.11213",
        "url": "https://arxiv.org/abs/2406.11213",
        "max_pages": 1,
    }

    first = json.loads(tools["fetch_paper_text"].invoke(inputs))
    second = json.loads(tools["fetch_paper_text"].invoke(inputs))

    assert first["available"] is True
    assert first["cached"] is False
    assert second["available"] is True
    assert second["cached"] is True
    assert calls == 1


def test_multi_source_search_splits_queries_and_merges_duplicate_papers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    atom = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://arxiv.org/abs/2501.00001</id>
        <published>2025-01-02T00:00:00Z</published>
        <title>Geo localization benchmark</title>
        <summary>A benchmark for image geolocation.</summary>
        <author><name>Arxiv Author</name></author>
        <link href="https://arxiv.org/pdf/2501.00001" type="application/pdf"/>
      </entry>
    </feed>"""
    requested_urls: list[str] = []

    def fake_urlopen(request, timeout):
        del timeout
        url = request.full_url
        requested_urls.append(url)
        if "api.openalex.org" in url:
            return FakeJsonResponse(
                {
                    "results": [
                        {
                            "id": "https://openalex.org/W-GEO",
                            "title": "Unified Geo Localization",
                            "authorships": [],
                            "publication_year": 2025,
                            "doi": "https://doi.org/10.1000/geo",
                            "primary_location": {},
                            "best_oa_location": {},
                            "abstract_inverted_index": {"image": [0], "geolocation": [1]},
                        }
                    ]
                }
            )
        if "api.crossref.org" in url:
            return FakeJsonResponse(
                {
                    "message": {
                        "items": [
                            {
                                "DOI": "10.1000/geo",
                                "title": ["Unified Geo Localization"],
                                "author": [],
                                "issued": {"date-parts": [[2025]]},
                                "type": "journal-article",
                                "container-title": ["Geo Journal"],
                                "URL": "https://doi.org/10.1000/geo",
                            }
                        ]
                    }
                }
            )
        if "api.semanticscholar.org" in url:
            return FakeJsonResponse(
                {
                    "data": [
                        {
                            "paperId": "S2-GEO",
                            "title": "Unified Geo Localization",
                            "authors": [],
                            "year": 2025,
                            "abstract": "Image geolocation with retrieval.",
                            "externalIds": {"DOI": "10.1000/geo"},
                            "url": "https://example.test/geo",
                            "venue": "Geo Journal",
                            "publicationTypes": ["JournalArticle"],
                        }
                    ]
                }
            )
        if "export.arxiv.org" in url:
            return FakeBinaryResponse(atom)
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(literature_module, "urlopen", fake_urlopen)
    tools = {item.name: item for item in build_literature_tools(tmp_path)}

    result = json.loads(
        tools["search_multi_source"].invoke(
            {
                "queries": [
                    "image geolocation retrieval",
                    "geolocation benchmark evaluation",
                ],
                "limit_per_source": 3,
                "year_from": 2024,
                "year_to": 2026,
            }
        )
    )

    assert result["sources_attempted"] == [
        "OpenAlex",
        "Crossref",
        "Semantic Scholar",
        "arXiv",
    ]
    assert len(result["source_status"]) == 8
    assert len(requested_urls) == 8
    duplicate = next(
        item
        for item in result["candidates"]
        if str(item.get("doi")).endswith("10.1000/geo")
    )
    assert duplicate["sources"] == [
        "OpenAlex",
        "Crossref",
        "Semantic Scholar",
    ]
    assert duplicate["matched_queries"] == [
        "image geolocation retrieval",
        "geolocation benchmark evaluation",
    ]
    assert duplicate["source"] == "OpenAlex + Crossref + Semantic Scholar"
    assert duplicate["relevance_score"] > 0
