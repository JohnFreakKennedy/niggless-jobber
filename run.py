#!/usr/bin/env python3
"""
niggless-jobber -- main entry point.

Usage:
  python run.py                          # full run
  python run.py --dry-run                # scrape + generate PDFs, no submissions
  python run.py --init                   # first-time setup (vault + personal_info)
  python run.py --auth-google            # OAuth2 flow for Google Workspace (Sheets/Calendar)
  python run.py --auth-google-account    # log in to Google in a browser (for job sites)
  python run.py --test-google            # verify Google API access
  python run.py --open-sheets            # open the tracking spreadsheet in browser
  python run.py --vault-set KEY VALUE    # write a secret to the vault
  python run.py --vault-get KEY          # read a secret from the vault
  python run.py --vault-list             # list all vault keys
  python run.py --add-totp HOST SEED     # add a TOTP seed for an MFA-protected site
  python run.py --retry-only             # only retry previously failed applications
  python run.py --no-apply               # scrape + generate PDFs, skip submission
  python run.py --url URL                # apply to a single job URL
  python run.py --create-calendar        # create a dedicated Google Calendar
  python run.py --daemon                 # run as a persistent daily scheduler
  python run.py --setup-gmail            # interactive Gmail IMAP credentials setup
  python run.py --check-credentials      # show vault key status table
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("niggless-jobber")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent
_CONFIG_PATH = _REPO_ROOT / "config.yaml"
_DATA_DIR = Path(os.environ.get("NIGGLESS_DATA_DIR", "") or Path.home() / ".niggless-jobber")
_OUTPUT_DIR = _REPO_ROOT / "output"
_LOG_DIR = _DATA_DIR / "logs"


def load_config() -> dict:
    with _CONFIG_PATH.open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="niggless-jobber job application automator")
    p.add_argument("--dry-run", action="store_true", help="Generate PDFs but do not submit")
    p.add_argument("--init", action="store_true", help="First-time setup wizard")
    p.add_argument("--auth-google", action="store_true", help="Run Google OAuth2 flow (Sheets/Calendar)")
    p.add_argument("--auth-google-account", action="store_true",
                   help="Log in to Google in a browser so job sites accept 'Sign in with Google'")
    p.add_argument("--credentials", default=None, help="Path to Google credentials JSON (used with --auth-google)")
    p.add_argument("--test-google", action="store_true", help="Verify Google API connectivity")
    p.add_argument("--open-sheets", action="store_true", help="Open the tracking spreadsheet")
    p.add_argument("--vault-set", nargs=2, metavar=("KEY", "VALUE"), help="Set a vault secret")
    p.add_argument("--vault-get", metavar="KEY", help="Get a vault secret")
    p.add_argument("--vault-list", action="store_true", help="List all vault keys")
    p.add_argument("--add-totp", nargs=2, metavar=("HOST", "SEED"), help="Add TOTP seed for a hostname")
    p.add_argument("--limit", type=int, default=None, metavar="N",
                   help="Apply to at most N jobs this run (overrides max_per_run in config.yaml)")
    p.add_argument("--retry-only", action="store_true", help="Only retry failed applications")
    p.add_argument("--no-apply", action="store_true", help="Scrape and generate PDFs only")
    p.add_argument("--url", default=None, metavar="URL", help="Apply to a single job URL")
    p.add_argument("--create-calendar", action="store_true", help="Create a dedicated Google Calendar")
    p.add_argument("--daemon", action="store_true", help="Run as persistent daily scheduler")
    p.add_argument("--check-credentials", action="store_true", help="Show which vault keys are set vs missing")
    p.add_argument("--setup-gmail", action="store_true", help="Interactive Gmail IMAP credentials setup")
    p.add_argument("--format-sheets", action="store_true", help="Apply conditional formatting to Sheets")
    p.add_argument("--edit-info", action="store_true", help="Open personal_info.json in $EDITOR")
    p.add_argument("--reset-run", action="store_true", help="Clear any stuck 'in progress' run lock from the database")
    return p


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def cmd_init() -> None:
    from storage.vault import init_vault
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Vault
    vault_path = _DATA_DIR / "vault.enc"
    if not vault_path.exists():
        init_vault()
    else:
        print(f"Vault already exists at {vault_path}")

    # personal_info.json
    pi_path = _DATA_DIR / "personal_info.json"
    if not pi_path.exists():
        template = _REPO_ROOT / "templates" / "personal_info.json.example"
        shutil.copy(template, pi_path)
        print(f"Personal info template copied to {pi_path}")
        print("Please edit it with your real information.")
    else:
        print(f"personal_info.json already exists at {pi_path}")

    print("\nSetup complete. Next steps:")
    print("  1. Edit", pi_path)
    print("  2. Add an AI key (pick one):")
    print("       python run.py --vault-set OPENAI_API_KEY sk-...")
    print("       python run.py --vault-set GEMINI_API_KEY AIza...")
    print("       python run.py --vault-set ANTHROPIC_API_KEY sk-ant-...")
    print("  3. Set up Gmail for MFA code collection:")
    print("       python run.py --setup-gmail")
    print("  4. Log in to Google so job sites accept 'Sign in with Google':")
    print("       python run.py --vault-set GOOGLE_EMAIL you@gmail.com")
    print("       python run.py --vault-set GOOGLE_PASSWORD yourpassword")
    print("       python run.py --auth-google-account")
    print("  5. Add Google Workspace auth for Sheets/Calendar (optional):")
    print("       python run.py --auth-google --credentials /path/to/creds.json")
    print("  6. Install cron: bash setup_cron.sh")
    print("  7. Test: python run.py --dry-run")


def cmd_auth_google(credentials_path: str | None) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow
    from storage.vault import set_secret
    import json

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/calendar",
    ]
    creds_file = credentials_path or str(_DATA_DIR / "google_credentials.json")
    if not Path(creds_file).exists():
        print(f"ERROR: credentials file not found at {creds_file}")
        print("Download it from Google Cloud Console: APIs & Services -> Credentials -> Desktop App")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(creds_file, scopes)
    creds = flow.run_local_server(port=0)
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
    }
    set_secret("GOOGLE_OAUTH_TOKEN", json.dumps(token_data))
    print("Google OAuth2 token stored in vault.")


def cmd_check_credentials() -> None:
    """
    Print a table of every vault key the system can use, showing SET or MISSING.
    """
    from storage.vault import get_secret

    def row(label: str, key: str, required: bool = True) -> str:
        val = get_secret(key)
        status = "[SET]    " if val else "[MISSING]"
        req = "required" if required else "optional"
        return f"  {status} {key:<30}  ({req}) {label}"

    lines = [
        "",
        "=" * 64,
        "niggless-jobber credential check",
        "=" * 64,
        "",
        "-- AI provider (at least one required) --",
        row("OpenAI GPT-4o",                  "OPENAI_API_KEY"),
        row("Google Gemini",                   "GEMINI_API_KEY",      required=False),
        row("Anthropic Claude",                "ANTHROPIC_API_KEY",   required=False),
        "",
        "-- Google account for job sites (sign-in with Google) --",
        row("Google email (job sites)",        "GOOGLE_EMAIL",        required=False),
        row("Google password (job sites)",     "GOOGLE_PASSWORD",     required=False),
        "",
        "-- Job boards --",
        row("LinkedIn login email",            "LINKEDIN_EMAIL"),
        row("LinkedIn login password",         "LINKEDIN_PASSWORD"),
        row("JustJoinIT login email",          "JUSTJOINIT_EMAIL",    required=False),
        row("JustJoinIT login password",       "JUSTJOINIT_PASSWORD", required=False),
        row("Djinni login email",              "DJINNI_EMAIL",        required=False),
        row("Djinni login password",           "DJINNI_PASSWORD",     required=False),
        row("Indeed login email",              "INDEED_EMAIL",        required=False),
        row("Indeed login password",           "INDEED_PASSWORD",     required=False),
        "",
        "-- CAPTCHA solver (at least one required) --",
        row("2captcha API key",                "2CAPTCHA_API_KEY"),
        row("CapSolver API key",               "CAPSOLVER_API_KEY",   required=False),
        "",
        "-- Gmail / email OTP --",
        row("IMAP server (imap.gmail.com)",    "IMAP_SERVER"),
        row("IMAP port (993)",                 "IMAP_PORT"),
        row("Gmail address",                   "IMAP_EMAIL"),
        row("Gmail App Password",              "IMAP_PASSWORD"),
        "",
        "-- Google Workspace (optional) --",
        row("Google OAuth2 token",             "GOOGLE_OAUTH_TOKEN",  required=False),
        row("Spreadsheet ID (auto-set)",       "GOOGLE_SHEET_ID",     required=False),
        row("Calendar ID (auto-set)",          "GOOGLE_CALENDAR_ID",  required=False),
        "",
        "-- iCIMS account (optional) --",
        row("iCIMS account password",          "ICIMS_PASSWORD",      required=False),
        "",
        "To set a key:        python run.py --vault-set KEY value",
        "Gmail setup:         python run.py --setup-gmail",
        "Google Workspace:    python run.py --auth-google --credentials /path/to/creds.json",
        "Google job login:    python run.py --auth-google-account",
        "",
    ]
    print("\n".join(lines))


def cmd_setup_gmail() -> None:
    """
    Interactive wizard to store Gmail IMAP credentials in the vault.

    Gmail requires an App Password (not your regular account password) when
    2-Step Verification is enabled.  Generate one at:
      https://myaccount.google.com/apppasswords
    Select "Mail" + "Other (custom name)" -> name it "niggless-jobber".
    """
    from storage.vault import set_secret
    print("Gmail IMAP setup")
    print("-" * 40)
    print("If you use 2-Step Verification (recommended) you need a Gmail App Password.")
    print("Generate one at: https://myaccount.google.com/apppasswords")
    print("Select App: Mail  |  Device: Other  |  Name: niggless-jobber")
    print()
    import getpass
    email = input("Gmail address: ").strip()
    app_password = getpass.getpass("App Password (16 chars, no spaces): ").strip()
    set_secret("IMAP_SERVER", "imap.gmail.com")
    set_secret("IMAP_PORT", "993")
    set_secret("IMAP_EMAIL", email)
    set_secret("IMAP_PASSWORD", app_password)
    print()
    print("Stored in vault:")
    print("  IMAP_SERVER  = imap.gmail.com")
    print("  IMAP_PORT    = 993")
    print(f"  IMAP_EMAIL   = {email}")
    print("  IMAP_PASSWORD = (hidden)")
    print()
    print("The MFA handler will now poll this inbox for verification codes.")


def cmd_auth_google_account() -> None:
    """
    Log in to Google using Playwright and save the browser profile so that
    job sites which offer 'Sign in with Google' authenticate automatically.

    Before running this, store credentials:
      python run.py --vault-set GOOGLE_EMAIL you@gmail.com
      python run.py --vault-set GOOGLE_PASSWORD yourpassword
    """
    from scrapers.google_auth import login, GOOGLE_PROFILE_DIR
    print("This will open a Chromium window to complete Google login.")
    print(f"Profile will be saved to: {GOOGLE_PROFILE_DIR}")
    asyncio.get_event_loop().run_until_complete(login(headless=False))


def cmd_reset_run() -> None:
    """Clear any stuck 'running' entries in run_log so a new run can start."""
    from storage.db import get_db
    db = get_db()
    result = db.execute(
        "UPDATE run_log SET status = 'reset', finished_at = datetime('now') WHERE status = 'running'"
    )
    affected = result.rowcount
    db.conn.commit()
    if affected:
        print(f"Cleared {affected} stuck run(s). You can now start a new run.")
    else:
        print("No stuck runs found.")


def cmd_test_google() -> None:
    try:
        from tracking.sheets import _get_service as get_sheets
        get_sheets()
        print("Google Sheets: OK")
    except Exception as exc:
        print(f"Google Sheets: FAIL ({exc})")
    try:
        from tracking.calendar import _get_service as get_cal
        get_cal()
        print("Google Calendar: OK")
    except Exception as exc:
        print(f"Google Calendar: FAIL ({exc})")


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(cfg: dict, dry_run: bool = False, no_apply: bool = False, limit: int | None = None) -> dict:
    from storage import db
    from parser.extractor import normalise_many

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if db.is_run_in_progress():
        log.error("Another run is already in progress. Exiting.")
        return {}

    run_id = db.start_run()
    stats = {"scraped": 0, "new": 0, "applied": 0, "failed": 0, "skipped": 0}

    try:
        # ------------------------------------------------------------------
        # 1. Scrape
        # ------------------------------------------------------------------
        search_cfg = cfg.get("search", {})
        keywords = search_cfg.get("keywords", [])
        locations = search_cfg.get("locations", [])
        sources = search_cfg.get("sources", [])
        max_per_source = search_cfg.get("max_per_source", 50)

        # When --limit is given, cap scraping so we don't fetch hundreds of
        # listings only to apply to 5.  3x the limit gives headroom for dedup.
        if limit is not None:
            max_per_source = min(max_per_source, max(limit * 3, 15))
            log.info("Scrape cap: %d listings per source (limit=%d)", max_per_source, limit)

        all_raw: list[dict] = []
        scraper_tasks = []

        scraper_map = _build_scraper_map(keywords, locations, max_per_source)
        for source in sources:
            if source in scraper_map:
                scraper_tasks.append((source, scraper_map[source]))

        log.info("Starting scrape from %d sources...", len(scraper_tasks))
        results = await asyncio.gather(
            *[s.run() for _, s in scraper_tasks],
            return_exceptions=True,
        )
        for (source, _), result in zip(scraper_tasks, results):
            if isinstance(result, Exception):
                log.error("Scraper %s failed: %s", source, result)
                continue
            raw_list = normalise_many(result, source)
            for listing in raw_list:
                all_raw.append(listing)
            log.info("Scraped %d listings from %s", len(result), source)

        stats["scraped"] = len(all_raw)
        log.info("Total scraped: %d", stats["scraped"])

        # ------------------------------------------------------------------
        # 2. Deduplicate
        # ------------------------------------------------------------------
        # Load URLs already logged in Google Sheets (guards against DB wipes)
        try:
            from tracking.sheets import get_applied_urls
            sheets_applied_urls = get_applied_urls()
        except Exception:
            sheets_applied_urls = set()

        new_listings = []
        _dedup_limit = (limit * 3) if limit is not None else None
        for listing in all_raw:
            if _dedup_limit and len(new_listings) >= _dedup_limit:
                break
            if db.job_exists(listing.id):
                continue
            if db.already_applied(listing.id):
                continue
            if listing.url in sheets_applied_urls or listing.apply_url in sheets_applied_urls:
                log.info("Skipping %s (already in Sheets): %s", listing.company, listing.url)
                continue
            db.insert_job(listing.to_db_dict())
            new_listings.append(listing)

        stats["new"] = len(new_listings)
        log.info("New listings after dedup: %d", stats["new"])

        if not new_listings:
            db.finish_run(run_id, **stats)
            return stats

        # ------------------------------------------------------------------
        # 3. AI Pipeline + Application
        # ------------------------------------------------------------------
        app_cfg = cfg.get("application", {})
        max_per_run = limit if limit is not None else app_cfg.get("max_per_run", 20)
        if limit is not None:
            log.info("--limit %d overrides config max_per_run", limit)
        blocked_companies = {c.lower() for c in (app_cfg.get("blocked_companies") or [])}
        headless = app_cfg.get("headless", True)

        ai_cfg_raw = cfg.get("ai", {})
        from cv_editor.client import AIClient, AIConfig
        ai_config = AIConfig(
            provider=os.environ.get("NIGGLESS_AI_PROVIDER") or ai_cfg_raw.get("provider", "openai"),
            model=ai_cfg_raw.get("model", "gpt-4o"),
            fallback_provider=ai_cfg_raw.get("fallback_provider", "anthropic"),
            fallback_model=ai_cfg_raw.get("fallback_model", "claude-3-5-sonnet-20241022"),
            temperature=ai_cfg_raw.get("temperature", 0.3),
            cv_max_tokens=ai_cfg_raw.get("cv_max_tokens", 4096),
            cover_letter_max_tokens=ai_cfg_raw.get("cover_letter_max_tokens", 1024),
        )
        ai_client = AIClient(ai_config)

        # Playwright browser (shared for all applications)
        browser = None
        if not no_apply and not dry_run:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            browser = await pw.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )

        applied_count = 0
        for listing in new_listings:
            if applied_count >= max_per_run:
                log.info("max_per_run (%d) reached; stopping", max_per_run)
                break

            if listing.company.lower() in blocked_companies:
                log.info("Skipping blocked company: %s", listing.company)
                db.insert_application(listing.id, "skipped", reason="blocked_company")
                stats["skipped"] += 1
                continue

            log.info("Processing: %s at %s", listing.title, listing.company)

            # Output directory for this job
            job_dir = _OUTPUT_DIR / listing.id[:12]
            job_dir.mkdir(parents=True, exist_ok=True)

            # CV tailoring
            cv_path = None
            pv_cv = ""
            try:
                from cv_editor.editor import tailor_cv, prompt_version as cv_pv
                from cover_letter.compiler import compile_pdf
                cv_tex = await tailor_cv(listing, job_dir, ai_client)
                cv_path = compile_pdf(cv_tex)
                pv_cv = cv_pv()
            except Exception as exc:
                log.warning("CV generation failed for %s: %s", listing.id[:12], exc)

            # Cover letter
            cl_path = None
            pv_cl = ""
            try:
                from cover_letter.generator import generate_cover_letter, prompt_version as cl_pv
                from cover_letter.compiler import compile_pdf
                cl_tex = await generate_cover_letter(listing, job_dir, ai_client)
                cl_path = compile_pdf(cl_tex)
                pv_cl = cl_pv()
            except Exception as exc:
                log.warning("Cover letter generation failed for %s: %s", listing.id[:12], exc)

            if no_apply:
                db.insert_application(
                    listing.id, "dry_run",
                    cv_path=str(cv_path) if cv_path else None,
                    cover_letter_path=str(cl_path) if cl_path else None,
                    prompt_version_cv=pv_cv, prompt_version_cl=pv_cl,
                )
                continue

            # Apply
            from applicator.engine import apply as do_apply
            result = await do_apply(
                listing, cv_path, cl_path, browser,
                dry_run=dry_run or os.environ.get("NIGGLESS_DRY_RUN") == "1",
                prompt_version_cv=pv_cv,
                prompt_version_cl=pv_cl,
            )

            # Persist result
            from applicator.ats.base import ApplicationStatus
            db.insert_application(
                listing.id,
                result.status.value,
                reason=result.reason,
                applied_at=result.applied_at,
                cv_path=str(result.cv_path) if result.cv_path else None,
                cover_letter_path=str(result.cover_letter_path) if result.cover_letter_path else None,
                prompt_version_cv=result.prompt_version_cv,
                prompt_version_cl=result.prompt_version_cl,
            )

            # Track
            tracking_cfg = cfg.get("tracking", {})
            if tracking_cfg.get("enabled", True):
                if tracking_cfg.get("sheets", True):
                    from tracking.sheets import append_application
                    append_application(result, listing)
                if tracking_cfg.get("calendar", True) and result.status == ApplicationStatus.APPLIED:
                    from tracking.calendar import create_followup_event
                    create_followup_event(result, listing, tracking_cfg.get("followup_weeks", 3))

            if result.status == ApplicationStatus.APPLIED:
                stats["applied"] += 1
                applied_count += 1
            elif result.status == ApplicationStatus.FAILED:
                stats["failed"] += 1
            elif result.status == ApplicationStatus.SKIPPED:
                stats["skipped"] += 1

        if browser:
            await browser.close()
            await pw.stop()

        # ------------------------------------------------------------------
        # 4. Retry failed applications
        # ------------------------------------------------------------------
        if not no_apply and not dry_run:
            await retry_failed(cfg, stats)

        # ------------------------------------------------------------------
        # 5. Cleanup old files
        # ------------------------------------------------------------------
        _cleanup(cfg)

    except Exception as exc:
        log.exception("Pipeline error")
        db.finish_run(run_id, error=str(exc), **stats)
        return stats

    db.finish_run(run_id, **stats)
    log.info(
        "Run complete: scraped=%d new=%d applied=%d failed=%d skipped=%d",
        stats["scraped"], stats["new"], stats["applied"], stats["failed"], stats["skipped"],
    )

    # Update the Summary tab with totals for this run
    try:
        from tracking.sheets import update_summary
        update_summary(stats)
    except Exception as _exc:
        log.debug("Summary update skipped: %s", _exc)

    return stats


async def retry_failed(cfg: dict, stats: dict) -> None:
    from storage import db
    from applicator.engine import apply as do_apply
    from applicator.ats.base import ApplicationStatus

    pending = db.get_pending_retries(max_retries=3, limit=10)
    if not pending:
        return

    log.info("Retrying %d failed applications...", len(pending))
    app_cfg = cfg.get("application", {})
    headless = app_cfg.get("headless", True)

    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless)

    for row in pending:
        from parser.schema import JobListing
        from parser.extractor import _parse_date
        listing_data = db.get_job(row["job_id"])
        if not listing_data:
            continue
        listing = JobListing(
            source=listing_data["source"],
            title=listing_data["title"],
            company=listing_data["company"],
            url=listing_data["url"],
            apply_url=listing_data["apply_url"],
            description=listing_data["description"],
            skills=listing_data.get("skills", []),
            location=listing_data.get("location", ""),
            salary_raw=listing_data.get("salary_raw", ""),
            posted_at=_parse_date(listing_data.get("posted_at", "")),
        )
        cv_pdf = Path(row["cv_path"]) if row.get("cv_path") else None
        cl_pdf = Path(row["cover_letter_path"]) if row.get("cover_letter_path") else None

        new_retry = (row.get("retry_count") or 0) + 1
        result = await do_apply(listing, cv_pdf, cl_pdf, browser, dry_run=False)
        db.update_application_status(
            row["id"],
            result.status.value,
            reason=result.reason,
            applied_at=result.applied_at,
            retry_count=new_retry,
        )
        if result.status == ApplicationStatus.APPLIED:
            stats["applied"] += 1
        else:
            stats["failed"] += 1

    await browser.close()
    await pw.stop()


async def run_single_url(url: str, cfg: dict, dry_run: bool = False) -> None:
    """Apply to a single job URL given on the command line."""
    from parser.schema import JobListing
    from datetime import datetime
    log.info("Applying to single URL: %s", url)
    listing = JobListing(
        source="manual",
        title="Manual Application",
        company="Unknown",
        url=url,
        apply_url=url,
        description="",
        posted_at=datetime.utcnow(),
    )

    from cv_editor.client import AIClient, AIConfig
    ai_client = AIClient(AIConfig())
    job_dir = _OUTPUT_DIR / listing.id[:12]
    job_dir.mkdir(parents=True, exist_ok=True)

    from cv_editor.editor import tailor_cv
    from cover_letter.generator import generate_cover_letter
    from cover_letter.compiler import compile_pdf

    cv_tex = await tailor_cv(listing, job_dir, ai_client)
    cv_path = compile_pdf(cv_tex)
    cl_tex = await generate_cover_letter(listing, job_dir, ai_client)
    cl_path = compile_pdf(cl_tex)

    from playwright.async_api import async_playwright
    from applicator.engine import apply as do_apply
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=cfg.get("application", {}).get("headless", True))
    result = await do_apply(listing, cv_path, cl_path, browser, dry_run=dry_run)
    await browser.close()
    await pw.stop()
    log.info("Result: %s (%s)", result.status.value, result.reason or "")


def _build_scraper_map(keywords, locations, max_per_source):
    from scrapers.linkedin import LinkedInScraper
    from scrapers.justjoinit import JustJoinITScraper
    from scrapers.dou import DouScraper
    from scrapers.djinni import DjinniScraper
    from scrapers.indeed import IndeedScraper
    return {
        "linkedin": LinkedInScraper(keywords, locations, max_per_source),
        "justjoinit": JustJoinITScraper(keywords, locations, max_per_source),
        "dou": DouScraper(keywords, locations, max_per_source),
        "djinni": DjinniScraper(keywords, locations, max_per_source),
        "indeed": IndeedScraper(keywords, locations, max_per_source),
    }


def _cleanup(cfg: dict) -> None:
    import datetime
    storage_cfg = cfg.get("storage", {})
    pdf_days = storage_cfg.get("pdf_retention_days", 90)
    log_days = storage_cfg.get("log_retention_days", 30)
    cutoff_pdf = datetime.datetime.now() - datetime.timedelta(days=pdf_days) if pdf_days else None
    cutoff_log = datetime.datetime.now() - datetime.timedelta(days=log_days) if log_days else None
    if cutoff_pdf and _OUTPUT_DIR.exists():
        for d in _OUTPUT_DIR.iterdir():
            if d.is_dir() and datetime.datetime.fromtimestamp(d.stat().st_mtime) < cutoff_pdf:
                shutil.rmtree(d, ignore_errors=True)
    if cutoff_log and _LOG_DIR.exists():
        for f in _LOG_DIR.glob("*.log"):
            if datetime.datetime.fromtimestamp(f.stat().st_mtime) < cutoff_log:
                f.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Daemon mode
# ---------------------------------------------------------------------------

def run_daemon(cfg: dict) -> None:
    import schedule
    import time

    tz_name = os.environ.get("TZ", "UTC")
    log.info("Daemon mode: scheduling daily run at 12:00 %s", tz_name)

    def job():
        asyncio.run(run_pipeline(cfg))

    schedule.every().day.at("12:00").do(job)
    while True:
        schedule.run_pending()
        time.sleep(30)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # These commands do not need config.yaml and must work before --init is run.
    if args.init:
        cmd_init()
        return

    if args.vault_set:
        from storage.vault import set_secret
        set_secret(args.vault_set[0], args.vault_set[1])
        print(f"Vault: {args.vault_set[0]} set.")
        return

    if args.vault_get:
        from storage.vault import get_secret
        val = get_secret(args.vault_get)
        print(val or "(not set)")
        return

    if args.vault_list:
        from storage.vault import list_secret_keys
        for k in list_secret_keys():
            print(k)
        return

    if args.add_totp:
        from storage.vault import add_totp_seed
        add_totp_seed(args.add_totp[0], args.add_totp[1])
        print(f"TOTP seed added for {args.add_totp[0]}")
        return

    if args.check_credentials:
        cmd_check_credentials()
        return

    if args.setup_gmail:
        cmd_setup_gmail()
        return

    if args.reset_run:
        cmd_reset_run()
        return

    if args.auth_google:
        cmd_auth_google(args.credentials)
        return

    if args.auth_google_account:
        cmd_auth_google_account()
        return

    # Everything below requires config.yaml to exist.
    cfg = load_config()

    if args.test_google:
        cmd_test_google()
        return

    if args.open_sheets:
        from tracking.sheets import open_url
        import webbrowser
        url = open_url()
        webbrowser.open(url)
        print(url)
        return

    if args.create_calendar:
        from tracking.calendar import create_dedicated_calendar
        cal_id = create_dedicated_calendar()
        print(f"Calendar created: {cal_id}")
        return

    if args.edit_info:
        pi_path = _DATA_DIR / "personal_info.json"
        editor = os.environ.get("EDITOR", "vi")
        os.execvp(editor, [editor, str(pi_path)])
        return

    if args.daemon:
        run_daemon(cfg)
        return

    # Set up log file for this run
    import datetime
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _LOG_DIR / f"run_{datetime.date.today()}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    dry_run = args.dry_run or os.environ.get("NIGGLESS_DRY_RUN") == "1"

    if args.url:
        asyncio.run(run_single_url(args.url, cfg, dry_run=dry_run))
        return

    if args.retry_only:
        async def _retry_only():
            stats = {"applied": 0, "failed": 0, "skipped": 0, "scraped": 0, "new": 0}
            await retry_failed(cfg, stats)
            log.info("Retry run: applied=%d failed=%d", stats["applied"], stats["failed"])
        asyncio.run(_retry_only())
        return

    asyncio.run(run_pipeline(cfg, dry_run=dry_run, no_apply=args.no_apply, limit=args.limit))


if __name__ == "__main__":
    main()
