"""
STRAVA INTEGRATION
==================
Pulls running activities from Strava and logs them as check-ins
on the running goal automatically.

How it works:
1. Uses your Strava OAuth refresh token to get a temporary access token
2. Fetches recent "Run" activities from the Strava API
3. Converts distances from meters to miles
4. Checks which runs are already logged (by Strava activity ID) to avoid duplicates
5. Creates check-ins for any new runs

Credentials are loaded from environment variables for security.
Set them in your shell profile (~/.zshrc) or a .env file:
    export STRAVA_CLIENT_ID="169410"
    export STRAVA_CLIENT_SECRET="your_secret"
    export STRAVA_REFRESH_TOKEN="your_token"
"""

import os
import requests
from datetime import datetime, timedelta
from database import get_db, add_checkin


# ── Configuration ────────────────────────────────────────────────────────
#
# We read credentials from environment variables instead of hardcoding them.
# This is a security best practice — if someone sees your code, they don't
# get your Strava account access.

STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
STRAVA_REFRESH_TOKEN = os.environ.get("STRAVA_REFRESH_TOKEN", "")

# Strava API endpoints
TOKEN_URL = "https://www.strava.com/oauth/token"
ACTIVITIES_URL = "https://www.strava.com/api/v3/athlete/activities"

# Conversion: 1 meter = 0.000621371 miles
METERS_TO_MILES = 0.000621371

# Cooldown: don't hit the Strava API more than once every 5 minutes
SYNC_COOLDOWN_SECONDS = 300

# Store the last sync time in memory (resets when server restarts, which is fine)
_last_sync_time = None


def is_configured():
    """Check if Strava credentials are set."""
    return all([STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_REFRESH_TOKEN])


def get_access_token():
    """
    Exchange the refresh token for a temporary access token.

    OAuth flow:
    - Your refresh token is permanent (like a master key)
    - The access token is temporary (~6 hours) and is what the API actually uses
    - This function trades the refresh token for a fresh access token each time
    """
    response = requests.post(TOKEN_URL, data={
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "refresh_token": STRAVA_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }, timeout=10)
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_recent_runs(days=7):
    """
    Fetch running activities from the last N days.

    Returns a list of dicts with:
    - strava_id: unique Strava activity ID (for deduplication)
    - name: activity name ("Morning Run", etc.)
    - distance_miles: distance in miles
    - date: ISO date string (YYYY-MM-DD)
    - moving_time_min: duration in minutes
    - start_date_raw: full timestamp for sorting
    """
    token = get_access_token()

    # Calculate the "after" timestamp — only fetch activities after this time
    after = int((datetime.utcnow() - timedelta(days=days)).timestamp())

    runs = []
    page = 1

    while True:
        response = requests.get(ACTIVITIES_URL, headers={
            "Authorization": f"Bearer {token}",
        }, params={
            "after": after,
            "per_page": 50,
            "page": page,
        }, timeout=10)
        response.raise_for_status()
        activities = response.json()

        if not activities:
            break

        for a in activities:
            # Only include runs (not rides, swims, etc.)
            if a.get("type") == "Run":
                runs.append({
                    "strava_id": a["id"],
                    "name": a.get("name", "Run"),
                    "distance_miles": round(a.get("distance", 0) * METERS_TO_MILES, 2),
                    "date": a["start_date"][:10],  # "2026-02-23T14:30:00Z" → "2026-02-23"
                    "moving_time_min": round(a.get("moving_time", 0) / 60, 1),
                    "start_date_raw": a["start_date"],
                })

        page += 1
        if len(activities) < 50:
            break  # No more pages

    return runs


def get_synced_strava_ids(goal_id):
    """
    Get all Strava activity IDs that have already been synced.

    We store the Strava ID in the check-in's notes field (prefixed with
    "strava:" ) so we can detect duplicates without a new database column.
    """
    db = get_db()
    rows = db.execute(
        "SELECT notes FROM checkins WHERE goal_id = ? AND notes LIKE 'strava:%'",
        (goal_id,)
    ).fetchall()
    db.close()

    ids = set()
    for row in rows:
        # notes format: "strava:12345678 - Morning Run"
        parts = row["notes"].split(" - ", 1)
        strava_part = parts[0]  # "strava:12345678"
        try:
            ids.add(int(strava_part.replace("strava:", "")))
        except ValueError:
            continue
    return ids


def sync_runs_to_goal(goal_id):
    """
    Main sync function. Fetches recent Strava runs and creates check-ins
    for any that haven't been logged yet.

    Returns a dict with:
    - synced: number of new runs added
    - skipped: number of duplicate runs skipped
    - error: error message if something went wrong, None otherwise
    """
    global _last_sync_time

    if not is_configured():
        return {
            "synced": 0,
            "skipped": 0,
            "error": "Strava credentials not configured. Set STRAVA_CLIENT_ID, "
                     "STRAVA_CLIENT_SECRET, and STRAVA_REFRESH_TOKEN environment variables.",
        }

    try:
        runs = fetch_recent_runs(days=30)
        already_synced = get_synced_strava_ids(goal_id)

        synced = 0
        skipped = 0

        for run in runs:
            if run["strava_id"] in already_synced:
                skipped += 1
                continue

            # Create a check-in with the Strava ID in notes for dedup
            notes = f"strava:{run['strava_id']} - {run['name']} ({run['moving_time_min']} min)"
            add_checkin(
                goal_id=goal_id,
                value=run["distance_miles"],
                checkin_date=run["date"],
                notes=notes,
            )
            synced += 1

        _last_sync_time = datetime.now()

        return {"synced": synced, "skipped": skipped, "error": None}

    except requests.RequestException as e:
        return {"synced": 0, "skipped": 0, "error": f"Strava API error: {e}"}
    except Exception as e:
        return {"synced": 0, "skipped": 0, "error": f"Sync error: {e}"}


def should_auto_sync():
    """Check if enough time has passed since the last sync (cooldown)."""
    global _last_sync_time
    if _last_sync_time is None:
        return True
    elapsed = (datetime.now() - _last_sync_time).total_seconds()
    return elapsed >= SYNC_COOLDOWN_SECONDS


def find_running_goal_id():
    """Find the running goal by looking for system_type='running'."""
    db = get_db()
    goal = db.execute(
        "SELECT id FROM goals WHERE system_type = 'running' LIMIT 1"
    ).fetchone()
    db.close()
    return goal["id"] if goal else None
