"""Google Calendar follow-up event creation."""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

log = logging.getLogger(__name__)


def _get_service():
    from storage.vault import get_secret
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    token_json = get_secret("GOOGLE_OAUTH_TOKEN")
    if not token_json:
        raise RuntimeError("GOOGLE_OAUTH_TOKEN not in vault.")
    creds_data = json.loads(token_json)
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=creds_data.get("client_id"),
        client_secret=creds_data.get("client_secret"),
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _get_calendar_id() -> str:
    from storage.vault import get_secret
    return get_secret("GOOGLE_CALENDAR_ID") or "primary"


def _event_exists(service, calendar_id: str, summary_prefix: str) -> bool:
    events = service.events().list(
        calendarId=calendar_id,
        q=summary_prefix,
        maxResults=5,
    ).execute()
    return bool(events.get("items"))


def create_followup_event(result: Any, listing: Any, followup_weeks: int = 3) -> None:
    """Create a Calendar follow-up event *followup_weeks* after application date."""
    from applicator.ats.base import ApplicationStatus
    if result.status != ApplicationStatus.APPLIED:
        return
    try:
        service = _get_service()
        calendar_id = _get_calendar_id()

        applied_date = result.applied_at.date() if result.applied_at else date.today()
        followup_date = applied_date + timedelta(weeks=followup_weeks)

        summary = f"Follow up: {listing.title} at {listing.company}"

        # Avoid duplicates
        if _event_exists(service, calendar_id, f"Follow up: {listing.title} at {listing.company}"):
            log.debug("Calendar event already exists for %s; skipping", listing.company)
            return

        event = {
            "summary": summary,
            "description": (
                f"Applied: {applied_date.isoformat()}\n"
                f"Source: {listing.source}\n"
                f"Job URL: {listing.url}\n"
                f"Apply URL: {listing.apply_url}\n"
                f"Status: {result.status.value}"
            ),
            "start": {"date": followup_date.isoformat()},
            "end": {"date": followup_date.isoformat()},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 480},
                    {"method": "email", "minutes": 1440},
                ],
            },
            "colorId": "5",
        }
        service.events().insert(calendarId=calendar_id, body=event).execute()
        log.info("Calendar follow-up created for %s on %s", listing.company, followup_date)
    except Exception as exc:
        log.warning("Calendar event creation failed: %s", exc)


def create_dedicated_calendar() -> str:
    """Create a 'Job Applications' calendar and store its ID in the vault."""
    from storage.vault import set_secret
    service = _get_service()
    body = {"summary": "Job Applications", "timeZone": "UTC"}
    resp = service.calendars().insert(body=body).execute()
    cal_id = resp["id"]
    set_secret("GOOGLE_CALENDAR_ID", cal_id)
    log.info("Created calendar: %s (id=%s)", body["summary"], cal_id)
    return cal_id
