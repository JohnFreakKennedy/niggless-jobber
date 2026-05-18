# Architecture

This document describes the end-to-end system design of niggless-jobber: how components are
organised, how data flows between them, and which third-party services are involved.

---

## High-Level Diagram

```
+------------------+
|  Cron Job        |  fires at 12:00 PM local time daily
|  (setup_cron.sh) |
+--------+---------+
         |
         v
+--------+---------+
|   run.py         |  top-level orchestrator; reads config.yaml
|   Orchestrator   |
+--------+---------+
         |
         | 1. Scrape
         v
+-------------------------------------------------------------+
|                     Scraping Layer                          |
|                                                             |
|  linkedin.py   justjoinit.py   dou.py   djinni.py          |
|  indeed.py     (company ATS fallback via Playwright)        |
+------------------------------+------------------------------+
                               |
                               | raw HTML / JSON per listing
                               v
+------------------------------+------------------------------+
|                  parser/extractor.py                        |
|  Extracts: title, company, description, skills, salary,     |
|  location, posted_at, source, apply_url                     |
+------------------------------+------------------------------+
                               |
                               | JobListing dataclass
                               v
+------------------------------+------------------------------+
|                  storage/db.py  (SQLite)                    |
|  Deduplication via SHA-256(source+company+title+posted_at)  |
|  Skips listings already in `jobs` table                     |
+------------------------------+------------------------------+
                               |
                               | new listings only
                               v
+------------------------------+------------------------------+
|                    AI Pipeline                              |
|                                                             |
|  cv_editor/editor.py                                        |
|    - reads templates/cv.tex                                 |
|    - sends base CV + job description to GPT-4o              |
|    - receives patched .tex; writes output/<job_id>/cv.tex   |
|                                                             |
|  cover_letter/generator.py                                  |
|    - reads personal_info.json + optional sample             |
|    - sends to GPT-4o; receives LaTeX body                   |
|    - writes output/<job_id>/cover_letter.tex                |
|                                                             |
|  cover_letter/compiler.py                                   |
|    - runs xelatex twice on both .tex files                  |
|    - writes output/<job_id>/cv.pdf + cover_letter.pdf       |
+------------------------------+------------------------------+
                               |
                               | PDF paths + JobListing
                               v
+------------------------------+------------------------------+
|                 Application Engine                          |
|                                                             |
|  applicator/engine.py                                       |
|    - selects ATS adapter or heuristic form filler           |
|    - launches Playwright (stealth mode, random user-agent)  |
|                                                             |
|  applicator/ats/                                            |
|    greenhouse.py | lever.py | workday.py | icims.py         |
|                                                             |
|  applicator/captcha.py                                      |
|    - 2captcha / CapSolver for reCAPTCHA v2, hCaptcha        |
|                                                             |
|  applicator/mfa.py                                          |
|    - TOTP via pyotp                                         |
|    - Email OTP via IMAP polling (imaplib)                   |
+------------------------------+------------------------------+
                               |
                               | ApplicationResult
                               v
         +---------------------+---------------------+
         |                                           |
         v                                           v
+--------+---------+                    +-----------+---------+
|  storage/db.py   |                    |  tracking/          |
|  applications    |                    |  sheets.py          |
|  run_log tables  |                    |  calendar.py        |
+------------------+                    +---------------------+
                                                    |
                                         Google Workspace
                                         (Sheets + Calendar)
```

---

## Component Descriptions

### Orchestrator (`run.py`)

The entry point. On each run it:

1. Loads `config.yaml` and resolves secrets from the vault.
2. Calls each enabled scraper concurrently (via `asyncio.gather`).
3. Passes raw results through the parser and deduplication layer.
4. For each new listing, triggers the AI pipeline sequentially (rate limits matter).
5. Passes the generated PDFs and listing data to the application engine.
6. Writes results to SQLite and Google Workspace.
7. Logs the run summary to `logs/run_YYYY-MM-DD.log`.

### Scraping Layer (`scrapers/`)

Each scraper implements the `BaseScraper` interface:

```
BaseScraper
  .search(keywords, locations) -> list[RawListing]
  .fetch_detail(url)           -> str (raw HTML or JSON)
```

Scrapers are async-first. They share a common `httpx.AsyncClient` with retry logic (3 attempts,
exponential backoff). Playwright-based scrapers spin up a shared browser instance for the run.

### Parser (`parser/`)

`extractor.py` receives raw HTML or JSON and produces a `JobListing` dataclass:

```python
@dataclass
class JobListing:
    id: str                 # SHA-256 fingerprint
    source: str
    title: str
    company: str
    url: str
    apply_url: str
    description: str        # cleaned plain text
    skills: list[str]       # extracted noun phrases / tech terms
    location: str
    salary_raw: str
    posted_at: datetime
```

BeautifulSoup4 + lxml handle HTML cleaning. A lightweight keyword extractor (regex + a curated
tech-term list) populates `skills` without requiring a heavy NLP dependency.

### AI Pipeline (`cv_editor/`, `cover_letter/`)

Both modules call the OpenAI (default) or Anthropic (fallback) API. Prompts are versioned in
`prompts.py` so changes are tracked in git. See [AI_PIPELINE.md](AI_PIPELINE.md) for full prompt
designs and token budgets.

### Application Engine (`applicator/`)

Playwright runs in headless mode by default (`config.yaml: headless: true`). On CAPTCHA encounter
the solver API is invoked; the token is injected via `page.evaluate`. On MFA screens the engine
pauses and resolves the code before continuing. Unknown forms use a heuristic label-to-field
mapper. See [APPLICATION_ENGINE.md](APPLICATION_ENGINE.md).

### Storage (`storage/`)

SQLite database at `~/.niggless-jobber/jobs.db`. All reads and writes go through `db.py`
(thin `sqlite-utils` wrapper). Secrets live in `~/.niggless-jobber/vault.enc`, accessed via
`vault.py`. See [STORAGE_AND_SECRETS.md](STORAGE_AND_SECRETS.md).

### Tracking (`tracking/`)

`sheets.py` appends one row per application to the current week's Google Sheet tab.
`calendar.py` creates a Calendar event 3 weeks after application date as a follow-up reminder.
See [GOOGLE_TRACKING.md](GOOGLE_TRACKING.md).

---

## Data Flow Summary

```
Cron
 -> Orchestrator reads config + secrets
 -> Scrapers collect raw listings
 -> Parser normalises to JobListing
 -> DB deduplicates; new listings proceed
 -> AI Pipeline produces cv.pdf + cover_letter.pdf
 -> Application Engine submits the application
 -> Result written to DB + Google Workspace
```

---

## Tech Stack

| Layer | Library / Tool | Version Pinned In |
|---|---|---|
| Language | Python 3.11+ | `.python-version` |
| Browser automation | Playwright + playwright-stealth | `requirements.txt` |
| HTTP client | httpx (async) | `requirements.txt` |
| HTML parsing | BeautifulSoup4 + lxml | `requirements.txt` |
| AI - primary | openai (GPT-4o) | `requirements.txt` |
| AI - fallback | anthropic (Claude 3.5 Sonnet) | `requirements.txt` |
| LaTeX compilation | xelatex (system) | macOS: `mactex` cask |
| Database | sqlite-utils | `requirements.txt` |
| Secrets | cryptography (Fernet) + keyring | `requirements.txt` |
| Config | pydantic-settings + PyYAML | `requirements.txt` |
| Google APIs | gspread + google-api-python-client | `requirements.txt` |
| CAPTCHA | 2captcha-python / capsolver-python | `requirements.txt` |
| TOTP | pyotp | `requirements.txt` |
| Scheduling | system crontab + setup_cron.sh | bash |

---

## External Service Dependencies

| Service | Purpose | Required |
|---|---|---|
| OpenAI API | CV tailoring + cover letter | YES (primary AI) |
| Anthropic API | CV tailoring + cover letter | NO (fallback) |
| 2captcha or CapSolver | CAPTCHA bypass | YES (if CAPTCHAs encountered) |
| Google Sheets API | Application log | NO (disable in config) |
| Google Calendar API | Follow-up reminders | NO (disable in config) |
| LinkedIn (account) | Job scraping + Easy Apply | YES (for LinkedIn source) |
| IMAP email account | Email OTP resolution | YES (if email MFA encountered) |

---

## Directory Ownership

```
run.py              Orchestrator
config.yaml         User config
setup_cron.sh       Scheduler setup
requirements.txt    Python deps
.env.example        Secrets template
templates/          LaTeX base files (user-owned)
scrapers/           Scraping layer
parser/             Normalisation layer
cv_editor/          AI CV editing
cover_letter/       AI cover letter + LaTeX compile
applicator/         Browser automation + ATS adapters
storage/            SQLite DB + Fernet vault
tracking/           Google Workspace integration
output/             Generated PDFs (gitignored)
logs/               Run logs (gitignored)
docs/               This documentation
```
