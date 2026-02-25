"""
GOAL TRACKER — Flask Application
=================================
This is the main web server. Flask handles:
- URL routing: maps URLs like /dashboard to Python functions
- Templates: renders HTML pages with dynamic data
- Forms: processes user input from check-ins and goal forms

To run: python app.py
Then visit: http://localhost:5000
"""

import secrets
import hashlib
import hmac
from flask import Flask, render_template, request, redirect, url_for, flash, session
from database import (
    init_db, get_all_goals, get_goal, create_goal, update_goal,
    delete_goal, add_checkin, get_checkins, get_todays_checkins,
    get_milestones, create_milestones
)
from systems import generate_system
from strava import sync_runs_to_goal, should_auto_sync, find_running_goal_id, is_configured as strava_configured
from gcal import sync_study_to_goal, should_auto_sync as gcal_should_auto_sync, find_study_goal_id, is_configured as gcal_configured
from datetime import date

# ── Create the Flask app ────────────────────────────────────────────────
#
# Flask is the web framework. Think of it as the traffic controller:
# when someone visits a URL, Flask figures out which Python function
# to call and sends back the HTML result.

app = Flask(__name__)
# secret_key is used to cryptographically sign session cookies and flash messages.
# A hardcoded key is fine for local-only use, but if you ever deploy this publicly,
# replace it with a random value: python3 -c "import secrets; print(secrets.token_hex(32))"
import os
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))


# ── Initialize database on startup ──────────────────────────────────────

with app.app_context():
    init_db()


# ── CSRF Protection ─────────────────────────────────────────────────────
#
# CSRF (Cross-Site Request Forgery) is when a malicious website tricks your
# browser into submitting a form to YOUR app. Example: you visit evil.com,
# and it has a hidden form that POSTs to localhost:5001/goals/1/delete.
#
# To prevent this, we generate a random token per session and require it
# in every POST form. A malicious site can't guess the token.

@app.before_request
def ensure_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)


@app.context_processor
def inject_csrf():
    """Make csrf_token available in all templates."""
    return {"csrf_token": session.get("csrf_token", "")}


def check_csrf():
    """Validate that the submitted CSRF token matches the session token."""
    token = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(token, expected):
        flash("Invalid form submission. Please try again.", "error")
        return False
    return True


# ── ROUTES ──────────────────────────────────────────────────────────────
#
# Each @app.route decorator says: "when someone visits this URL, run
# this function." The function returns HTML (via render_template).


# ── Dashboard (home page) ──────────────────────────────────────────────

@app.route("/")
def dashboard():
    """
    The main dashboard. Shows:
    - Progress bars for every goal
    - Pace status (ahead/behind/on track)
    - Today's recommended actions from each system
    - Today's check-ins so far
    """
    # Auto-sync Strava runs if configured and cooldown has passed
    strava_status = None
    if strava_configured() and should_auto_sync():
        running_goal_id = find_running_goal_id()
        if running_goal_id:
            result = sync_runs_to_goal(running_goal_id)
            if result["synced"] > 0:
                strava_status = f"Synced {result['synced']} new run(s) from Strava!"
            elif result["error"]:
                strava_status = f"Strava sync error: {result['error']}"

    # Auto-sync Google Calendar study sessions if configured and cooldown has passed
    gcal_status = None
    if gcal_configured() and gcal_should_auto_sync():
        study_goal_id = find_study_goal_id()
        if study_goal_id:
            result = sync_study_to_goal(study_goal_id)
            if result["synced"] > 0:
                gcal_status = f"Synced {result['synced']} study session(s) from Google Calendar!"
            elif result["error"]:
                gcal_status = f"Calendar sync error: {result['error']}"

    goals = get_all_goals()
    today = date.today()

    goal_data = []
    for goal in goals:
        system = generate_system(goal)
        start = goal["start_value"] or 0
        total = goal["target_value"] - start
        done = goal["current_value"] - start
        pct = min((done / total * 100) if total > 0 else 0, 100)

        goal_data.append({
            "goal": goal,
            "system": system,
            "progress_pct": round(pct, 1),
            "milestones": get_milestones(goal["id"]),
        })

    todays_checkins = get_todays_checkins()

    return render_template("dashboard.html",
                           goal_data=goal_data,
                           todays_checkins=todays_checkins,
                           today=today,
                           strava_status=strava_status,
                           strava_configured=strava_configured(),
                           gcal_status=gcal_status,
                           gcal_configured=gcal_configured())


# ── Strava Sync ─────────────────────────────────────────────────────────

@app.route("/sync/strava", methods=["POST"])
def strava_sync():
    """
    Manual Strava sync. Triggered by the "Sync Strava" button on the dashboard.
    Ignores the cooldown timer so you can force a refresh anytime.
    """
    if not check_csrf():
        return redirect(url_for("dashboard"))

    running_goal_id = find_running_goal_id()
    if not running_goal_id:
        flash("No running goal found. Create a goal with system type 'Running'.", "error")
        return redirect(url_for("dashboard"))

    if not strava_configured():
        flash("Strava not configured. Set STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, "
              "and STRAVA_REFRESH_TOKEN environment variables.", "error")
        return redirect(url_for("dashboard"))

    result = sync_runs_to_goal(running_goal_id)

    if result["error"]:
        flash(f"Strava sync error: {result['error']}", "error")
    elif result["synced"] > 0:
        flash(f"Synced {result['synced']} new run(s) from Strava! "
              f"({result['skipped']} already logged)", "success")
    else:
        flash(f"Already up to date — all recent runs are logged. "
              f"({result['skipped']} checked)", "success")

    # 303 = "See Other" — tells the browser to follow the redirect with GET, not POST.
    # A 302 redirect can cause browsers to re-POST, which is why we saw 405 errors.
    return redirect(url_for("dashboard"), code=303)


# ── Google Calendar Sync ──────────────────────────────────────────────

@app.route("/sync/gcal", methods=["POST"])
def gcal_sync():
    """
    Manual Google Calendar sync. Triggered by the "Sync Calendar" button
    on the dashboard. Ignores the cooldown timer so you can force a refresh.
    """
    if not check_csrf():
        return redirect(url_for("dashboard"))

    study_goal_id = find_study_goal_id()
    if not study_goal_id:
        flash("No study goal found. Create a goal with system type 'Study'.", "error")
        return redirect(url_for("dashboard"))

    if not gcal_configured():
        flash("Google Calendar not configured. Set GOOGLE_CALENDAR_ID and "
              "provide a service_account.json file.", "error")
        return redirect(url_for("dashboard"))

    result = sync_study_to_goal(study_goal_id)

    if result["error"]:
        flash(f"Calendar sync error: {result['error']}", "error")
    elif result["synced"] > 0:
        flash(f"Synced {result['synced']} study session(s) from Google Calendar! "
              f"({result['skipped']} already logged)", "success")
    else:
        flash(f"Already up to date — all recent study sessions are logged. "
              f"({result['skipped']} checked)", "success")

    return redirect(url_for("dashboard"), code=303)


# ── Daily Check-in ──────────────────────────────────────────────────────

@app.route("/checkin", methods=["GET", "POST"])
def checkin():
    """
    The daily check-in form. GET shows the form, POST saves the entries.

    Why GET and POST?
    - GET: "Show me the form" (when you visit the page)
    - POST: "Save this data" (when you submit the form)
    """
    goals = get_all_goals()

    if request.method == "POST":
        if not check_csrf():
            return redirect(url_for("checkin"))
        # Loop through each goal and check if the user entered a value
        count = 0
        for goal in goals:
            value_key = f"value_{goal['id']}"
            notes_key = f"notes_{goal['id']}"
            value = request.form.get(value_key, "").strip()

            if value:  # Only log goals where the user entered something
                try:
                    add_checkin(
                        goal_id=goal["id"],
                        value=float(value),
                        notes=request.form.get(notes_key, "").strip()
                    )
                    count += 1
                except ValueError:
                    flash(f"Invalid number for {goal['name']}", "error")

        if count > 0:
            flash(f"Logged progress on {count} goal(s)!", "success")
        return redirect(url_for("dashboard"))

    # For GET request, generate systems for context (show today's targets)
    goal_data = []
    for goal in goals:
        system = generate_system(goal)
        goal_data.append({"goal": goal, "system": system})

    return render_template("checkin.html", goal_data=goal_data, today=date.today())


# ── Goals List ──────────────────────────────────────────────────────────

@app.route("/goals")
def goals_list():
    """Show all goals with options to add, edit, delete."""
    goals = get_all_goals()
    return render_template("goals.html", goals=goals)


# ── Add Goal ────────────────────────────────────────────────────────────

@app.route("/goals/add", methods=["GET", "POST"])
def goal_add():
    """
    Add a new goal. The form collects name, target, unit, deadline, etc.
    After saving, we auto-generate milestones for it.
    """
    if request.method == "POST":
        if not check_csrf():
            return redirect(url_for("goal_add"))
        name = request.form["name"].strip()
        target_value = float(request.form["target_value"])
        unit = request.form["unit"].strip()
        deadline = request.form["deadline"]
        category = request.form.get("category", "").strip()
        notes = request.form.get("notes", "").strip()
        start_value = float(request.form.get("start_value", 0) or 0)
        system_type = request.form.get("system_type", "generic")

        goal_id = create_goal(name, target_value, unit, deadline,
                              category, notes, start_value, system_type)

        # Auto-generate milestones
        goal = get_goal(goal_id)
        system = generate_system(goal)
        if "milestones" in system:
            create_milestones(goal_id, system["milestones"])

        flash(f"Goal '{name}' created!", "success")
        return redirect(url_for("goal_detail", goal_id=goal_id))

    return render_template("goal_form.html", goal=None, today=date.today())


# ── Edit Goal ───────────────────────────────────────────────────────────

@app.route("/goals/<int:goal_id>/edit", methods=["GET", "POST"])
def goal_edit(goal_id):
    """Edit an existing goal. Re-generates milestones if target/deadline changed."""
    goal = get_goal(goal_id)
    if not goal:
        flash("Goal not found.", "error")
        return redirect(url_for("goals_list"))

    if request.method == "POST":
        if not check_csrf():
            return redirect(url_for("goal_edit", goal_id=goal_id))
        name = request.form["name"].strip()
        target_value = float(request.form["target_value"])
        unit = request.form["unit"].strip()
        deadline = request.form["deadline"]
        category = request.form.get("category", "").strip()
        notes = request.form.get("notes", "").strip()
        start_value = float(request.form.get("start_value", 0) or 0)
        system_type = request.form.get("system_type", "generic")

        update_goal(goal_id, name, target_value, unit, deadline,
                    category, notes, start_value, system_type)

        # Re-generate milestones
        goal = get_goal(goal_id)
        system = generate_system(goal)
        if "milestones" in system:
            create_milestones(goal_id, system["milestones"])

        flash(f"Goal '{name}' updated!", "success")
        return redirect(url_for("goal_detail", goal_id=goal_id))

    return render_template("goal_form.html", goal=goal, today=date.today())


# ── Delete Goal ─────────────────────────────────────────────────────────

@app.route("/goals/<int:goal_id>/delete", methods=["POST"])
def goal_delete(goal_id):
    """
    Delete a goal. Uses POST (not GET) because deleting data should never
    happen from a simple link click — it should require a form submission.
    This prevents accidental deletion from bots or prefetch.
    """
    if not check_csrf():
        return redirect(url_for("goals_list"))
    goal = get_goal(goal_id)
    if goal:
        delete_goal(goal_id)
        flash(f"Goal '{goal['name']}' deleted.", "success")
    return redirect(url_for("goals_list"))


# ── Goal Detail ─────────────────────────────────────────────────────────

@app.route("/goals/<int:goal_id>")
def goal_detail(goal_id):
    """
    Deep dive into one goal. Shows:
    - Full achievement system (schedule, daily actions, tips)
    - Milestone tracker
    - Recent check-in history
    - Pace analysis
    """
    goal = get_goal(goal_id)
    if not goal:
        flash("Goal not found.", "error")
        return redirect(url_for("goals_list"))

    system = generate_system(goal)
    milestones = get_milestones(goal_id)
    checkins = get_checkins(goal_id, limit=30)

    start = goal["start_value"] or 0
    total = goal["target_value"] - start
    done = goal["current_value"] - start
    pct = min((done / total * 100) if total > 0 else 0, 100)

    return render_template("goal_detail.html",
                           goal=goal,
                           system=system,
                           milestones=milestones,
                           checkins=checkins,
                           progress_pct=round(pct, 1))


# ── Run the app ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    # host="0.0.0.0" makes it accessible from your phone on the same Wi-Fi
    # debug mode: set FLASK_DEBUG=1 env var when you want auto-reload during development
    # NEVER use debug=True when exposed to the internet — it lets anyone run Python code
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"\n  Goal Tracker running at http://localhost:5001 (debug={'on' if debug else 'off'})\n")
    app.run(debug=debug, host="0.0.0.0", port=5001)
