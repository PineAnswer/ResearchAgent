from __future__ import annotations

import json
import hashlib
import html
import ipaddress
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HttpRetryPolicy:
    max_retries: int = 3
    backoff_seconds: float = 1.0
    max_retry_wait_seconds: float = 30.0
    user_agent: str = "evidence-research-agent-demo/0.2"


class AcademicApiError(RuntimeError):
    """A recoverable academic API failure safe to return to an Agent."""

    def __init__(
        self,
        *,
        source: str,
        error_code: str,
        message: str,
        attempts: int,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        rate_limit: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.source = source
        self.error_code = error_code
        self.attempts = attempts
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.rate_limit = rate_limit or {}

    def to_payload(self) -> dict:
        return {
            "ok": False,
            "source": self.source,
            "error_code": self.error_code,
            "error": str(self),
            "status_code": self.status_code,
            "attempts": self.attempts,
            "retryable": self.retryable,
            "retry_after_seconds": self.retry_after_seconds,
            "rate_limit": self.rate_limit,
            "instruction": "保留已有搜索结果并继续生成 SearchReport，不要重复相同查询。",
        }


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


def _rate_limit_headers(headers) -> dict[str, str]:
    names = (
        "Retry-After",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Credits-Used",
        "X-RateLimit-Reset",
    )
    return {name: value for name in names if (value := headers.get(name)) is not None}


def _abstract_from_inverted_index(index: dict | None) -> str:
    if not index:
        return ""
    positioned = []
    for word, positions in index.items():
        positioned.extend((int(position), str(word)) for position in positions)
    return " ".join(word for _, word in sorted(positioned))


def _plain_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def _safe_public_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback or address.is_link_local)


def extract_pdf_pages(path: str | Path, max_pages: int = 30) -> list[dict[str, str | int]]:
    """Extract page-numbered text for both workspace tools and library ingestion."""
    from pypdf import PdfReader

    reader = PdfReader(Path(path))
    pages: list[dict[str, str | int]] = []
    for index, page in enumerate(
        reader.pages[: max(1, min(int(max_pages), 100))],
        start=1,
    ):
        pages.append({"page": index, "text": page.extract_text() or ""})
    return pages


def _arxiv_id(value: str) -> str:
    candidate = value.strip().removesuffix(".pdf")
    modern = re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", candidate)
    legacy = re.fullmatch(r"[a-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?", candidate, re.I)
    if not (modern or legacy):
        return ""
    number = candidate.split(".")[-1].split("v", maxsplit=1)[0]
    return "" if number and set(number) == {"0"} else candidate


def _paper_cache_key(paper_id: str, doi: str, url: str) -> str:
    if match := re.search(r"\bW\d+\b", paper_id, flags=re.IGNORECASE):
        return match.group(0).upper()
    normalized_doi = doi.removeprefix("https://doi.org/").strip().casefold()
    return normalized_doi or paper_id.strip().casefold() or url.strip().casefold()


def _get_json(url: str, *, source: str, policy: HttpRetryPolicy) -> dict:
    for attempt in range(policy.max_retries + 1):
        attempts = attempt + 1
        request = Request(url, headers={"User-Agent": policy.user_agent})
        try:
            with urlopen(
                request, timeout=20
            ) as response:  # noqa: S310 - fixed academic API hosts
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
            delay = (
                retry_after
                if retry_after is not None
                else policy.backoff_seconds * (2**attempt)
            )
            if (
                retryable
                and attempt < policy.max_retries
                and delay <= policy.max_retry_wait_seconds
            ):
                time.sleep(delay)
                continue
            error_code = "rate_limited" if exc.code == 429 else "http_error"
            raise AcademicApiError(
                source=source,
                error_code=error_code,
                message=f"{source} HTTP {exc.code}: {exc.reason}",
                attempts=attempts,
                retryable=retryable,
                status_code=exc.code,
                retry_after_seconds=retry_after,
                rate_limit=_rate_limit_headers(exc.headers),
            ) from exc
        except URLError as exc:
            delay = policy.backoff_seconds * (2**attempt)
            if attempt < policy.max_retries and delay <= policy.max_retry_wait_seconds:
                time.sleep(delay)
                continue
            raise AcademicApiError(
                source=source,
                error_code="network_error",
                message=f"{source} network error: {exc.reason}",
                attempts=attempts,
                retryable=True,
            ) from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AcademicApiError(
                source=source,
                error_code="invalid_response",
                message=f"{source} returned an invalid JSON response",
                attempts=attempts,
                retryable=False,
            ) from exc

    raise AssertionError("retry loop ended unexpectedly")


def build_literature_tools(
    workspace_root: str | Path | None = None,
    *,
    openalex_api_key: str | None = None,
    contact_email: str | None = None,
    venue_index: Any | None = None,
    max_retries: int = 3,
    backoff_seconds: float = 1.0,
    max_retry_wait_seconds: float = 30.0,
):
    from langchain_core.tools import tool

    allowed_root = Path(workspace_root).resolve() if workspace_root is not None else None
    user_agent = "evidence-research-agent-demo/0.2"
    if contact_email:
        user_agent += f" (mailto:{contact_email})"
    retry_policy = HttpRetryPolicy(
        max_retries=max(0, max_retries),
        backoff_seconds=max(0.0, backoff_seconds),
        max_retry_wait_seconds=max(0.0, max_retry_wait_seconds),
        user_agent=user_agent,
    )

    def resolve_workspace_path(value: str) -> Path:
        """Resolve host paths and Deep Agents virtual paths inside the workspace."""
        raw_path = Path(value)
        if allowed_root is None:
            return raw_path.resolve()
        if raw_path.drive:
            candidate = raw_path
        elif value.startswith(("/", "\\")):
            candidate = allowed_root.joinpath(*raw_path.parts[1:])
        else:
            candidate = allowed_root / raw_path
        return candidate.resolve()

    def recoverable_error(error: AcademicApiError) -> str:
        return json.dumps(error.to_payload(), ensure_ascii=False)

    def prepare_candidates(
        candidates: list[dict[str, Any]],
        *,
        limit: int,
        year_from: int | None,
        year_to: int | None,
        quality_venues_only: bool,
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        for candidate in candidates:
            year = candidate.get("year")
            if year_from is not None and (year is None or int(year) < year_from):
                continue
            if year_to is not None and (year is None or int(year) > year_to):
                continue
            enriched = (
                venue_index.enrich_candidate(candidate)
                if venue_index is not None
                else dict(candidate)
            )
            if quality_venues_only and (
                venue_index is None
                or not venue_index.qualifies_for_quality_filter(enriched)
            ):
                continue
            prepared.append(enriched)
            if len(prepared) >= limit:
                break
        return prepared

    @tool
    def search_openalex(
        query: str,
        limit: int = 5,
        year_from: int | None = None,
        year_to: int | None = None,
        quality_venues_only: bool = False,
    ) -> str:
        """Search OpenAlex with enforced year and venue-quality constraints."""
        limit = max(1, min(limit, 20))
        upstream_limit = min(50, max(limit, limit * 5 if quality_venues_only else limit))
        params = {
            "search": query,
            "per-page": upstream_limit,
            "select": (
                "id,title,authorships,publication_year,doi,primary_location,"
                "best_oa_location,abstract_inverted_index"
            ),
        }
        filters: list[str] = []
        if year_from is not None:
            filters.append(f"from_publication_date:{max(2000, year_from)}-01-01")
        if year_to is not None:
            filters.append(f"to_publication_date:{min(2026, year_to)}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        if openalex_api_key:
            params["api_key"] = openalex_api_key
        if contact_email:
            params["mailto"] = contact_email
        url = "https://api.openalex.org/works?" + urlencode(params)
        try:
            data = _get_json(url, source="OpenAlex", policy=retry_policy)
        except AcademicApiError as exc:
            return recoverable_error(exc)
        works = []
        for item in data.get("results", []):
            best_oa = item.get("best_oa_location") or {}
            primary = item.get("primary_location") or {}
            venue_source = primary.get("source") or best_oa.get("source") or {}
            works.append(
                {
                    "paper_id": item.get("id", ""),
                    "title": item.get("title", ""),
                    "authors": [
                        author.get("author", {}).get("display_name", "")
                        for author in item.get("authorships", [])
                    ],
                    "year": item.get("publication_year"),
                    "abstract": _abstract_from_inverted_index(
                        item.get("abstract_inverted_index")
                    ),
                    "doi": item.get("doi"),
                    "url": (
                        best_oa.get("pdf_url")
                        or best_oa.get("landing_page_url")
                        or primary.get("landing_page_url")
                    ),
                    "source": "OpenAlex",
                    "venue": venue_source.get("display_name", ""),
                    "venue_type": venue_source.get("type"),
                }
            )
        return json.dumps(
            prepare_candidates(
                works,
                limit=limit,
                year_from=year_from,
                year_to=year_to,
                quality_venues_only=quality_venues_only,
            ),
            ensure_ascii=False,
        )

    @tool
    def search_crossref(
        query: str,
        limit: int = 5,
        year_from: int | None = None,
        year_to: int | None = None,
        quality_venues_only: bool = False,
    ) -> str:
        """Search Crossref with enforced year and venue-quality constraints."""
        limit = max(1, min(limit, 20))
        upstream_limit = min(50, max(limit, limit * 5 if quality_venues_only else limit))
        params = {"query": query, "rows": upstream_limit}
        filters: list[str] = []
        if year_from is not None:
            filters.append(f"from-pub-date:{max(2000, year_from)}-01-01")
        if year_to is not None:
            filters.append(f"until-pub-date:{min(2026, year_to)}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        if contact_email:
            params["mailto"] = contact_email
        url = "https://api.crossref.org/works?" + urlencode(params)
        try:
            data = _get_json(url, source="Crossref", policy=retry_policy)
        except AcademicApiError as exc:
            return recoverable_error(exc)
        records = []
        for item in data.get("message", {}).get("items", []):
            title = (item.get("title") or [""])[0]
            date = (
                item.get("published-print")
                or item.get("published-online")
                or item.get("issued")
                or {}
            )
            date_parts = date.get("date-parts") or []
            year = (
                date_parts[0][0]
                if date_parts and isinstance(date_parts[0], list) and date_parts[0]
                else None
            )
            item_type = str(item.get("type") or "")
            venue_type = (
                "conference"
                if item_type in {"proceedings-article", "proceedings"}
                else "journal"
                if item_type in {"journal-article", "journal"}
                else None
            )
            records.append(
                {
                    "paper_id": item.get("DOI", ""),
                    "title": title,
                    "authors": [
                        " ".join(filter(None, [author.get("given"), author.get("family")]))
                        for author in item.get("author", [])
                    ],
                    "abstract": _plain_text(item.get("abstract")),
                    "doi": item.get("DOI"),
                    "url": item.get("URL"),
                    "source": "Crossref",
                    "year": year,
                    "venue": (item.get("container-title") or [""])[0],
                    "venue_type": venue_type,
                }
            )
        return json.dumps(
            prepare_candidates(
                records,
                limit=limit,
                year_from=year_from,
                year_to=year_to,
                quality_venues_only=quality_venues_only,
            ),
            ensure_ascii=False,
        )

    def openalex_pdf_urls(paper_id: str, doi: str) -> list[str]:
        identifier = paper_id.strip()
        if identifier.startswith("https://openalex.org/"):
            identifier = identifier.rsplit("/", maxsplit=1)[-1]
        if not identifier.startswith("https://openalex.org/") and doi:
            normalized = doi.removeprefix("https://doi.org/").strip()
            if not identifier.startswith("W"):
                identifier = f"https://doi.org/{normalized}"
        if not identifier:
            return []
        lookup = "https://api.openalex.org/works/" + quote(identifier, safe=":/")
        params = {"select": "best_oa_location,locations"}
        if openalex_api_key:
            params["api_key"] = openalex_api_key
        if contact_email:
            params["mailto"] = contact_email
        try:
            data = _get_json(
                lookup + "?" + urlencode(params),
                source="OpenAlex",
                policy=retry_policy,
            )
        except AcademicApiError:
            return []
        locations = [data.get("best_oa_location") or {}, *data.get("locations", [])]
        return [str(item.get("pdf_url")) for item in locations if item.get("pdf_url")]

    def direct_pdf_urls(doi: str, url: str) -> list[str]:
        values = []
        normalized_doi = doi.removeprefix("https://doi.org/").strip()
        trusted_arxiv_id = ""
        if normalized_doi.lower().startswith("10.48550/arxiv."):
            trusted_arxiv_id = _arxiv_id(
                normalized_doi[len("10.48550/arxiv.") :]
            )
        url_arxiv_id = ""
        if "arxiv.org/abs/" in url:
            url_arxiv_id = _arxiv_id(
                url.split("arxiv.org/abs/", maxsplit=1)[-1].split("?", maxsplit=1)[0]
            )
        elif "arxiv.org/pdf/" in url:
            url_arxiv_id = _arxiv_id(
                url.split("arxiv.org/pdf/", maxsplit=1)[-1].split("?", maxsplit=1)[0]
            )
        if trusted_arxiv_id:
            values.append(f"https://export.arxiv.org/pdf/{trusted_arxiv_id}")
            values.append(f"https://arxiv.org/pdf/{trusted_arxiv_id}.pdf")
        if (
            url_arxiv_id
            and trusted_arxiv_id
            and url_arxiv_id == trusted_arxiv_id
            and url.lower().split("?", maxsplit=1)[0].endswith(".pdf")
        ):
            values.append(url)
        elif "arxiv.org/" not in url.lower() and url.lower().split("?", 1)[0].endswith(
            ".pdf"
        ):
            values.append(url)
        return values

    @tool
    def fetch_paper_text(
        paper_id: str,
        doi: str = "",
        url: str = "",
        max_pages: int = 30,
    ) -> str:
        """Fetch an openly available paper PDF and return page-numbered text.

        The tool checks arXiv and OpenAlex open-access locations, saves a bounded
        PDF under /papers, and never treats a DOI landing page as a PDF.
        """
        if allowed_root is None:
            return json.dumps(
                {
                    "available": False,
                    "error_code": "workspace_unavailable",
                    "attempted_urls": [],
                },
                ensure_ascii=False,
            )
        papers_dir = allowed_root / "papers"
        papers_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(
            _paper_cache_key(paper_id, doi, url).encode()
        ).hexdigest()[:20]
        path = papers_dir / f"{digest}.pdf"
        if path.exists():
            try:
                pages = extract_pdf_pages(path, max_pages)
                return json.dumps(
                    {
                        "available": True,
                        "source_url": None,
                        "local_pdf_path": f"/papers/{path.name}",
                        "pages": pages,
                        "cached": True,
                    },
                    ensure_ascii=False,
                )
            except Exception:
                pass
        candidates = direct_pdf_urls(doi, url)
        if not candidates:
            candidates = openalex_pdf_urls(paper_id, doi)
        candidates = list(dict.fromkeys(item for item in candidates if _safe_public_url(item)))
        errors = []
        for candidate in candidates:
            try:
                request = Request(candidate, headers={"User-Agent": retry_policy.user_agent})
                with urlopen(request, timeout=30) as response:  # noqa: S310 - validated public URL
                    content = response.read(25 * 1024 * 1024 + 1)
                if len(content) > 25 * 1024 * 1024:
                    errors.append({"url": candidate, "error": "pdf_too_large"})
                    continue
                if not content.startswith(b"%PDF"):
                    errors.append({"url": candidate, "error": "response_is_not_pdf"})
                    continue
                path.write_bytes(content)
                pages = extract_pdf_pages(path, max_pages)
                return json.dumps(
                    {
                        "available": True,
                        "source_url": candidate,
                        "local_pdf_path": f"/papers/{path.name}",
                        "pages": pages,
                        "cached": False,
                    },
                    ensure_ascii=False,
                )
            except Exception as exc:  # Every source failure remains recoverable.
                errors.append({"url": candidate, "error": str(exc)})
        return json.dumps(
            {
                "available": False,
                "error_code": "open_full_text_unavailable",
                "attempted_urls": candidates,
                "errors": errors,
                "hint": "Use a non-empty abstract as abstract-level evidence; otherwise leave findings empty.",
            },
            ensure_ascii=False,
        )

    @tool
    def extract_pdf_text(pdf_path: str, max_pages: int = 30) -> str:
        """Extract page-numbered text from an existing workspace PDF.

        Deep Agents virtual paths such as /papers/example.pdf are resolved from
        the workspace root. This tool reads local files only; it does not
        download papers or infer PDF locations from DOI values. A structured
        error is returned when a PDF is unavailable or cannot be read.
        """
        path = resolve_workspace_path(pdf_path)
        if allowed_root is not None and not path.is_relative_to(allowed_root):
            return json.dumps(
                {
                    "available": False,
                    "error_code": "path_outside_workspace",
                    "error": f"PDF path must stay inside the workspace: {pdf_path}",
                    "hint": "Use an existing PDF under the virtual /papers directory.",
                },
                ensure_ascii=False,
            )
        if not path.exists():
            return json.dumps(
                {
                    "available": False,
                    "error_code": "pdf_not_found",
                    "error": f"PDF 文件不存在：{pdf_path}",
                    "hint": (
                        "论文 PDF 尚未下载。请基于检索阶段的元数据生成有限的 "
                        "PaperCard，并在 limitations 中标注未获取全文。"
                    ),
                },
                ensure_ascii=False,
            )
        try:
            pages = extract_pdf_pages(path, max_pages)
        except Exception as exc:  # PDF parser errors must remain recoverable tool results.
            return json.dumps(
                {
                    "available": False,
                    "error_code": "pdf_unreadable",
                    "error": f"PDF 无法解析：{pdf_path}",
                    "detail": str(exc),
                    "hint": "请将该论文标记为未获取全文，并继续处理其他论文。",
                },
                ensure_ascii=False,
            )
        return json.dumps(pages, ensure_ascii=False)

    @tool
    def verify_doi(doi: str) -> str:
        """Resolve DOI metadata through Crossref or return a recoverable error JSON."""
        normalized = doi.removeprefix("https://doi.org/").strip()
        params = {"mailto": contact_email} if contact_email else {}
        query = f"?{urlencode(params)}" if params else ""
        url = f"https://api.crossref.org/works/{normalized}{query}"
        try:
            data = _get_json(url, source="Crossref", policy=retry_policy).get("message", {})
        except AcademicApiError as exc:
            return recoverable_error(exc)
        result = {
            "doi": data.get("DOI"),
            "title": (data.get("title") or [""])[0],
            "authors": data.get("author", []),
            "url": data.get("URL"),
        }
        return json.dumps(result, ensure_ascii=False)

    return [search_openalex, search_crossref, fetch_paper_text, extract_pdf_text, verify_doi]
