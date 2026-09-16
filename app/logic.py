import json
import sqlite3

from . import database


def run_distribution_algorithm(event_uid, day_type=None):
    db = database.get_db()
    db.row_factory = sqlite3.Row

    # Get the active day types for the event
    event = db.execute(
        "SELECT active_days, slot_count FROM events WHERE uid = ?", (event_uid,)
    ).fetchone()
    if not event:
        return

    slot_count = event["slot_count"] if event["slot_count"] is not None else 49

    if day_type:
        active_days = [day_type]
    else:
        active_days = [
            day
            for day, is_active in json.loads(event["active_days"]).items()
            if is_active
        ]

    # Reset all relevant submissions for the event to 'Pending' before starting
    if day_type:
        db.execute(
            "UPDATE submissions SET status = 'Pending' WHERE event_uid = ? AND day_type = ?",
            (event_uid, day_type),
        )
    else:
        db.execute(
            "UPDATE submissions SET status = 'Pending' WHERE event_uid = ?",
            (event_uid,),
        )

    # Loop through each active day and run the distribution for it
    for current_day_type in active_days:
        # 1. Preparation for the current day_type
        # Clear all non-locked assignments for this specific day
        db.execute(
            "DELETE FROM assignments WHERE event_uid = ? AND day_type = ? AND is_locked = 0",
            (event_uid, current_day_type),
        )

        # Fetch submissions specifically for this day_type
        submissions = db.execute(
            "SELECT * FROM submissions WHERE event_uid = ? AND day_type = ? ORDER BY resources DESC, timestamp ASC",
            (event_uid, current_day_type),
        ).fetchall()

        # Fetch locked assignments specifically for this day_type
        locked_assignments_raw = db.execute(
            "SELECT * FROM assignments WHERE event_uid = ? AND day_type = ? AND is_locked = 1",
            (event_uid, current_day_type),
        ).fetchall()

        taken_slots = {a["slot_index"] for a in locked_assignments_raw}
        assigned_player_ids = {a["player_id"] for a in locked_assignments_raw}

        # Update status for players who already have locked assignments
        for pid in assigned_player_ids:
            db.execute(
                "UPDATE submissions SET status = 'Locked' WHERE event_uid = ? AND day_type = ? AND player_id = ?",
                (event_uid, current_day_type, pid),
            )

        # 2. Calculate Demand for each slot (static demand based on all submissions for this day)
        slot_demand = {i: 0 for i in range(slot_count)}
        for sub in submissions:
            try:
                if not sub["feasible_slots"]:
                    continue
                f_slots = json.loads(sub["feasible_slots"])
                for s in f_slots:
                    if 0 <= s < slot_count:
                        slot_demand[s] += 1
            except (json.JSONDecodeError, TypeError):
                continue

        # 3. Ranking & Allocation for the current day_type
        for submission in submissions:
            # Skip if player already has a locked assignment for this day
            if submission["player_id"] in assigned_player_ids:
                continue

            is_assigned = False
            try:
                feasible_slots = json.loads(submission["feasible_slots"])
            except (json.JSONDecodeError, TypeError):
                feasible_slots = None

            if not isinstance(feasible_slots, list) or not feasible_slots:
                db.execute(
                    "UPDATE submissions SET status = 'Waitlisted' WHERE id = ?",
                    (submission["id"],),
                )
                continue

            # Filter out invalid indices or non-integers to avoid KeyError/TypeError
            feasible_slots = [
                s for s in feasible_slots if isinstance(s, int) and 0 <= s < slot_count
            ]

            if not feasible_slots:
                db.execute(
                    "UPDATE submissions SET status = 'Waitlisted' WHERE id = ?",
                    (submission["id"],),
                )
                continue

            # Filter to slots that are not yet taken
            available_feasible = [s for s in feasible_slots if s not in taken_slots]

            if available_feasible:
                # SMARTER LOGIC: Pick the slot with the LEAST overall demand
                # This leaves high-demand slots for players who might ONLY be able to do those slots.
                # Tie-break by slot index.
                best_slot = min(available_feasible, key=lambda s: (slot_demand[s], s))

                # Assign this slot to the player for this specific day
                db.execute(
                    """
                    INSERT INTO assignments (event_uid, day_type, slot_index, player_id, is_locked)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (
                        event_uid,
                        current_day_type,
                        best_slot,
                        submission["player_id"],
                        0,
                    ),
                )

                # Update submission status
                db.execute(
                    "UPDATE submissions SET status = 'Confirmed' WHERE id = ?",
                    (submission["id"],),
                )

                taken_slots.add(best_slot)
                assigned_player_ids.add(submission["player_id"])
                is_assigned = True

            if not is_assigned:
                # Waitlist the player
                db.execute(
                    "UPDATE submissions SET status = 'Waitlisted' WHERE id = ?",
                    (submission["id"],),
                )

    db.commit()


def format_minutes(total_minutes):
    if not total_minutes:
        return "0m"
    days = total_minutes // 1440
    hours = (total_minutes % 1440) // 60
    minutes = total_minutes % 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def get_superadmin_metrics(db, time_range: str = "all") -> dict:
    """Computes global KPIs across all registered events."""
    valid_range = time_range if time_range in ("1w", "2w", "4w") else "all"
    time_filters = {"1w": "-7 days", "2w": "-14 days", "4w": "-28 days"}

    if valid_range in time_filters:
        events_rows = db.execute(
            "SELECT uid, name, active_days, admin_secret, slot_count, created_at "
            "FROM events WHERE created_at >= datetime('now', ?) ORDER BY created_at DESC",
            (time_filters[valid_range],)
        ).fetchall()
    else:
        events_rows = db.execute(
            "SELECT uid, name, active_days, admin_secret, slot_count, created_at "
            "FROM events ORDER BY created_at DESC"
        ).fetchall()

    events = [
        {"uid": r[0], "name": r[1], "active_days": r[2],
         "admin_secret": r[3], "slot_count": r[4], "created_at": r[5]}
        for r in events_rows
    ]

    if not events:
        return {
            "time_range": valid_range, "total_events": 0,
            "total_submissions": 0, "total_unique_players": 0,
            "total_alliances": 0, "total_kingdoms": 0,
            "total_assigned_slots": 0, "avg_submissions_per_event": 0.0,
            "events": [],
        }

    # Fetch all submissions
    if valid_range in time_filters:
        subs = db.execute(
            "SELECT s.event_uid, s.day_type, s.player_name, s.player_id, "
            "s.alliance_name, s.resources, s.status "
            "FROM submissions s JOIN events e ON s.event_uid = e.uid "
            "WHERE e.created_at >= datetime('now', ?)",
            (time_filters[valid_range],)
        ).fetchall()
        assigns = db.execute(
            "SELECT a.event_uid FROM assignments a JOIN events e ON a.event_uid = e.uid "
            "WHERE e.created_at >= datetime('now', ?) AND a.player_id IS NOT NULL",
            (time_filters[valid_range],)
        ).fetchall()
    else:
        subs = db.execute(
            "SELECT event_uid, day_type, player_name, player_id, "
            "alliance_name, resources, status FROM submissions"
        ).fetchall()
        assigns = db.execute(
            "SELECT event_uid FROM assignments WHERE player_id IS NOT NULL"
        ).fetchall()

    unique_players = {r[3] for r in subs}
    unique_alliances = {r[4] for r in subs if r[4]}
    # kingdoms: parse from raw_data not available here, use empty set
    total_subs = len(subs)
    total_assigned = len(assigns)

    # Per-event stats
    event_stats = {}
    for ev in events:
        event_stats[ev["uid"]] = {
            **ev,
            "submission_count": 0,
            "assigned_count": 0,
            "total_resources": 0.0,
        }
    for s in subs:
        uid = s[0]
        if uid in event_stats:
            event_stats[uid]["submission_count"] += 1
            event_stats[uid]["total_resources"] += s[5] or 0
    for a in assigns:
        uid = a[0]
        if uid in event_stats:
            event_stats[uid]["assigned_count"] += 1

    events_list = sorted(event_stats.values(), key=lambda x: x["created_at"], reverse=True)

    return {
        "time_range": valid_range,
        "total_events": len(events),
        "total_submissions": total_subs,
        "total_unique_players": len(unique_players),
        "total_alliances": len(unique_alliances),
        "total_assigned_slots": total_assigned,
        "avg_submissions_per_event": round(total_subs / len(events), 1) if events else 0.0,
        "events": events_list,
    }
