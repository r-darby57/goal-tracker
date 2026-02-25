"""
ACHIEVEMENT SYSTEMS ENGINE
===========================
This file generates personalized plans for each goal. Instead of hard-coding
plans, it calculates everything dynamically from the goal's current state.

That means if you fall behind, the plan automatically adjusts (e.g., "you now
need 22 miles/week instead of 19"). If you get ahead, it eases off.

How it works:
1. Look at the goal's system_type (running, study, reading, savings, fitness, generic)
2. Calculate time remaining, progress remaining, and required pace
3. Generate daily actions, weekly targets, and milestones specific to that type
"""

from datetime import date, timedelta
import math


def generate_system(goal):
    """
    Main entry point. Takes a goal (database row) and returns a dict with:
    - weekly_target: what to do each week
    - daily_actions: specific daily recommendations
    - schedule: which days to do what
    - milestones: checkpoint list for the milestones table
    - pace_status: "ahead", "on_track", or "behind"
    - pace_detail: human-readable explanation
    """
    system_type = goal["system_type"]
    deadline = date.fromisoformat(goal["deadline"])
    today = date.today()

    # How much time and work is left
    days_left = max((deadline - today).days, 1)  # Avoid division by zero
    weeks_left = max(days_left / 7, 0.1)
    start = goal["start_value"] or 0
    total_work = goal["target_value"] - start           # Total amount of work
    done = goal["current_value"] - start                # Work completed so far
    remaining = max(total_work - done, 0)               # Work still to do
    progress_pct = (done / total_work * 100) if total_work > 0 else 0

    # How much time has passed (as a percentage of total timeline)
    created = date.fromisoformat(goal["created_at"][:10])
    total_days = max((deadline - created).days, 1)
    time_pct = ((today - created).days / total_days) * 100

    # Are we ahead or behind? Compare progress % to time elapsed %
    pace_diff = progress_pct - time_pct
    if pace_diff >= 5:
        pace_status = "ahead"
    elif pace_diff >= -5:
        pace_status = "on_track"
    else:
        pace_status = "behind"

    # Base context every system type uses
    context = {
        "days_left": days_left,
        "weeks_left": round(weeks_left, 1),
        "remaining": round(remaining, 1),
        "progress_pct": round(progress_pct, 1),
        "time_pct": round(time_pct, 1),
        "pace_status": pace_status,
        "done": round(done, 1),
        "total_work": round(total_work, 1),
    }

    # Route to the right system generator
    generators = {
        "running": _running_system,
        "study": _study_system,
        "reading": _reading_system,
        "savings": _savings_system,
        "fitness_weight": _fitness_weight_system,
        "fitness_waist": _fitness_waist_system,
        "generic": _generic_system,
    }

    generator = generators.get(system_type, _generic_system)
    system = generator(goal, context)
    system["pace_status"] = pace_status
    system["progress_pct"] = context["progress_pct"]
    system["context"] = context

    return system


# ── RUNNING SYSTEM ───────────────────────────────────────────────────────

def _running_system(goal, ctx):
    """
    Running plan: calculates weekly mileage, suggests run days,
    and flags if behind pace.

    Assumes 3 runs per week (matches Ryan's schedule).
    """
    weekly_miles = ctx["remaining"] / ctx["weeks_left"] if ctx["weeks_left"] > 0 else 0
    runs_per_week = 3
    miles_per_run = weekly_miles / runs_per_week if runs_per_week > 0 else 0

    # Suggest a schedule: Tue/Thu/Sat for 3x/week
    run_days = ["Tuesday", "Thursday", "Saturday"]
    # Make one run longer (the weekend run)
    short_run = round(miles_per_run * 0.85, 1)
    long_run = round(weekly_miles - (short_run * 2), 1)

    schedule = []
    for i, day in enumerate(run_days):
        dist = long_run if i == 2 else short_run
        schedule.append(f"{day}: {dist} miles")

    daily_per_day = round(ctx["remaining"] / ctx["days_left"], 1)

    pace_detail = _pace_message(ctx, "miles")

    return {
        "weekly_target": f"{round(weekly_miles, 1)} miles per week",
        "daily_actions": [
            f"Run ~{round(miles_per_run, 1)} miles on run days",
            f"That's about {daily_per_day} miles/day averaged across the whole week",
            "Log your run distance after each session",
        ],
        "schedule": schedule,
        "rest_days": ["Monday", "Wednesday", "Friday", "Sunday"],
        "pace_detail": pace_detail,
        "milestones": _percentage_milestones(goal, [10, 25, 50, 75, 90, 100]),
        "tips": _running_tips(ctx),
    }


def _running_tips(ctx):
    tips = []
    if ctx["pace_status"] == "behind":
        tips.append("Consider adding a 4th short run day to catch up.")
        tips.append("Even a 1-mile jog counts — consistency beats intensity.")
    if ctx["pace_status"] == "ahead":
        tips.append("Great pace! Don't increase mileage more than 10% per week to avoid injury.")
    tips.append("Rest days are for recovery — stretching and foam rolling help.")
    return tips


# ── STUDY SYSTEM (CISSP) ────────────────────────────────────────────────

def _study_system(goal, ctx):
    """
    Study plan: divides content into domains/sections across available weeks.
    CISSP has 8 domains — we allocate weeks proportionally.

    For non-CISSP study goals, falls back to a generic weekly hour target.
    """
    # CISSP-specific domains with approximate weight percentages
    cissp_domains = [
        ("Security & Risk Management", 15),
        ("Asset Security", 10),
        ("Security Architecture & Engineering", 13),
        ("Communication & Network Security", 13),
        ("Identity & Access Management", 13),
        ("Security Assessment & Testing", 12),
        ("Security Operations", 13),
        ("Software Development Security", 11),
    ]

    is_cissp = "cissp" in goal["name"].lower()

    weekly_hours = ctx["remaining"] / ctx["weeks_left"] if ctx["weeks_left"] > 0 else 0
    daily_hours = ctx["remaining"] / ctx["days_left"] if ctx["days_left"] > 0 else 0

    # Study 5 days/week, rest on weekends
    study_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    hours_per_study_day = weekly_hours / 5 if weekly_hours > 0 else 0

    schedule = []
    daily_actions = [
        f"Study {round(hours_per_study_day, 1)} hours on weekdays",
        "Log your study hours after each session",
    ]

    if is_cissp:
        # Allocate weeks to each domain based on weight
        total_weight = sum(w for _, w in cissp_domains)
        domain_schedule = []
        week_cursor = 0
        for domain_name, weight in cissp_domains:
            weeks_for_domain = max(round(ctx["weeks_left"] * weight / total_weight), 1)
            domain_schedule.append({
                "domain": domain_name,
                "weeks": weeks_for_domain,
                "weight": f"{weight}%",
                "start_week": week_cursor + 1,
            })
            week_cursor += weeks_for_domain

        # Figure out which domain we should be on NOW
        elapsed_weeks = ctx["time_pct"] / 100 * (ctx["weeks_left"] + (ctx["time_pct"] / 100 * ctx["weeks_left"] / (1 - ctx["time_pct"] / 100 + 0.001)))
        current_domain = cissp_domains[0][0]
        cumulative = 0
        for ds in domain_schedule:
            cumulative += ds["weeks"]
            if elapsed_weeks <= cumulative:
                current_domain = ds["domain"]
                break

        daily_actions.insert(0, f"Current focus: {current_domain}")
        daily_actions.append("Review flashcards for 15 min at end of session")
        daily_actions.append("Take practice questions on weekends")

        schedule = [f"Weeks {ds['start_week']}-{ds['start_week']+ds['weeks']-1}: "
                    f"{ds['domain']} ({ds['weight']} of exam)"
                    for ds in domain_schedule]

        for day in study_days:
            pass  # The domain schedule above serves as the schedule

    else:
        for day in study_days:
            schedule.append(f"{day}: Study {round(hours_per_study_day, 1)} hours")

    pace_detail = _pace_message(ctx, "study hours")

    # Target is pass/fail, so milestones are study-hour based
    milestones = _percentage_milestones(goal, [10, 25, 50, 75, 90, 100])

    return {
        "weekly_target": f"{round(weekly_hours, 1)} study hours per week",
        "daily_actions": daily_actions,
        "schedule": schedule,
        "pace_detail": pace_detail,
        "milestones": milestones,
        "tips": [
            "Active recall beats passive reading — test yourself constantly.",
            "Space out review: revisit old domains every 2 weeks.",
            "Practice exams are the #1 predictor of passing.",
        ],
    }


# ── READING SYSTEM ───────────────────────────────────────────────────────

def _reading_system(goal, ctx):
    """
    Reading plan: calculates books per month and pages per day.
    Assumes ~300 pages per book (adjustable via notes field).
    """
    pages_per_book = 300  # Reasonable average
    books_left = ctx["remaining"]
    total_pages_left = books_left * pages_per_book
    pages_per_day = total_pages_left / ctx["days_left"] if ctx["days_left"] > 0 else 0
    pages_per_week = pages_per_day * 7
    books_per_month = books_left / (ctx["weeks_left"] / 4.33) if ctx["weeks_left"] > 0 else 0

    # Reading time estimate (250 words/min, ~250 words/page = 1 min/page)
    minutes_per_day = round(pages_per_day)

    schedule = [
        "Daily: Read for ~{} minutes ({} pages)".format(minutes_per_day, round(pages_per_day)),
        "Weekly: Finish ~{} pages".format(round(pages_per_week)),
        "Monthly: Complete ~{} book(s)".format(round(books_per_month, 1)),
    ]

    daily_actions = [
        f"Read {round(pages_per_day)} pages today (~{minutes_per_day} min)",
        "Log each book as you finish it (check in with value = 1)",
        "Keep a running list of your next books so you never stall between reads",
    ]

    pace_detail = _pace_message(ctx, "books")

    return {
        "weekly_target": f"~{round(pages_per_week)} pages per week",
        "daily_actions": daily_actions,
        "schedule": schedule,
        "pace_detail": pace_detail,
        "milestones": _count_milestones(goal),
        "tips": [
            "Audiobooks count! Great for commutes and runs.",
            "If a book isn't clicking after 50 pages, drop it and move on.",
            "Read before bed — even 20 pages/night adds up fast.",
        ],
    }


# ── SAVINGS SYSTEM ───────────────────────────────────────────────────────

def _savings_system(goal, ctx):
    """
    Savings plan: calculates per-paycheck and per-month savings needed.
    Assumes biweekly paychecks (26 per year).
    """
    paychecks_left = round(ctx["weeks_left"] / 2)  # Biweekly
    per_paycheck = ctx["remaining"] / paychecks_left if paychecks_left > 0 else 0
    per_month = ctx["remaining"] / (ctx["weeks_left"] / 4.33) if ctx["weeks_left"] > 0 else 0
    per_week = ctx["remaining"] / ctx["weeks_left"] if ctx["weeks_left"] > 0 else 0

    schedule = [
        f"Per paycheck (biweekly): ${round(per_paycheck, 2):,.2f}",
        f"Per month: ${round(per_month, 2):,.2f}",
        f"Per week: ${round(per_week, 2):,.2f}",
    ]

    daily_actions = [
        f"Save ${round(per_paycheck, 2):,.2f} each paycheck",
        "Log your balance after each deposit (check in with the DEPOSIT amount, not total balance)",
        "Review and cut one unnecessary subscription this month",
    ]

    pace_detail = _pace_message(ctx, "dollars", prefix="$")

    return {
        "weekly_target": f"${round(per_week, 2):,.2f} per week",
        "daily_actions": daily_actions,
        "schedule": schedule,
        "pace_detail": pace_detail,
        "milestones": _dollar_milestones(goal),
        "tips": [
            "Automate transfers on payday so you never forget.",
            "Round up: if the target is $577/paycheck, set it to $600.",
            "Any windfalls (tax refund, bonus) can accelerate this massively.",
        ],
    }


# ── FITNESS WEIGHT SYSTEM ───────────────────────────────────────────────

def _fitness_weight_system(goal, ctx):
    """
    Weight loss plan for the 6-pack goal.
    Safe rate: 1-2 lbs per week. Calculates if the timeline is realistic.

    NOTE: For weight tracking, check-ins work differently — you log your
    CURRENT weight, and the system calculates the difference. We handle
    this by making the "value" the weight loss since last check-in.
    """
    lbs_to_lose = ctx["remaining"]
    lbs_per_week = lbs_to_lose / ctx["weeks_left"] if ctx["weeks_left"] > 0 else 0

    # Is the rate safe? (1-2 lbs/week is healthy)
    if lbs_per_week > 2:
        rate_warning = f"WARNING: {round(lbs_per_week, 1)} lbs/week needed — above the safe 2 lbs/week rate. Consider extending your deadline."
    elif lbs_per_week > 1.5:
        rate_warning = f"Aggressive but doable: {round(lbs_per_week, 1)} lbs/week. Stay consistent with diet and training."
    else:
        rate_warning = f"Healthy rate: {round(lbs_per_week, 1)} lbs/week. Very achievable with consistency."

    # Calorie math (rough): 3,500 cal = 1 lb. At 6'3" 240lbs active male, TDEE ~3,000 cal
    daily_deficit = round(lbs_per_week * 3500 / 7)
    target_calories = 3000 - daily_deficit

    schedule = [
        "Monday/Wednesday/Friday: Lift (compound movements)",
        "Tuesday/Thursday/Saturday: Run",
        "Sunday: Active recovery (walk, stretch)",
        f"Daily calorie target: ~{target_calories} cal",
    ]

    daily_actions = [
        f"Weigh yourself each morning (log weekly average, not daily)",
        f"Target ~{target_calories} calories today",
        "Hit 200g+ protein to preserve muscle while cutting",
        "Drink 1 gallon of water",
    ]

    return {
        "weekly_target": f"Lose ~{round(lbs_per_week, 1)} lbs per week",
        "daily_actions": daily_actions,
        "schedule": schedule,
        "pace_detail": rate_warning,
        "milestones": _percentage_milestones(goal, [10, 25, 50, 75, 100]),
        "tips": [
            "Weekly averages matter more than daily weigh-ins (water weight fluctuates).",
            "Log weekly: compare this Monday to last Monday.",
            "Protein is king — it preserves muscle and keeps you full.",
            "Sleep 7+ hours. Recovery is when results happen.",
        ],
    }


# ── FITNESS WAIST SYSTEM ────────────────────────────────────────────────

def _fitness_waist_system(goal, ctx):
    """
    Waist measurement tracking for the 6-pack goal.
    Target is to reduce waist circumference.
    """
    inches_to_lose = ctx["remaining"]
    inches_per_week = inches_to_lose / ctx["weeks_left"] if ctx["weeks_left"] > 0 else 0
    inches_per_month = inches_per_week * 4.33

    daily_actions = [
        "Measure waist at navel level each Monday morning",
        "Log the measurement weekly (check in with inches LOST that week)",
        "Focus on core work 3x/week: planks, hanging leg raises, ab wheel",
    ]

    schedule = [
        f"Weekly target: lose ~{round(inches_per_week, 2)} inches",
        f"Monthly target: lose ~{round(inches_per_month, 1)} inches",
        "Measure same time, same spot each week for consistency",
    ]

    return {
        "weekly_target": f"Lose ~{round(inches_per_week, 2)} inches per week",
        "daily_actions": daily_actions,
        "schedule": schedule,
        "pace_detail": _pace_message(ctx, "inches"),
        "milestones": _percentage_milestones(goal, [25, 50, 75, 100]),
        "tips": [
            "Waist measurement is the best visual progress indicator.",
            "Measure at the same time each week (morning, before eating).",
            "Core exercises help but fat loss is mostly diet-driven.",
        ],
    }


# ── GENERIC SYSTEM (for custom goals) ───────────────────────────────────

def _generic_system(goal, ctx):
    """
    Fallback system for any goal type. Calculates simple daily/weekly rates.
    This is what new custom goals get until they're assigned a specific type.
    """
    per_day = ctx["remaining"] / ctx["days_left"] if ctx["days_left"] > 0 else 0
    per_week = per_day * 7
    unit = goal["unit"]

    daily_actions = [
        f"Complete {round(per_day, 2)} {unit} today",
        f"Log your progress daily",
    ]

    schedule = [
        f"Daily target: {round(per_day, 2)} {unit}",
        f"Weekly target: {round(per_week, 2)} {unit}",
    ]

    return {
        "weekly_target": f"{round(per_week, 2)} {unit} per week",
        "daily_actions": daily_actions,
        "schedule": schedule,
        "pace_detail": _pace_message(ctx, unit),
        "milestones": _percentage_milestones(goal, [25, 50, 75, 100]),
        "tips": [
            "Consistency beats intensity — show up every day.",
            "Track daily to catch slips early.",
        ],
    }


# ── MILESTONE GENERATORS ────────────────────────────────────────────────

def _percentage_milestones(goal, percentages):
    """Generate milestones at given percentage points."""
    start = goal["start_value"] or 0
    total = goal["target_value"] - start
    unit = goal["unit"]
    milestones = []
    for pct in percentages:
        value = start + (total * pct / 100)
        if pct == 100:
            name = f"GOAL COMPLETE! {goal['target_value']} {unit}!"
        else:
            name = f"{pct}% — {round(value, 1)} {unit} reached"
        milestones.append((name, round(value, 1)))
    return milestones


def _count_milestones(goal):
    """Generate milestones for count-based goals (books, certifications)."""
    target = int(goal["target_value"])
    start = int(goal["start_value"] or 0)
    milestones = []
    for i in range(start + 1, target + 1):
        name = f"Book #{i} complete!" if "book" in goal["unit"].lower() else f"{i} {goal['unit']} done!"
        milestones.append((name, i))
    return milestones


def _dollar_milestones(goal):
    """Generate milestones at round dollar amounts."""
    start = goal["start_value"] or 0
    target = goal["target_value"]
    milestones = []
    # Create milestones every $2,500 from start to target
    step = 2500
    current = math.ceil(start / step) * step
    if current <= start:
        current += step
    while current <= target:
        if current == target:
            milestones.append((f"GOAL COMPLETE! ${current:,.0f}!", current))
        else:
            milestones.append((f"${current:,.0f} reached!", current))
        current += step
    # Always include the exact target
    if milestones and milestones[-1][1] != target:
        milestones.append((f"GOAL COMPLETE! ${target:,.0f}!", target))
    return milestones


# ── HELPERS ──────────────────────────────────────────────────────────────

def _pace_message(ctx, unit, prefix=""):
    """Generate a human-readable pace status message."""
    if ctx["remaining"] <= 0:
        return "Goal complete! You did it!"

    status = ctx["pace_status"]
    diff = abs(ctx["progress_pct"] - ctx["time_pct"])

    if status == "ahead":
        return (f"You're AHEAD of pace! {prefix}{ctx['done']} {unit} done "
                f"({ctx['progress_pct']}% complete with {ctx['time_pct']}% of time elapsed). "
                f"Keep it up — {prefix}{ctx['remaining']} {unit} to go.")
    elif status == "on_track":
        return (f"You're ON TRACK. {prefix}{ctx['done']} {unit} done "
                f"({ctx['progress_pct']}% complete, {ctx['time_pct']}% of time elapsed). "
                f"{prefix}{ctx['remaining']} {unit} remaining.")
    else:
        return (f"You're BEHIND pace by ~{round(diff)}%. "
                f"{prefix}{ctx['done']} {unit} done ({ctx['progress_pct']}% complete "
                f"but {ctx['time_pct']}% of time has passed). "
                f"Need to pick up the pace — {prefix}{ctx['remaining']} {unit} to go.")
