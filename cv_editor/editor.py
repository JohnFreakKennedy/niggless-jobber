"""CV tailoring: reads base CV, calls AI, writes patched .tex to output dir."""
from __future__ import annotations

import difflib
import json
import logging
import math
import re
from datetime import date
from pathlib import Path

from cv_editor.client import AIClient
from cv_editor.prompts import CV_PROMPT_VERSION, CV_SYSTEM_PROMPT, CV_USER_TEMPLATE
from parser.schema import JobListing

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent
_TEMPLATES_DIR = _REPO_ROOT / "templates"
_BASE_CV = _TEMPLATES_DIR / "cv.tex"
_PERSONAL_INFO_CANDIDATES = [
    _REPO_ROOT / "personal_info.json",
    Path.home() / ".niggless-jobber" / "personal_info.json",
]

# If more than this fraction of lines differ, treat as hallucination.
# Slightly higher than default to accommodate the years-of-experience edit.
_MAX_DIFF_FRACTION = 0.35

# Patterns that express years of experience in a CV summary paragraph.
# Matches: "3 years", "3+ years", "over 3 years", "more than 3 years"
_YEARS_PATTERN = re.compile(
    r"(over\s+|more\s+than\s+|approximately\s+)?(\d+)\+?\s+years?\s+of\s+experience",
    re.IGNORECASE,
)

# Patterns in a job description that express a required minimum experience.
# Handles: "5+ years", "at least 3 years", "minimum 4 years",
#          "5 years of experience", "3 years of relevant experience",
#          "2 years of hands-on cloud experience"
_JD_YEARS_PATTERN = re.compile(
    r"(?:at\s+least\s+|minimum\s+)?(\d+)\+?\s+years?(?:\s+of)?\s+(?:\w+\s+){0,3}experience",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Personal info loader
# ---------------------------------------------------------------------------

def _load_personal_info() -> dict:
    for path in _PERSONAL_INFO_CANDIDATES:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


# ---------------------------------------------------------------------------
# Experience calculation
# ---------------------------------------------------------------------------

def _parse_ym(date_str: str) -> date | None:
    """Parse 'YYYY-MM' or 'present' into a date (1st of the month)."""
    if not date_str:
        return None
    if date_str.strip().lower() == "present":
        return date.today()
    try:
        parts = date_str.strip().split("-")
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        return date(year, month, 1)
    except (ValueError, IndexError):
        return None


def calculate_experience_years(work_history: list[dict]) -> float:
    """
    Sum up non-overlapping months across all work_history entries.
    Overlapping periods (e.g. two simultaneous jobs) are counted once.
    Returns total years as a float rounded to one decimal place.
    """
    intervals: list[tuple[date, date]] = []
    for job in work_history:
        start = _parse_ym(job.get("start", ""))
        end = _parse_ym(job.get("end", "present"))
        if start and end and end >= start:
            intervals.append((start, end))

    if not intervals:
        return 0.0

    # Merge overlapping intervals to avoid double-counting
    intervals.sort(key=lambda x: x[0])
    merged: list[tuple[date, date]] = [intervals[0]]
    for start, end in intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    total_days = sum((e - s).days for s, e in merged)
    years = total_days / 365.25
    return round(years, 1)


def _display_years(actual: float) -> str:
    """
    Convert float years to a human-readable string for the CV.
    4.8 -> "5"   (rounds to nearest integer)
    4.0 -> "4"
    """
    return str(round(actual))


# ---------------------------------------------------------------------------
# Job description parser for required years
# ---------------------------------------------------------------------------

def extract_required_years(description: str) -> int | None:
    """
    Extract the minimum years of experience stated in the job description.
    Returns the highest found requirement (most conservative), or None.
    """
    matches = _JD_YEARS_PATTERN.findall(description)
    if not matches:
        return None
    values = [int(m) for m in matches if m.isdigit()]
    return max(values) if values else None


# ---------------------------------------------------------------------------
# Pre-AI years substitution (deterministic, no hallucination risk)
# ---------------------------------------------------------------------------

def patch_years_in_tex(content: str, display_years: str) -> str:
    """
    Replace any 'X years of experience' pattern in the TeX source with
    the correct calculated value.  This runs before the AI call so even
    if the AI makes no change the number is still corrected.
    """
    def replacer(m: re.Match) -> str:
        return f"{display_years} years of experience"

    patched, n = _YEARS_PATTERN.subn(replacer, content)
    if n:
        log.debug("Pre-patched %d years-of-experience occurrence(s) -> %s years", n, display_years)
    return patched


# ---------------------------------------------------------------------------
# Output sanitizer
# ---------------------------------------------------------------------------

def _sanitize_tex(content: str) -> str:
    """
    Clean up AI output before writing to disk:
    - Strip everything after the first \\end{document} (AI sometimes appends
      stray template fragments).
    - Remove lines that contain only a '#' character (broken placeholder).
    """
    # Truncate after first \end{document}
    end_marker = r"\end{document}"
    idx = content.find(end_marker)
    if idx != -1:
        content = content[: idx + len(end_marker)] + "\n"

    # Remove lines that are just whitespace + '#' (leaked placeholder)
    lines = content.splitlines()
    lines = [ln for ln in lines if not re.fullmatch(r"\s*#\s*", ln)]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def tailor_cv(
    listing: JobListing,
    output_dir: Path,
    ai_client: AIClient,
) -> Path:
    """
    Generate a tailored cv.tex for *listing* in *output_dir*.
    Returns the path to the written file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "cv.tex"

    # ------------------------------------------------------------------
    # 1. Load base CV and personal info
    # ------------------------------------------------------------------
    base_content = _BASE_CV.read_text(encoding="utf-8")
    info = _load_personal_info()
    work_history = info.get("work_history", [])

    actual_years = calculate_experience_years(work_history)
    display = _display_years(actual_years)
    required_years = extract_required_years(listing.description)

    log.info(
        "Job %s: actual experience=%.1f yrs (shown as '%s'), job requires=%s yrs",
        listing.id[:12],
        actual_years,
        display,
        required_years or "unspecified",
    )

    # ------------------------------------------------------------------
    # 2. Deterministic pre-patch: fix years before touching AI
    # ------------------------------------------------------------------
    pre_patched = patch_years_in_tex(base_content, display)

    # ------------------------------------------------------------------
    # 3. AI tailoring
    # ------------------------------------------------------------------
    required_str = f"{required_years}+" if required_years else "not specified"
    user_msg = CV_USER_TEMPLATE.format(
        actual_years=f"{display} years",
        required_years=required_str,
        description=listing.description[:3000],
        skills=", ".join(listing.skills),
        cv_content=pre_patched,
    )

    try:
        result = await ai_client.complete(
            CV_SYSTEM_PROMPT,
            user_msg,
            max_tokens=ai_client.config.cv_max_tokens,
        )
    except Exception as exc:
        log.error("CV AI call failed for job %s: %s; using pre-patched CV", listing.id[:12], exc)
        out_path.write_text(pre_patched, encoding="utf-8")
        return out_path

    result = result.strip()

    # ------------------------------------------------------------------
    # 4. Sanity checks
    # ------------------------------------------------------------------

    # Must look like LaTeX
    if not (result.startswith("\\documentclass") or result.startswith("%")):
        log.warning("AI response for job %s not valid LaTeX; using pre-patched CV", listing.id[:12])
        out_path.write_text(pre_patched, encoding="utf-8")
        return out_path

    # Guard against inflated years: scan AI output for any year claim > actual
    ai_years_matches = _YEARS_PATTERN.findall(result)
    for _, yr_str in ai_years_matches:
        if int(yr_str) > math.ceil(actual_years) + 1:
            log.warning(
                "AI claimed %s years for job %s but actual is %.1f; reverting to pre-patched CV",
                yr_str,
                listing.id[:12],
                actual_years,
            )
            out_path.write_text(pre_patched, encoding="utf-8")
            return out_path

    # Diff fraction guard
    base_lines = pre_patched.splitlines()
    result_lines = result.splitlines()
    changed = 1.0 - difflib.SequenceMatcher(None, base_lines, result_lines).ratio()
    if changed > _MAX_DIFF_FRACTION:
        log.warning(
            "CV diff %.0f%% exceeds limit for job %s; using pre-patched CV",
            changed * 100,
            listing.id[:12],
        )
        out_path.write_text(pre_patched, encoding="utf-8")
        return out_path

    result = _sanitize_tex(result)
    out_path.write_text(result, encoding="utf-8")
    log.info(
        "Tailored CV written to %s (%.0f%% lines changed, %s yrs shown)",
        out_path,
        changed * 100,
        display,
    )
    return out_path


def prompt_version() -> str:
    return CV_PROMPT_VERSION
