from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pdfplumber


CCF_SOURCE_URL = "https://www.ccf.org.cn/Academic_Evaluation/By_category/"
IEEE_SOURCE_URL = (
    "https://open.ieee.org/wp-content/uploads/IEEE-Title-List-January-2026.pdf"
)
NATURE_SOURCE_URL = (
    "https://www.nature.com/nature-portfolio/about-journals/journal-metrics"
)


# Nature Portfolio's official 2025 metrics table. The first number in each row
# is the Journal Impact Factor. Quartiles are deliberately not inferred because
# the publisher page does not publish JCR quartiles.
NATURE_METRICS = """
Nature|56.1
Nature Communications|18.1
Scientific Reports|4.9
Scientific Data|7.2
Nature Astronomy|15.2
Nature Biomedical Engineering|26.3
Nature Biotechnology|44.5
Nature Cancer|28.0
Nature Catalysis|48.3
Nature Cell Biology|22.7
Nature Chemical Biology|15.8
Nature Chemistry|24.5
Nature Climate Change|26.9
Nature Computational Science|20.3
Nature Ecology & Evolution|17.1
Nature Electronics|42.3
Nature Energy|70.1
Nature Food|24.7
Nature Genetics|25.5
Nature Geoscience|20.6
Nature Human Behaviour|17.5
Nature Immunology|26.5
Nature Machine Intelligence|29.8
Nature Materials|38.0
Nature Medicine|52.5
Nature Metabolism|27.5
Nature Methods|28.3
Nature Mental Health|9.3
Nature Microbiology|18.7
Nature Nanotechnology|37.5
Nature Neuroscience|20.3
Nature Photonics|38.1
Nature Physics|18.0
Nature Plants|15.0
Nature Protocols|18.4
Nature Structural and Molecular Biology|10.1
Nature Sustainability|32.1
Nature Reviews Cancer|60.7
Nature Reviews Cardiology|50.2
Nature Reviews Chemistry|50.3
Nature Reviews Clinical Oncology|94.6
Nature Reviews Disease Primers|79.8
Nature Reviews Drug Discovery|91.2
Nature Reviews Earth & Environment|74.1
Nature Reviews Endocrinology|39.1
Nature Reviews Gastroenterology & Hepatology|57.5
Nature Reviews Genetics|51.4
Nature Reviews Immunology|47.1
Nature Reviews Materials|83.3
Nature Reviews Methods Primers|54.2
Nature Reviews Microbiology|104.6
Nature Reviews Molecular Cell Biology|118.0
Nature Reviews Nephrology|46.7
Nature Reviews Neurology|31.2
Nature Reviews Neuroscience|29.0
Nature Reviews Physics|46.6
Nature Reviews Psychology|22.0
Nature Reviews Rheumatology|33.7
Nature Reviews Urology|13.6
Communications Biology|5.8
Communications Chemistry|6.9
Communications Earth & Environment|9.3
Communications Materials|9.1
Communications Physics|5.5
npj 2D Materials and Applications|9.2
npj Aging|13.0
npj Biofilms & Microbiomes|11.4
npj Breast Cancer|8.4
npj Clean Water|11.0
npj Climate and Atmospheric Science|9.6
npj Computational Materials|13.1
npj Digital Medicine|18.0
npj Flexible Electronics|15.4
npj Genomic Medicine|5.3
npj Materials Degradation|7.5
npj Microgravity|4.9
npj Parkinson's Disease|8.5
npj Precision Oncology|9.9
npj Primary Care Respiratory Medicine|4.7
npj Quantum Information|9.0
npj Quantum Materials|6.6
npj Regenerative Medicine|7.0
npj Science of Food|9.7
npj Science of Learning|4.3
npj Systems Biology and Applications|4.4
npj Urban Sustainability|10.9
npj Vaccines|7.2
npj Ocean Sustainability|8.8
Lab Animal|6.8
NPG Asia Materials|9.0
""".strip()


EXTRA_ALIASES = {
    "Conference on Neural Information Processing Systems": [
        "NeurIPS",
        "NIPS",
        "Neural Information Processing Systems",
    ],
    "IEEE Transactions on Pattern Analysis and Machine Intelligence": [
        "TPAMI",
        "T-PAMI",
        "PAMI",
    ],
    "IEEE/CVF Computer Vision and Pattern Recognition Conference": [
        "CVPR",
        "Computer Vision and Pattern Recognition",
        "IEEE CVPR",
    ],
    "Annual Meeting of the Association for Computational Linguistics": [
        "ACL",
        "ACL Annual Meeting",
    ],
    "International Conference on Learning Representations": ["ICLR"],
    "International Conference on Machine Learning": ["ICML"],
    "International Conference on Computer Vision": ["ICCV"],
    "ACM SIGKDD Conference on Knowledge Discovery and Data Mining": [
        "KDD",
        "ACM KDD",
    ],
    "ACM International Conference on Multimedia": ["ACM MM", "MM"],
    "ACM Conference on Computer and Communications Security": [
        "CCS",
        "ACM CCS",
    ],
    "IEEE Symposium on Security and Privacy": ["S&P", "IEEE S&P", "Oakland"],
    "USENIX Security Symposium": ["USENIX Security"],
    "IEEE Transactions on Geoscience and Remote Sensing": ["TGRS", "T-GRS"],
    "IEEE Transactions on Image Processing": ["TIP", "T-IP"],
    "IEEE Transactions on Knowledge and Data Engineering": ["TKDE", "T-KDE"],
    "IEEE Transactions on Neural Networks and Learning Systems": [
        "TNNLS",
        "T-NNLS",
    ],
}


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def venue_id(name: str, venue_type: str) -> str:
    digest = hashlib.sha1(f"{venue_type}:{normalize(name)}".encode()).hexdigest()[:16]
    return f"VEN-{digest}"


def source(label: str, url: str, year: int) -> dict[str, Any]:
    return {"label": label, "url": url, "year": year}


def clean_aliases(values: list[str]) -> list[str]:
    aliases: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = re.sub(r"\s+", " ", str(raw).replace("\n", " ")).strip()
        key = normalize(value)
        if not value or not key or key in seen:
            continue
        seen.add(key)
        aliases.append(value)
    return aliases


def new_record(name: str, venue_type: str) -> dict[str, Any]:
    return {
        "venue_id": venue_id(name, venue_type),
        "canonical_name": name,
        "venue_type": venue_type,
        "acronym": "",
        "aliases": clean_aliases([name, *EXTRA_ALIASES.get(name, [])]),
        "publisher": "",
        "ccf_rank": None,
        "ccf_category": None,
        "ccf_year": None,
        "sci_quartile": None,
        "index_name": None,
        "impact_factor": None,
        "impact_factor_year": None,
        "nature_portfolio": False,
        "sources": [],
    }


def record_key(name: str, venue_type: str) -> tuple[str, str]:
    return venue_type, normalize(name)


def merge_record(
    records: dict[tuple[str, str], dict[str, Any]],
    name: str,
    venue_type: str,
    *,
    acronym: str = "",
) -> dict[str, Any]:
    direct_key = record_key(name, venue_type)
    record = records.get(direct_key)
    if record is None and acronym:
        acronym_key = normalize(acronym)
        for candidate in records.values():
            if candidate["venue_type"] != venue_type:
                continue
            if acronym_key and acronym_key in {
                normalize(candidate.get("acronym") or ""),
                *(normalize(item) for item in candidate.get("aliases", [])),
            }:
                record = candidate
                break
    if record is None:
        record = new_record(name, venue_type)
        records[direct_key] = record
    record["aliases"] = clean_aliases(
        [
            *record["aliases"],
            name,
            acronym,
            *EXTRA_ALIASES.get(name, []),
        ]
    )
    if acronym and not record["acronym"]:
        record["acronym"] = acronym
    return record


def add_ccf(
    records: dict[tuple[str, str], dict[str, Any]],
    ccf_json: Path,
) -> None:
    data = json.loads(ccf_json.read_text(encoding="utf-8-sig"))
    categories = {
        int(item["id"]): f"{item['chinese']} / {item['english']}"
        for item in data["category"]
    }
    for key, venue_type in (("journals", "journal"), ("conferences", "conference")):
        for item in data[key]:
            if item.get("rank") != "A":
                continue
            record = merge_record(
                records,
                str(item["name"]).strip(),
                venue_type,
                acronym=str(item.get("abbr") or "").strip(),
            )
            record["publisher"] = str(item.get("publisher") or record["publisher"])
            record["ccf_rank"] = "A"
            record["ccf_category"] = categories[int(item["category_id"])]
            record["ccf_year"] = 2026
            record["sources"].append(
                source("CCF推荐国际学术会议和期刊目录（第七版）", CCF_SOURCE_URL, 2026)
            )


def add_ieee(
    records: dict[tuple[str, str], dict[str, Any]],
    ieee_pdf: Path,
) -> None:
    with pdfplumber.open(ieee_pdf) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                continue
            for row in tables[0][2:]:
                if len(row) < 13:
                    continue
                acronym, name, _, _, _, index_name, jif, _, quartile = row[:9]
                name = re.sub(r"\s+", " ", str(name or "").replace("\n", " ")).strip()
                acronym = str(acronym or "").strip()
                quartile = str(quartile or "").strip()
                if not name or quartile not in {"Q1", "Q2"}:
                    continue
                try:
                    impact_factor = float(jif)
                except (TypeError, ValueError):
                    continue
                record = merge_record(
                    records,
                    name,
                    "journal",
                    acronym=acronym,
                )
                record["publisher"] = record["publisher"] or "IEEE"
                record["sci_quartile"] = quartile
                record["index_name"] = str(index_name or "").strip() or None
                record["impact_factor"] = impact_factor
                record["impact_factor_year"] = 2024
                record["sources"].append(
                    source("IEEE Title List (January 2026)", IEEE_SOURCE_URL, 2026)
                )


def add_nature(records: dict[tuple[str, str], dict[str, Any]]) -> None:
    for line in NATURE_METRICS.splitlines():
        name, impact_factor = line.rsplit("|", maxsplit=1)
        record = merge_record(records, name.strip(), "journal")
        record["publisher"] = record["publisher"] or "Springer Nature"
        record["nature_portfolio"] = True
        record["impact_factor"] = float(impact_factor)
        record["impact_factor_year"] = 2025
        record["sources"].append(
            source("Nature Portfolio 2025 Journal Metrics", NATURE_SOURCE_URL, 2025)
        )


def dedupe_sources(record: dict[str, Any]) -> None:
    seen: set[tuple[str, str, int]] = set()
    sources = []
    for item in record["sources"]:
        key = (item["label"], item["url"], int(item["year"]))
        if key in seen:
            continue
        seen.add(key)
        sources.append(item)
    record["sources"] = sources


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ccf-json", type=Path, required=True)
    parser.add_argument("--ieee-pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records: dict[tuple[str, str], dict[str, Any]] = {}
    add_ccf(records, args.ccf_json)
    add_ieee(records, args.ieee_pdf)
    add_nature(records)
    venues = list({item["venue_id"]: item for item in records.values()}.values())
    for item in venues:
        dedupe_sources(item)
        item["aliases"] = clean_aliases(item["aliases"])
    venues.sort(key=lambda item: (item["venue_type"], item["canonical_name"].casefold()))
    payload = {
        "schema_version": 1,
        "generated_from": [
            source("CCF推荐国际学术会议和期刊目录（第七版）", CCF_SOURCE_URL, 2026),
            source("IEEE Title List (January 2026)", IEEE_SOURCE_URL, 2026),
            source("Nature Portfolio 2025 Journal Metrics", NATURE_SOURCE_URL, 2025),
        ],
        "venues": venues,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "venues": len(venues),
                "ccf_a": sum(item["ccf_rank"] == "A" for item in venues),
                "q1": sum(item["sci_quartile"] == "Q1" for item in venues),
                "q2": sum(item["sci_quartile"] == "Q2" for item in venues),
                "nature_portfolio": sum(item["nature_portfolio"] for item in venues),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
