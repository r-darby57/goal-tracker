"""
SEED DATA
=========
Run this once to populate the database with Ryan's 2026 goals.
Each goal gets its system_type so the achievement engine knows
what kind of plan to generate.

Usage: python seed_data.py
"""

from database import init_db, create_goal, create_milestones, get_db
from systems import generate_system, _percentage_milestones


def seed():
    """Create the database tables and insert the 5 pre-loaded goals."""
    init_db()

    # Check if goals already exist (don't double-seed)
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
    db.close()
    if count > 0:
        print(f"Database already has {count} goals. Skipping seed.")
        print("Delete goals.db and re-run to reseed.")
        return

    goals = [
        {
            "name": "CISSP Certification",
            "target_value": 300,  # 300 study hours is a common recommendation
            "unit": "study hours",
            "deadline": "2026-12-01",  # Give yourself December to take the exam
            "category": "career",
            "notes": "ISC2 CISSP — 8 domains. Target: pass by December 2026.",
            "start_value": 0,
            "system_type": "study",
        },
        {
            "name": "Run 1,000 Miles",
            "target_value": 1000,
            "unit": "miles",
            "deadline": "2026-12-31",
            "category": "fitness",
            "notes": "Running 3x/week. Log each run's distance.",
            "start_value": 0,
            "system_type": "running",
        },
        {
            "name": "Read 10 Books",
            "target_value": 10,
            "unit": "books",
            "deadline": "2026-12-31",
            "category": "personal",
            "notes": "Log 1 each time you finish a book.",
            "start_value": 0,
            "system_type": "reading",
        },
        {
            "name": "HYSA Savings to $45,000",
            "target_value": 45000,
            "unit": "dollars",
            "deadline": "2026-12-31",
            "category": "finance",
            "notes": "High-yield savings account. Log each deposit amount (not total balance).",
            "start_value": 30000,
            "system_type": "savings",
        },
        {
            "name": "6-Pack: Weight Loss",
            "target_value": 40,  # Lose 40 lbs (240 → 200)
            "unit": "lbs lost",
            "deadline": "2026-12-31",
            "category": "fitness",
            "notes": "Starting: 240 lbs, 6'3\". Target: ~200 lbs. Log weekly weight LOSS (e.g., lost 1.5 lbs this week → enter 1.5).",
            "start_value": 0,
            "system_type": "fitness_weight",
        },
        {
            "name": "6-Pack: Waist Reduction",
            "target_value": 6,  # Lose 6 inches (est. ~38\" → 32\")
            "unit": "inches lost",
            "deadline": "2026-12-31",
            "category": "fitness",
            "notes": "Measure at navel level Monday mornings. Log inches LOST each week.",
            "start_value": 0,
            "system_type": "fitness_waist",
        },
    ]

    print("Seeding database with 2026 goals...\n")

    for g in goals:
        goal_id = create_goal(**g)
        print(f"  Created: {g['name']} (ID: {goal_id})")

        # Generate and save milestones for this goal
        from database import get_goal
        goal = get_goal(goal_id)
        system = generate_system(goal)
        if "milestones" in system:
            create_milestones(goal_id, system["milestones"])
            print(f"    → {len(system['milestones'])} milestones created")

    print("\nDone! Start the app with: python app.py")


if __name__ == "__main__":
    seed()
