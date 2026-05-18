"""Cover letter generation: AI body + LaTeX template assembly."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from cover_letter.prompts import CL_PROMPT_VERSION, CL_SYSTEM_PROMPT, CL_USER_TEMPLATE
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

_WORD_MIN = 200
_WORD_MAX = 450


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
    out_path = output_dir / "cover_letter.tex"

    info = _load_personal_info()
    sample = _load_sample()

    user_msg = CL_USER_TEMPLATE.format(
        name=info.get("name", {}).get("full") or info.get("name", ""),
        summary=info.get("summary", ""),
        skills=", ".join(info.get("skills", [])),
        years_of_experience=info.get("years_of_experience", ""),
        company=listing.company,
        title=listing.title,
        source=listing.source,
        description=listing.description[:2000],
        sample=sample,
    )

    body = await _call_with_length_retry(ai_client, user_msg)
    tex = _assemble(body, listing, info)
    out_path.write_text(tex, encoding="utf-8")
    log.info("Cover letter written to %s", out_path)
    return out_path


async def _call_with_length_retry(ai_client: AIClient, user_msg: str) -> str:
    body = await ai_client.complete(
        CL_SYSTEM_PROMPT,
        user_msg,
        max_tokens=ai_client.config.cover_letter_max_tokens,
    )
    word_count = _word_count(_strip_latex(body))
    if _WORD_MIN <= word_count <= _WORD_MAX:
        return body

    log.warning("Cover letter word count %d outside [%d, %d]; retrying", word_count, _WORD_MIN, _WORD_MAX)
    retry_prefix = (
        f"IMPORTANT: Your cover letter must be between {_WORD_MIN} and {_WORD_MAX} words. "
        f"Previous attempt was {word_count} words. Try again.\n\n"
    )
    body = await ai_client.complete(
        CL_SYSTEM_PROMPT,
        retry_prefix + user_msg,
        max_tokens=ai_client.config.cover_letter_max_tokens,
    )
    return body


def _assemble(body: str, listing: JobListing, info: dict) -> str:
    template = _CL_TEMPLATE.read_text(encoding="utf-8")
    name = info.get("name", {}).get("full") or info.get("name", "")
    email = info.get("email", "")
    phone = info.get("phone", "")
    address = (info.get("address") or {}).get("city", "") + ", " + (info.get("address") or {}).get("country", "")
    return (
        template
        .replace("%%NAME%%", name)
        .replace("%%EMAIL%%", email)
        .replace("%%PHONE%%", phone)
        .replace("%%ADDRESS%%", address.strip(", "))
        .replace("%%COMPANY%%", listing.company)
        .replace("%%JOBTITLE%%", listing.title)
        .replace("%%BODY%%", body)
    )


def prompt_version() -> str:
    return CL_PROMPT_VERSION
