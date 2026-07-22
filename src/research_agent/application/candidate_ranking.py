from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any


SOURCE_WEIGHTS = {"OpenAlex": 0.50, "Semantic Scholar": 0.35, "Crossref": 0.15}
MOMENTUM_SOURCE_WEIGHTS = {
    "OpenAlex": 0.60,
    "Semantic Scholar": 0.30,
    "Crossref": 0.10,
}
COMPONENT_WEIGHTS = {
    "relevance": 0.55,
    "impact": 0.30,
    "authority": 0.10,
    "diversity": 0.05,
}


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp01(value: Any) -> float | None:
    number = _finite_float(value)
    if number is None:
        return None
    if 1.0 < number <= 100.0:
        number /= 100.0
    return min(1.0, max(0.0, number))


def _non_negative(value: Any) -> float | None:
    number = _finite_float(value)
    if number is None:
        return None
    return max(0.0, number)


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in re.findall(r"[\w]+", str(value or "").casefold(), flags=re.UNICODE)
        if len(token) > 1
    }


def _candidate_tokens(candidate: Mapping[str, Any]) -> set[str]:
    return _tokens(
        " ".join(
            [
                str(candidate.get("title") or ""),
                str(candidate.get("abstract") or "")[:1000],
                str(candidate.get("venue") or ""),
                *[str(item) for item in candidate.get("fields_of_study") or []],
            ]
        )
    )


def _relevance(candidate: Mapping[str, Any], queries: Sequence[str]) -> float:
    title_tokens = _tokens(candidate.get("title"))
    abstract_tokens = _tokens(candidate.get("abstract"))
    scores: list[float] = []
    for query in queries:
        query_tokens = _tokens(query)
        if not query_tokens:
            continue
        title_coverage = len(query_tokens & title_tokens) / len(query_tokens)
        abstract_coverage = len(query_tokens & abstract_tokens) / len(query_tokens)
        scores.append(0.70 * title_coverage + 0.30 * abstract_coverage)
    if not scores:
        existing = _finite_float(candidate.get("relevance_score"))
        return min(100.0, max(0.0, existing or 0.0))
    return round(100.0 * (0.70 * max(scores) + 0.30 * (sum(scores) / len(scores))), 2)


def _cohort_key(candidate: Mapping[str, Any]) -> tuple[str, int | None, str]:
    fields = candidate.get("fields_of_study") or []
    field = str(fields[0]).casefold().strip() if fields else ""
    try:
        year = int(candidate.get("year")) if candidate.get("year") is not None else None
    except (TypeError, ValueError):
        year = None
    publication_type = str(candidate.get("publication_type") or "").casefold().strip()
    return field, year, publication_type


def _comparison_indices(
    candidates: Sequence[Mapping[str, Any]],
    index: int,
    values: Sequence[float | None],
) -> list[int]:
    field, year, publication_type = _cohort_key(candidates[index])
    available = [position for position, value in enumerate(values) if value is not None]
    if len(available) <= 1:
        return available

    def matches(position: int, *, year_window: int | None, require_field: bool) -> bool:
        other_field, other_year, other_type = _cohort_key(candidates[position])
        if require_field and field and other_field and field != other_field:
            return False
        if publication_type and other_type and publication_type != other_type:
            return False
        if year_window is not None and year is not None and other_year is not None:
            return abs(year - other_year) <= year_window
        return True

    for year_window, require_field, minimum in (
        (0, True, 5),
        (1, True, 5),
        (None, True, 5),
        (1, False, 5),
        (None, False, 2),
    ):
        cohort = [
            position
            for position in available
            if matches(position, year_window=year_window, require_field=require_field)
        ]
        if len(cohort) >= minimum:
            return cohort
    return available


def _empirical_percentile(value: float, population: Sequence[float]) -> float:
    if len(population) <= 1:
        # A single unnormalized count contains weak evidence. Use a conservative,
        # saturating annual-impact proxy instead of treating it as either 0 or 100.
        return 1.0 - math.exp(-max(0.0, value) / 20.0)
    lower = sum(item < value for item in population)
    equal = sum(item == value for item in population)
    return (lower + 0.5 * equal) / len(population)


def _fill_source_percentiles(candidates: list[dict[str, Any]]) -> None:
    for source in SOURCE_WEIGHTS:
        counts = [
            _non_negative((candidate.get("citation_counts") or {}).get(source))
            for candidate in candidates
        ]
        for index, candidate in enumerate(candidates):
            percentiles = dict(candidate.get("citation_percentiles") or {})
            existing = _clamp01(percentiles.get(source))
            if existing is not None:
                percentiles[source] = existing
                candidate["citation_percentiles"] = percentiles
                continue
            count = counts[index]
            if count is None:
                continue
            cohort = _comparison_indices(candidates, index, counts)
            population = [counts[position] for position in cohort if counts[position] is not None]
            percentiles[source] = round(_empirical_percentile(count, population), 6)
            candidate["citation_percentiles"] = percentiles


def _fill_influential_percentiles(candidates: list[dict[str, Any]]) -> None:
    values = [
        _non_negative(
            (candidate.get("influential_citation_counts") or {}).get("Semantic Scholar")
        )
        for candidate in candidates
    ]
    for index, candidate in enumerate(candidates):
        value = values[index]
        if value is None:
            continue
        cohort = _comparison_indices(candidates, index, values)
        population = [values[position] for position in cohort if values[position] is not None]
        candidate["influential_citation_percentile"] = round(
            _empirical_percentile(value, population), 6
        )


def _fill_momentum_percentiles(candidates: list[dict[str, Any]]) -> None:
    for source in MOMENTUM_SOURCE_WEIGHTS:
        velocities = [
            _non_negative((candidate.get("recent_citation_velocities") or {}).get(source))
            for candidate in candidates
        ]
        for index, candidate in enumerate(candidates):
            velocity = velocities[index]
            if velocity is None:
                continue
            cohort = _comparison_indices(candidates, index, velocities)
            population = [
                velocities[position]
                for position in cohort
                if velocities[position] is not None
            ]
            percentiles = dict(candidate.get("momentum_percentiles") or {})
            percentiles[source] = round(_empirical_percentile(velocity, population), 6)
            candidate["momentum_percentiles"] = percentiles


def _weighted_available(
    values: Mapping[str, Any], weights: Mapping[str, float]
) -> tuple[float | None, float]:
    numerator = 0.0
    denominator = 0.0
    for source, weight in weights.items():
        value = _clamp01(values.get(source))
        if value is None:
            continue
        numerator += weight * value
        denominator += weight
    if denominator <= 0:
        return None, 0.0
    return numerator / denominator, denominator


def _impact(candidate: dict[str, Any]) -> tuple[float | None, float, list[str]]:
    citation, citation_coverage = _weighted_available(
        candidate.get("citation_percentiles") or {}, SOURCE_WEIGHTS
    )
    influential = _clamp01(candidate.get("influential_citation_percentile"))
    momentum, momentum_coverage = _weighted_available(
        candidate.get("momentum_percentiles") or {}, MOMENTUM_SOURCE_WEIGHTS
    )
    components = (
        (citation, 0.65, "领域归一化引用"),
        (influential, 0.20, "高价值引用"),
        (momentum, 0.15, "近期引用增长"),
    )
    numerator = sum(weight * value for value, weight, _ in components if value is not None)
    denominator = sum(weight for value, weight, _ in components if value is not None)
    if denominator <= 0:
        return None, 0.0, ["暂无可靠的论文级影响数据"]

    available_percentiles = [
        value
        for value in (
            _clamp01(item)
            for item in (candidate.get("citation_percentiles") or {}).values()
        )
        if value is not None
    ]
    if len(available_percentiles) >= 2:
        agreement = 1.0 - min(
            1.0, 2.0 * (max(available_percentiles) - min(available_percentiles))
        )
    else:
        agreement = 0.5
    channel_coverage = citation_coverage
    component_coverage = denominator
    confidence = 100.0 * (
        0.55 * channel_coverage + 0.25 * component_coverage + 0.20 * agreement
    )
    notes = [
        f"{label} {round(value * 100)}"
        for value, _weight, label in components
        if value is not None
    ]
    if citation_coverage < 1.0:
        notes.append("部分检索渠道未提供影响指标，已按可用数据重加权")
    if momentum_coverage == 0:
        notes.append("暂无近期引用增长数据")
    return round(100.0 * numerator / denominator, 2), round(confidence, 2), notes


def _authority(candidate: Mapping[str, Any]) -> tuple[float | None, list[str]]:
    values: list[tuple[float, str]] = []
    ccf = str(candidate.get("ccf_rank") or "").upper()
    if ccf in {"A", "B", "C"}:
        score = {"A": 100.0, "B": 75.0, "C": 55.0}[ccf]
        values.append((score, f"CCF-{ccf}"))
    quartile = str(candidate.get("sci_quartile") or "").upper()
    if quartile in {"Q1", "Q2", "Q3", "Q4"}:
        score = {"Q1": 90.0, "Q2": 70.0, "Q3": 50.0, "Q4": 30.0}[quartile]
        values.append((score, f"JCR {quartile}"))
    if candidate.get("nature_portfolio"):
        values.append((95.0, "Nature Portfolio"))
    if not values:
        return None, ["暂无适用于该领域的场馆评级"]
    score, _ = max(values, key=lambda item: item[0])
    return score, [label for _, label in values]


def _composite(candidate: Mapping[str, Any], diversity: float) -> float:
    values = {
        "relevance": _finite_float(candidate.get("relevance_score")),
        "impact": _finite_float(candidate.get("impact_score")),
        "authority": _finite_float(candidate.get("authority_score")),
        "diversity": diversity,
    }
    numerator = 0.0
    denominator = 0.0
    for name, weight in COMPONENT_WEIGHTS.items():
        value = values[name]
        if value is None:
            continue
        numerator += weight * min(100.0, max(0.0, value))
        denominator += weight
    return round(numerator / denominator, 2) if denominator else 0.0


def _novelty(candidate: Mapping[str, Any], selected: Sequence[Mapping[str, Any]]) -> float:
    if not selected:
        return 100.0
    tokens = _candidate_tokens(candidate)
    if not tokens:
        return 50.0
    similarities = []
    for item in selected:
        other = _candidate_tokens(item)
        union = tokens | other
        similarities.append(len(tokens & other) / len(union) if union else 0.0)
    return round(100.0 * (1.0 - max(similarities, default=0.0)), 2)


def _decision_priority(candidate: Mapping[str, Any]) -> int:
    decision = str(candidate.get("agent_decision") or "").casefold()
    return {"include": 0, "uncertain": 1, "exclude": 2}.get(decision, 0)


def rank_candidates(
    candidates: Sequence[Mapping[str, Any]],
    queries: Sequence[str],
    *,
    decisions: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Score and greedily rerank candidates with missing-data-safe components."""
    prepared = [dict(candidate) for candidate in candidates]
    if not prepared:
        return []
    normalized_decisions = {
        str(key).casefold().strip(): str(value).casefold().strip()
        for key, value in (decisions or {}).items()
    }
    for candidate in prepared:
        identity = str(candidate.get("paper_id") or candidate.get("doi") or "")
        decision = normalized_decisions.get(identity.casefold().strip())
        if decision:
            candidate["agent_decision"] = decision
        candidate["relevance_score"] = _relevance(candidate, queries)

    _fill_source_percentiles(prepared)
    _fill_influential_percentiles(prepared)
    _fill_momentum_percentiles(prepared)
    for candidate in prepared:
        impact, confidence, impact_notes = _impact(candidate)
        authority, authority_notes = _authority(candidate)
        candidate["impact_score"] = impact
        candidate["impact_confidence"] = confidence
        candidate["authority_score"] = authority
        candidate["impact_explanation"] = impact_notes
        candidate["authority_explanation"] = authority_notes
        candidate["is_retracted"] = bool(candidate.get("is_retracted"))

    remaining = list(prepared)
    ranked: list[dict[str, Any]] = []
    while remaining:
        scored = []
        for original_index, candidate in enumerate(remaining):
            diversity = _novelty(candidate, ranked)
            score = _composite(candidate, diversity)
            if candidate.get("is_retracted"):
                # Preserve the measured impact, but keep retracted work out of
                # default recommendations unless the user explicitly finds it.
                score = min(score, 5.0)
            scored.append(
                (
                    _decision_priority(candidate),
                    -score,
                    -float(candidate.get("relevance_score") or 0),
                    -float(candidate.get("impact_score") or 0),
                    -int(candidate.get("year") or 0),
                    str(candidate.get("title") or "").casefold(),
                    original_index,
                    diversity,
                    score,
                    candidate,
                )
            )
        *_, original_index, diversity, score, candidate = min(scored)
        remaining.pop(original_index)
        candidate["diversity_score"] = diversity
        candidate["composite_score"] = score
        candidate["ranking_explanation"] = [
            f"相关性 {candidate['relevance_score']:.0f}",
            (
                f"影响程度 {candidate['impact_score']:.0f}"
                if candidate.get("impact_score") is not None
                else "影响程度暂无数据"
            ),
            (
                f"场馆权威性 {candidate['authority_score']:.0f}"
                if candidate.get("authority_score") is not None
                else "场馆权威性暂无适用评级"
            ),
            f"候选差异性 {diversity:.0f}",
        ]
        ranked.append(candidate)
    return ranked


def recent_citation_velocity(counts_by_year: Sequence[Mapping[str, Any]]) -> float | None:
    current_year = datetime.now(UTC).year
    values = []
    for item in counts_by_year:
        try:
            year = int(item.get("year"))
            count = max(0.0, float(item.get("cited_by_count") or 0))
        except (TypeError, ValueError):
            continue
        if year in {current_year - 1, current_year - 2}:
            values.append(count)
    return sum(values) / len(values) if values else None
