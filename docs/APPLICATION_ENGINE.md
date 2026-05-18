# Application Engine

This document covers how the engine submits job applications: Playwright configuration,
anti-detection strategy, CAPTCHA and MFA handling, ATS-specific adapters, the heuristic
form filler, and result state definitions.

---

## Overview

```
applicator/engine.py   <- top-level apply() function called per job
    |
    +--> detect_ats(apply_url)           determine which adapter to use
    |
    +--> adapter.apply(page, listing, personal_info, pdfs)
    |       |
    |       +--> form_filler.py          fills individual form fields
    |       +--> captcha.py             resolves CAPTCHA when encountered
    |       +--> mfa.py                 resolves MFA when encountered
    |
    +--> returns ApplicationResult
```

The engine opens **one** Playwright browser for the entire run (shared across all applications).
Each application gets a fresh `BrowserContext` (isolated cookies, storage, viewport) to prevent
sites from fingerprinting cross-application sessions.

---

## Playwright Configuration

### Launch settings

```python
browser = await playwright.chromium.launch(
    headless=config.application.headless,   # default: True
    args=[
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ],
)
```

### Context settings (per application)

```python
context = await browser.new_context(
    viewport={"width": random.randint(1280, 1920), "height": random.randint(720, 1080)},
    user_agent=random.choice(USER_AGENT_POOL),   # pool of 20 desktop Chrome/Firefox UAs
    locale="en-US",
    timezone_id="Europe/Kyiv",                   # matches your actual timezone
    java_script_enabled=True,
    accept_downloads=True,
)
await stealth_async(context)   # playwright-stealth patches navigator, WebGL, plugins
```

### Human-like interaction

All clicks and keyboard input are wrapped in helpers that add realistic delays:

```python
async def human_click(page, selector):
    element = await page.wait_for_selector(selector, timeout=10_000)
    await asyncio.sleep(random.uniform(0.3, 0.9))
    await element.hover()
    await asyncio.sleep(random.uniform(0.1, 0.4))
    await element.click()

async def human_type(page, selector, text):
    await human_click(page, selector)
    for char in text:
        await page.keyboard.press(char)
        await asyncio.sleep(random.uniform(0.04, 0.15))
```

Random mouse moves are injected between major actions (navigating to a new section, submitting).

---

## ATS Detection

`applicator/engine.py` inspects the `apply_url` hostname and path to route to the correct adapter:

| URL pattern | Adapter |
|---|---|
| `*.greenhouse.io` or `boards.greenhouse.io` | `ats/greenhouse.py` |
| `jobs.lever.co` | `ats/lever.py` |
| `*.myworkdayjobs.com` | `ats/workday.py` |
| `*.icims.com` | `ats/icims.py` |
| `jobs.ashbyhq.com` | heuristic form filler |
| `*.linkedin.com` + Easy Apply flag | `ats/linkedin_easy_apply.py` |
| anything else | heuristic form filler |

If the URL redirects (detected via `page.on("response")`) the final URL is re-evaluated.

---

## ATS Adapters

Each adapter implements `BaseATSAdapter` from `applicator/ats/base.py`:

```python
class BaseATSAdapter(ABC):
    @abstractmethod
    async def apply(
        self,
        page: Page,
        listing: JobListing,
        info: PersonalInfo,
        cv_pdf: Path,
        cover_letter_pdf: Path,
    ) -> ApplicationResult:
        ...
```

### Greenhouse (`ats/greenhouse.py`)

Greenhouse forms are standardised. Steps:

1. Fill personal details (first name, last name, email, phone, LinkedIn URL, website/GitHub).
2. Upload CV PDF via the file input (`input[type="file"][name*="resume"]`).
3. Upload cover letter PDF if a file input is present for it.
4. Answer custom questions (free-text, dropdowns, checkboxes) using the heuristic mapper.
5. Solve any reCAPTCHA that appears (uncommon on Greenhouse but possible).
6. Submit and verify the success message (`div.confirmation`).

### Lever (`ats/lever.py`)

Lever uses a single-page form:

1. Fill: name, email, phone, current company (set to last role from personal_info), LinkedIn,
   GitHub, "How did you hear about us?" (set to source platform name).
2. Upload CV PDF.
3. Fill the cover letter textarea with the plain-text version of the cover letter
   (generated from the `.tex` source by stripping LaTeX commands).
4. Submit and verify `div.application-confirmation`.

### Workday (`ats/workday.py`)

Workday is the most complex ATS. It uses a multi-step wizard with dynamic React-rendered forms.

```
Step 1: My Information
    - Personal details
    - Contact information
Step 2: My Experience
    - Work history (filled from personal_info.work_history[])
    - Education
    - Skills
Step 3: Application Questions
    - Custom employer questions (heuristic mapper)
Step 4: Self Identify (EEO)
    - "Prefer not to answer" selected for all fields by default
Step 5: Voluntary Disclosures
    - Defaults selected
Step 6: Review and Submit
```

Workday uses shadow DOM in some versions. All selectors use `page.evaluate` with
`document.querySelector` when standard Playwright selectors fail.

### iCIMS (`ats/icims.py`)

iCIMS redirects through a login/register gate:

1. Check if already logged in (session cookie). If not, register or log in with email + password
   from personal_info.
2. Navigate to the job URL.
3. Fill the multi-page application form.
4. Upload documents.
5. Submit and check for confirmation page.

### LinkedIn Easy Apply (`ats/linkedin_easy_apply.py`)

1. Navigate to `https://www.linkedin.com/jobs/view/<jobPostingId>/`.
2. Click "Easy Apply" button.
3. Walk through the modal steps:
   - Contact info: pre-filled from LinkedIn profile (verify).
   - Resume: upload CV PDF (LinkedIn accepts PDF uploads in Easy Apply).
   - Questions: answer using heuristic mapper.
   - Review: confirm and submit.
4. Verify "Your application was sent" confirmation banner.

---

## Heuristic Form Filler

Used when no ATS adapter matches or for custom questions inside adapters.

`applicator/form_filler.py` scans all visible `<input>`, `<textarea>`, and `<select>` elements
on the page and maps each one to a `personal_info` field using label text matching:

```python
LABEL_FIELD_MAP = {
    # exact and partial matches (case-insensitive)
    "first name": "name.first",
    "last name": "name.last",
    "full name": "name.full",
    "email": "email",
    "phone": "phone",
    "linkedin": "linkedin_url",
    "github": "github_url",
    "website": "website_url",
    "city": "address.city",
    "country": "address.country",
    "years of experience": "years_of_experience",
    "cover letter": "_cover_letter_text",    # special: plain-text cover letter
    "salary": "_salary_expectation",         # special: from config
    "notice period": "_notice_period",       # special: from config
    "work authorization": "work_authorization",
    "sponsorship": "_sponsorship_required",  # special: from config
}
```

For dropdowns (`<select>`), the filler picks the option whose text best matches the expected
value using fuzzy string matching (`difflib.get_close_matches`).

For checkboxes labelled "I agree to the terms" or similar, it checks the box automatically.
For checkboxes that appear to be EEO / diversity questions, it selects "Prefer not to say".

Any field that cannot be mapped is left blank and logged with `field_label` and `field_id`
for manual review.

---

## File Upload Handling

```python
async def upload_file(page, selector, file_path: Path):
    file_input = await page.wait_for_selector(selector, state="attached")
    await file_input.set_input_files(str(file_path))
    # Wait for the upload progress indicator to clear
    await page.wait_for_selector(".upload-progress", state="hidden", timeout=30_000)
```

If no file input is visible (drag-and-drop only), the engine uses:

```python
await page.evaluate("""
    const input = document.createElement('input');
    input.type = 'file';
    document.body.appendChild(input);
    input.style.display = 'none';
    window.__uploadInput = input;
""")
await page.locator("css=[type=file]").last.set_input_files(str(file_path))
```

---

## CAPTCHA Handling

### Supported CAPTCHA types

| Type | Method |
|---|---|
| reCAPTCHA v2 (checkbox) | 2captcha / CapSolver audio solve |
| reCAPTCHA v2 (invisible) | 2captcha token injection |
| reCAPTCHA v3 | CapSolver enterprise action solve |
| hCaptcha | 2captcha / CapSolver |
| Cloudflare Turnstile | CapSolver |
| Image CAPTCHA | 2captcha image API |

### Detection

After every page navigation, `captcha.py` runs a detection pass:

```python
async def detect_captcha(page) -> CaptchaType | None:
    if await page.query_selector("iframe[src*='recaptcha']"):
        return CaptchaType.RECAPTCHA_V2
    if await page.query_selector("iframe[src*='hcaptcha']"):
        return CaptchaType.HCAPTCHA
    if await page.query_selector(".cf-turnstile"):
        return CaptchaType.TURNSTILE
    if await page.query_selector("img.captcha-image"):
        return CaptchaType.IMAGE
    return None
```

### Resolution flow

```
1. Detect CAPTCHA type
2. Extract site-key (from iframe src or data attribute)
3. POST solve request to 2captcha/CapSolver API with site-key + page URL
4. Poll API every 5s until solution is ready (max 120s)
5. Inject solution token:
     page.evaluate(f"document.getElementById('g-recaptcha-response').innerHTML = '{token}'")
     page.evaluate(f"___grecaptcha_cfg.clients[0].aa.l.callback('{token}')")
6. Continue form submission
```

Provider is selected from `config.yaml: captcha.provider`. API key is read from vault.

---

## MFA Handling

### TOTP (Authenticator App)

```
1. Detect TOTP prompt (page contains "authenticator", "6-digit code", "verification code")
2. Read TOTP seed from vault key "totp_seeds.<hostname>"
3. Generate current token: pyotp.TOTP(seed).now()
4. Type the 6-digit code into the prompt field
5. Submit
```

TOTP seeds per hostname are stored in the vault. To add a seed:

```bash
python run.py --add-totp linkedin.com JBSWY3DPEHPK3PXP
```

### Email OTP

```
1. Detect email OTP prompt (page contains "email", "sent you a code", "check your inbox")
2. Poll IMAP inbox for a message from the expected sender domain:
     - Connect to IMAP server (credentials from vault)
     - Search UNSEEN messages from <domain> in the last 2 minutes
     - Retry every 5s, up to 60s
3. Extract 4-8 digit code from message subject or body (regex: \b[0-9]{4,8}\b)
4. Type the code into the prompt field
5. Submit
```

IMAP credentials (server, port, email, password) are stored in the vault under `imap.*`.

### Unrecognised MFA

If an MFA prompt is detected but neither TOTP nor email OTP patterns match:

1. The application is paused.
2. A desktop notification is sent (via `osascript` on macOS).
3. The engine waits up to 5 minutes for manual intervention.
4. If not resolved, the job is marked `manual_review_needed`.

---

## Result States

Every application concludes with an `ApplicationResult`:

```python
@dataclass
class ApplicationResult:
    job_id: str
    status: ApplicationStatus
    reason: str | None        # set on non-success statuses
    applied_at: datetime | None
    cv_path: Path | None
    cover_letter_path: Path | None
    prompt_version_cv: str
    prompt_version_cl: str
```

```python
class ApplicationStatus(str, Enum):
    APPLIED           = "applied"             # submitted successfully
    FAILED            = "failed"              # error during submission
    SKIPPED           = "skipped"             # filtered out before attempt
    MANUAL_REVIEW     = "manual_review_needed" # paused, needs human
    DRY_RUN           = "dry_run"             # --dry-run mode, not submitted
```

### Skip conditions (evaluated before attempting)

- Job ID already in `applications` table with status `applied`.
- `listing.salary_raw` parses to a value below `config.application.skip_if_salary_below`.
- `listing.company` is in `config.application.blocked_companies` list.
- `listing.title` matches a pattern in `config.application.blocked_title_patterns`.
- Daily `max_per_run` limit already reached.

### Retry logic

`failed` applications are retried on the next run, up to 3 times total. The `retry_count` column
in the `applications` table tracks this. After 3 failures the status is permanently set to
`failed` with `reason: max_retries_exceeded`.

---

## Logging

Every action the engine takes is logged at DEBUG level to `logs/run_YYYY-MM-DD.log`. Each log
line includes the job ID so you can reconstruct the full sequence for a given application.

On `APPLIED` the engine also takes a screenshot of the confirmation page and saves it to
`output/<job_id>/confirmation.png` as proof of submission.
