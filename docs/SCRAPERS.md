# Scrapers

This document describes the scraping strategy for each supported job board: authentication method,
request approach, rate limits, CSS/JSON selectors, and anti-detection notes.

---

## Common Output Schema

Every scraper returns a list of `RawListing` dicts. The parser layer normalises these into the
`JobListing` dataclass (see `parser/schema.py`).

```python
# parser/schema.py
@dataclass
class JobListing:
    id: str            # SHA-256(source + company + title + posted_at ISO date)
    source: str        # "linkedin" | "justjoinit" | "dou" | "djinni" | "indeed"
    title: str
    company: str
    url: str           # canonical listing URL
    apply_url: str     # direct apply / ATS URL (may equal url)
    description: str   # cleaned plain text, HTML tags stripped
    skills: list[str]  # tech terms / keywords extracted from description
    location: str
    salary_raw: str    # raw string e.g. "$120k - $160k" or "" if absent
    posted_at: datetime
```

The `id` field acts as the primary deduplication key. If the same job appears on two boards,
each source gets its own record (different `source` value, different `id`).

---

## Base Scraper Interface

All scrapers implement `BaseScraper` from `scrapers/base.py`:

```python
class BaseScraper(ABC):
    def __init__(self, config: ScraperConfig, client: httpx.AsyncClient):
        self.config = config
        self.client = client

    @abstractmethod
    async def search(
        self,
        keywords: list[str],
        locations: list[str],
        max_results: int = 50,
    ) -> list[RawListing]:
        """Return raw listing dicts for the given search params."""

    @abstractmethod
    async def fetch_detail(self, url: str) -> str:
        """Return the full raw HTML (or JSON string) for a single listing page."""
```

Scrapers that require Playwright receive a shared `playwright.async_api.BrowserContext` instead
of an `httpx.AsyncClient`.

---

## Shared Behaviours

### Retry logic

All HTTP requests use a shared retry wrapper:

```
3 attempts, exponential backoff: 1s, 4s, 16s
On 429 (rate limit): wait for Retry-After header value + jitter (0-2s)
On 5xx: retry up to 3 times
On connection error: retry up to 3 times
```

### Request headers

All non-Playwright scrapers send a realistic browser `User-Agent` chosen at random from a pool
of 20 desktop Chrome/Firefox strings. `Accept-Language: en-US,en;q=0.9` is always included.

### Playwright stealth

Any scraper that uses Playwright imports `playwright_stealth.stealth_async` and applies it to
every new page before navigation. This patches navigator properties, WebGL fingerprints, and
plugin lists to defeat common bot-detection heuristics.

---

## LinkedIn

### Authentication

LinkedIn requires a logged-in session. Credentials (email + password) are stored in the Fernet
vault. On startup the scraper checks for a cached session cookie file at
`~/.niggless-jobber/linkedin_session.json`. If the cookie is still valid (tested via
`GET /voyager/api/me`) it is reused; otherwise a fresh login is performed with Playwright and
the cookie is saved.

```
Strategy: Playwright login -> cookie persistence -> httpx requests with cookie jar
Fallback: full Playwright page scraping if the API endpoint changes
```

### Search endpoint

LinkedIn exposes an internal Voyager API used by the web app:

```
GET https://www.linkedin.com/voyager/api/jobs/jobPostings
    ?keywords=<encoded>
    &location=<encoded>
    &start=<offset>
    &count=25
    &decorationId=com.linkedin.voyager.deco.jobs.web.shared.WebFullJobPosting-65
```

The response is JSON. Pagination continues until fewer than `count` results are returned or
`max_results` is reached.

### Key JSON paths

```
$.included[]
  .entityUrn                     -> listing ID (extract numeric part)
  .title                         -> title
  .companyDetails.company.name   -> company
  .description.text              -> description (markdown-like)
  .listedAt                      -> Unix ms timestamp
  .applyMethod.companyApplyUrl   -> apply_url (if Easy Apply: null)
  .jobPostingId                  -> used to construct canonical url
```

If `applyMethod.companyApplyUrl` is null the job uses LinkedIn Easy Apply; `apply_url` is set to
`https://www.linkedin.com/jobs/view/<jobPostingId>/`.

### Easy Apply

When `apply_url` points back to LinkedIn, the application engine uses the dedicated
`applicator/ats/linkedin_easy_apply.py` adapter which drives the multi-step Easy Apply modal
via Playwright.

### Rate limits

| Limit | Value |
|---|---|
| Requests per minute | 30 (enforced client-side via `asyncio.sleep`) |
| Max results per run | configurable, default 50 |
| Session cookie TTL | ~24 hours; refreshed automatically |

---

## JustJoinIT

### Authentication

None required. JustJoinIT exposes a fully public JSON API.

### Search endpoint

```
GET https://justjoin.it/api/offers
    ?keyword=<encoded>
    &city=<encoded>
    &remote=yes          (optional)
    &page=<n>
    &per_page=50
```

Response: `application/json` array of offer objects.

### Key JSON paths

```
$[]
  .id                            -> used to build canonical URL
  .title
  .companyName
  .city + .country_code          -> location
  .salary[0].from + .to + .currency -> salary_raw
  .publishedAt                   -> ISO 8601
  .skills[].name                 -> skills[]
  .body                          -> description HTML (fetch detail endpoint for full)
```

Detail endpoint:

```
GET https://justjoin.it/api/offers/<id>
```

Returns the full description HTML in `.body`.

### Rate limits

| Limit | Value |
|---|---|
| No official rate limit documented | - |
| Client-side throttle applied | 1 req/s |
| Max results per run | 100 |

---

## Dou

Dou (dou.ua) is the primary Ukrainian job board for software engineers. It serves content in
Ukrainian; the scraper targets the Ukrainian-language pages since most listings are posted there.

### Authentication

None required for listing pages. An account is required to see email contacts, but the scraper
does not need email contacts.

### Strategy

Playwright + BeautifulSoup. Dou uses server-side rendering with occasional JS lazy-loading of
the salary block and the full description.

```
1. Navigate to https://jobs.dou.ua/vacancies/?category=Python&search=<keywords>
2. Scroll to bottom to trigger lazy-load (page.evaluate "window.scrollTo(0, document.body.scrollHeight)")
3. Wait for network idle
4. Extract listings from rendered DOM
5. For each listing navigate to its detail URL and extract the full description
```

### CSS selectors

| Field | Selector |
|---|---|
| Listing container | `li.l-vacancy` |
| Title | `.vt a` |
| Company | `.company a` |
| Location | `.cities` |
| Salary | `.salary` |
| Posted date | `.date` |
| Detail URL | `.vt a[href]` |
| Full description | `div.b-typo.vacancy-section` |
| Apply button URL | `a.f-btn[href]` |

### Language handling

Dou listings are in Ukrainian. The job description is passed as-is to the AI pipeline; GPT-4o
handles Ukrainian natively. The `location` field is transliterated to Latin by the parser
(`unidecode` library) so it is comparable to other sources.

### Rate limits

| Limit | Value |
|---|---|
| Requests per minute | 10 (Playwright navigation is slow) |
| Delay between pages | 3-5s randomised |
| Max results per run | 50 |

---

## Djinni

Djinni (djinni.co) is the leading Ukrainian remote-first job board. Listings require an account
to see the full description and apply URL.

### Authentication

Playwright login with email + password from the vault. Session cookie cached at
`~/.niggless-jobber/djinni_session.json`. TTL: ~7 days.

### Strategy

Playwright-only (Djinni blocks raw HTTP clients):

```
1. Log in if session expired
2. Navigate to https://djinni.co/jobs/?primary_keyword=Python&keyword=<keywords>
3. Paginate via the "next" button
4. For each listing click to open detail page, extract full description and apply URL
```

### CSS selectors

| Field | Selector |
|---|---|
| Listing container | `li.list-jobs__item` |
| Title | `.job-list-item__title a` |
| Company | `.job-list-item__company` |
| Location / remote | `.location-text` |
| Salary | `.public-salary-item` |
| Posted date | `.text-muted.float-right` |
| Detail URL | `.job-list-item__title a[href]` |
| Full description | `.job-description` |
| Apply URL | `a.btn-apply[href]` |

### Rate limits

| Limit | Value |
|---|---|
| Page navigations per minute | 8 |
| Delay between listings | 4-7s randomised |
| Max results per run | 40 |

---

## Indeed

Indeed aggressively blocks scrapers. The strategy relies on Playwright with full stealth
configuration.

### Authentication

None required for browsing. Applying via Indeed's own "Apply Now" flow requires a logged-in
Indeed account; credentials stored in vault. Most listings redirect to the company ATS, so
the Indeed account is only needed for native Indeed applications.

### Strategy

```
1. Launch Playwright page with stealth + random viewport (1280-1920 x 720-1080)
2. Navigate to https://www.indeed.com/jobs?q=<keywords>&l=<location>&fromage=1
   (fromage=1 = posted within last 1 day, so daily runs avoid stale results)
3. Solve any CAPTCHA (Cloudflare challenge or Indeed's own CAPTCHA) if intercepted
4. Extract listings from rendered HTML
5. For each listing navigate to detail page; extract description + apply URL
```

### CSS selectors

| Field | Selector |
|---|---|
| Listing container | `div.job_seen_beacon` |
| Title | `h2.jobTitle span[title]` |
| Company | `span.companyName` |
| Location | `div.companyLocation` |
| Salary | `div.salary-snippet-container` |
| Posted date | `span.date` |
| Detail URL | `h2.jobTitle a[href]` |
| Full description | `div#jobDescriptionText` |
| Apply button | `button#indeedApplyButton` or `a.jobsearch-IndeedApplyButton-newDesign` |

### Anti-detection notes

- `playwright-stealth` must be applied before any navigation.
- Random mouse movement via `page.mouse.move` before clicking.
- Random typing delay (50-150ms per character) when filling search boxes.
- If Cloudflare intercepts, wait 5s and check for `cf-challenge-running`; if present, invoke
  the CAPTCHA solver or pause for 30s to let the JS challenge resolve.

### Rate limits

| Limit | Value |
|---|---|
| Listings per run | 30 (aggressive throttle to avoid bans) |
| Delay between listings | 5-10s randomised |
| Delay between paginated searches | 8-15s randomised |

---

## Company Websites (ATS Fallback)

When any scraper returns an `apply_url` that is not on the source platform (e.g. a Greenhouse
or Lever URL), the application engine navigates to that URL directly. However, the scraper itself
may also encounter company-hosted job listing pages when following links from Indeed or LinkedIn.

In those cases a generic Playwright-based scraper is used:

```
1. Navigate to the company jobs URL
2. Try known ATS URL patterns to identify the system:
   - greenhouse.io        -> Greenhouse adapter
   - lever.co             -> Lever adapter
   - myworkdayjobs.com    -> Workday adapter
   - icims.com            -> iCIMS adapter
   - jobs.ashbyhq.com     -> Ashby adapter (heuristic form)
   - Unknown              -> heuristic form filler
3. Extract job description from the page for AI pipeline input
```

---

## Adding a New Scraper

1. Create `scrapers/<name>.py` implementing `BaseScraper`.
2. Add a config entry under `config.yaml: search.sources`.
3. Register it in `run.py` in the `SCRAPER_REGISTRY` dict.
4. Document the platform's selectors and rate limits in this file.

---

## Common Failure Modes

| Failure | Handling |
|---|---|
| 429 Too Many Requests | Exponential backoff + Retry-After |
| Cloudflare block | CAPTCHA solver invoked; if unsolvable, scraper skipped for this run |
| Session expired (LinkedIn, Djinni) | Auto re-login; if re-login fails, logged as error and skipped |
| Selector no longer matches | `ScraperError` raised; logged; scraper skipped for this run |
| Network timeout | 30s timeout; 3 retries; then skip |
| Zero results | Logged as warning (not an error) |
