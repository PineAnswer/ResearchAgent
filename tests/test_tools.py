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
