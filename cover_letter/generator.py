"""Cover letter generation: AI body + LaTeX template assembly."""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from cover_letter.prompts import (
    CL_FIX_MESSAGE,
    CL_PROMPT_VERSION,
    CL_SYSTEM_PROMPT,
    CL_USER_TEMPLATE,
)
from cv_editor.client import AIClient
from parser.schema import JobListing

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).parent.parent
_TEMPLATES_DIR = _REPO_ROOT / "templates"
_CL_TEMPLATE = _TEMPLATES_DIR / "cover_letter.tex"
_CL_SAMPLE = _TEMPLATES_DIR / "cover_letter_sample.txt"
_PERSONAL_INFO_CANDIDATES = [
    _REPO_ROOT / "personal_info.json",
    Path.home() / ".niggless-jobber" / "personal_info.json",
]

# Length the prompt asks for, and the wider window a letter must land in.
_WORD_MIN = 170
_WORD_MAX = 300
_ACCEPT_MIN = 140
_ACCEPT_MAX = 360
# Below this, what is left after dropping bad sentences is not worth sending.
_SALVAGE_MIN = 50


def _load_personal_info() -> dict:
    for path in _PERSONAL_INFO_CANDIDATES:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _load_sample() -> str:
    if _CL_SAMPLE.exists():
        return _CL_SAMPLE.read_text(encoding="utf-8").strip()
    return "No sample provided."


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _strip_latex(text: str) -> str:
    """Remove LaTeX commands, leaving plain text (used for word count)."""
    text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\[a-zA-Z]+", " ", text)
    return text.strip()


def _strip_formatting(text: str) -> str:
    """
    Remove all inline LaTeX formatting from the cover letter body.
    \\textbf{foo} -> foo, \\textit{foo} -> foo, etc.
    Repeated until no more matches (handles nesting).
    """
    pattern = re.compile(r"\\(?:textbf|textit|emph|underline|textrm|texttt)\{([^{}]*)\}")
    prev = None
    while prev != text:
        prev = text
        text = pattern.sub(r"\1", text)
    return text


_TEX_SPECIALS = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def _escape_plain(text: str) -> str:
    """Plain text (possibly with a few LaTeX escapes already) -> safe LaTeX."""
    text = re.sub(r"\\([%&$#_{}])", r"\1", text)
    return "".join(_TEX_SPECIALS.get(ch, ch) for ch in text)


def _tex_to_plain(text: str) -> str:
    """LaTeX snippet from the CV -> plain text (\\% -> %, \\textbf{x} -> x)."""
    text = _strip_formatting(text)
    text = re.sub(r"\\([%&$#_{}])", r"\1", text)
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# CV facts: what the letter may say about the candidate
# ---------------------------------------------------------------------------

def _cv_facts(info: dict, years: str = "") -> tuple[str, str]:
    """
    Return (facts, current_title) from the base CV the CV editor uses, so the
    letter cites the same achievements. Falls back to personal_info.json.
    """
    from cv_editor.editor import _resolve_base_cv, patch_years_in_tex
    from cv_editor.structure import parse_cv

    try:
        tex = _resolve_base_cv().read_text(encoding="utf-8")
        cv = parse_cv(patch_years_in_tex(tex, years) if years else tex)
    except (FileNotFoundError, OSError):
        cv = None

    lines: list[str] = []
    title = ""
    if cv is not None and not cv.empty:
        if cv.summary_id:
            lines.append("Summary: " + _tex_to_plain(cv.units[cv.summary_id].text))
        for role in cv.roles:
            title = title or role.title
            lines.append(f"{_tex_to_plain(role.title)} at {_tex_to_plain(role.company)}:")
            lines += ["- " + _tex_to_plain(cv.units[b].text) for b in role.bullet_ids]
        for uid in cv.skill_ids:
            unit = cv.units[uid]
            lines.append(f"Skills ({_tex_to_plain(unit.category)}): {_tex_to_plain(', '.join(unit.items))}")
    else:
        if info.get("summary"):
            lines.append("Summary: " + info["summary"])
        if info.get("skills"):
            skills = info["skills"]
            if isinstance(skills, dict):
                skills = [s for v in skills.values() for s in (v if isinstance(v, list) else [v])]
            lines.append("Skills: " + ", ".join(str(s) for s in skills))
        for job in info.get("work_history") or []:
            lines.append(f"{job.get('title', '')} at {job.get('company', '')}")

    history = info.get("work_history") or []
    if history and isinstance(history[0], dict) and history[0].get("title"):
        title = history[0]["title"]
    return "\n".join(lines), title or "not given"


def _years(info: dict) -> str:
    """Same years figure the CV shows (computed from work_history)."""
    from cv_editor.editor import _display_years, calculate_experience_years
    history = info.get("work_history") or []
    computed = _display_years(calculate_experience_years(history)) if history else "0"
    if computed != "0":
        return computed
    stated = re.search(r"\d+", str(info.get("years_of_experience", "") or ""))
    return stated.group(0) if stated else ""


# ---------------------------------------------------------------------------
# Body checks
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(
    r"^\s*(dear\b|hello\b|hi\b|to whom|sincerely|best regards|kind regards|regards|"
    r"best,|thank you,|yours)", re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
# Phrases the prompt bans; models still use them, so they are checked.
_CLICHES = (
    "i am writing to", "i am excited", "passionate", "leverage", "synergy", "go-getter",
    "proven track record", "fast-paced", "hit the ground running", "honed", "aligns well",
    "strong foundation", "my expertise in", "particularly appealing", "i am confident",
    "equipped me with",
)
_NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
# "over four years of experience", "5+ years", "nearly 6 years in DevOps"
_YEARS_CLAIM_RE = re.compile(
    r"(over\s+|more\s+than\s+|nearly\s+|almost\s+|about\s+|around\s+)?"
    r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")(\+)?\s+years?'?\s+(?:of\s+)?(?:professional\s+|hands-on\s+)?experience\b",
    re.IGNORECASE,
)


def _clean_body(body: str) -> str:
    """Deterministic cleanup: markdown, greetings/sign-offs, LaTeX, cliches, em-dashes."""
    from cv_editor.cleaner import clean_description

    body = _strip_formatting(body.strip())
    body = re.sub(r"^```[a-z]*\s*|\s*```$", "", body)
    paragraphs = []
    for para in re.split(r"\n\s*\n", body):
        lines = [re.sub(r"^\s*(?:#+|[-*\u2022]|\d+[.)])\s+", "", ln) for ln in para.splitlines()]
        para = re.sub(r"\*\*|__", "", " ".join(" ".join(lines).split()))
        if not para or _GREETING_RE.match(para) and _word_count(para) <= 6:
            continue
        paragraphs.append(clean_description(para))
    return "\n\n".join(paragraphs)


def _problems(body: str, facts: str, listing: JobListing, years: str) -> list[str]:
    from cv_editor.tailoring import build_vocabulary, mentions

    problems = []
    words = _word_count(body)
    if not _ACCEPT_MIN <= words <= _ACCEPT_MAX:
        problems.append(f"it has {words} words; write {_WORD_MIN}-{_WORD_MAX}")
    vocab = build_vocabulary(list(listing.skills or []))
    invented = sorted(t for t in vocab if mentions(body, t) and not mentions(facts, t))
    if invented:
        problems.append("it claims skills that are not in the CV facts: " + ", ".join(invented))
    allowed = set(_NUMBER_RE.findall(facts)) | set(_NUMBER_RE.findall(listing.description or ""))
    allowed.add(years)
    new_numbers = sorted(set(_NUMBER_RE.findall(body)) - allowed)
    if new_numbers:
        problems.append("it uses numbers that are not in the CV facts: " + ", ".join(new_numbers))
    cliches = [c for c in _CLICHES if re.search(r"\b" + re.escape(c) + r"\b", body, re.IGNORECASE)]
    if cliches:
        problems.append("it uses banned phrases: " + ", ".join(f'"{c}"' for c in cliches))
    for m in _YEARS_CLAIM_RE.finditer(body):
        qualifier, amount = (m.group(1) or "").strip() + (m.group(3) or ""), m.group(2).lower()
        value = amount if amount.isdigit() else str(_NUMBER_WORDS.get(amount, ""))
        if value != years or qualifier:
            problems.append(f'it says "{m.group(0).strip()}"; write exactly "{years} years"')
            break
    return problems


def _drop_bad_sentences(body: str, facts: str, listing: JobListing, years: str) -> str:
    """Last resort: remove sentences that invent skills or numbers."""
    from cv_editor.tailoring import split_sentences

    kept_paragraphs = []
    for para in body.split("\n\n"):
        kept = [
            s for s in split_sentences(para)
            if not [p for p in _problems(s, facts, listing, years) if "words" not in p]
        ]
        if kept:
            kept_paragraphs.append(" ".join(kept))
    return "\n\n".join(kept_paragraphs)


async def generate_cover_letter(
    listing: JobListing,
    output_dir: Path,
    ai_client: AIClient,
) -> Path:
    """
    Generate cover_letter.tex for *listing* in *output_dir*.
    Returns the path to the written .tex file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    from utils import artifact_stem
    out_path = output_dir / f"{artifact_stem(listing.company, 'cover_letter')}.tex"

    info = _load_personal_info()
    years = _years(info)
    facts, current_title = _cv_facts(info, years)

    user_msg = CL_USER_TEMPLATE.format(
        name=_full_name(info),
        current_title=current_title,
        years_of_experience=years or "not given",
        cv_facts=facts or "not available",
        company=listing.company or "not given",
        title=listing.title,
        description=(listing.description or "")[:3000],
        sample=_load_sample(),
    )

    body = await _write_body(ai_client, user_msg, facts, listing, years)
    tex = _assemble(body, listing, info)
    out_path.write_text(tex, encoding="utf-8")
    log.info("Cover letter written to %s (%d words)", out_path, _word_count(body))
    return out_path


async def _write_body(ai_client: AIClient, user_msg: str, facts: str, listing: JobListing, years: str) -> str:
    """Ask for the body, check it, ask once more with the problems listed, then salvage."""
    system = CL_SYSTEM_PROMPT.format(word_min=_WORD_MIN, word_max=_WORD_MAX)
    max_tokens = ai_client.config.cover_letter_max_tokens
    body = _clean_body(await ai_client.complete(system, user_msg, max_tokens=max_tokens))
    problems = _problems(body, facts, listing, years)
    if not problems:
        return body

    log.warning("Cover letter rejected (%s); asking again", "; ".join(problems))
    retry = user_msg + "\n\n" + CL_FIX_MESSAGE.format(
        problems="\n".join("- " + p for p in problems), previous=body,
    )
    second = _clean_body(await ai_client.complete(system, retry, max_tokens=max_tokens))
    second_problems = _problems(second, facts, listing, years)
    if not second_problems:
        return second
    # Keep whichever attempt is closer to acceptable, then drop its bad sentences.
    if len(second_problems) <= len(problems) and _word_count(second) >= _ACCEPT_MIN:
        body, problems = second, second_problems
    log.warning("Cover letter still has problems (%s); dropping the offending sentences",
                "; ".join(problems))
    body = _drop_bad_sentences(body, facts, listing, years)
    if _word_count(body) < _SALVAGE_MIN:
        raise RuntimeError(
            f"cover letter too short after removing invalid sentences ({_word_count(body)} words)"
        )
    return body


def _full_name(info: dict) -> str:
    name = info.get("name", "")
    return name.get("full", "") if isinstance(name, dict) else str(name or "")


def _assemble(body: str, listing: JobListing, info: dict) -> str:
    from utils import looks_like_location
    template = _CL_TEMPLATE.read_text(encoding="utf-8")
    name = _escape_plain(_full_name(info))
    email = info.get("email", "")
    phone = info.get("phone", "")
    addr = info.get("address") or {}
    address = _escape_plain(", ".join(p for p in [addr.get("city", ""), addr.get("country", "")] if p))
    today = date.today().strftime("%B %-d, %Y")

    # Guard: if company looks like a location string (scraper bug), omit it
    # so the letter doesn't read "I am applying at Poland (Remote)."
    company = listing.company
    if looks_like_location(company):
        log.warning(
            "Job %s: company field '%s' looks like a location; omitting from cover letter",
            listing.id[:12],
            company,
        )
        company = ""

    # When company is empty, remove the "Hiring Team \\ %%COMPANY%%" line
    # so no dangling backslash remains in the compiled PDF.
    if company:
        template = template.replace("%%COMPANY%%", _escape_plain(company))
    else:
        template = re.sub(
            r"\\noindent Hiring Team\s*\\\\\s*%%COMPANY%%",
            r"\\noindent Hiring Team",
            template,
        )
        # "...position at \textbf{}." would read "position at ."
        template = re.sub(r"\s+at\s+\\textbf\{%%COMPANY%%\}", "", template)
        template = template.replace("%%COMPANY%%", "")

    return (
        template
        .replace("%%NAME%%", name)
        .replace("{mailto:%%EMAIL%%}", "{mailto:" + email + "}")
        .replace("%%EMAIL%%", _escape_plain(email))
        .replace("%%PHONE%%", _escape_plain(phone))
        .replace("%%ADDRESS%%", address)
        .replace("%%JOBTITLE%%", _escape_plain(listing.title))
        .replace("%%DATE%%", today)
        .replace("%%BODY%%", _escape_plain(body))
    )


def prompt_version() -> str:
    return CL_PROMPT_VERSION
