from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, Literal

from research_agent.application.paper_ids import (
    canonical_paper_key,
    normalize_doi,
    normalize_paper_id,
    normalize_title,
)
from research_agent.application.ports import ResearchRepositoryPort
from research_agent.domain.models import (
    LibraryAttachment,
    LibraryArtifact,
    LibraryChunk,
    LibraryCollection,
    LibraryNote,
    LibraryPaper,
    LibraryPaperAnalysis,
    ProjectPaper,
)


ProjectPaperStatus = Literal["candidate", "included", "excluded", "uncertain"]
MAX_COLLECTION_DEPTH = 3
LIBRARY_CHUNK_SIZE = 1800
LIBRARY_CHUNK_OVERLAP = 180
LIBRARY_SOURCE_LIMIT = 20_000
QUERY_STOP_WORDS = {
    "about",
    "and",
    "are",
    "for",
    "from",
    "how",
    "into",
    "the",
    "what",
    "which",
    "with",
    "什么",
    "哪些",
    "如何",
    "是否",
    "这个",
    "这些",
    "研究",
    "论文",
    "文献",
}


class LibraryService:
    """Long-lived paper library layered over project-scoped research artifacts."""

    def __init__(self, repository: ResearchRepositoryPort):
        self.repository = repository

    @staticmethod
    def _clean_tags(tags: list[str] | None) -> list[str]:
        return sorted({str(tag).strip() for tag in tags or [] if str(tag).strip()})

    def _find_existing(self, payload: dict[str, Any]) -> LibraryPaper | None:
        key = canonical_paper_key(
            doi=payload.get("doi"),
            paper_id=payload.get("paper_id"),
            title=payload.get("title"),
            year=payload.get("year"),
        )
        exact = self.repository.get_library_paper_by_key(key)
        if exact is not None:
            return exact

        doi = normalize_doi(payload.get("doi"))
        paper_id = normalize_paper_id(payload.get("paper_id"))
        title = normalize_title(payload.get("title"))
        year = payload.get("year")
        for paper in self.repository.list_library_papers(
            saved_only=False,
            include_archived=True,
            limit=500,
        ):
            if doi and normalize_doi(paper.doi) == doi:
                return paper
            if paper_id and normalize_paper_id(paper.paper_id) == paper_id:
                return paper
            if title and normalize_title(paper.title) == title and paper.year == year:
                return paper
        return None

    def upsert_paper(
        self,
        payload: dict[str, Any],
        *,
        saved: bool = True,
        tags: list[str] | None = None,
    ) -> LibraryPaper:
        title = str(payload.get("title") or "").strip()
        doi = normalize_doi(payload.get("doi"))
        paper_id = normalize_paper_id(payload.get("paper_id"))
        if not title and not doi and not paper_id:
            raise ValueError("Library paper requires a title, DOI, or paper_id")
        if not title:
            title = doi or paper_id

        existing = self._find_existing({**payload, "title": title})
        now = datetime.now(UTC)
        incoming_authors = [
            str(author).strip() for author in payload.get("authors") or [] if str(author).strip()
        ]
        incoming_abstract = str(payload.get("abstract") or "").strip()
        incoming_tags = self._clean_tags(tags or payload.get("tags"))

        if existing is None:
            paper = LibraryPaper(
                library_id=f"LP-{uuid.uuid4().hex[:12]}",
                paper_id=paper_id,
                title=title,
                authors=incoming_authors,
                year=payload.get("year"),
                abstract=incoming_abstract,
                doi=doi,
                url=payload.get("url") or None,
                source=str(payload.get("source") or "user"),
                venue=str(payload.get("venue") or ""),
                venue_type=payload.get("venue_type"),
                venue_acronym=str(payload.get("venue_acronym") or ""),
                ccf_rank=payload.get("ccf_rank"),
                ccf_category=payload.get("ccf_category"),
                ccf_year=payload.get("ccf_year"),
                sci_quartile=payload.get("sci_quartile"),
                index_name=payload.get("index_name"),
                impact_factor=payload.get("impact_factor"),
                impact_factor_year=payload.get("impact_factor_year"),
                nature_portfolio=bool(payload.get("nature_portfolio")),
                venue_rating_explanation=str(
                    payload.get("venue_rating_explanation") or ""
                ),
                venue_rating_source_url=payload.get("venue_rating_source_url"),
                venue_rating_source_label=payload.get("venue_rating_source_label"),
                tags=incoming_tags,
                starred=bool(payload.get("starred", False)),
                saved=saved,
            )
        else:
            paper = existing.model_copy(deep=True)
            paper.paper_id = paper.paper_id or paper_id
            if len(title) > len(paper.title):
                paper.title = title
            if len(incoming_authors) > len(paper.authors):
                paper.authors = incoming_authors
            paper.year = paper.year or payload.get("year")
            if len(incoming_abstract) > len(paper.abstract):
                paper.abstract = incoming_abstract
            paper.doi = paper.doi or doi
            paper.url = paper.url or payload.get("url") or None
            if paper.source == "user" and payload.get("source"):
                paper.source = str(payload["source"])
            for field in (
                "venue",
                "venue_type",
                "venue_acronym",
                "ccf_rank",
                "ccf_category",
                "ccf_year",
                "sci_quartile",
                "index_name",
                "impact_factor",
                "impact_factor_year",
                "venue_rating_explanation",
                "venue_rating_source_url",
                "venue_rating_source_label",
            ):
                value = payload.get(field)
                if value not in (None, ""):
                    setattr(paper, field, value)
            if "nature_portfolio" in payload:
                paper.nature_portfolio = bool(payload["nature_portfolio"])
            paper.tags = self._clean_tags([*paper.tags, *incoming_tags])
            paper.saved = paper.saved or saved
            if saved:
                paper.archived_at = None
            if "starred" in payload:
                paper.starred = bool(payload["starred"])
            paper.updated_at = now

        key = canonical_paper_key(
            doi=paper.doi,
            paper_id=paper.paper_id,
            title=paper.title,
            year=paper.year,
        )
        return self.repository.save_library_paper(paper, key)

    def add_project_paper(
        self,
        project_id: str,
        payload: dict[str, Any],
        *,
        status: ProjectPaperStatus = "candidate",
        reason: str = "",
        saved: bool = True,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        paper = self.upsert_paper(payload, saved=saved, tags=tags)
        now = datetime.now(UTC)
        relation = ProjectPaper(
            project_id=project_id,
            library_id=paper.library_id,
            source_paper_id=normalize_paper_id(payload.get("paper_id")) or paper.paper_id,
            status=status,
            reason=reason,
            updated_at=now,
        )
        relation = self.repository.link_project_paper(relation)
        return {
            "paper": paper.model_dump(mode="json"),
            "relation": relation.model_dump(mode="json"),
        }

    def update_paper(self, library_id: str, changes: dict[str, Any]) -> LibraryPaper:
        paper = self.repository.get_library_paper(library_id)
        allowed = {
            "paper_id",
            "title",
            "authors",
            "year",
            "abstract",
            "doi",
            "url",
            "source",
            "venue",
            "venue_type",
            "venue_acronym",
            "ccf_rank",
            "ccf_category",
            "ccf_year",
            "sci_quartile",
            "index_name",
            "impact_factor",
            "impact_factor_year",
            "nature_portfolio",
            "venue_rating_explanation",
            "venue_rating_source_url",
            "venue_rating_source_label",
            "tags",
            "starred",
            "saved",
        }
        unexpected = sorted(set(changes) - allowed)
        if unexpected:
            raise ValueError("Unsupported library fields: " + ", ".join(unexpected))
        payload = paper.model_dump(mode="json")
        payload.update(changes)
        payload["tags"] = self._clean_tags(payload.get("tags"))
        payload["authors"] = [
            str(author).strip()
            for author in payload.get("authors") or []
            if str(author).strip()
        ]
        payload["title"] = str(payload.get("title") or "").strip()
        payload["doi"] = normalize_doi(payload.get("doi"))
        payload["paper_id"] = normalize_paper_id(payload.get("paper_id"))
        if not payload["title"] and not payload["doi"] and not payload["paper_id"]:
            raise ValueError("Library paper requires a title, DOI, or paper_id")
        if not payload["title"]:
            payload["title"] = payload["doi"] or payload["paper_id"]
        payload["library_id"] = library_id
        updated = LibraryPaper.model_validate(payload)
        updated.updated_at = datetime.now(UTC)
        if updated.saved:
            updated.archived_at = None
        key = canonical_paper_key(
            doi=updated.doi,
            paper_id=updated.paper_id,
            title=updated.title,
            year=updated.year,
        )
        return self.repository.save_library_paper(updated, key)

    @staticmethod
    def _split_page_text(
        text: str,
        *,
        chunk_size: int = LIBRARY_CHUNK_SIZE,
        overlap: int = LIBRARY_CHUNK_OVERLAP,
    ) -> list[str]:
        normalized = re.sub(r"[ \t]+", " ", str(text or ""))
        normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
        if not normalized:
            return []
        chunk_size = max(400, int(chunk_size))
        overlap = max(0, min(int(overlap), chunk_size // 3))
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            hard_end = min(len(normalized), start + chunk_size)
            end = hard_end
            if hard_end < len(normalized):
                floor = start + int(chunk_size * 0.6)
                candidates = [
                    normalized.rfind(marker, floor, hard_end)
                    for marker in ("\n\n", "。", ". ", "; ", "；", " ")
                ]
                boundary = max(candidates)
                if boundary > floor:
                    end = boundary + 1
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            start = max(start + 1, end - overlap)
        return chunks

    def index_attachment_pages(
        self,
        library_id: str,
        attachment_id: str,
        pages: list[dict[str, Any]],
    ) -> list[LibraryChunk]:
        paper = self.repository.get_library_paper(library_id)
        attachment = self.repository.get_library_attachment(attachment_id)
        if attachment.library_id != paper.library_id:
            raise ValueError("Attachment does not belong to the requested paper")
        chunks: list[LibraryChunk] = []
        chunk_index = 0
        for item in pages:
            page = item.get("page")
            page_number = int(page) if page is not None else None
            for text in self._split_page_text(str(item.get("text") or "")):
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                chunk_key = f"{attachment_id}:{page_number}:{chunk_index}:{digest}"
                chunks.append(
                    LibraryChunk(
                        chunk_id=f"LC-{hashlib.sha256(chunk_key.encode()).hexdigest()[:16]}",
                        library_id=library_id,
                        attachment_id=attachment_id,
                        page=page_number,
                        chunk_index=chunk_index,
                        text=text,
                        content_hash=digest,
                    )
                )
                chunk_index += 1
        return self.repository.replace_library_chunks(
            library_id,
            attachment_id,
            chunks,
        )

    def save_paper_analysis(
        self,
        library_id: str,
        attachment_id: str,
        analysis: LibraryPaperAnalysis,
        *,
        mode: Literal["agent", "extractive"] = "agent",
    ) -> LibraryArtifact:
        artifact = LibraryArtifact(
            artifact_id=f"LAR-{uuid.uuid4().hex[:12]}",
            library_id=library_id,
            attachment_id=attachment_id,
            kind="PaperCard",
            payload=analysis.model_dump(mode="json"),
            mode=mode,
        )
        return self.repository.save_library_artifact(artifact)

    @staticmethod
    def _query_terms(query: str) -> set[str]:
        normalized = str(query or "").casefold()
        terms = {
            token
            for token in re.findall(r"[a-z0-9][a-z0-9_+.-]*|[\u4e00-\u9fff]{2,}", normalized)
            if token not in QUERY_STOP_WORDS and len(token) > 1
        }
        for phrase in re.findall(r"[\u4e00-\u9fff]{4,}", normalized):
            terms.update(
                phrase[index : index + 2]
                for index in range(len(phrase) - 1)
                if phrase[index : index + 2] not in QUERY_STOP_WORDS
            )
        return terms

    @classmethod
    def _text_score(cls, text: str, terms: set[str]) -> float:
        if not terms:
            return 1.0
        normalized = str(text or "").casefold()
        score = 0.0
        for term in terms:
            occurrences = normalized.count(term)
            if occurrences:
                score += min(occurrences, 5) * max(1.0, min(len(term), 8) / 2)
        return score

    @staticmethod
    def _source_id(prefix: str, *parts: Any) -> str:
        digest = hashlib.sha256(
            "|".join(str(part) for part in parts).encode("utf-8")
        ).hexdigest()[:16]
        return f"{prefix}-{digest}"

    def _paper_sources(self, paper: LibraryPaper) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []

        def add_source(
            source_id: str,
            source_type: str,
            text: str,
            *,
            page: int | None = None,
            attachment_id: str | None = None,
        ) -> None:
            clean = str(text or "").strip()
            if not clean:
                return
            sources.append(
                {
                    "source_id": source_id,
                    "source_type": source_type,
                    "library_id": paper.library_id,
                    "title": paper.title,
                    "page": page,
                    "attachment_id": attachment_id,
                    "text": clean[:4000],
                }
            )

        add_source(f"ABSTRACT-{paper.library_id}", "abstract", paper.abstract)
        for note in self.repository.list_library_notes(paper.library_id):
            add_source(f"NOTE-{note.note_id}", "note", note.content)
        artifacts = self.repository.list_library_artifacts(paper.library_id)
        for artifact in [item for item in artifacts if item.kind in {"PaperCard", "PaperAnalysis"}][
            :3
        ]:
            payload = artifact.payload
            add_source(
                self._source_id("ANALYSIS", artifact.artifact_id, "summary"),
                "analysis-summary",
                payload.get("summary", ""),
                attachment_id=artifact.attachment_id,
            )
            for index, finding in enumerate(payload.get("findings", [])):
                add_source(
                    self._source_id("ANALYSIS", artifact.artifact_id, index),
                    "analysis-finding",
                    "\n".join(
                        filter(
                            None,
                            [finding.get("claim", ""), finding.get("quote", "")],
                        )
                    ),
                    page=finding.get("page"),
                    attachment_id=artifact.attachment_id,
                )
        for card in self._paper_evidence(paper):
            for finding in card.get("findings", []):
                add_source(
                    self._source_id(
                        "EVIDENCE",
                        paper.library_id,
                        card.get("project_id"),
                        finding.get("evidence_id"),
                    ),
                    "project-evidence",
                    "\n".join(
                        filter(
                            None,
                            [finding.get("claim", ""), finding.get("quote", "")],
                        )
                    ),
                    page=finding.get("page"),
                )
        return sources

    def retrieve_library_sources(
        self,
        query: str,
        *,
        library_ids: list[str] | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        allowed_ids = set(library_ids or [])
        papers = self.repository.list_library_papers(limit=500)
        if allowed_ids:
            papers = [paper for paper in papers if paper.library_id in allowed_ids]
        paper_map = {paper.library_id: paper for paper in papers}
        sources = [source for paper in papers for source in self._paper_sources(paper)]
        chunks = (
            self.repository.list_library_chunks(
                library_ids=list(paper_map),
                limit=LIBRARY_SOURCE_LIMIT,
            )
            if paper_map
            else []
        )
        for chunk in chunks:
            paper = paper_map.get(chunk.library_id)
            if paper is None:
                continue
            sources.append(
                {
                    "source_id": chunk.chunk_id,
                    "source_type": "pdf",
                    "library_id": chunk.library_id,
                    "title": paper.title,
                    "page": chunk.page,
                    "attachment_id": chunk.attachment_id,
                    "text": chunk.text,
                }
            )
        terms = self._query_terms(query)
        ranked = []
        for source in sources:
            score = self._text_score(source["text"], terms)
            if terms and score <= 0:
                continue
            ranked.append({**source, "relevance_score": round(score, 3)})
        ranked.sort(
            key=lambda item: (
                -float(item["relevance_score"]),
                item["source_type"] != "pdf",
                item["title"],
                item.get("page") or 0,
            )
        )
        return ranked[: max(1, min(int(limit), 50))]

    def search_library(
        self,
        query: str,
        *,
        library_ids: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        allowed_ids = set(library_ids or [])
        papers = self.repository.list_library_papers(limit=500)
        if allowed_ids:
            papers = [paper for paper in papers if paper.library_id in allowed_ids]
        terms = self._query_terms(query)
        sources = self.retrieve_library_sources(
            query,
            library_ids=[paper.library_id for paper in papers],
            limit=50,
        )
        sources_by_paper: dict[str, list[dict[str, Any]]] = {}
        for source in sources:
            sources_by_paper.setdefault(source["library_id"], []).append(source)
        results = []
        for paper in papers:
            metadata_text = " ".join(
                [
                    paper.title,
                    " ".join(paper.authors),
                    paper.abstract,
                    paper.doi,
                    " ".join(paper.tags),
                ]
            )
            score = self._text_score(metadata_text, terms) * 2
            paper_sources = sources_by_paper.get(paper.library_id, [])
            score += sum(float(item["relevance_score"]) for item in paper_sources[:3])
            if terms and score <= 0:
                continue
            results.append(
                {
                    "paper_id": paper.paper_id or paper.doi or paper.library_id,
                    "library_id": paper.library_id,
                    "title": paper.title,
                    "authors": paper.authors,
                    "year": paper.year,
                    "abstract": paper.abstract,
                    "doi": paper.doi or None,
                    "url": str(paper.url) if paper.url else None,
                    "source": "library",
                    "relevance_score": round(score, 3),
                    "sources": paper_sources[:3],
                }
            )
        results.sort(key=lambda item: (-float(item["relevance_score"]), item["title"]))
        return results[: max(1, min(int(limit), 20))]

    def _paper_evidence(self, paper: LibraryPaper) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        target_ids = {
            normalize_paper_id(paper.paper_id),
            normalize_doi(paper.doi),
        } - {""}
        for relation in self.repository.list_library_paper_projects(paper.library_id):
            for artifact in self.repository.list_artifacts(relation.project_id, "PaperCard"):
                payload = artifact.payload
                artifact_ids = {
                    normalize_paper_id(payload.get("paper_id")),
                    normalize_doi(payload.get("doi")),
                } - {""}
                title_match = normalize_title(payload.get("title")) == normalize_title(paper.title)
                if target_ids.isdisjoint(artifact_ids) and not title_match:
                    continue
                evidence.append(
                    {
                        "project_id": relation.project_id,
                        "artifact_id": artifact.artifact_id,
                        "research_question": payload.get("research_question", ""),
                        "methods": payload.get("methods", []),
                        "datasets": payload.get("datasets", []),
                        "findings": payload.get("findings", []),
                        "limitations": payload.get("limitations", []),
                    }
                )
        return evidence

    def get_paper(self, library_id: str) -> dict[str, Any]:
        paper = self.repository.get_library_paper(library_id)
        artifacts = self.repository.list_library_artifacts(library_id)
        chunks = self.repository.list_library_chunks(
            library_ids=[library_id],
            limit=LIBRARY_SOURCE_LIMIT,
        )
        projects = []
        for relation in self.repository.list_library_paper_projects(library_id):
            project = self.repository.get_project(relation.project_id)
            projects.append(
                {
                    "relation": relation.model_dump(mode="json"),
                    "project": project.model_dump(mode="json"),
                }
            )
        return {
            "paper": paper.model_dump(mode="json"),
            "projects": projects,
            "collection_ids": self.repository.list_paper_collection_ids(library_id),
            "notes": [
                note.model_dump(mode="json")
                for note in self.repository.list_library_notes(library_id)
            ],
            "attachments": [
                attachment.model_dump(mode="json")
                for attachment in self.repository.list_library_attachments(library_id)
            ],
            "analyses": [artifact.model_dump(mode="json") for artifact in artifacts],
            "indexed_chunk_count": len(chunks),
            "evidence": self._paper_evidence(paper),
        }

    def list_papers(
        self,
        query: str = "",
        limit: int = 100,
        *,
        view: str = "all",
        collection_id: str | None = None,
    ) -> list[dict[str, Any]]:
        include_archived = view == "trash"
        papers = self.repository.list_library_papers(
            query=query,
            include_archived=include_archived,
            limit=500,
        )
        if view == "trash":
            papers = [paper for paper in papers if paper.archived_at is not None]
        elif view == "starred":
            papers = [paper for paper in papers if paper.starred]
        elif view == "unfiled":
            papers = [
                paper
                for paper in papers
                if not self.repository.list_paper_collection_ids(paper.library_id)
            ]
        if collection_id:
            member_ids = set(self.repository.list_collection_paper_ids(collection_id))
            papers = [paper for paper in papers if paper.library_id in member_ids]
        papers = papers[: max(1, min(int(limit), 500))]
        result = []
        for paper in papers:
            relations = self.repository.list_library_paper_projects(paper.library_id)
            result.append(
                {
                    **paper.model_dump(mode="json"),
                    "project_count": len(relations),
                    "project_statuses": sorted({relation.status for relation in relations}),
                    "collection_ids": self.repository.list_paper_collection_ids(
                        paper.library_id
                    ),
                    "note_count": len(self.repository.list_library_notes(paper.library_id)),
                    "attachment_count": len(
                        self.repository.list_library_attachments(paper.library_id)
                    ),
                }
            )
        return result

    def library_overview(self) -> dict[str, Any]:
        active = self.repository.list_library_papers(limit=500)
        all_papers = self.repository.list_library_papers(
            saved_only=True,
            include_archived=True,
            limit=500,
        )
        collections = self.repository.list_library_collections()
        return {
            "counts": {
                "all": len(active),
                "starred": sum(paper.starred for paper in active),
                "unfiled": sum(
                    not self.repository.list_paper_collection_ids(paper.library_id)
                    for paper in active
                ),
                "trash": sum(paper.archived_at is not None for paper in all_papers),
            },
            "collections": [
                {
                    **collection.model_dump(mode="json"),
                    "paper_count": len(
                        self.repository.list_collection_paper_ids(collection.collection_id)
                    ),
                }
                for collection in collections
            ],
        }

    def create_collection(
        self, name: str, parent_id: str | None = None
    ) -> LibraryCollection:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Collection name is required")
        collections = self.repository.list_library_collections()
        by_id = {item.collection_id: item for item in collections}
        if parent_id:
            if parent_id not in by_id:
                raise ValueError("Parent collection does not exist")
            if self._collection_depth(parent_id, by_id) >= MAX_COLLECTION_DEPTH:
                raise ValueError("Collections support at most three levels")
        collection = LibraryCollection(
            collection_id=f"LC-{uuid.uuid4().hex[:12]}",
            name=clean_name,
            parent_id=parent_id,
        )
        return self.repository.create_library_collection(collection)

    @staticmethod
    def _collection_depth(
        collection_id: str,
        by_id: dict[str, LibraryCollection],
    ) -> int:
        depth = 1
        current = by_id[collection_id]
        visited = {collection_id}
        while current.parent_id:
            if current.parent_id in visited:
                raise ValueError("Collection tree contains a cycle")
            parent = by_id.get(current.parent_id)
            if parent is None:
                break
            visited.add(parent.collection_id)
            depth += 1
            current = parent
        return depth

    @staticmethod
    def _collection_subtree_height(
        collection_id: str,
        collections: list[LibraryCollection],
    ) -> int:
        children: dict[str, list[str]] = {}
        for item in collections:
            if item.parent_id:
                children.setdefault(item.parent_id, []).append(item.collection_id)

        def height(node_id: str, path: set[str]) -> int:
            if node_id in path:
                raise ValueError("Collection tree contains a cycle")
            descendants = children.get(node_id, [])
            if not descendants:
                return 1
            next_path = {*path, node_id}
            return 1 + max(height(child_id, next_path) for child_id in descendants)

        return height(collection_id, set())

    def update_collection(
        self,
        collection_id: str,
        *,
        name: str,
        parent_id: str | None = None,
    ) -> LibraryCollection:
        collections = self.repository.list_library_collections()
        by_id = {item.collection_id: item for item in collections}
        current = by_id.get(collection_id)
        if current is None:
            raise ValueError("Collection does not exist")
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Collection name is required")
        if parent_id == collection_id:
            raise ValueError("A collection cannot contain itself")
        if parent_id and parent_id not in by_id:
            raise ValueError("Parent collection does not exist")
        ancestor_id = parent_id
        visited: set[str] = set()
        while ancestor_id:
            if ancestor_id == collection_id:
                raise ValueError("A collection cannot be moved below its descendant")
            if ancestor_id in visited:
                raise ValueError("Collection tree contains a cycle")
            visited.add(ancestor_id)
            ancestor = by_id.get(ancestor_id)
            ancestor_id = ancestor.parent_id if ancestor else None
        new_depth = self._collection_depth(parent_id, by_id) + 1 if parent_id else 1
        subtree_height = self._collection_subtree_height(collection_id, collections)
        if new_depth + subtree_height - 1 > MAX_COLLECTION_DEPTH:
            raise ValueError("Collections support at most three levels")
        current.name = clean_name
        current.parent_id = parent_id
        current.updated_at = datetime.now(UTC)
        return self.repository.update_library_collection(current)

    def add_note(
        self,
        library_id: str,
        content: str,
        project_id: str | None = None,
    ) -> LibraryNote:
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("Note content is required")
        return self.repository.save_library_note(
            LibraryNote(
                note_id=f"LN-{uuid.uuid4().hex[:12]}",
                library_id=library_id,
                content=clean_content,
                project_id=project_id,
            )
        )

    def update_note(self, note_id: str, library_id: str, content: str) -> LibraryNote:
        note = next(
            item
            for item in self.repository.list_library_notes(library_id)
            if item.note_id == note_id
        )
        clean_content = content.strip()
        if not clean_content:
            raise ValueError("Note content is required")
        note.content = clean_content
        note.updated_at = datetime.now(UTC)
        return self.repository.save_library_note(note)

    def add_attachment(
        self,
        library_id: str,
        *,
        name: str,
        url: str,
        media_type: str = "application/pdf",
    ) -> LibraryAttachment:
        clean_name = name.strip()
        clean_url = url.strip()
        if not clean_name or not clean_url:
            raise ValueError("Attachment name and URL are required")
        status = "ready" if clean_url.casefold().startswith("file:") else "linked"
        return self.repository.save_library_attachment(
            LibraryAttachment(
                attachment_id=f"LA-{uuid.uuid4().hex[:12]}",
                library_id=library_id,
                name=clean_name,
                url=clean_url,
                media_type=media_type.strip() or "application/pdf",
                full_text_status=status,
            )
        )

    def bulk_update(
        self,
        library_ids: list[str],
        action: str,
        value: Any = None,
    ) -> list[str]:
        ids = list(dict.fromkeys(library_ids))
        if not ids:
            raise ValueError("Select at least one paper")
        for library_id in ids:
            if action == "archive":
                self.repository.archive_library_paper(library_id)
            elif action == "restore":
                self.repository.restore_library_paper(library_id)
            elif action == "delete":
                self.repository.permanently_delete_library_paper(library_id)
            elif action in {"star", "unstar"}:
                self.update_paper(library_id, {"starred": action == "star"})
            elif action in {"add_tags", "remove_tags"}:
                paper = self.repository.get_library_paper(library_id)
                tags = self._clean_tags(value if isinstance(value, list) else [])
                merged = (
                    self._clean_tags([*paper.tags, *tags])
                    if action == "add_tags"
                    else [tag for tag in paper.tags if tag not in set(tags)]
                )
                self.update_paper(library_id, {"tags": merged})
            elif action == "add_collection":
                self.repository.add_paper_to_collection(str(value), library_id)
            elif action == "remove_collection":
                self.repository.remove_paper_from_collection(str(value), library_id)
            elif action == "add_project":
                paper = self.repository.get_library_paper(library_id)
                self.add_project_paper(
                    str(value), paper.model_dump(mode="json"), saved=True
                )
            else:
                raise ValueError(f"Unsupported bulk action: {action}")
        return ids

    def duplicate_groups(self) -> list[dict[str, Any]]:
        papers = self.repository.list_library_papers(
            saved_only=False,
            include_archived=False,
            limit=500,
        )
        groups: list[dict[str, Any]] = []
        used: set[str] = set()
        for index, paper in enumerate(papers):
            if paper.library_id in used:
                continue
            matches = [paper]
            normalized = normalize_title(paper.title)
            for other in papers[index + 1 :]:
                if other.library_id in used:
                    continue
                score = SequenceMatcher(
                    None, normalized, normalize_title(other.title)
                ).ratio()
                author_overlap = bool(set(paper.authors) & set(other.authors))
                year_close = not paper.year or not other.year or abs(paper.year - other.year) <= 1
                if score >= 0.92 and year_close and (author_overlap or score >= 0.97):
                    matches.append(other)
            if len(matches) > 1:
                used.update(item.library_id for item in matches)
                groups.append(
                    {
                        "score": round(
                            min(
                                SequenceMatcher(
                                    None,
                                    normalized,
                                    normalize_title(item.title),
                                ).ratio()
                                for item in matches[1:]
                            ),
                            3,
                        ),
                        "papers": [item.model_dump(mode="json") for item in matches],
                    }
                )
        return groups

    def merge_papers(self, primary_id: str, duplicate_id: str) -> LibraryPaper:
        primary = self.repository.get_library_paper(primary_id).model_copy(deep=True)
        duplicate = self.repository.get_library_paper(duplicate_id)
        if len(duplicate.title) > len(primary.title):
            primary.title = duplicate.title
        if len(duplicate.authors) > len(primary.authors):
            primary.authors = duplicate.authors
        if len(duplicate.abstract) > len(primary.abstract):
            primary.abstract = duplicate.abstract
        primary.paper_id = primary.paper_id or duplicate.paper_id
        primary.doi = primary.doi or duplicate.doi
        primary.url = primary.url or duplicate.url
        primary.year = primary.year or duplicate.year
        primary.tags = self._clean_tags([*primary.tags, *duplicate.tags])
        primary.starred = primary.starred or duplicate.starred
        primary.saved = primary.saved or duplicate.saved
        primary.archived_at = None
        primary.updated_at = datetime.now(UTC)
        key = canonical_paper_key(
            doi=primary.doi,
            paper_id=primary.paper_id,
            title=primary.title,
            year=primary.year,
        )
        return self.repository.merge_library_papers(primary, duplicate_id, key)

    def compare_papers(self, library_ids: list[str]) -> dict[str, Any]:
        if len(library_ids) < 2 or len(library_ids) > 8:
            raise ValueError("Compare between 2 and 8 papers")
        rows = []
        for library_id in library_ids:
            detail = self.get_paper(library_id)
            evidence = detail["evidence"]
            analyses = [
                artifact["payload"]
                for artifact in detail.get("analyses", [])
                if artifact.get("kind") in {"PaperCard", "PaperAnalysis"}
            ]
            reading_records = [*evidence, *analyses]
            rows.append(
                {
                    "paper": detail["paper"],
                    "methods": sorted(
                        {
                            method
                            for card in reading_records
                            for method in card.get("methods", [])
                        }
                    ),
                    "datasets": sorted(
                        {
                            dataset
                            for card in reading_records
                            for dataset in card.get("datasets", [])
                        }
                    ),
                    "findings": [
                        finding
                        for card in reading_records
                        for finding in card.get("findings", [])
                    ],
                    "limitations": sorted(
                        {
                            limitation
                            for card in reading_records
                            for limitation in card.get("limitations", [])
                        }
                    ),
                    "notes": detail["notes"],
                    "analyses": detail.get("analyses", []),
                }
            )
        return {"rows": rows}

    def answer_library_question(
        self, library_ids: list[str], question: str
    ) -> dict[str, Any]:
        clean_question = question.strip()
        if not clean_question:
            raise ValueError("Question is required")
        sources = self.retrieve_library_sources(
            clean_question,
            library_ids=library_ids or None,
            limit=8,
        )
        snippets: list[str] = []
        citations: list[dict[str, Any]] = []
        for index, source in enumerate(sources, start=1):
            excerpt = source["text"]
            if len(excerpt) > 500:
                excerpt = excerpt[:497].rstrip() + "…"
            page = f"，第 {source['page']} 页" if source.get("page") else ""
            snippets.append(f"[{index}]《{source['title']}》{page}：{excerpt}")
            citations.append(
                {
                    "citation": f"[{index}]",
                    **{key: value for key, value in source.items() if key != "text"},
                    "quote": excerpt,
                }
            )
        answer = (
            "\n\n".join(snippets)
            or "文献库中暂时没有可用于回答的全文、摘要、笔记或证据。"
        )
        return {
            "question": clean_question,
            "answer": answer,
            "citations": citations,
            "mode": "extractive",
            "coverage_note": "模型不可用，当前展示与问题最相关的原始来源。",
        }

    def list_project_papers(self, project_id: str) -> list[dict[str, Any]]:
        return [
            {
                "relation": relation.model_dump(mode="json"),
                "paper": paper.model_dump(mode="json"),
            }
            for relation, paper in self.repository.list_project_papers(project_id)
        ]

    @staticmethod
    def _candidate_id(candidate: dict[str, Any]) -> str:
        return normalize_paper_id(candidate.get("paper_id")) or normalize_doi(
            candidate.get("doi")
        )

    def sync_project(self, project_id: str) -> list[dict[str, Any]]:
        """Incrementally index legacy project artifacts without rewriting history."""
        artifacts = self.repository.list_artifacts(project_id)
        candidates: dict[str, dict[str, Any]] = {}
        statuses: dict[str, ProjectPaperStatus] = {}
        reasons: dict[str, str] = {}
        saved_ids: set[str] = set()

        for artifact in artifacts:
            if artifact.kind in {
                "SearchReport",
                "SupplementalSearchReport",
                "CandidateSetSnapshot",
            }:
                for candidate in artifact.payload.get("candidates", []):
                    if not isinstance(candidate, dict):
                        continue
                    identity = self._candidate_id(candidate) or canonical_paper_key(
                        title=candidate.get("title"), year=candidate.get("year")
                    )
                    previous = candidates.get(identity, {})
                    if sum(bool(value) for value in candidate.values()) >= sum(
                        bool(value) for value in previous.values()
                    ):
                        candidates[identity] = candidate
                if artifact.kind == "CandidateSetSnapshot":
                    for paper_id in artifact.payload.get("agent_included_paper_ids", []):
                        statuses[normalize_paper_id(paper_id)] = "included"
                    for paper_id in artifact.payload.get("agent_excluded_paper_ids", []):
                        statuses[normalize_paper_id(paper_id)] = "excluded"
                    for paper_id in artifact.payload.get("agent_uncertain_paper_ids", []):
                        statuses[normalize_paper_id(paper_id)] = "uncertain"
                    reasons.update(
                        {
                            normalize_paper_id(key): str(value)
                            for key, value in artifact.payload.get(
                                "agent_screening_reasons", {}
                            ).items()
                        }
                    )
            elif artifact.kind == "ScreeningDecision":
                for paper_id in artifact.payload.get("included_paper_ids", []):
                    normalized_id = normalize_paper_id(paper_id)
                    statuses[normalized_id] = "included"
                    saved_ids.add(normalized_id)
                for paper_id in artifact.payload.get("excluded_paper_ids", []):
                    statuses[normalize_paper_id(paper_id)] = "excluded"
            elif artifact.kind == "PaperCard":
                paper_id = normalize_paper_id(artifact.payload.get("paper_id"))
                if paper_id:
                    statuses[paper_id] = "included"
                    saved_ids.add(paper_id)
                    existing = candidates.get(paper_id, {})
                    candidates[paper_id] = {
                        **artifact.payload,
                        **existing,
                        "paper_id": paper_id,
                        "source": existing.get("source") or "project-paper-card",
                    }

        for identity, candidate in candidates.items():
            paper_id = self._candidate_id(candidate) or identity
            status = statuses.get(paper_id, "candidate")
            self.add_project_paper(
                project_id,
                candidate,
                status=status,
                reason=reasons.get(paper_id, ""),
                saved=paper_id in saved_ids,
            )
        return self.list_project_papers(project_id)

    @staticmethod
    def _parse_bibtex(content: str) -> list[dict[str, Any]]:
        papers: list[dict[str, Any]] = []
        entries: list[str] = []
        for start in (match.start() for match in re.finditer(r"@\w+\s*\{", content)):
            open_brace = content.find("{", start)
            depth = 0
            for index in range(open_brace, len(content)):
                if content[index] == "{":
                    depth += 1
                elif content[index] == "}":
                    depth -= 1
                    if depth == 0:
                        entries.append(content[open_brace + 1 : index])
                        break

        for entry in entries:
            _, separator, body = entry.partition(",")
            if not separator:
                continue
            fields: dict[str, str] = {}
            for field in re.finditer(
                r"(?P<key>\w+)\s*=\s*(?:\{(?P<braced>.*?)\}|\"(?P<quoted>.*?)\")\s*,?",
                body,
                flags=re.DOTALL,
            ):
                fields[field.group("key").casefold()] = (
                    field.group("braced") or field.group("quoted") or ""
                ).strip()
            title = fields.get("title", "")
            if not title:
                continue
            year_match = re.search(r"\d{4}", fields.get("year", ""))
            papers.append(
                {
                    "title": title,
                    "authors": [
                        author.strip()
                        for author in fields.get("author", "").split(" and ")
                        if author.strip()
                    ],
                    "year": int(year_match.group()) if year_match else None,
                    "abstract": fields.get("abstract", ""),
                    "doi": fields.get("doi", ""),
                    "url": fields.get("url") or None,
                    "source": "bibtex",
                }
            )
        return papers

    @staticmethod
    def _parse_ris(content: str) -> list[dict[str, Any]]:
        papers: list[dict[str, Any]] = []
        for block in re.split(r"(?m)^ER\s*-.*$", content):
            fields: dict[str, list[str]] = {}
            for line in block.splitlines():
                match = re.match(r"^([A-Z0-9]{2})\s*-\s*(.*)$", line.strip())
                if match:
                    fields.setdefault(match.group(1), []).append(match.group(2).strip())
            title = next(iter(fields.get("TI") or fields.get("T1") or []), "")
            if not title:
                continue
            year_value = next(iter(fields.get("PY") or fields.get("Y1") or []), "")
            year_match = re.search(r"\d{4}", year_value)
            papers.append(
                {
                    "title": title,
                    "authors": fields.get("AU") or fields.get("A1") or [],
                    "year": int(year_match.group()) if year_match else None,
                    "abstract": next(iter(fields.get("AB") or []), ""),
                    "doi": next(iter(fields.get("DO") or []), ""),
                    "url": next(iter(fields.get("UR") or []), None),
                    "source": "ris",
                }
            )
        return papers

    def import_records(
        self,
        content: str,
        format_name: Literal["bibtex", "ris"],
        tags: list[str] | None = None,
    ) -> list[LibraryPaper]:
        parser = self._parse_bibtex if format_name == "bibtex" else self._parse_ris
        payloads = parser(content)
        if not payloads:
            raise ValueError(f"No valid {format_name.upper()} records found")
        return [self.upsert_paper(payload, saved=True, tags=tags) for payload in payloads]

    def export_records(
        self,
        format_name: Literal["bibtex", "ris"],
        query: str = "",
        library_ids: list[str] | None = None,
    ) -> str:
        papers = self.repository.list_library_papers(query=query, limit=500)
        if library_ids:
            selected_ids = set(library_ids)
            papers = [paper for paper in papers if paper.library_id in selected_ids]
        if format_name == "ris":
            blocks = []
            for paper in papers:
                lines = ["TY  - JOUR", f"TI  - {paper.title}"]
                lines.extend(f"AU  - {author}" for author in paper.authors)
                if paper.year:
                    lines.append(f"PY  - {paper.year}")
                if paper.doi:
                    lines.append(f"DO  - {paper.doi}")
                if paper.url:
                    lines.append(f"UR  - {paper.url}")
                if paper.abstract:
                    lines.append(f"AB  - {paper.abstract}")
                lines.append("ER  -")
                blocks.append("\n".join(lines))
            return "\n\n".join(blocks) + ("\n" if blocks else "")

        entries = []
        for index, paper in enumerate(papers, start=1):
            key = re.sub(r"\W+", "", paper.authors[0] if paper.authors else "paper")
            key = f"{key}{paper.year or ''}_{index}"
            fields = [f"  title = {{{paper.title}}}"]
            if paper.authors:
                fields.append(f"  author = {{{' and '.join(paper.authors)}}}")
            if paper.year:
                fields.append(f"  year = {{{paper.year}}}")
            if paper.doi:
                fields.append(f"  doi = {{{paper.doi}}}")
            if paper.url:
                fields.append(f"  url = {{{paper.url}}}")
            if paper.abstract:
                fields.append(f"  abstract = {{{paper.abstract}}}")
            entries.append(f"@article{{{key},\n" + ",\n".join(fields) + "\n}")
        return "\n\n".join(entries) + ("\n" if entries else "")
