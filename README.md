# niggless-jobber

Automated job application pipeline that scrapes job boards daily, tailors your CV and cover letter
using AI, submits applications through browser automation, and tracks everything in Google Workspace.

---

## Features

- Scrapes LinkedIn, JustJoinIT, Dou, Djinni, Indeed, and company ATS pages
- Deduplicates listings via a content hash so the same job is never applied to twice
- Rewrites your base CV per job description using GPT-4o (keywords, bullet reordering)
- Generates a personalised cover letter compiled to PDF from a LaTeX template
- Fills application forms automatically via Playwright with stealth anti-detection
- Handles reCAPTCHA / hCaptcha via a third-party solver API
- Handles TOTP and email-OTP MFA
- Supports major ATS platforms: Greenhouse, Lever, Workday, iCIMS
- Logs every application to Google Sheets and creates a Google Calendar reminder
- Stores all secrets locally in an encrypted Fernet vault (never committed to git)
- Runs every day at 12:00 PM in your local timezone via a system cron job

---

## Repository Layout

```
niggless-jobber/
  run.py                          # main entry point
  config.yaml                     # user-editable settings
  setup_cron.sh                   # installs/updates the daily cron entry
  requirements.txt
  .env.example                    # template for environment variables
  .gitignore

  templates/
    cv.tex                        # base CV LaTeX template
    cover_letter.tex              # base cover letter LaTeX template

  scrapers/                       # one module per job board
    linkedin.py
    justjoinit.py
    dou.py
    djinni.py
    indeed.py
    base.py                       # shared scraper interface

  parser/
    extractor.py                  # NLP extraction from raw job HTML
    schema.py                     # JobListing dataclass / output schema

  cv_editor/
    editor.py                     # sends CV + JD to AI, returns patched .tex
    prompts.py                    # prompt templates

  cover_letter/
    generator.py                  # sends personal_info + JD to AI
    prompts.py
    compiler.py                   # runs xelatex, returns PDF path

  applicator/
    engine.py                     # top-level apply() orchestrator
    form_filler.py                # heuristic label -> personal_info mapper
    captcha.py                    # 2captcha / CapSolver integration
    mfa.py                        # TOTP + IMAP email-OTP polling
    ats/
      greenhouse.py
      lever.py
      workday.py
      icims.py
      base.py                     # ATS adapter interface

  storage/
    db.py                         # sqlite-utils wrapper
    vault.py                      # Fernet vault read/write
    schema.sql                    # table definitions

  tracking/
    sheets.py                     # gspread integration
    calendar.py                   # google-api-python-client integration

  output/                         # generated PDFs (gitignored)
  logs/                           # cron + run logs (gitignored)

  docs/
    ARCHITECTURE.md
    SCRAPERS.md
    AI_PIPELINE.md
    APPLICATION_ENGINE.md
    STORAGE_AND_SECRETS.md
    SCHEDULER.md
    GOOGLE_TRACKING.md
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.11+ | `pyenv` recommended |
| xelatex | any recent | `brew install --cask mactex` on macOS |
| Playwright browsers | bundled | installed via `playwright install` |
| OpenAI API key | - | GPT-4o access required |
| Google Cloud project | - | Sheets + Calendar APIs enabled |
| 2captcha or CapSolver account | - | for CAPTCHA bypass |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/your-user/niggless-jobber.git
cd niggless-jobber
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure secrets

```bash
cp .env.example .env
# Fill in API keys - see docs/STORAGE_AND_SECRETS.md
python -c "from storage.vault import init_vault; init_vault()"
```

### 3. Fill in your personal information

Edit `~/.niggless-jobber/personal_info.json` (created on first run, or copy the template):

```bash
python run.py --init
```

### 4. Add your base CV

Copy your CV source to `templates/cv.tex`. The AI editor will never modify this file directly;
it produces a per-job copy under `output/<job_id>/cv.tex`.

### 5. (Optional) Add a sample cover letter

Copy a plain-text or LaTeX sample to `templates/cover_letter_sample.txt`. If omitted, the AI
generates one from scratch using your personal_info.

### 6. Set up Google Workspace credentials

Follow the OAuth2 flow described in `docs/GOOGLE_TRACKING.md`, then run:

```bash
python run.py --auth-google
```

### 7. Install the daily cron job

```bash
bash setup_cron.sh
```

This installs a crontab entry that fires at 12:00 PM in your local timezone every day.

### 8. Run manually for the first time

```bash
python run.py --dry-run          # scrape + parse + generate PDFs, no submissions
python run.py                    # full run including application submissions
```

---

## Configuration

All user-editable settings live in `config.yaml`:

```yaml
search:
  keywords:
    - "senior software engineer"
    - "backend engineer"
  locations:
    - "Remote"
    - "Kyiv"
  sources:
    - linkedin
    - justjoinit
    - dou
    - djinni
    - indeed

ai:
  provider: openai          # or: anthropic
  model: gpt-4o
  cv_max_tokens: 4096
  cover_letter_max_tokens: 1024

application:
  max_per_run: 20
  skip_if_salary_below: 0   # 0 = no filter
  dry_run: false

captcha:
  provider: 2captcha        # or: capsolver
```

---

## Documentation

| Document | Description |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System diagram, component map, data flow |
| [SCRAPERS.md](docs/SCRAPERS.md) | Per-platform scraping strategy and selectors |
| [AI_PIPELINE.md](docs/AI_PIPELINE.md) | CV tailoring and cover letter generation |
| [APPLICATION_ENGINE.md](docs/APPLICATION_ENGINE.md) | Form filling, CAPTCHA/MFA, ATS adapters |
| [STORAGE_AND_SECRETS.md](docs/STORAGE_AND_SECRETS.md) | Database schema, vault, personal_info spec |
| [SCHEDULER.md](docs/SCHEDULER.md) | Cron setup, timezone, idempotency, retries |
| [GOOGLE_TRACKING.md](docs/GOOGLE_TRACKING.md) | Sheets and Calendar integration |

---

## Security Notes

- No secrets are ever committed. `.env`, `vault.enc`, and `personal_info.json` are all gitignored.
- The Fernet vault key is derived from your OS keyring (macOS Keychain / GNOME Keyring / KWallet).
- Generated PDFs under `output/` contain your full personal information and are also gitignored.
- LinkedIn session cookies and IMAP credentials are stored only in the encrypted vault.

---

## License

MIT
