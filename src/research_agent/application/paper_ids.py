from __future__ import annotations

import re
from typing import Any


_OPENALEX_ID_RE = re.compile(
    r"(?:https?://)?(?:api\.)?openalex\.org/(?:works/)?(W\d+)",
    flags=re.IGNORECASE,
)
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


def normalize_paper_id(value: Any) -> str:
    """Return a stable paper identifier for internal comparisons.

    OpenAlex may appear as a full URL (``https://openalex.org/W...``), an API URL
    (``https://api.openalex.org/works/W...``), or the bare work id (``W...``).
    The research workflow should treat all of those as the same paper.
    """
    raw = str(value or "").strip().rstrip(".,;，。；)")
    match = _OPENALEX_ID_RE.search(raw)
    if match:
        return match.group(1).upper()
    return raw


def same_paper_id(left: Any, right: Any) -> bool:
    return normalize_paper_id(left) == normalize_paper_id(right)


def normalize_doi(value: Any) -> str:
    raw = str(value or "").strip()
    raw = _DOI_PREFIX_RE.sub("", raw)
    return raw.rstrip(".,;，。；)").casefold()


def normalize_title(value: Any) -> str:
    raw = str(value or "").casefold().strip()
    return re.sub(r"[^\w]+", " ", raw, flags=re.UNICODE).strip()


def canonical_paper_key(
    *,
    doi: Any = "",
    paper_id: Any = "",
    title: Any = "",
    year: Any = None,
) -> str:
    """Build a conservative identity key for library deduplication."""
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        return f"doi:{normalized_doi}"
    normalized_id = normalize_paper_id(paper_id)
    if normalized_id:
        return f"id:{normalized_id.casefold()}"
    normalized_title = normalize_title(title)
    if not normalized_title:
        raise ValueError("Library paper requires a DOI, paper_id, or title")
    normalized_year = str(year or "").strip()
    return f"title:{normalized_title}|year:{normalized_year}"
