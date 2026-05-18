"""Heuristic form filler: maps visible form fields to personal_info values."""
from __future__ import annotations

import difflib
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)

_PERSONAL_INFO = Path.home() / ".niggless-jobber" / "personal_info.json"

LABEL_FIELD_MAP: dict[str, str] = {
    "first name": "name.first",
    "last name": "name.last",
    "full name": "name.full",
    "name": "name.full",
    "email": "email",
    "email address": "email",
    "phone": "phone",
    "phone number": "phone",
    "mobile": "phone",
    "linkedin": "linkedin_url",
    "linkedin url": "linkedin_url",
    "github": "github_url",
    "github url": "github_url",
    "website": "website_url",
    "portfolio": "website_url",
    "city": "address.city",
    "location": "address.city",
    "country": "address.country",
    "years of experience": "years_of_experience",
    "years experience": "years_of_experience",
    "experience": "years_of_experience",
    "cover letter": "_cover_letter_text",
    "salary": "_salary_expectation",
    "salary expectation": "_salary_expectation",
    "expected salary": "_salary_expectation",
    "notice period": "_notice_period",
    "start date": "_notice_period",
    "work authorization": "work_authorization",
    "work eligibility": "work_authorization",
    "visa sponsorship": "_sponsorship_required",
    "sponsorship": "_sponsorship_required",
    "current company": "_current_company",
    "current employer": "_current_company",
    "current title": "_current_title",
    "current role": "_current_title",
    "how did you hear": "_hear_about",
    "referral": "_hear_about",
}

EEO_KEYWORDS = ("gender", "race", "ethnicity", "disability", "veteran", "diversity", "equal opportunity")
TOS_KEYWORDS = ("agree", "terms", "consent", "authorize", "privacy policy")


_REPO_ROOT = Path(__file__).parent.parent


def _load_info() -> dict:
    # Check repo root first (developer convenience), then ~/.niggless-jobber/
    candidates = [
        _REPO_ROOT / "personal_info.json",
        _PERSONAL_INFO,
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


# ---------------------------------------------------------------------------
# Salary resolution
# ---------------------------------------------------------------------------

_CURRENCY_SYMBOLS = {
    "USD": "USD", "$": "USD", "dollar": "USD", "dollars": "USD",
    "EUR": "EUR", "euro": "EUR", "euros": "EUR",
    "PLN": "PLN", "zloty": "PLN", "pln": "PLN",
    "GBP": "GBP", "pound": "GBP", "pounds": "GBP", "gbp": "GBP",
}

_CURRENCY_PREFIXES = {"USD": "$", "EUR": "EUR ", "PLN": "PLN ", "GBP": "GBP "}

# Exchange rates relative to USD (approximate May 2026)
_TO_USD: dict[str, float] = {"USD": 1.0, "EUR": 1.12, "PLN": 0.26, "GBP": 1.27}
_FROM_USD: dict[str, float] = {"USD": 1.0, "EUR": 0.893, "PLN": 3.85, "GBP": 0.787}

_PERIOD_KEYWORDS = {
    "hourly": ("hour", "hourly", "/h", "per hour", "/hr"),
    "monthly": ("month", "monthly", "/m", "per month", "/mo"),
    "yearly": ("year", "yearly", "annual", "/y", "per year", "per annum", "p.a."),
}

_CONTRACT_KEYWORDS = {
    "b2b": ("b2b", "contract", "freelance", "business to business", "self-employed", "b2b/uop"),
    "permanent": ("permanent", "full-time", "employee", "employment", "perm", "uop"),
}

# Regex to find a salary RANGE in job description text.
# Matches patterns like:  $4,000 - $8,000   |   60 000 - 90 000 PLN   |   40-60 EUR/h
_RANGE_RE = re.compile(
    r"(?:[\$\€\£])?\s*(\d[\d\s,\.]*\d|\d+)"   # first number
    r"\s*[-–—to]+\s*"                           # separator
    r"(?:[\$\€\£])?\s*(\d[\d\s,\.]*\d|\d+)"   # second number
    r"\s*"
    r"(USD|EUR|PLN|GBP|\$|euro?s?|zloty|pounds?|dollars?)?"  # optional currency after
    r"\s*"
    r"(?:per\s+)?(hour|hourly|month|monthly|year|yearly|annual|h\b|mo\b|yr\b|/h\b|/m\b|/y\b)?",
    re.IGNORECASE,
)


def _clean_number(s: str) -> float:
    """Remove spaces/commas used as thousand separators and parse."""
    return float(s.replace(" ", "").replace(",", "").replace(".", "").lstrip("0") or "0")


def _detect_currency_in_text(text: str) -> str:
    """Return the first recognisable currency code found in text."""
    for sym, code in _CURRENCY_SYMBOLS.items():
        if re.search(r"\b" + re.escape(sym) + r"\b", text, re.IGNORECASE):
            return code
    if "$" in text:
        return "USD"
    if "\u20ac" in text or "EUR" in text.upper():
        return "EUR"
    if "£" in text:
        return "GBP"
    return "USD"


def _detect_period_in_text(text: str) -> str:
    """Return period keyword (hourly/monthly/yearly) detected in text."""
    t = text.lower()
    for period, kws in _PERIOD_KEYWORDS.items():
        if any(k in t for k in kws):
            return period
    return "yearly"


def extract_salary_range(
    listing_description: str,
    currency: str,
    period: str,
) -> tuple[float, float] | None:
    """
    Scan *listing_description* for a salary range expressed in *currency* and *period*.
    Returns (low, high) as gross amounts in *currency*, or None if not found.
    """
    if not listing_description:
        return None

    best: tuple[float, float] | None = None

    for m in _RANGE_RE.finditer(listing_description):
        try:
            low_raw = _clean_number(m.group(1))
            high_raw = _clean_number(m.group(2))
        except ValueError:
            continue

        if low_raw <= 0 or high_raw <= low_raw:
            continue

        # Plausibility filter: ignore obviously wrong matches (e.g. version numbers)
        if high_raw > 10_000_000 or low_raw < 1:
            continue

        # Currency detection from match or surrounding context
        match_currency_raw = m.group(3) or ""
        found_currency = _CURRENCY_SYMBOLS.get(match_currency_raw.strip(), None)
        if found_currency is None:
            found_currency = _detect_currency_in_text(
                listing_description[max(0, m.start() - 30):m.end() + 30]
            )

        # Period detection
        match_period_raw = m.group(4) or ""
        found_period = _detect_period_in_text(match_period_raw) if match_period_raw else None
        if found_period is None:
            found_period = _detect_period_in_text(
                listing_description[max(0, m.start() - 60):m.end() + 60]
            )

        # Normalise to requested currency and period
        low_usd = low_raw * _TO_USD.get(found_currency, 1.0)
        high_usd = high_raw * _TO_USD.get(found_currency, 1.0)

        # Convert period to monthly for comparison
        if found_period == "hourly":
            low_usd *= 160; high_usd *= 160
        elif found_period == "yearly":
            low_usd /= 12; high_usd /= 12

        # Convert back to target currency in target period
        factor = _FROM_USD.get(currency, 1.0)
        if period == "hourly":
            factor /= 160
        elif period == "yearly":
            factor *= 12

        low_target = low_usd * factor
        high_target = high_usd * factor

        # Keep the widest / most plausible range
        if best is None or (high_target - low_target) > (best[1] - best[0]):
            best = (low_target, high_target)

    return best


def _top_half_of_range(low: float, high: float) -> float:
    """Return the 75th-percentile value of [low, high]."""
    return low + 0.75 * (high - low)


def _pick_salary(
    expectations: dict,
    hint: str,
    listing_description: str = "",
    net_floor_usd_monthly: float = 0.0,
) -> str:
    """
    Pick the salary value to put into an application form field.

    Priority:
    1. If the job description contains a salary range -> use the top quarter of
       that range (never below our net floor).
    2. Otherwise -> use the value from salary_expectations.
    """
    hint_l = hint.lower()
    full_context = hint_l + " " + listing_description.lower()

    # --- Detect contract type ---
    contract = "permanent"
    for key, kws in _CONTRACT_KEYWORDS.items():
        if any(k in full_context for k in kws):
            contract = key
            break

    # --- Detect period ---
    period = "yearly"
    for key, kws in _PERIOD_KEYWORDS.items():
        if any(k in hint_l for k in kws):
            period = key
            break

    # --- Detect currency ---
    currency = _detect_currency_in_text(hint)

    # --- Compute floor in the target currency and period ---
    floor_usd_monthly = net_floor_usd_monthly
    # Inflate to gross:  B2B ~23% tax,  permanent ~30%
    gross_factor = 1.0 / 0.77 if contract == "b2b" else 1.0 / 0.70
    floor_gross_monthly_usd = floor_usd_monthly * gross_factor
    floor = floor_gross_monthly_usd * _FROM_USD.get(currency, 1.0)
    if period == "hourly":
        floor /= 160
    elif period == "yearly":
        floor *= 12

    # --- Try range from job description ---
    salary_range = extract_salary_range(listing_description, currency, period)
    if salary_range:
        low, high = salary_range
        picked = _top_half_of_range(low, high)
        # Never go below our floor
        picked = max(picked, floor)
        # Round to a clean number
        magnitude = 10 ** max(0, len(str(int(picked))) - 2)
        picked = round(picked / magnitude) * magnitude
        log.debug(
            "Salary from JD range: %.0f-%.0f %s %s -> %.0f (floor=%.0f)",
            low, high, currency, period, picked, floor,
        )
        prefix = _CURRENCY_PREFIXES.get(currency, "")
        return f"{prefix}{int(picked):,}"

    # --- Fall back to personal_info values ---
    subtree = expectations.get(contract, expectations.get("permanent", {}))
    period_tree = subtree.get(period)
    if period_tree is None:
        for fallback in ("monthly", "yearly", "hourly"):
            period_tree = subtree.get(fallback)
            if period_tree:
                break

    if not period_tree:
        return ""

    amount = period_tree.get(currency, period_tree.get("USD", 0))
    if not amount:
        return ""

    prefix = _CURRENCY_PREFIXES.get(currency, "")
    return f"{prefix}{amount:,}"


def _resolve(
    field_key: str,
    info: dict,
    cover_letter_text: str = "",
    hint: str = "",
    listing_description: str = "",
) -> str:
    if field_key == "_salary_expectation":
        expectations = info.get("salary_expectations")
        if expectations:
            return _pick_salary(
                expectations,
                hint,
                listing_description=listing_description,
                net_floor_usd_monthly=float(info.get("net_floor_usd_monthly") or 0),
            )
        # Legacy flat field
        return str(info.get("salary_expectation", ""))

    special = {
        "_cover_letter_text": cover_letter_text,
        "_notice_period": info.get("notice_period", ""),
        "_sponsorship_required": "No" if not info.get("sponsorship_required") else "Yes",
        "_current_company": (info.get("work_history") or [{}])[0].get("company", ""),
        "_current_title": (info.get("work_history") or [{}])[0].get("title", ""),
        "_hear_about": "Job board",
    }
    if field_key in special:
        return special[field_key]

    parts = field_key.split(".")
    value = info
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            value = ""
    return str(value) if value else ""


def _best_label_match(label_text: str) -> str | None:
    label_lower = label_text.lower().strip()
    if label_lower in LABEL_FIELD_MAP:
        return LABEL_FIELD_MAP[label_lower]
    # Fuzzy match
    matches = difflib.get_close_matches(label_lower, LABEL_FIELD_MAP.keys(), n=1, cutoff=0.75)
    return LABEL_FIELD_MAP.get(matches[0]) if matches else None


async def fill_form(
    page: "Page",
    cover_letter_text: str = "",
    listing_description: str = "",
) -> list[str]:
    """
    Fill all visible form fields on the page.
    Returns a list of field labels that could not be mapped.
    """
    info = _load_info()
    unmapped: list[str] = []

    inputs = await page.query_selector_all("input:visible, textarea:visible, select:visible")
    for el in inputs:
        tag = await el.evaluate("e => e.tagName.toLowerCase()")
        input_type = (await el.get_attribute("type") or "text").lower()

        if input_type in ("submit", "button", "reset", "hidden", "file"):
            continue

        label_text = await _get_label(page, el)
        placeholder = await el.get_attribute("placeholder") or ""
        name_attr = await el.get_attribute("name") or ""
        hint = label_text or placeholder or name_attr

        # EEO / diversity checkboxes -> prefer not to answer / skip
        if any(k in hint.lower() for k in EEO_KEYWORDS):
            if tag == "select":
                await _select_prefer_not(el)
            continue

        # Terms of service checkboxes -> check them
        if tag == "input" and input_type == "checkbox":
            if any(k in hint.lower() for k in TOS_KEYWORDS):
                checked = await el.is_checked()
                if not checked:
                    await el.check()
            continue

        field_key = _best_label_match(hint)
        if field_key is None:
            log.debug("Unmapped field: label=%r name=%r", label_text, name_attr)
            unmapped.append(hint or name_attr)
            continue

        value = _resolve(field_key, info, cover_letter_text, hint=hint,
                         listing_description=listing_description)
        if not value:
            log.debug("No value for field_key=%r (hint=%r)", field_key, hint)
            continue

        try:
            if tag == "select":
                await _fill_select(el, value)
            elif tag == "textarea" or input_type == "text":
                await el.fill(value)
            elif input_type in ("tel", "email", "url", "number"):
                await el.fill(value)
        except Exception as exc:
            log.warning("Could not fill field %r: %s", hint, exc)

    return unmapped


async def _get_label(page: "Page", el) -> str:
    el_id = await el.get_attribute("id")
    if el_id:
        label = await page.query_selector(f"label[for='{el_id}']")
        if label:
            return await label.inner_text()
    # Try aria-label
    aria = await el.get_attribute("aria-label")
    if aria:
        return aria
    # Walk up to find a wrapping label
    try:
        text = await el.evaluate(
            "e => { let p = e.parentElement; for(let i=0;i<3;i++){ if(!p) break; const l=p.querySelector('label'); if(l) return l.innerText; p=p.parentElement; } return ''; }"
        )
        return text.strip()
    except Exception:
        return ""


async def _fill_select(el, value: str) -> None:
    options: list[str] = await el.evaluate(
        "e => Array.from(e.options).map(o => o.text)"
    )
    matches = difflib.get_close_matches(value, options, n=1, cutoff=0.5)
    target = matches[0] if matches else None
    if target:
        await el.select_option(label=target)
    else:
        # Try selecting by value directly
        try:
            await el.select_option(value=value)
        except Exception:
            pass


async def _select_prefer_not(el) -> None:
    options: list[str] = await el.evaluate(
        "e => Array.from(e.options).map(o => o.text)"
    )
    for opt in options:
        if any(k in opt.lower() for k in ("prefer not", "decline", "not disclose", "i prefer")):
            await el.select_option(label=opt)
            return
