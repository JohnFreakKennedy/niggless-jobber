"""
Post-processing cleaner for AI-generated CV descriptions.

Borrowed and extended from Resume-Matcher (srbhr/Resume-Matcher, Apache-2.0):
  apps/backend/app/prompts/refinement.py

Removes AI-characteristic buzzwords and replaces them with plain alternatives
so the CV reads like a human wrote it.  Applied only to the work-experience
paragraphs -- not to LaTeX commands, company names, or dates.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Phrase -> replacement mapping (case-insensitive, whole-word matching)
# ---------------------------------------------------------------------------
_REPLACEMENTS: list[tuple[str, str]] = [
    # Overused action verbs
    ("spearheaded", "led"),
    ("orchestrated", "coordinated"),
    ("championed", "advocated for"),
    ("synergized", "collaborated"),
    ("leveraged", "used"),
    ("revolutionized", "transformed"),
    ("pioneered", "introduced"),
    ("catalyzed", "initiated"),
    ("operationalized", "implemented"),
    ("architected", "designed"),
    ("envisioned", "planned"),
    ("effectuated", "completed"),
    ("endeavored", "worked"),
    ("facilitated", "helped"),
    ("utilized", "used"),
    # Corporate buzzwords
    ("synergy", "collaboration"),
    ("paradigm shift", "change"),
    ("best-in-class", "top-performing"),
    ("world-class", "high-quality"),
    ("cutting-edge", "modern"),
    ("bleeding-edge", "modern"),
    ("game-changing", "innovative"),
    ("game-changer", "innovation"),
    ("holistic", "comprehensive"),
    ("robust", "strong"),
    ("scalable", "expandable"),
    ("actionable", "practical"),
    ("impactful", "effective"),
    ("proactively", "actively"),
    ("proactive", "active"),
    ("deliverables", "outputs"),
    ("circle back", "follow up"),
    ("move the needle", "make progress"),
    ("low-hanging fruit", "quick wins"),
    ("touch base", "connect"),
    ("value-add", "benefit"),
    ("deep dive", "analysis"),
    # Verbose filler phrases
    ("in order to", "to"),
    ("for the purpose of", "to"),
    ("with a view to", "to"),
    ("on a daily basis", "daily"),
    ("on a regular basis", "regularly"),
    ("in a timely manner", "promptly"),
    ("due to the fact that", "because"),
    ("in the event that", "if"),
    ("in light of the fact that", "since"),
]

# Blank-out phrases (no replacement)
_REMOVE: list[str] = [
    "at the end of the day",
    "moving forward",
    "going forward",
    "at this point in time",
]

# Pre-compile all patterns once
_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE), replacement)
    for phrase, replacement in _REPLACEMENTS
]
_COMPILED_REMOVE: list[re.Pattern] = [
    re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE)
    for phrase in _REMOVE
]

# Em-dash variants -> comma+space
_EM_DASH_PAT = re.compile(r"\s*(?:\u2014|---?)\s*")


def _dash_to_comma(m: re.Match) -> str:
    """Em-dash -> comma, but leave number ranges such as 2020--2021 alone."""
    before = m.string[: m.start()].rstrip()
    after = m.string[m.end():].lstrip()
    if m.group(0).strip() == "--" and before[-1:].isdigit() and after[:1].isdigit():
        return m.group(0)
    return ", "


def _is_latex_command_line(line: str) -> bool:
    """Return True for lines that are LaTeX commands, not prose."""
    stripped = line.strip()
    return (
        stripped.startswith("\\")
        or stripped.startswith("%")
        or stripped == ""
    )


def clean_description(text: str) -> str:
    """
    Apply the phrase blacklist and em-dash cleanup to a single prose string.
    Safe to call on individual description paragraphs.
    """
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    for pattern in _COMPILED_REMOVE:
        text = pattern.sub("", text)
    text = _EM_DASH_PAT.sub(_dash_to_comma, text)
    # Collapse any double spaces left by removal
    text = re.sub(r"  +", " ", text).strip(", ").strip()
    return text


def clean_cv_tex(content: str) -> str:
    """
    Apply phrase cleanup to every prose line in a LaTeX CV source.
    LaTeX command lines (starting with \\ or %) are left untouched.
    """
    lines = content.splitlines(keepends=True)
    cleaned = []
    for line in lines:
        if _is_latex_command_line(line):
            cleaned.append(line)
        else:
            cleaned.append(clean_description(line.rstrip("\n")) + "\n" if line.endswith("\n") else clean_description(line))
    return "".join(cleaned)
