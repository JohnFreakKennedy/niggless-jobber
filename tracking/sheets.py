"""
Google Sheets tracking.

Layout
------
- One tab per calendar week  (e.g. "2026-W20") -- one row per application.
- A persistent "Summary" tab -- one row per week aggregating totals, plus an
  "All Time" totals row at the top that is always kept up to date.

Week tab columns
----------------
Date | Time | Company | Title | Source | Status | Reason | Salary Offered |
Location | Remote | Contract Type | URL | Apply URL | CV Version |
Cover Letter Path | Retry Count | Notes

Conditional formatting (status column F):
  applied  -> green
  failed   -> red
  skipped  -> grey
  dry_run  -> light blue
  manual   -> orange
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column layout -- single source of truth
# ---------------------------------------------------------------------------
WEEK_HEADERS = [
    "Date",          # A
    "Time",          # B
    "Company",       # C
    "Title",         # D
    "Source",        # E
    "Status",        # F  <-- color-coded
    "Reason",        # G
    "Salary Offered",# H
    "Location",      # I
    "Remote",        # J
    "Contract Type", # K
    "URL",           # L
    "Apply URL",     # M
    "CV Version",    # N
    "Cover Letter",  # O
    "Retry Count",   # P
    "Notes",         # Q
]

SUMMARY_HEADERS = [
    "Week",
    "Scraped",
    "New Listings",
    "Applied",
    "Failed",
    "Skipped",
    "Success Rate %",
]

# Status -> background color (RGB hex without #)
_STATUS_COLORS: dict[str, tuple[float, float, float]] = {
    "applied":  (0.714, 0.843, 0.659),   # light green
    "failed":   (0.918, 0.600, 0.600),   # light red
    "skipped":  (0.851, 0.851, 0.851),   # light grey
    "dry_run":  (0.643, 0.761, 0.957),   # light blue
    "manual":   (0.988, 0.737, 0.400),   # orange
}
_DEFAULT_COLOR = (1.0, 1.0, 1.0)

_STATUS_COL_INDEX = WEEK_HEADERS.index("Status")  # 0-based


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _get_service():
    from storage.vault import get_secret
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_json = get_secret("GOOGLE_OAUTH_TOKEN")
    if not token_json:
        raise RuntimeError("GOOGLE_OAUTH_TOKEN not in vault. Run: python run.py --auth-google")
    creds_data = json.loads(token_json)
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=creds_data.get("client_id"),
        client_secret=creds_data.get("client_secret"),
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _get_spreadsheet_id() -> str:
    from storage.vault import get_secret
    sid = get_secret("GOOGLE_SHEET_ID")
    if not sid:
        sid = _create_spreadsheet()
    return sid


# ---------------------------------------------------------------------------
# Spreadsheet bootstrap
# ---------------------------------------------------------------------------

def _create_spreadsheet() -> str:
    from storage.vault import set_secret
    service = _get_service()
    body = {
        "properties": {"title": "niggless-jobber Applications"},
        "sheets": [
            {"properties": {"title": "Summary", "index": 0}},
        ],
    }
    resp = service.spreadsheets().create(body=body, fields="spreadsheetId").execute()
    sid = resp["spreadsheetId"]
    set_secret("GOOGLE_SHEET_ID", sid)
    log.info("Created Google Sheet: https://docs.google.com/spreadsheets/d/%s", sid)
    _init_summary_tab(service, sid)
    return sid


def _week_tab_name() -> str:
    return date.today().strftime("%G-W%V")


def _sheet_id_for_tab(service, spreadsheet_id: str, tab_name: str) -> int | None:
    """Return the numeric sheetId for a named tab, or None if not found."""
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        if s["properties"]["title"] == tab_name:
            return s["properties"]["sheetId"]
    return None


def _ensure_week_tab(service, spreadsheet_id: str, tab_name: str) -> int:
    """
    Create the week tab if it does not exist, write the header row, freeze it,
    bold it, and return the numeric sheetId.
    """
    sheet_id = _sheet_id_for_tab(service, spreadsheet_id, tab_name)
    if sheet_id is not None:
        return sheet_id

    # Add the sheet
    resp = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab_name}}}]},
    ).execute()
    sheet_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]

    # Write header
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{tab_name}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [WEEK_HEADERS]},
    ).execute()

    # Freeze row 1 + bold + background
    _apply_header_format(service, spreadsheet_id, sheet_id)
    log.debug("Created Sheets tab: %s (sheetId=%d)", tab_name, sheet_id)
    return sheet_id


def _init_summary_tab(service, spreadsheet_id: str) -> None:
    """Write headers and the All Time row to the Summary tab."""
    summary_id = _sheet_id_for_tab(service, spreadsheet_id, "Summary")
    if summary_id is None:
        return

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Summary!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [SUMMARY_HEADERS, ["All Time", 0, 0, 0, 0, 0, "=IF(D2=0,0,ROUND(D2/(D2+E2)*100,1))&\"%\""]]},
    ).execute()

    _apply_header_format(service, spreadsheet_id, summary_id)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _rgb(r: float, g: float, b: float) -> dict:
    return {"red": r, "green": g, "blue": b}


def _apply_header_format(service, spreadsheet_id: str, sheet_id: int) -> None:
    """Freeze row 1, bold it, and give it a dark grey background."""
    requests = [
        # Freeze row 1
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        # Bold + dark background for header row
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": _rgb(0.263, 0.263, 0.263),
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": _rgb(1.0, 1.0, 1.0),
                        },
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def _color_status_row(service, spreadsheet_id: str, sheet_id: int, row_index: int, status: str) -> None:
    """Paint the entire row a color based on its status value."""
    color = _STATUS_COLORS.get(status.lower(), _DEFAULT_COLOR)
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_index,
                            "endRowIndex": row_index + 1,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": _rgb(*color),
                            }
                        },
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                }
            ]
        },
    ).execute()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def append_application(result: Any, listing: Any) -> None:
    """Append one row for *result* to the current week's tab and update Summary."""
    try:
        service = _get_service()
        spreadsheet_id = _get_spreadsheet_id()
        tab = _week_tab_name()
        sheet_id = _ensure_week_tab(service, spreadsheet_id, tab)

        applied_date = result.applied_at.date().isoformat() if result.applied_at else date.today().isoformat()
        applied_time = result.applied_at.strftime("%H:%M:%S") if result.applied_at else ""
        status_str = result.status.value if hasattr(result.status, "value") else str(result.status)

        # Determine contract type from listing description heuristically
        desc_lower = (listing.description or "").lower()
        if "b2b" in desc_lower or "business to business" in desc_lower:
            contract = "B2B"
        elif "permanent" in desc_lower or "full-time" in desc_lower or "employment" in desc_lower:
            contract = "Permanent"
        else:
            contract = ""

        is_remote = "Yes" if "remote" in (listing.location or "").lower() or "remote" in desc_lower else "No"

        row = [
            applied_date,
            applied_time,
            listing.company,
            listing.title,
            listing.source,
            status_str,
            result.reason or "",
            listing.salary_raw or "",
            listing.location or "",
            is_remote,
            contract,
            listing.url,
            listing.apply_url or "",
            result.prompt_version_cv or "",
            str(result.cover_letter_path) if result.cover_letter_path else "",
            str(getattr(result, "retry_count", 0)),
            "",  # Notes -- filled in manually
        ]

        append_resp = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{tab}!A:Q",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

        # Color the row we just appended
        updated_range = append_resp.get("updates", {}).get("updatedRange", "")
        if updated_range:
            # Parse row number from "SheetName!A5:Q5" style range
            try:
                row_num = int(updated_range.split("!")[1].split(":")[0].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")) - 1
                _color_status_row(service, spreadsheet_id, sheet_id, row_num, status_str)
            except Exception:
                pass

        log.info("Logged application to Sheets tab %s", tab)
    except Exception as exc:
        log.warning("Sheets logging failed: %s", exc)


def update_summary(stats: dict) -> None:
    """
    Upsert a row for the current week in the Summary tab and refresh All Time totals.
    *stats* must have keys: scraped, new, applied, failed, skipped.
    """
    try:
        service = _get_service()
        spreadsheet_id = _get_spreadsheet_id()
        week = _week_tab_name()

        # Read existing summary rows
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Summary!A:G",
        ).execute()
        rows = result.get("values", [])

        # Find or create the row for this week (rows[0] = header, rows[1] = All Time)
        week_row_index = None
        for i, r in enumerate(rows):
            if r and r[0] == week:
                week_row_index = i
                break

        applied = stats.get("applied", 0)
        failed = stats.get("failed", 0)
        total = applied + failed
        rate = f"{round(applied / total * 100, 1)}%" if total > 0 else "0%"
        week_data = [
            week,
            stats.get("scraped", 0),
            stats.get("new", 0),
            applied,
            failed,
            stats.get("skipped", 0),
            rate,
        ]

        if week_row_index is not None:
            # Update existing row
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=f"Summary!A{week_row_index + 1}:G{week_row_index + 1}",
                valueInputOption="USER_ENTERED",
                body={"values": [week_data]},
            ).execute()
        else:
            # Append a new row
            service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range="Summary!A:G",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [week_data]},
            ).execute()

        # Rebuild All Time totals from all week rows (skip header row[0] and All Time row[1])
        all_rows = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range="Summary!A:G",
        ).execute().get("values", [])

        total_scraped = total_new = total_applied = total_failed = total_skipped = 0
        for r in all_rows[2:]:  # skip header + All Time
            if len(r) >= 6:
                try:
                    total_scraped += int(r[1])
                    total_new += int(r[2])
                    total_applied += int(r[3])
                    total_failed += int(r[4])
                    total_skipped += int(r[5])
                except (ValueError, IndexError):
                    pass

        all_time_total = total_applied + total_failed
        all_time_rate = f"{round(total_applied / all_time_total * 100, 1)}%" if all_time_total > 0 else "0%"
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="Summary!A2:G2",
            valueInputOption="USER_ENTERED",
            body={"values": [["All Time", total_scraped, total_new, total_applied, total_failed, total_skipped, all_time_rate]]},
        ).execute()

        log.info("Summary tab updated for week %s", week)
    except Exception as exc:
        log.warning("Sheets summary update failed: %s", exc)


def apply_conditional_formatting(spreadsheet_id: str | None = None) -> None:
    """
    Add or refresh conditional formatting rules on the current week's tab.
    Called by --format-sheets CLI flag.
    """
    try:
        service = _get_service()
        sid = spreadsheet_id or _get_spreadsheet_id()
        tab = _week_tab_name()
        sheet_id = _sheet_id_for_tab(service, sid, tab)
        if sheet_id is None:
            log.warning("Tab %s not found; skipping conditional formatting", tab)
            return

        col = _STATUS_COL_INDEX  # 0-based column index for Status
        requests = []
        for status_val, color in _STATUS_COLORS.items():
            requests.append({
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{"sheetId": sheet_id, "startColumnIndex": col, "endColumnIndex": col + 1}],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": status_val}],
                            },
                            "format": {"backgroundColor": _rgb(*color)},
                        },
                    },
                    "index": 0,
                }
            })

        service.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests": requests},
        ).execute()
        log.info("Conditional formatting applied to tab %s", tab)
    except Exception as exc:
        log.warning("Conditional formatting failed: %s", exc)


def get_applied_urls() -> set[str]:
    """
    Return the set of all job URLs that have a non-skipped / non-dry_run row
    in any week tab of the spreadsheet.  Used to prevent duplicate applications
    even if the local SQLite DB was wiped.
    Returns an empty set if Google Sheets is not configured or unreachable.
    """
    try:
        service = _get_service()
        spreadsheet_id = _get_spreadsheet_id()
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        tab_names = [
            s["properties"]["title"]
            for s in meta.get("sheets", [])
            if s["properties"]["title"] != "Summary"
        ]

        url_col = WEEK_HEADERS.index("URL")           # column L (0-based index 11)
        status_col = WEEK_HEADERS.index("Status")     # column F (0-based index 5)
        skip_statuses = {"skipped", "dry_run"}

        applied: set[str] = set()
        for tab in tab_names:
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=f"{tab}!A:Q",
            ).execute()
            rows = result.get("values", [])
            for row in rows[1:]:  # skip header
                if len(row) <= url_col:
                    continue
                status = row[status_col].strip().lower() if len(row) > status_col else ""
                if status in skip_statuses:
                    continue
                url = row[url_col].strip()
                if url:
                    applied.add(url)
        log.debug("Sheets dedup: %d applied URLs loaded", len(applied))
        return applied
    except Exception as exc:
        log.debug("Sheets dedup skipped (Sheets not configured): %s", exc)
        return set()


def open_url() -> str:
    sid = _get_spreadsheet_id()
    return f"https://docs.google.com/spreadsheets/d/{sid}"
