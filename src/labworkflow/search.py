"""Full-text-ish search over experiment metadata and README files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .registry import load_for_read
from .util import ALL_STATUSES, truncate
from .workspace import Workspace

README_LIMIT = 200_000


@dataclass
class SearchHit:
    record: dict[str, Any]
    snippet: str
    path: Path | None
    matched: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.record,
            "snippet": self.snippet,
            "exists": bool(self.path and self.path.is_dir()),
            "matched": self.matched,
        }


@dataclass
class SearchReport:
    hits: list[SearchHit]
    total_before_limit: int
    query: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "count": len(self.hits),
            "total": self.total_before_limit,
            "hits": [hit.to_dict() for hit in self.hits],
        }


def _record_haystack(record: dict[str, Any]) -> str:
    values: list[str] = [
        str(record.get("id", "")),
        str(record.get("name", "")),
        str(record.get("slug", "")),
        str(record.get("template", "")),
        str(record.get("category", "")),
        str(record.get("description", "")),
        str(record.get("summary", "")),
        str(record.get("conclusion", "")),
    ]
    for field in ("tags", "technologies", "related_projects"):
        values.extend(str(item) for item in record.get(field, []) or [])
    return "\n".join(values)


def _read_readme(path: Path | None) -> str:
    if path is None:
        return ""
    readme = path / "README.md"
    if not readme.is_file():
        return ""
    try:
        with open(readme, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(README_LIMIT)
    except OSError:
        return ""


def _snippet(terms: list[str], record: dict[str, Any], readme: str) -> str:
    candidates = [
        str(record.get("summary", "")),
        str(record.get("conclusion", "")),
        str(record.get("description", "")),
        readme,
    ]
    lowered_terms = [term.casefold() for term in terms]
    for text in candidates:
        if not text:
            continue
        lowered = text.casefold()
        positions = [lowered.find(term) for term in lowered_terms if lowered.find(term) >= 0]
        if not positions:
            continue
        index = min(positions)
        start = max(0, index - 40)
        end = min(len(text), index + 90)
        piece = " ".join(text[start:end].split())
        if start > 0:
            piece = "…" + piece
        if end < len(text):
            piece += "…"
        return truncate(piece, 120)
    fallback = (
        record.get("summary")
        or record.get("description")
        or record.get("name")
        or ", ".join(record.get("tags", []) or [])
        or ""
    )
    return truncate(str(fallback), 120)


def search_experiments(
    workspace: Workspace,
    query: str = "",
    *,
    statuses: list[str] | None = None,
    tags: list[str] | None = None,
    template: str | None = None,
    category: str | None = None,
    technology: list[str] | None = None,
    limit: int = 20,
) -> SearchReport:
    records = load_for_read(workspace)
    terms = [term for term in str(query or "").casefold().split() if term]
    wanted_statuses = set(statuses or ALL_STATUSES)
    wanted_tags = {tag.casefold() for tag in tags or []}
    wanted_tech = {item.casefold() for item in technology or [] if item}
    hits: list[SearchHit] = []

    for record in records:
        if record.get("status") not in wanted_statuses:
            continue
        if wanted_tags and not wanted_tags.issubset(
            {str(tag).casefold() for tag in record.get("tags", []) or []}
        ):
            continue
        if template and str(record.get("template", "")).casefold() != template.casefold():
            continue
        if category and str(record.get("category", "")).casefold() != category.casefold():
            continue
        if wanted_tech and not wanted_tech.issubset(
            {str(item).casefold() for item in record.get("technologies", []) or []}
        ):
            continue

        try:
            path = workspace.experiment_path(record)
        except Exception:
            path = None
        readme = _read_readme(path) if path and path.is_dir() else ""
        haystack = (_record_haystack(record) + "\n" + readme).casefold()
        if terms and not all(term in haystack for term in terms):
            continue
        matched = [term for term in terms if term in haystack]
        hits.append(
            SearchHit(
                record=record,
                snippet=_snippet(terms, record, readme) if terms else truncate(
                    record.get("summary") or record.get("description") or "", 120
                ),
                path=path if path and path.is_dir() else None,
                matched=matched,
            )
        )

    hits.sort(
        key=lambda hit: (str(hit.record.get("created_at", "")), str(hit.record.get("id", ""))),
        reverse=True,
    )
    total = len(hits)
    if limit > 0:
        hits = hits[:limit]
    return SearchReport(hits=hits, total_before_limit=total, query=query)
