# Google Workspace Tracking

This document covers how niggless-jobber logs application activity to Google Sheets and creates
follow-up reminders in Google Calendar: OAuth2 setup, sheet schema, calendar event design,
and how to disable tracking if you do not need it.

---

## Overview

After each application attempt the orchestrator calls two optional tracking functions:

```
ApplicationResult
    |
    +--> tracking/sheets.py
    |       appends one row to the current week's tab
    |
    +--> tracking/calendar.py
            creates a Calendar follow-up event (on APPLIED only)
```

Both are disabled if the Google OAuth token is absent from the vault. Neither blocks the
application run - tracking failures are logged as warnings and never cause the run to fail.

---

## OAuth2 Setup

### 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com).
2. Create a new project named "niggless-jobber".
3. Enable the following APIs:
   - Google Sheets API
   - Google Calendar API

### 2. Create OAuth2 credentials

1. In the project, go to **APIs & Services -> Credentials**.
2. Click **Create Credentials -> OAuth client ID**.
3. Application type: **Desktop app**.
4. Download the JSON credentials file.

### 3. Authenticate

```bash
python run.py --auth-google --credentials /path/to/downloaded-credentials.json
```

This opens a browser, asks you to log in, and saves the token to the vault under
`GOOGLE_OAUTH_TOKEN`. The credentials JSON file can be deleted afterwards.

### 4. Verify

```bash
python run.py --test-google
# Prints: "Google Sheets: OK  |  Google Calendar: OK"
```

### Token refresh

OAuth2 access tokens expire after 1 hour. The `google-auth-oauthlib` library handles refresh
automatically using the refresh token embedded in `GOOGLE_OAUTH_TOKEN`. Refresh tokens do not
expire as long as the app is used at least once every 6 months.

---

## Google Sheets

### Spreadsheet structure

One Google Spreadsheet is used for all tracking. Its ID is stored in the vault under
`GOOGLE_SHEET_ID` (set automatically on first run, or manually via
`python run.py --vault-set GOOGLE_SHEET_ID <id>`).

On first run `tracking/sheets.py` creates a new spreadsheet named "niggless-jobber Applications"
in your Google Drive root.

### Tab naming

One tab per calendar week, named `YYYY-Www` (ISO week format):

```
2026-W20
2026-W21
2026-W22
...
```

The tab is created automatically if it does not exist when the first application of that week
is logged.

### Row schema

Each row represents one application attempt:

| Column | Header | Content |
|---|---|---|
| A | Date | ISO 8601 date of application (`2026-05-11`) |
| B | Time | Local time (`12:34:07`) |
| C | Company | Company name |
| D | Title | Job title |
| E | Source | Scraper source (linkedin / justjoinit / dou / djinni / indeed) |
| F | Status | applied / failed / skipped / manual_review_needed / dry_run |
| G | Reason | Reason for non-applied status; empty on success |
| H | Salary | Raw salary string from listing; empty if absent |
| I | Location | Job location |
| J | URL | Canonical job listing URL |
| K | Apply URL | ATS / apply page URL |
| L | CV Version | Prompt version tag (e.g. `cv-v3`) |
| M | Cover Letter | Path to `cover_letter.pdf` relative to `output/` |
| N | Retry Count | Number of retry attempts (0 on first success) |

### Header row

The header row is inserted automatically when a new tab is created:

```python
HEADERS = [
    "Date", "Time", "Company", "Title", "Source", "Status", "Reason",
    "Salary", "Location", "URL", "Apply URL", "CV Version",
    "Cover Letter", "Retry Count",
]
```

### Appending a row

```python
# tracking/sheets.py
def append_application(service, spreadsheet_id, result: ApplicationResult, listing: JobListing):
    week_tab = datetime.date.today().strftime("%G-W%V")
    _ensure_tab(service, spreadsheet_id, week_tab)
    row = [
        result.applied_at.date().isoformat() if result.applied_at else date.today().isoformat(),
        result.applied_at.strftime("%H:%M:%S") if result.applied_at else "",
        listing.company,
        listing.title,
        listing.source,
        result.status.value,
        result.reason or "",
        listing.salary_raw or "",
        listing.location or "",
        listing.url,
        listing.apply_url,
        result.prompt_version_cv or "",
        str(result.cover_letter_path) if result.cover_letter_path else "",
        str(result.retry_count if hasattr(result, "retry_count") else 0),
    ]
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{week_tab}!A:N",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()
```

### Conditional formatting (applied manually or via script)

The following colour rules make the sheet easy to scan at a glance:

| Status | Background |
|---|---|
| applied | Green (light) |
| failed | Red (light) |
| skipped | Yellow (light) |
| manual_review_needed | Orange (light) |
| dry_run | Grey (light) |

To apply these programmatically run:

```bash
python run.py --format-sheets
```

---

## Google Calendar

### Event design

For every application with `status = applied`, a Calendar event is created as a follow-up
reminder. The premise: if you applied on May 11, a nudge to follow up or expect to hear back
appears 3 weeks later (June 1).

```python
# tracking/calendar.py
def create_followup_event(service, result: ApplicationResult, listing: JobListing):
    if result.status != ApplicationStatus.APPLIED:
        return
    applied_date = result.applied_at.date()
    followup_date = applied_date + datetime.timedelta(weeks=3)
    event = {
        "summary": f"Follow up: {listing.title} at {listing.company}",
        "description": (
            f"Applied: {applied_date.isoformat()}\n"
            f"Job URL: {listing.url}\n"
            f"Apply URL: {listing.apply_url}\n"
            f"Status: {result.status.value}"
        ),
        "start": {"date": followup_date.isoformat()},
        "end":   {"date": followup_date.isoformat()},
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup",  "minutes": 480},   # 8 hours before (start of day)
                {"method": "email",  "minutes": 1440},  # 1 day before
            ],
        },
        "colorId": "5",   # banana yellow - distinct from other calendar items
    }
    service.events().insert(calendarId="primary", body=event).execute()
```

### Calendar used

Events are inserted into your **primary** Google Calendar. To use a different calendar,
set `GOOGLE_CALENDAR_ID` in the vault:

```bash
python run.py --vault-set GOOGLE_CALENDAR_ID abc123@group.calendar.google.com
```

To create a dedicated calendar for job tracking:

```bash
python run.py --create-calendar
# Creates "Job Applications" calendar and stores its ID in the vault
```

### Event deduplication

Before creating an event, the engine checks whether a follow-up event for the same job already
exists (by searching for events with `summary` matching `"Follow up: * at <company>"`). If one
exists, no duplicate is created.

---

## Disabling Tracking

### Disable Sheets only

```yaml
# config.yaml
tracking:
  sheets: false
```

### Disable Calendar only

```yaml
# config.yaml
tracking:
  calendar: false
```

### Disable both

```yaml
# config.yaml
tracking:
  enabled: false
```

When tracking is disabled, the run proceeds normally and all data is still written to the local
SQLite database.

---

## Viewing Your Data

### Quick stats from SQLite (always available)

```bash
# Applications this week
sqlite3 ~/.niggless-jobber/jobs.db \
  "SELECT status, COUNT(*) FROM applications
   WHERE applied_at >= date('now', 'weekday 0', '-7 days')
   GROUP BY status;"

# Companies applied to this month
sqlite3 ~/.niggless-jobber/jobs.db \
  "SELECT j.company, j.title, a.applied_at, a.status
   FROM applications a JOIN jobs j ON a.job_id = j.id
   WHERE strftime('%Y-%m', a.applied_at) = strftime('%Y-%m', 'now')
   ORDER BY a.applied_at DESC;"
```

### Google Sheet

Open the spreadsheet directly:

```bash
python run.py --open-sheets
# Opens the spreadsheet URL in your default browser
```
