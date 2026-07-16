from __future__ import annotations

import re
from typing import Any


_OPENALEX_ID_RE = re.compile(
    r"(?:https?://)?(?:api\.)?openalex\.org/(?:works/)?(W\d+)",
    flags=re.IGNORECASE,
)


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
