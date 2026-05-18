# Storage and Secrets

This document covers the local SQLite database schema, the encrypted secrets vault, the
`personal_info.json` structure, the `.env` file layout, and the `.gitignore` rules that
ensure no sensitive data is ever committed.

---

## Directory Layout

All runtime data lives under `~/.niggless-jobber/` (user home, outside the git repo):

```
~/.niggless-jobber/
  jobs.db                 SQLite database
  vault.enc               Fernet-encrypted secrets store
  personal_info.json      Your personal details for form filling
  linkedin_session.json   Cached LinkedIn session cookies
  djinni_session.json     Cached Djinni session cookies
  logs/
    run_2026-05-11.log
    cron.log
```

The git repo itself only contains code, templates, and configuration. No data files.

---

## SQLite Database (`jobs.db`)

Managed via `sqlite-utils`. Schema is defined in `storage/schema.sql` and applied on first run
by `storage/db.py`.

### Table: `jobs`

Stores every scraped listing (including ones not applied to).

```sql
CREATE TABLE jobs (
    id              TEXT PRIMARY KEY,    -- SHA-256 fingerprint
    source          TEXT NOT NULL,       -- linkedin | justjoinit | dou | djinni | indeed
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    url             TEXT NOT NULL,
    apply_url       TEXT NOT NULL,
    description     TEXT NOT NULL,
    skills          TEXT NOT NULL,       -- JSON array of strings
    location        TEXT,
    salary_raw      TEXT,
    posted_at       TEXT NOT NULL,       -- ISO 8601
    scraped_at      TEXT NOT NULL        -- ISO 8601
);
```

Deduplication: before inserting, `db.py` checks `SELECT 1 FROM jobs WHERE id = ?`. If the row
already exists, the listing is skipped.

### Table: `applications`

One row per application attempt.

```sql
CREATE TABLE applications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              TEXT NOT NULL REFERENCES jobs(id),
    status              TEXT NOT NULL,   -- applied | failed | skipped | manual_review_needed | dry_run
    reason              TEXT,            -- null on success; error message or skip reason otherwise
    applied_at          TEXT,            -- ISO 8601; null if not yet applied
    retry_count         INTEGER NOT NULL DEFAULT 0,
    cv_path             TEXT,            -- relative path under output/
    cover_letter_path   TEXT,
    prompt_version_cv   TEXT,            -- e.g. "cv-v3"
    prompt_version_cl   TEXT,            -- e.g. "cl-v2"
    created_at          TEXT NOT NULL    -- ISO 8601
);
```

Indexes:

```sql
CREATE INDEX idx_applications_job_id ON applications(job_id);
CREATE INDEX idx_applications_status ON applications(status);
CREATE INDEX idx_applications_applied_at ON applications(applied_at);
```

### Table: `run_log`

One row per daily run.

```sql
CREATE TABLE run_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,       -- ISO 8601
    finished_at     TEXT,
    status          TEXT NOT NULL,       -- running | completed | error
    scraped_count   INTEGER DEFAULT 0,
    new_count       INTEGER DEFAULT 0,   -- new listings after dedup
    applied_count   INTEGER DEFAULT 0,
    failed_count    INTEGER DEFAULT 0,
    skipped_count   INTEGER DEFAULT 0,
    error_message   TEXT
);
```

### Querying examples

```bash
# See all applied jobs today
sqlite3 ~/.niggless-jobber/jobs.db \
  "SELECT j.company, j.title, a.applied_at
   FROM applications a JOIN jobs j ON a.job_id = j.id
   WHERE a.status = 'applied' AND date(a.applied_at) = date('now')
   ORDER BY a.applied_at DESC;"

# See failed applications pending retry
sqlite3 ~/.niggless-jobber/jobs.db \
  "SELECT j.company, j.title, a.reason, a.retry_count
   FROM applications a JOIN jobs j ON a.job_id = j.id
   WHERE a.status = 'failed' AND a.retry_count < 3;"
```

---

## Encrypted Secrets Vault (`vault.enc`)

### Design

Secrets are stored as a JSON object encrypted with Fernet symmetric encryption
(`cryptography` library). The Fernet key is derived from your OS keyring so it is never written
to disk in plaintext.

```
Fernet key <- OS keyring (service: "niggless-jobber", username: "vault-key")
    |
    v
vault.enc  <- Fernet.encrypt(json.dumps(secrets_dict).encode())
```

On macOS the key is stored in the macOS Keychain. On Linux it uses GNOME Keyring or the
`SecretService` D-Bus API via the `keyring` library.

### Initialisation

```bash
python run.py --init
# Generates a Fernet key, saves it to OS keyring, creates an empty vault.enc
```

If the OS keyring is unavailable (headless server), a passphrase-derived key is used instead:

```bash
python run.py --init --passphrase
# Prompts for a passphrase; derives key via PBKDF2-HMAC-SHA256 (100k iterations)
# Key is NOT stored anywhere - must be provided on each run via NIGGLESS_PASSPHRASE env var
```

### Vault structure (decrypted JSON)

```json
{
  "OPENAI_API_KEY": "sk-...",
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "2CAPTCHA_API_KEY": "...",
  "CAPSOLVER_API_KEY": "...",
  "LINKEDIN_EMAIL": "you@example.com",
  "LINKEDIN_PASSWORD": "...",
  "DJINNI_EMAIL": "you@example.com",
  "DJINNI_PASSWORD": "...",
  "INDEED_EMAIL": "you@example.com",
  "INDEED_PASSWORD": "...",
  "IMAP_SERVER": "imap.gmail.com",
  "IMAP_PORT": "993",
  "IMAP_EMAIL": "you@example.com",
  "IMAP_PASSWORD": "...",
  "GOOGLE_OAUTH_TOKEN": "{ ... }",
  "totp_seeds": {
    "linkedin.com": "JBSWY3DPEHPK3PXP",
    "some-company-ats.com": "ANOTHER_SEED"
  }
}
```

### Managing vault entries

```bash
# Add or update a key
python run.py --vault-set OPENAI_API_KEY sk-abc123

# Read a key (prints to stdout; use carefully)
python run.py --vault-get OPENAI_API_KEY

# List all keys (no values shown)
python run.py --vault-list

# Add a TOTP seed
python run.py --add-totp linkedin.com JBSWY3DPEHPK3PXP

# Re-encrypt with a new key (after OS keyring migration)
python run.py --vault-rekey
```

---

## Personal Information (`personal_info.json`)

Stored at `~/.niggless-jobber/personal_info.json`. Created interactively by `python run.py --init`.
Never stored inside the git repo.

### Full schema

```json
{
  "name": {
    "first": "Artem",
    "last": "Dankov",
    "full": "Artem Dankov"
  },
  "email": "artem@example.com",
  "phone": "+380501234567",
  "address": {
    "city": "Kyiv",
    "country": "Ukraine",
    "country_code": "UA"
  },
  "linkedin_url": "https://linkedin.com/in/artem-dankov",
  "github_url": "https://github.com/artem-dankov",
  "website_url": "https://artem.dev",
  "summary": "Backend software engineer with 6 years of experience in Python and Go...",
  "years_of_experience": 6,
  "work_authorization": "EU citizen",
  "willing_to_relocate": false,
  "sponsorship_required": false,
  "notice_period": "2 weeks",
  "salary_expectation": "$120,000",
  "skills": [
    "Python", "Go", "PostgreSQL", "Kubernetes", "AWS"
  ],
  "work_history": [
    {
      "company": "Acme Corp",
      "title": "Senior Software Engineer",
      "start": "2022-03",
      "end": "present",
      "location": "Remote"
    },
    {
      "company": "Startup XYZ",
      "title": "Software Engineer",
      "start": "2019-06",
      "end": "2022-02",
      "location": "Kyiv"
    }
  ],
  "education": [
    {
      "institution": "Kyiv Polytechnic Institute",
      "degree": "Bachelor of Computer Science",
      "year": 2019
    }
  ]
}
```

All fields are optional except `name`, `email`, and `phone`. Missing fields are left blank in
forms (and logged as warnings).

### Editing

```bash
python run.py --edit-info
# Opens personal_info.json in $EDITOR (default: vim)
```

---

## Environment Variables (`.env`)

The `.env` file (in the project root) holds non-secret configuration that may differ between
machines. It is loaded by `pydantic-settings` at startup.

Copy from the provided template:

```bash
cp .env.example .env
```

### `.env.example`

```bash
# Timezone for the scheduler and for logging
# Format: IANA timezone name
TZ=Europe/Kyiv

# Data directory (default: ~/.niggless-jobber)
NIGGLESS_DATA_DIR=

# Set to 1 to use a passphrase for vault decryption instead of OS keyring
# If set, also set NIGGLESS_PASSPHRASE (do not commit this value)
NIGGLESS_USE_PASSPHRASE=0
NIGGLESS_PASSPHRASE=

# Override the AI provider for a single run (useful for testing)
# Values: openai | anthropic
NIGGLESS_AI_PROVIDER=

# Set to 1 to run in dry-run mode regardless of config.yaml setting
NIGGLESS_DRY_RUN=0

# Log level: DEBUG | INFO | WARNING | ERROR
LOG_LEVEL=INFO
```

`.env` is gitignored. `NIGGLESS_PASSPHRASE` must never be committed under any circumstances.

---

## .gitignore Rules

The following entries are enforced in `.gitignore`:

```
# Runtime data
output/
logs/
*.log

# Secrets and personal data (belt + suspenders)
.env
*.enc
personal_info.json
*_session.json

# Python
__pycache__/
*.pyc
.venv/
*.egg-info/

# LaTeX build artifacts
*.aux
*.fls
*.fdb_latexmk
*.synctex.gz
*.out

# macOS
.DS_Store
```

Additionally a `.gitattributes` file marks `*.enc` as binary to prevent accidental diffs of
encrypted content.

---

## Data Retention

By default:

- SQLite records are kept indefinitely (the DB is small even after years of use).
- Generated PDFs under `output/` older than 90 days are deleted on each run
  (`config.yaml: storage.pdf_retention_days: 90`).
- Log files older than 30 days are deleted on each run
  (`config.yaml: storage.log_retention_days: 30`).

Both retention values can be set to `0` to disable automatic cleanup.
