"""
DATABASE SETUP
==============
This file handles all database operations. We use SQLite, which stores
everything in a single file (goals.db). No separate database server needed.

Key concepts:
- A "table" is like a spreadsheet tab (goals, checkins, milestones)
- Each "row" is one record (one goal, one check-in entry)
- Each "column" is a field (name, target, deadline, etc.)
- Foreign keys link tables together (a check-in belongs to a goal)
"""

import sqlite3
from datetime import datetime, date


# Where the database file lives. SQLite creates it automatically.
DATABASE = "goals.db"


def get_db():
    """
    Open a connection to the database.

    'row_factory = sqlite3.Row' lets us access columns by name
    instead of by number. So instead of row[1] we can write row['name'].
    """
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")  # Enforce relationships between tables
    return db


def init_db():
    """
    Create all tables if they don't exist yet.
    Called once when the app first starts.
    """
    db = get_db()

    db.executescript("""
        -- GOALS TABLE: Each row is one goal you're tracking.
        -- Example: "Run 1,000 miles", target=1000, unit="miles"
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,               -- "Run 1,000 miles"
            target_value REAL NOT NULL,        -- 1000
            current_value REAL DEFAULT 0,      -- How far you've gotten so far
            unit TEXT NOT NULL,                -- "miles", "books", "dollars"
            deadline TEXT NOT NULL,            -- "2026-12-31" (stored as text, ISO format)
            created_at TEXT NOT NULL,          -- When you created this goal
            category TEXT DEFAULT '',          -- Optional grouping like "fitness", "finance"
            notes TEXT DEFAULT '',             -- Any extra info about this goal
            start_value REAL DEFAULT 0,        -- Starting point (e.g., $30,000 for HYSA)
            system_type TEXT DEFAULT 'generic' -- Hints for the achievement system engine
        );

        -- CHECKINS TABLE: Each row is one daily progress entry.
        -- Example: "Today I ran 3.5 miles" → goal_id=2, value=3.5
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            value REAL NOT NULL,              -- How much progress today
            date TEXT NOT NULL,               -- "2026-02-23"
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE
        );

        -- MILESTONES TABLE: Auto-generated checkpoints for each goal.
        -- Example: "250 miles reached!" at target_value=250
        CREATE TABLE IF NOT EXISTS milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            name TEXT NOT NULL,                -- "25% complete - 250 miles!"
            target_value REAL NOT NULL,        -- The value that triggers this milestone
            completed INTEGER DEFAULT 0,       -- 0=not yet, 1=done
            completed_at TEXT,
            FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE
        );
    """)

    db.commit()
    db.close()


# ── Helper functions for common database operations ──────────────────────


def get_all_goals():
    """Fetch every goal, newest first."""
    db = get_db()
    goals = db.execute("SELECT * FROM goals ORDER BY deadline ASC").fetchall()
    db.close()
    return goals


def get_goal(goal_id):
    """Fetch one goal by its ID."""
    db = get_db()
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    db.close()
    return goal


def create_goal(name, target_value, unit, deadline, category="", notes="",
                start_value=0, system_type="generic"):
    """
    Insert a new goal into the database.
    Returns the new goal's ID so we can redirect to it.
    """
    db = get_db()
    cursor = db.execute(
        """INSERT INTO goals (name, target_value, current_value, unit, deadline,
           created_at, category, notes, start_value, system_type)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, target_value, start_value, unit, deadline,
         datetime.now().isoformat(), category, notes, start_value, system_type)
    )
    goal_id = cursor.lastrowid
    db.commit()
    db.close()
    return goal_id


def update_goal(goal_id, name, target_value, unit, deadline, category="",
                notes="", start_value=0, system_type="generic"):
    """Update an existing goal's details."""
    db = get_db()
    db.execute(
        """UPDATE goals SET name=?, target_value=?, unit=?, deadline=?,
           category=?, notes=?, start_value=?, system_type=?
           WHERE id=?""",
        (name, target_value, unit, deadline, category, notes,
         start_value, system_type, goal_id)
    )
    db.commit()
    db.close()


def delete_goal(goal_id):
    """
    Delete a goal and all its check-ins and milestones.
    The ON DELETE CASCADE in our table definitions handles the related records
    automatically — when a goal is deleted, its check-ins and milestones go too.
    """
    db = get_db()
    db.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    db.commit()
    db.close()


def add_checkin(goal_id, value, checkin_date=None, notes=""):
    """
    Record progress on a goal.
    Also updates the goal's current_value by adding this check-in's value.

    Why update current_value separately? So the dashboard can show progress
    instantly without summing up every check-in each time.
    """
    if checkin_date is None:
        checkin_date = date.today().isoformat()

    db = get_db()
    db.execute(
        """INSERT INTO checkins (goal_id, value, date, notes, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (goal_id, value, checkin_date, notes, datetime.now().isoformat())
    )
    # Add this check-in's value to the running total
    db.execute(
        "UPDATE goals SET current_value = current_value + ? WHERE id = ?",
        (value, goal_id)
    )
    db.commit()

    # Check if any milestones were just reached
    goal = db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if goal:
        db.execute(
            """UPDATE milestones SET completed = 1, completed_at = ?
               WHERE goal_id = ? AND target_value <= ? AND completed = 0""",
            (datetime.now().isoformat(), goal_id, goal["current_value"])
        )
        db.commit()

    db.close()


def get_checkins(goal_id, limit=30):
    """Get recent check-ins for a goal, newest first."""
    db = get_db()
    checkins = db.execute(
        """SELECT * FROM checkins WHERE goal_id = ?
           ORDER BY date DESC LIMIT ?""",
        (goal_id, limit)
    ).fetchall()
    db.close()
    return checkins


def get_todays_checkins():
    """Get all check-ins logged today, grouped by goal."""
    db = get_db()
    today = date.today().isoformat()
    checkins = db.execute(
        """SELECT c.*, g.name as goal_name, g.unit
           FROM checkins c JOIN goals g ON c.goal_id = g.id
           WHERE c.date = ? ORDER BY c.created_at DESC""",
        (today,)
    ).fetchall()
    db.close()
    return checkins


def get_milestones(goal_id):
    """Get all milestones for a goal, ordered by target value."""
    db = get_db()
    milestones = db.execute(
        "SELECT * FROM milestones WHERE goal_id = ? ORDER BY target_value ASC",
        (goal_id,)
    ).fetchall()
    db.close()
    return milestones


def create_milestones(goal_id, milestones_list):
    """
    Bulk-create milestones for a goal.
    milestones_list is like: [("25% - 250 miles", 250), ("50% - 500 miles", 500)]
    """
    db = get_db()
    # Clear old milestones first (in case goal was edited)
    db.execute("DELETE FROM milestones WHERE goal_id = ?", (goal_id,))
    for name, target_val in milestones_list:
        db.execute(
            "INSERT INTO milestones (goal_id, name, target_value) VALUES (?, ?, ?)",
            (goal_id, name, target_val)
        )
    db.commit()
    db.close()


def get_checkin_history(goal_id, days=90):
    """Get daily check-in totals for charting purposes."""
    db = get_db()
    rows = db.execute(
        """SELECT date, SUM(value) as total
           FROM checkins WHERE goal_id = ?
           GROUP BY date ORDER BY date DESC LIMIT ?""",
        (goal_id, days)
    ).fetchall()
    db.close()
    return rows
