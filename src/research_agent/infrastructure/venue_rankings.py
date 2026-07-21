from __future__ import annotations

import json
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


DEFAULT_SEED_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "venue_rankings.json"
)


def normalize_venue_name(value: str) -> str:
    """Normalize publisher punctuation without translating or guessing names."""
    normalized = str(value or "").casefold()
    normalized = normalized.replace("&", " and ")
    normalized = re.sub(r"\bproceedings\s+of\s+(?:the\s+)?", " ", normalized)
    normalized = re.sub(r"\bjournal\s+of\s+the\b", " journal ", normalized)
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _venue_type(value: str | None) -> str | None:
    normalized = str(value or "").casefold().strip()
    if normalized in {"journal", "journal-article", "periodical"}:
        return "journal"
    if normalized in {
        "conference",
        "conference-paper",
        "conference-proceedings",
        "proceedings",
        "proceedings-article",
    }:
        return "conference"
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class VenueRankingIndex:
    """SQLite-backed local retrieval index for venue ratings and metrics.

    The index uses exact normalized aliases first, then SQLite FTS5 and a
    conservative in-process reranker. It never invents a quartile, CCF grade,
    or impact factor when the local evidence does not match confidently.
    """

    def __init__(
        self,
        database_path: str | Path,
        seed_path: str | Path = DEFAULT_SEED_PATH,
    ) -> None:
        self.database_path = Path(database_path)
        self.seed_path = Path(seed_path)
        self._fts_available = False
        self._init_schema()
        self._seed()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS venue_rankings (
                    venue_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    venue_type TEXT NOT NULL,
                    acronym TEXT NOT NULL DEFAULT '',
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    publisher TEXT NOT NULL DEFAULT '',
                    ccf_rank TEXT,
                    ccf_category TEXT,
                    ccf_year INTEGER,
                    sci_quartile TEXT,
                    index_name TEXT,
                    impact_factor REAL,
                    impact_factor_year INTEGER,
                    nature_portfolio INTEGER NOT NULL DEFAULT 0,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    search_text TEXT NOT NULL,
                    seed_schema_version INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS venue_aliases (
                    venue_id TEXT NOT NULL,
                    alias TEXT NOT NULL,
                    normalized_alias TEXT NOT NULL,
                    PRIMARY KEY(venue_id, normalized_alias),
                    FOREIGN KEY(venue_id) REFERENCES venue_rankings(venue_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_venue_alias_normalized
                    ON venue_aliases(normalized_alias);
                CREATE INDEX IF NOT EXISTS idx_venue_rank_quality
                    ON venue_rankings(ccf_rank, sci_quartile, nature_portfolio);
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS venue_rankings_fts
                    USING fts5(
                        venue_id UNINDEXED,
                        canonical_name,
                        acronym,
                        aliases,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                    """
                )
                self._fts_available = True
            except sqlite3.OperationalError:
                self._fts_available = False

    def _seed(self) -> None:
        payload = json.loads(self.seed_path.read_text(encoding="utf-8"))
        schema_version = int(payload.get("schema_version", 1))
        with self._connect() as connection:
            for item in payload.get("venues", []):
                aliases = list(item.get("aliases") or [])
                search_text = " ".join(
                    [
                        str(item.get("canonical_name") or ""),
                        str(item.get("acronym") or ""),
                        *[str(alias) for alias in aliases],
                    ]
                )
                connection.execute(
                    """
                    INSERT INTO venue_rankings(
                        venue_id, canonical_name, venue_type, acronym, aliases_json,
                        publisher, ccf_rank, ccf_category, ccf_year, sci_quartile,
                        index_name, impact_factor, impact_factor_year,
                        nature_portfolio, sources_json, search_text,
                        seed_schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(venue_id) DO UPDATE SET
                        canonical_name = excluded.canonical_name,
                        venue_type = excluded.venue_type,
                        acronym = excluded.acronym,
                        aliases_json = excluded.aliases_json,
                        publisher = excluded.publisher,
                        ccf_rank = excluded.ccf_rank,
                        ccf_category = excluded.ccf_category,
                        ccf_year = excluded.ccf_year,
                        sci_quartile = excluded.sci_quartile,
                        index_name = excluded.index_name,
                        impact_factor = excluded.impact_factor,
                        impact_factor_year = excluded.impact_factor_year,
                        nature_portfolio = excluded.nature_portfolio,
                        sources_json = excluded.sources_json,
                        search_text = excluded.search_text,
                        seed_schema_version = excluded.seed_schema_version
                    """,
                    (
                        item["venue_id"],
                        item["canonical_name"],
                        item["venue_type"],
                        item.get("acronym") or "",
                        json.dumps(aliases, ensure_ascii=False),
                        item.get("publisher") or "",
                        item.get("ccf_rank"),
                        item.get("ccf_category"),
                        item.get("ccf_year"),
                        item.get("sci_quartile"),
                        item.get("index_name"),
                        _safe_float(item.get("impact_factor")),
                        item.get("impact_factor_year"),
                        int(bool(item.get("nature_portfolio"))),
                        json.dumps(item.get("sources") or [], ensure_ascii=False),
                        search_text,
                        schema_version,
                    ),
                )
                connection.execute(
                    "DELETE FROM venue_aliases WHERE venue_id = ?",
                    (item["venue_id"],),
                )
                for alias in {
                    item["canonical_name"],
                    item.get("acronym") or "",
                    *aliases,
                }:
                    normalized = normalize_venue_name(alias)
                    if not normalized:
                        continue
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO venue_aliases(
                            venue_id, alias, normalized_alias
                        ) VALUES (?, ?, ?)
                        """,
                        (item["venue_id"], alias, normalized),
                    )
            if self._fts_available:
                connection.execute("DELETE FROM venue_rankings_fts")
                connection.execute(
                    """
                    INSERT INTO venue_rankings_fts(
                        venue_id, canonical_name, acronym, aliases
                    )
                    SELECT
                        venue_id, canonical_name, acronym,
                        replace(replace(aliases_json, '[', ' '), ']', ' ')
                    FROM venue_rankings
                    """
                )

    @staticmethod
    def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "venue_id": row["venue_id"],
            "canonical_name": row["canonical_name"],
            "venue_type": row["venue_type"],
            "acronym": row["acronym"],
            "aliases": json.loads(row["aliases_json"]),
            "publisher": row["publisher"],
            "ccf_rank": row["ccf_rank"],
            "ccf_category": row["ccf_category"],
            "ccf_year": row["ccf_year"],
            "sci_quartile": row["sci_quartile"],
            "index_name": row["index_name"],
            "impact_factor": row["impact_factor"],
            "impact_factor_year": row["impact_factor_year"],
            "nature_portfolio": bool(row["nature_portfolio"]),
            "sources": json.loads(row["sources_json"]),
        }

    def _get_rows(self, venue_ids: list[str]) -> list[sqlite3.Row]:
        if not venue_ids:
            return []
        placeholders = ",".join("?" for _ in venue_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM venue_rankings WHERE venue_id IN ({placeholders})",
                venue_ids,
            ).fetchall()
        by_id = {row["venue_id"]: row for row in rows}
        return [by_id[item] for item in venue_ids if item in by_id]

    @staticmethod
    def _score(query: str, row: sqlite3.Row) -> float:
        query_key = normalize_venue_name(query)
        values = [
            row["canonical_name"],
            row["acronym"],
            *json.loads(row["aliases_json"]),
        ]
        best = 0.0
        for value in values:
            candidate = normalize_venue_name(value)
            if not candidate:
                continue
            if query_key == candidate:
                return 1.0
            if len(candidate) >= 5 and (
                candidate in query_key or query_key in candidate
            ):
                length_ratio = min(len(query_key), len(candidate)) / max(
                    len(query_key), len(candidate)
                )
                if length_ratio >= 0.75:
                    best = max(best, 0.96)
            best = max(best, SequenceMatcher(None, query_key, candidate).ratio())
        return best

    def lookup(
        self,
        venue_name: str,
        venue_type: str | None = None,
    ) -> dict[str, Any] | None:
        query = str(venue_name or "").strip()
        query_key = normalize_venue_name(query)
        if not query_key:
            return None
        normalized_type = _venue_type(venue_type)
        with self._connect() as connection:
            params: list[Any] = [query_key]
            type_sql = ""
            if normalized_type:
                type_sql = " AND vr.venue_type = ?"
                params.append(normalized_type)
            exact = connection.execute(
                f"""
                SELECT vr.*
                FROM venue_aliases va
                JOIN venue_rankings vr ON vr.venue_id = va.venue_id
                WHERE va.normalized_alias = ?{type_sql}
                ORDER BY
                    CASE WHEN vr.ccf_rank = 'A' THEN 0 ELSE 1 END,
                    CASE WHEN vr.sci_quartile = 'Q1' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                params,
            ).fetchone()
            if exact is not None:
                result = self._row_payload(exact)
                result.update(match_confidence=1.0, match_method="exact_alias")
                return result

            venue_ids: list[str] = []
            if self._fts_available:
                tokens = re.findall(r"[a-z0-9]{2,}", query.casefold())
                if tokens:
                    expression = " OR ".join(f'"{token}"' for token in tokens[:12])
                    venue_ids = [
                        row["venue_id"]
                        for row in connection.execute(
                            """
                            SELECT venue_id
                            FROM venue_rankings_fts
                            WHERE venue_rankings_fts MATCH ?
                            ORDER BY bm25(venue_rankings_fts)
                            LIMIT 20
                            """,
                            (expression,),
                        ).fetchall()
                    ]
            if not venue_ids:
                like = f"%{query.casefold()}%"
                rows = connection.execute(
                    """
                    SELECT venue_id
                    FROM venue_rankings
                    WHERE lower(search_text) LIKE ?
                    LIMIT 20
                    """,
                    (like,),
                ).fetchall()
                venue_ids = [row["venue_id"] for row in rows]

        candidates = self._get_rows(venue_ids)
        if normalized_type:
            candidates = [
                item for item in candidates if item["venue_type"] == normalized_type
            ]
        scored = sorted(
            ((self._score(query, row), row) for row in candidates),
            key=lambda item: item[0],
            reverse=True,
        )
        if not scored or scored[0][0] < 0.82:
            return None
        confidence, row = scored[0]
        result = self._row_payload(row)
        result.update(
            match_confidence=round(confidence, 4),
            match_method="fts_rerank",
        )
        return result

    @staticmethod
    def qualifies_for_quality_filter(ranking: dict[str, Any] | None) -> bool:
        if not ranking:
            return False
        return bool(
            ranking.get("ccf_rank") == "A"
            or ranking.get("sci_quartile") == "Q1"
            or ranking.get("nature_portfolio")
        )

    @staticmethod
    def _explanation(ranking: dict[str, Any]) -> str:
        labels: list[str] = []
        if ranking.get("ccf_rank"):
            labels.append(
                f"CCF-{ranking['ccf_rank']}（{ranking.get('ccf_year') or '年份未知'}版）"
            )
        if ranking.get("sci_quartile"):
            index = f" · {ranking['index_name']}" if ranking.get("index_name") else ""
            labels.append(
                f"JCR {ranking['sci_quartile']}{index}"
                f"（{ranking.get('impact_factor_year') or '年份未知'} JCR）"
            )
        if ranking.get("nature_portfolio"):
            labels.append("Nature Portfolio")
        if ranking.get("impact_factor") is not None:
            labels.append(
                f"IF {ranking['impact_factor']:g}"
                f"（{ranking.get('impact_factor_year') or '年份未知'}）"
            )
        return " · ".join(labels) or "本地评级库已命中，但暂无可展示指标。"

    def enrich_candidate(self, candidate: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(candidate)
        venue_name = str(
            enriched.get("venue")
            or enriched.get("journal")
            or enriched.get("container_title")
            or ""
        ).strip()
        normalized_type = _venue_type(enriched.get("venue_type"))
        ranking = self.lookup(venue_name, normalized_type) if venue_name else None
        enriched["venue"] = venue_name
        if ranking is None:
            # OpenAlex also returns source types such as ``repository``. Keep
            # those candidates, but normalize unsupported types to unknown so
            # they remain compatible with PaperCandidate's public schema.
            enriched["venue_type"] = normalized_type
            enriched.update(
                ccf_rank=None,
                ccf_category=None,
                ccf_year=None,
                sci_quartile=None,
                index_name=None,
                impact_factor=None,
                impact_factor_year=None,
                nature_portfolio=False,
                venue_rating_explanation=(
                    "未在本地评级库中可靠命中；不推断期刊分区、影响因子或会议评级。"
                    if venue_name
                    else "检索源未返回期刊或会议名称，无法评级。"
                ),
                venue_rating_source_url=None,
                venue_rating_source_label=None,
                venue_match_confidence=None,
            )
            return enriched

        sources = ranking.get("sources") or []
        preferred_source = (
            "IEEE"
            if ranking.get("sci_quartile")
            else "Nature"
            if ranking.get("nature_portfolio")
            else "CCF"
        )
        primary_source = next(
            (
                item
                for item in sources
                if preferred_source.casefold()
                in str(item.get("label") or "").casefold()
            ),
            sources[0] if sources else {},
        )
        enriched.update(
            venue=ranking["canonical_name"],
            venue_type=ranking["venue_type"],
            venue_acronym=ranking.get("acronym") or "",
            ccf_rank=ranking.get("ccf_rank"),
            ccf_category=ranking.get("ccf_category"),
            ccf_year=ranking.get("ccf_year"),
            sci_quartile=ranking.get("sci_quartile"),
            index_name=ranking.get("index_name"),
            impact_factor=ranking.get("impact_factor"),
            impact_factor_year=ranking.get("impact_factor_year"),
            nature_portfolio=bool(ranking.get("nature_portfolio")),
            venue_rating_explanation=self._explanation(ranking),
            venue_rating_source_url=primary_source.get("url"),
            venue_rating_source_label=primary_source.get("label"),
            venue_match_confidence=ranking.get("match_confidence"),
        )
        return enriched

    def stats(self) -> dict[str, int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    count(*) AS venues,
                    sum(CASE WHEN ccf_rank = 'A' THEN 1 ELSE 0 END) AS ccf_a,
                    sum(CASE WHEN sci_quartile = 'Q1' THEN 1 ELSE 0 END) AS q1,
                    sum(CASE WHEN sci_quartile = 'Q2' THEN 1 ELSE 0 END) AS q2,
                    sum(nature_portfolio) AS nature_portfolio
                FROM venue_rankings
                """
            ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}
