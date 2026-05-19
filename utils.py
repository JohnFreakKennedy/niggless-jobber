"""Shared utility helpers used across the project."""
from __future__ import annotations

import re


def sanitize_name(text: str, max_len: int = 25) -> str:
    """
    Return a filesystem-safe, underscore-delimited version of *text*.

    - Strips characters that are unsafe on any major OS.
    - Collapses runs of whitespace and hyphens into a single underscore.
    - Collapses consecutive underscores.
    - Trims to *max_len* characters.
    """
    text = re.sub(r"[^\w\s-]", "", text).strip()
    text = re.sub(r"[\s-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:max_len].strip("_")


def artifact_stem(company: str, kind: str) -> str:
    """
    Return the base filename stem for a job artifact.

    Examples:
        artifact_stem("Google", "cv")            -> "Google_cv"
        artifact_stem("Acme Corp", "cover_letter") -> "Acme_Corp_cover_letter"
        artifact_stem("",  "cv")                 -> "Unknown_cv"
    """
    company_part = sanitize_name(company) or "Unknown"
    return f"{company_part}_{kind}"
