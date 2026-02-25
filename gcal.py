"""
GOOGLE CALENDAR INTEGRATION
============================
Pulls study session events from Google Calendar and logs them as check-ins
on the study goal automatically.

How it works:
1. Uses a Google service account to access your calendar (no browser login needed)
2. Fetches recent events matching a keyword (default: "CISSP")
3. Calculates duration from event start/end times
4. Checks which events are already logged (by event ID) to avoid duplicates
5. Creates check-ins for any new study sessions

Setup:
1. Enable the Google Calendar API in your Google Cloud project
2. Create a service account and download the JSON key file
3. Share your calendar with the service account email (found in the JSON as client_email)
4. Set environment variables:
    export GOOGLE_CALENDAR_ID="your_email@gmail.com"
    export GOOGLE_SERVICE_ACCOUNT_FILE="service_account.json"
    export GCAL_EVENT_KEYWORD="CISSP"  (optional, defaults to "CISSP")
"""

import os
from datetime import datetime, timedelta
from database import get_db, add_checkin


# ── Configuration ────────────────────────────────────────────────────────

GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "")
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
GCAL_EVENT_KEYWORD = os.environ.get("GCAL_EVENT_KEYWORD", "CISSP")

# Cooldown: don't hit the Calendar API more than once every 5 minutes
SYNC_COOLDOWN_SECONDS = 300
_last_sync_time = None


def is_configured():
    """Check if Google Calendar credentials are available."""
    return bool(GOOGLE_CALENDAR_ID) and os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE)


def get_calendar_service():
    """
    Build the Google Calendar API client using service account credentials.

    A service account is like a robot Google account — it has its own email
    address and can access calendars shared with it. No browser login needed.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    return build("calendar", "v3", credentials=credentials)


def fetch_recent_study_events(days=30):
    """
    Fetch calendar events matching the study keyword from the last N days.

    Returns a list of dicts with:
    - event_id: unique Google Calendar event ID (for deduplication)
    - summary: event title ("CISSP Study - Domain 3", etc.)
    - duration_hours: duration in hours (e.g. 1.5)
    - date: ISO date string (YYYY-MM-DD)
    """
    service = get_calendar_service()

    now = datetime.utcnow()
    time_min = (now - timedelta(days=days)).isoformat() + "Z"
    time_max = now.isoformat() + "Z"

    events = []
    page_token = None

    while True:
        result = service.events().list(
            calendarId=GOOGLE_CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            q=GCAL_EVENT_KEYWORD,
            singleEvents=True,      # Expand recurring events into individual instances
            orderBy="startTime",
            maxResults=100,
            pageToken=page_token,
        ).execute()

        for event in result.get("items", []):
            start = event.get("start", {})
            end = event.get("end", {})

            # Skip all-day events (no specific time = not a study session)
            if "dateTime" not in start or "dateTime" not in end:
                continue

            start_dt = datetime.fromisoformat(start["dateTime"])
            end_dt = datetime.fromisoformat(end["dateTime"])
            duration_hours = round((end_dt - start_dt).total_seconds() / 3600, 2)

            # Skip events with zero or negative duration
            if duration_hours <= 0:
                continue

            events.append({
                "event_id": event["id"],
                "summary": event.get("summary", "Study Session"),
                "duration_hours": duration_hours,
                "date": start_dt.date().isoformat(),
            })

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return events


def get_synced_gcal_ids(goal_id):
    """
    Get all Google Calendar event IDs that have already been synced.

    We store the event ID in the check-in's notes field (prefixed with
    "gcal:") so we can detect duplicates without a new database column.
    """
    db = get_db()
    rows = db.execute(
        "SELECT notes FROM checkins WHERE goal_id = ? AND notes LIKE 'gcal:%'",
        (goal_id,)
    ).fetchall()
    db.close()

    ids = set()
    for row in rows:
        # notes format: "gcal:abc123def456 - CISSP Study (1.5h)"
        parts = row["notes"].split(" - ", 1)
        gcal_part = parts[0]  # "gcal:abc123def456"
        event_id = gcal_part.replace("gcal:", "")
        if event_id:
            ids.add(event_id)
    return ids


def sync_study_to_goal(goal_id):
    """
    Main sync function. Fetches recent study events from Google Calendar
    and creates check-ins for any that haven't been logged yet.

    Returns a dict with:
    - synced: number of new sessions added
    - skipped: number of duplicate events skipped
    - error: error message if something went wrong, None otherwise
    """
    global _last_sync_time

    if not is_configured():
        return {
            "synced": 0,
            "skipped": 0,
            "error": "Google Calendar not configured. Set GOOGLE_CALENDAR_ID and "
                     "provide a service_account.json file.",
        }

    try:
        events = fetch_recent_study_events(days=30)
        already_synced = get_synced_gcal_ids(goal_id)

        synced = 0
        skipped = 0

        for event in events:
            if event["event_id"] in already_synced:
                skipped += 1
                continue

            notes = f"gcal:{event['event_id']} - {event['summary']} ({event['duration_hours']}h)"
            add_checkin(
                goal_id=goal_id,
                value=event["duration_hours"],
                checkin_date=event["date"],
                notes=notes,
            )
            synced += 1

        _last_sync_time = datetime.now()
        return {"synced": synced, "skipped": skipped, "error": None}

    except Exception as e:
        return {"synced": 0, "skipped": 0, "error": f"Calendar sync error: {e}"}


def should_auto_sync():
    """Check if enough time has passed since the last sync (cooldown)."""
    global _last_sync_time
    if _last_sync_time is None:
        return True
    elapsed = (datetime.now() - _last_sync_time).total_seconds()
    return elapsed >= SYNC_COOLDOWN_SECONDS


def find_study_goal_id():
    """Find the study goal by looking for system_type='study'."""
    db = get_db()
    goal = db.execute(
        "SELECT id FROM goals WHERE system_type = 'study' LIMIT 1"
    ).fetchone()
    db.close()
    return goal["id"] if goal else None
