"""
GOOGLE CALENDAR INTEGRATION
============================
Pulls study session events from Google Calendar and logs them as check-ins
on the study goal automatically.

How it works:
1. Uses your calendar's secret iCal URL (no API keys or GCP project needed)
2. Fetches the .ics feed and parses events matching a keyword (default: "CISSP")
3. Calculates duration from event start/end times
4. Checks which events are already logged (by event UID) to avoid duplicates
5. Creates check-ins for any new study sessions

Setup:
1. Go to Google Calendar > Settings > your calendar > "Secret address in iCal format"
2. Copy that URL
3. Set one environment variable:
    export GCAL_ICAL_URL="https://calendar.google.com/calendar/ical/..."
    export GCAL_EVENT_KEYWORD="CISSP"  (optional, defaults to "CISSP")
"""

import os
import requests
from datetime import datetime, timedelta, date, timezone
from icalendar import Calendar
from database import get_db, add_checkin


# ── Configuration ────────────────────────────────────────────────────────

GCAL_ICAL_URL = os.environ.get("GCAL_ICAL_URL", "")
GCAL_EVENT_KEYWORD = os.environ.get("GCAL_EVENT_KEYWORD", "CISSP").lower()

# Cooldown: don't hit the calendar more than once every 5 minutes
SYNC_COOLDOWN_SECONDS = 300
_last_sync_time = None


def is_configured():
    """Check if the iCal URL is set."""
    return bool(GCAL_ICAL_URL)


def fetch_recent_study_events(days=30):
    """
    Fetch the iCal feed and extract study events from the last N days.

    Returns a list of dicts with:
    - event_id: unique event UID (for deduplication)
    - summary: event title ("CISSP Study - Domain 3", etc.)
    - duration_hours: duration in hours (e.g. 1.5)
    - date: ISO date string (YYYY-MM-DD)
    """
    response = requests.get(GCAL_ICAL_URL, timeout=15)
    response.raise_for_status()

    cal = Calendar.from_ical(response.text)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    events = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        summary = str(component.get("summary", ""))

        # Filter by keyword
        if GCAL_EVENT_KEYWORD not in summary.lower():
            continue

        dtstart = component.get("dtstart")
        dtend = component.get("dtend")
        if not dtstart or not dtend:
            continue

        start_dt = dtstart.dt
        end_dt = dtend.dt

        all_day = isinstance(start_dt, date) and not isinstance(start_dt, datetime)

        if all_day:
            # All-day events: create one 8-hour entry (9am-5pm) per day
            end_date = end_dt if isinstance(end_dt, date) else end_dt.date()
            current = start_dt
            while current < end_date:
                day_dt = datetime.combine(current, datetime.min.time(), tzinfo=timezone.utc)
                if day_dt < cutoff:
                    current += timedelta(days=1)
                    continue
                uid = str(component.get("uid", ""))
                if not uid:
                    current += timedelta(days=1)
                    continue
                events.append({
                    "event_id": f"{uid}_{current.isoformat()}",
                    "summary": summary,
                    "duration_hours": 8.0,
                    "date": current.isoformat(),
                })
                current += timedelta(days=1)
            continue

        # Make timezone-aware for comparison
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)

        # Skip events older than our cutoff
        if start_dt < cutoff:
            continue

        if isinstance(end_dt, date) and not isinstance(end_dt, datetime):
            continue
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        duration_hours = round((end_dt - start_dt).total_seconds() / 3600, 2)
        if duration_hours <= 0:
            continue

        uid = str(component.get("uid", ""))
        if not uid:
            continue

        events.append({
            "event_id": uid,
            "summary": summary,
            "duration_hours": duration_hours,
            "date": start_dt.date().isoformat(),
        })

    return events


def get_synced_gcal_ids(goal_id):
    """
    Get all Google Calendar event UIDs that have already been synced.

    We store the event UID in the check-in's notes field (prefixed with
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
        # notes format: "gcal:uid123@google.com - CISSP Study (1.5h)"
        parts = row["notes"].split(" - ", 1)
        gcal_part = parts[0]  # "gcal:uid123@google.com"
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
            "error": "Google Calendar not configured. Set GCAL_ICAL_URL to your "
                     "calendar's secret iCal address.",
        }

    try:
        events = fetch_recent_study_events(days=90)
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

    except requests.RequestException as e:
        return {"synced": 0, "skipped": 0, "error": f"Calendar fetch error: {e}"}
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
