"""Shared utility helpers used across the project."""
from __future__ import annotations

import re

# Words that strongly suggest a field contains a location, not a company name.
_LOCATION_KEYWORDS = re.compile(
    r"\b(remote|on.?site|hybrid|fully\s+remote|anywhere|worldwide|europe|emea|eu|"
    r"poland|germany|ukraine|france|spain|uk|usa|us|"
    r"united\s+states|united\s+kingdom|netherlands|czech\s+republic|czechia|czech|austria|sweden|"
    r"portugal|romania|lithuania|"
    r"warsaw|warszawa|krakow|kraków|wroclaw|wrocław|gdansk|gdańsk|poznan|poznań|lodz|łódź|"
    r"berlin|munich|london|paris|amsterdam|kyiv|lviv|prague|vienna|lisbon|madrid)\b",
    re.IGNORECASE,
)
# Typical location patterns like "City (Remote)" or "Country (On-site)"
_LOCATION_PATTERN = re.compile(r"^[^(]+\((remote|on.?site|hybrid)\)\s*$", re.IGNORECASE)


def looks_like_location(name: str) -> bool:
    """
    Return True when *name* looks like a location string rather than a company
    name (e.g. "Poland (Remote)", "Warsaw, Poland", "On-site, Germany").

    Used to guard against scrapers that accidentally put location data in the
    company field for anonymous / staffing-agency postings.

    A company whose name merely contains a place ("Berlin Brands Group",
    "London Stock Exchange Group", "Allegro Poland") is not a location: the
    string must be made only of location words, plus at most one unknown
    word (a city missing from the list) next to a separator, as in
    "Gdynia, Poland".
    """
    name = name.strip()
    if not name:
        return False
    if _LOCATION_PATTERN.match(name):
        return True
    if not _LOCATION_KEYWORDS.search(name):
        return False
    leftover = re.findall(r"[^\W\d_]+", _LOCATION_KEYWORDS.sub(" ", name))
    if not leftover:
        return True
    has_separator = bool(re.search(r"[,/|()]", name))
    return len(leftover) == 1 and has_separator


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
