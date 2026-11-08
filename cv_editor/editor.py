"""CV tailoring: reads base CV, calls AI, writes patched .tex to output dir."""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from cv_editor.client import AIClient
from cv_editor.prompts import (
    CV_PROMPT_VERSION,
    CV_RETRY_MESSAGE,
    CV_SYSTEM_PROMPT,
    CV_USER_TEMPLATE,
    JD_EXTRACT_PROMPT,
    JD_EXTRACT_SYSTEM,
)
from cv_editor.structure import (
    CVStructure,
    headline_text,
    parse_cv,
    plain_text,
    render,
    to_prompt_payload,
)
from cv_editor.tailoring import (
    MIN_BULLET_OVERLAP,
    build_vocabulary,
    check_permutation,
    check_rewrite,
    check_skill_items,
    coverage,
    mentions,
    parse_json_object,
    rank_by_relevance,
    split_sentences,
    str_list,
)
from parser.schema import JobListing

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent
_TEMPLATES_DIR = _REPO_ROOT / "templates"
_BASE_CV = _TEMPLATES_DIR / "cv.tex"
_SAMPLE_CV = _TEMPLATES_DIR / "cv_sample.tex"


def _resolve_base_cv() -> Path:
    if _BASE_CV.exists():
        return _BASE_CV
    if _SAMPLE_CV.exists():
        log.warning(
            "templates/cv.tex not found; using cv_sample.tex. "
            "Run: cp templates/cv_sample.tex templates/cv.tex and fill in your details."
        )
        return _SAMPLE_CV
    raise FileNotFoundError(
        "No CV template found. Copy templates/cv_sample.tex to templates/cv.tex and fill in your details."
    )
_PERSONAL_INFO_CANDIDATES = [
    _REPO_ROOT / "personal_info.json",
    Path.home() / ".niggless-jobber" / "personal_info.json",
]

# The summary may grow more than a bullet: it is where the match is stated.
SUMMARY_GROWTH_RATIO = 1.6
SUMMARY_MIN_LIMIT = 360

_YEARS_CLAIM_RE = re.compile(r"(\d+)\s*\+?\s*(?:years?|yrs)\b", re.IGNORECASE)
_LEADING_SENIORITY_RE = re.compile(r"^\s*(senior|lead|staff|principal)\s+", re.IGNORECASE)

# How much of the job description goes into each prompt (characters).
_JD_EXTRACT_CHARS = 6000
_JD_PROMPT_CHARS = 4000

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

# Matches the job-title macro of templates built on cv_sample.tex.
_JOBTITLE_MACRO_PATTERN = re.compile(
    r"^([ \t]*\\newcommand\{\\jobtitle\}\{)[^{}\n]*(\})", re.MULTILINE
)

# Matches the CV subtitle line (job title) in the header tabular block of
# older templates.
# The subtitle is the second row of the tabular: comes right after the name
# row which ends with `\\`, before the `&` column separator.
_JOB_TITLE_IN_CV_PATTERN = re.compile(
    r"(\\textbf\{\\huge\s+[^}]+\}\s*&[^\n]+\\\\\n\s*)([^\n&{\\][^\n&]*)(\s*&)",
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


def patch_job_title_in_tex(content: str, title: str) -> str:
    """
    Set the CV subtitle (job title) to *title*.

    Templates built on cv_sample.tex define ``\\newcommand{\\jobtitle}{...}``;
    older templates put the subtitle on the second row of the header
    tabular, right before the phone/LinkedIn column. Both are handled.
    LaTeX special characters in *title* (C#, R&D) are escaped.
    If neither form is found the content is returned unchanged.
    """
    safe = _latex_escape_title(title)
    patched, n = _JOBTITLE_MACRO_PATTERN.subn(
        lambda m: f"{m.group(1)}{safe}{m.group(2)}", content
    )
    if not n:
        patched, n = _JOB_TITLE_IN_CV_PATTERN.subn(
            lambda m: f"{m.group(1)}{safe} {m.group(3).lstrip()}", content
        )
    if n:
        log.debug("Pre-patched CV job title -> '%s'", title)
    return patched


def _latex_escape_title(title: str) -> str:
    title = " ".join(title.split())
    title = re.sub(r"[{}\\^~]", "", title)
    return re.sub(r"(?<!\\)([&%#_$])", r"\\\1", title)


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
# JD metadata extractor (structured pre-pass)
# ---------------------------------------------------------------------------

_EMPTY_JD_META = {
    "required_skills": [],
    "preferred_skills": [],
    "seniority_level": "unknown",
    "responsibilities": [],
    "keywords": [],
}


async def extract_jd_metadata(description: str, ai_client: AIClient, job_title: str = "") -> dict:
    """
    Ask the AI to extract structured metadata from the job description:
    required/preferred skills, seniority, responsibilities, domain keywords.

    Always returns a dict with every key of _EMPTY_JD_META, so callers never
    need to handle a failure.
    """
    prompt = JD_EXTRACT_PROMPT.format(
        job_title=job_title or "not given",
        job_description=description[:_JD_EXTRACT_CHARS],
    )
    try:
        raw = await ai_client.complete(JD_EXTRACT_SYSTEM, prompt, max_tokens=700, json_mode=True)
        data = parse_json_object(raw)
    except Exception as exc:
        log.warning("JD metadata extraction failed: %s", exc)
        return dict(_EMPTY_JD_META)
    seniority = data.get("seniority_level")
    return {
        "required_skills": str_list(data.get("required_skills")),
        "preferred_skills": str_list(data.get("preferred_skills")),
        "seniority_level": seniority.strip().lower() if isinstance(seniority, str) and seniority.strip() else "unknown",
        "responsibilities": str_list(data.get("responsibilities"), limit=6),
        "keywords": str_list(data.get("keywords"), limit=8),
    }


# ---------------------------------------------------------------------------
# AI edits -> checked edits
# ---------------------------------------------------------------------------

async def _request_edits(ai_client: AIClient, user_msg: str) -> dict:
    """Call the model for tailoring edits; one retry when the JSON is broken."""
    max_tokens = ai_client.config.cv_max_tokens
    raw = await ai_client.complete(CV_SYSTEM_PROMPT, user_msg, max_tokens=max_tokens, json_mode=True)
    try:
        return parse_json_object(raw)
    except ValueError as exc:
        log.warning("CV edits were not valid JSON (%s); asking again", exc)
        retry_msg = user_msg + "\n\n" + CV_RETRY_MESSAGE.format(error=exc)
        raw = await ai_client.complete(CV_SYSTEM_PROMPT, retry_msg, max_tokens=max_tokens, json_mode=True)
        return parse_json_object(raw)


def _check_summary(original, proposed, cv, cv_text, vocabulary, display_years, reject) -> str | None:
    """
    Check the rewritten summary. When it fails, drop the sentences that break
    a rule (a missing skill, a seniority claim) and keep the rest, as long as
    the opening sentence survives.
    """
    def check(text: str):
        wrong = [n for n in _YEARS_CLAIM_RE.findall(text) if n != display_years]
        if wrong:
            return None, f"claims {wrong[0]} years (actual {display_years})"
        return check_rewrite(
            original, text, cv_text, vocabulary,
            allowed_numbers={display_years}, number_source=cv_text,
            max_chars=max(int(len(original) * SUMMARY_GROWTH_RATIO), SUMMARY_MIN_LIMIT),
        )

    if not isinstance(proposed, str):
        reject("summary", "not a string")
        return None
    # Models copy the job's "Senior ..." title into the opening; drop a leading
    # seniority word the CV does not use rather than lose the whole summary.
    lead = _LEADING_SENIORITY_RE.match(proposed)
    if lead and not mentions(cv_text, lead.group(1)):
        proposed = proposed[lead.end():]
        proposed = proposed[:1].upper() + proposed[1:]
    new, reason = check(proposed)
    if new is not None:
        return new
    sentences = split_sentences(proposed)
    if len(sentences) > 1:
        kept = [sentences[0]] + [
            s for s in sentences[1:]
            if not [n for n in _YEARS_CLAIM_RE.findall(s) if n != display_years]
            and check_rewrite(original, s, cv_text, vocabulary, allowed_numbers={display_years},
                              number_source=cv_text, max_chars=10_000, min_chars=0)[0] is not None
        ]
        if len(kept) < len(sentences):
            salvaged, _ = check(" ".join(kept))
            if salvaged is not None:
                reject("summary", f"{reason}; dropped {len(sentences) - len(kept)} sentence(s), kept the rest")
                return salvaged
    reject("summary", reason)
    return None


def _apply_checked_edits(
    cv: CVStructure,
    edits: dict,
    vocabulary: set[str],
    display_years: str,
    required: list[str],
    preferred: list[str],
    report: dict,
) -> str:
    """Validate every proposed edit and render the CV with the accepted ones."""
    cv_text = plain_text(cv)
    texts: dict[str, str] = {}
    rejected: list[dict] = report["rejected"]

    def reject(uid: str, reason: str) -> None:
        rejected.append({"id": uid, "reason": reason})

    proposed_summary = edits.get("summary")
    if cv.summary_id and proposed_summary:
        original = cv.units[cv.summary_id].text
        new = _check_summary(original, proposed_summary, cv, cv_text, vocabulary, display_years, reject)
        if new is not None and new != original:
            texts[cv.summary_id] = new

    # A bullet may only name technologies its own role already mentions.
    role_text = {
        r.id: " ".join([r.title] + [cv.units[b].text for b in r.bullet_ids]) for r in cv.roles
    }
    bullets = edits.get("bullets") if isinstance(edits.get("bullets"), dict) else {}
    for uid, proposed in bullets.items():
        unit = cv.units.get(uid)
        if unit is None or unit.kind != "bullet":
            continue
        new, reason = check_rewrite(
            unit.text, proposed, cv_text, vocabulary,
            term_source=role_text[unit.role_id], min_overlap=MIN_BULLET_OVERLAP,
        )
        if new is None:
            reject(uid, reason)
        elif new != unit.text:
            texts[uid] = new

    bullet_order: dict[str, list[str]] = {}
    proposed_orders = edits.get("bullet_order") if isinstance(edits.get("bullet_order"), dict) else {}
    for role in cv.roles:
        if role.id not in proposed_orders or not role.reorderable:
            continue
        order = check_permutation(proposed_orders[role.id], role.bullet_ids)
        if order is None:
            reject(role.id, "bullet_order is not a reorder of the role's bullets")
        elif order != role.bullet_ids:
            bullet_order[role.id] = order

    skill_items: dict[str, list[str]] = {}
    proposed_skills = edits.get("skills") if isinstance(edits.get("skills"), dict) else {}
    for uid in cv.skill_ids:
        unit = cv.units[uid]
        items = None
        if uid in proposed_skills:
            items = check_skill_items(proposed_skills[uid], unit.items)
            if items is None:
                reject(uid, "skills must be a reorder of the same items")
        if items is None:
            # Deterministic fallback: items the job asks for first.
            items = rank_by_relevance(unit.items, lambda i: i, required, preferred)
        if items != unit.items:
            skill_items[uid] = items

    skill_order = None
    if "skill_order" in edits:
        skill_order = check_permutation(edits.get("skill_order"), cv.skill_ids)
        if skill_order is None:
            reject("skill_order", "skill_order is not a reorder of the skill lines")
    if skill_order is None and cv.skill_ids:
        def line_text(uid: str) -> str:
            return cv.units[uid].category + " " + ", ".join(cv.units[uid].items)
        skill_order = rank_by_relevance(cv.skill_ids, line_text, required, preferred)

    report["edited_units"] = sorted(texts)
    report["reordered_roles"] = sorted(bullet_order)
    report["reordered_skill_lines"] = sorted(skill_items)
    report["skill_order_changed"] = bool(skill_order and skill_order != cv.skill_ids)
    return render(cv, texts, bullet_order, skill_items, skill_order)


def _candidate_title(cv: CVStructure, info: dict) -> str:
    """The candidate's own current title: latest work_history entry, else the first CV role."""
    history = info.get("work_history") or []
    if history:
        latest = max(history, key=lambda j: _parse_ym(j.get("end", "present")) or date.min)
        if isinstance(latest.get("title"), str) and latest["title"].strip():
            return latest["title"].strip()
    for role in cv.roles:
        if role.title:
            return role.title
    return "not given"


def _write_report(out_path: Path, report: dict) -> None:
    report_path = out_path.with_name(out_path.stem + "_tailoring.json")
    try:
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not write tailoring report %s: %s", report_path, exc)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def tailor_cv(
    listing: JobListing,
    output_dir: Path,
    ai_client: AIClient,
) -> Path:
    """
    Generate a tailored .tex CV for *listing* in *output_dir*.
    Returns the path to the written file. A ``<stem>_tailoring.json`` report
    next to it records keyword coverage before/after, the job's skills the
    CV lacks, and every AI edit that was discarded and why.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    from utils import artifact_stem
    out_path = output_dir / f"{artifact_stem(listing.company, 'cv')}.tex"

    # 1. Load base CV and personal info
    base_content = _resolve_base_cv().read_text(encoding="utf-8")
    info = _load_personal_info()
    actual_years = calculate_experience_years(info.get("work_history", []))
    display = _display_years(actual_years)
    required_years = extract_required_years(listing.description)
    log.info(
        "Job %s: actual experience=%.1f yrs (shown as '%s'), job requires=%s yrs",
        listing.id[:12], actual_years, display, required_years or "unspecified",
    )

    # 2. Deterministic pre-patch: years + job title
    pre_patched = patch_years_in_tex(base_content, display)
    pre_patched = patch_job_title_in_tex(pre_patched, listing.title)

    report: dict = {
        "prompt_version": CV_PROMPT_VERSION,
        "job": {"title": listing.title, "company": listing.company},
        "status": "",
        "rejected": [],
        "ai_edits": {},
    }

    cv = parse_cv(pre_patched)
    if cv.empty:
        log.warning(
            "CV template has no tailorable parts (summary markers, \\resumeItem, "
            "\\skillline); see templates/cv_sample.tex. Using the CV as is."
        )
        report["status"] = "no tailorable parts in template"
        out_path.write_text(pre_patched, encoding="utf-8")
        _write_report(out_path, report)
        return out_path

    # 3. Job analysis
    jd_meta = await extract_jd_metadata(listing.description, ai_client, listing.title)
    required = jd_meta["required_skills"] or list(listing.skills or [])
    preferred = jd_meta["preferred_skills"]
    keywords = required + preferred
    cv_text = plain_text(cv)
    gaps = [k for k in required if not mentions(cv_text, k)]
    report["jd"] = jd_meta
    report["gaps"] = gaps
    report["coverage_before"] = coverage(cv_text, keywords)
    report["prominence_before"] = coverage(headline_text(cv), keywords)["score"]
    log.info(
        "Job %s: seniority=%s, required=%s, preferred=%s, gaps=%s",
        listing.id[:12], jd_meta["seniority_level"], required[:8], preferred[:5], gaps[:8],
    )

    # 4. AI edits
    user_msg = CV_USER_TEMPLATE.format(
        job_title=listing.title,
        company=listing.company or "not given",
        seniority_level=jd_meta["seniority_level"],
        required_skills=", ".join(required) or "not specified",
        preferred_skills=", ".join(preferred) or "not specified",
        responsibilities="; ".join(jd_meta["responsibilities"]) or "not specified",
        keywords=", ".join(jd_meta["keywords"]) or "not specified",
        gaps=", ".join(gaps) or "none",
        description=listing.description[:_JD_PROMPT_CHARS],
        actual_years=display,
        candidate_title=_candidate_title(cv, info),
        cv_json=json.dumps(to_prompt_payload(cv), indent=1, ensure_ascii=False),
    )
    try:
        edits = await _request_edits(ai_client, user_msg)
        report["status"] = "tailored"
        report["ai_edits"] = edits
    except Exception as exc:
        log.error("CV AI tailoring failed for job %s: %s; reordering skills only", listing.id[:12], exc)
        edits = {}
        report["status"] = f"ai failed ({type(exc).__name__}); skills reordered only"

    # 5. Check every edit, keep the good ones
    vocabulary = build_vocabulary(keywords + jd_meta["keywords"])
    result = _apply_checked_edits(cv, edits, vocabulary, display, required, preferred, report)
    for item in report["rejected"]:
        log.info("Job %s: discarded AI edit %s: %s", listing.id[:12], item["id"], item["reason"])

    # 6. Deterministic post-patch: re-enforce title and years
    result = _sanitize_tex(result)
    result = patch_job_title_in_tex(result, listing.title)
    result = patch_years_in_tex(result, display)

    tailored = parse_cv(result)
    report["coverage_after"] = coverage(plain_text(tailored), keywords)
    report["prominence_after"] = coverage(headline_text(tailored), keywords)["score"]
    out_path.write_text(result, encoding="utf-8")
    _write_report(out_path, report)
    log.info(
        "Tailored CV written to %s (%d units edited, %d edits discarded, "
        "job keywords up front %s -> %s)",
        out_path, len(report["edited_units"]), len(report["rejected"]),
        report["prominence_before"], report["prominence_after"],
    )
    return out_path


def prompt_version() -> str:
    return CV_PROMPT_VERSION
