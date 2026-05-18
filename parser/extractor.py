"""
Normalises raw scraper output into JobListing dataclasses.

Each scraper produces a list of raw dicts.  This module:
  1. Strips HTML from the description field.
  2. Extracts technology keywords into the skills list.
  3. Parses / normalises the posted_at date.
  4. Transliterates non-ASCII location strings to ASCII.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup
from unidecode import unidecode

from parser.schema import JobListing

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tech keyword list (expanded as needed)
# ---------------------------------------------------------------------------
_TECH_TERMS: set[str] = {
    "python", "go", "golang", "rust", "java", "kotlin", "scala", "c++", "c#",
    "javascript", "typescript", "nodejs", "node.js", "react", "vue", "angular",
    "svelte", "next.js", "nuxt", "django", "flask", "fastapi", "spring", "rails",
    "postgresql", "postgres", "mysql", "sqlite", "mongodb", "redis", "elasticsearch",
    "kafka", "rabbitmq", "grpc", "graphql", "rest", "openapi", "swagger",
    "docker", "kubernetes", "k8s", "helm", "terraform", "ansible", "chef", "puppet",
    "aws", "gcp", "azure", "s3", "ec2", "lambda", "cloudfront", "bigquery",
    "linux", "bash", "git", "ci/cd", "github actions", "gitlab ci", "jenkins",
    "prometheus", "grafana", "datadog", "sentry", "elk", "opensearch",
    "machine learning", "ml", "deep learning", "pytorch", "tensorflow", "scikit-learn",
    "llm", "openai", "langchain", "vector database", "pinecone", "weaviate",
    "airflow", "spark", "flink", "dbt", "snowflake", "databricks",
    "microservices", "monorepo", "event-driven", "cqrs", "ddd",
    "agile", "scrum", "kanban", "jira", "confluence",
}

_TECH_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(t) for t in sorted(_TECH_TERMS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
]

_RELATIVE_RE = re.compile(r"(\d+)\s*(hour|day|week|month)s?\s*ago", re.IGNORECASE)


def _parse_date(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        # Unix ms timestamp
        ts = raw / 1000 if raw > 1e10 else raw
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if not isinstance(raw, str) or not raw.strip():
        return datetime.now(timezone.utc)

    raw = raw.strip()

    m = _RELATIVE_RE.search(raw)
    if m:
        from datetime import timedelta
        n, unit = int(m.group(1)), m.group(2).lower()
        delta_map = {"hour": timedelta(hours=1), "day": timedelta(days=1),
                     "week": timedelta(weeks=1), "month": timedelta(days=30)}
        return datetime.now(timezone.utc) - delta_map[unit] * n

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass

    log.debug("Could not parse date %r; using now", raw)
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text(separator=" ", strip=True)


# ---------------------------------------------------------------------------
# Skill extraction
# ---------------------------------------------------------------------------

def _extract_skills(text: str) -> list[str]:
    found = {m.group(0).lower() for m in _TECH_PATTERN.finditer(text)}
    # Normalise common aliases
    aliases = {
        "golang": "go", "postgres": "postgresql", "nodejs": "node.js",
        "k8s": "kubernetes",
    }
    normalised = {aliases.get(s, s) for s in found}
    return sorted(normalised)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalise(raw: dict, source: str) -> JobListing | None:
    """
    Convert a raw scraper dict into a JobListing.
    Returns None and logs a warning on any validation failure.
    """
    try:
        description_raw = raw.get("description") or raw.get("body") or ""
        description = _strip_html(description_raw) if "<" in description_raw else description_raw

        title = (raw.get("title") or "").strip()
        company = (raw.get("company") or raw.get("companyName") or "").strip()

        if not title or not company:
            log.warning("Skipping listing with missing title/company: %r", raw.get("url"))
            return None

        location_raw = (
            raw.get("location") or raw.get("city") or raw.get("cities") or ""
        )
        location = unidecode(str(location_raw)).strip()

        apply_url = (raw.get("apply_url") or raw.get("applyUrl") or raw.get("url") or "").strip()
        url = (raw.get("url") or apply_url).strip()

        return JobListing(
            source=source,
            title=title,
            company=company,
            url=url,
            apply_url=apply_url,
            description=description,
            skills=_extract_skills(description),
            location=location,
            salary_raw=str(raw.get("salary_raw") or raw.get("salary") or ""),
            posted_at=_parse_date(raw.get("posted_at") or raw.get("publishedAt") or raw.get("listedAt")),
        )
    except Exception as exc:
        log.warning("Failed to normalise listing %r: %s", raw.get("url"), exc)
        return None


def normalise_many(raws: list[dict], source: str) -> list[JobListing]:
    results = []
    for raw in raws:
        listing = normalise(raw, source)
        if listing:
            results.append(listing)
    return results
