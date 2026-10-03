import json
import sqlite3

from app import database
from app.logic import format_minutes


def test_format_minutes():
    assert format_minutes(0) == "0m"
    assert format_minutes(1) == "1m"
    assert format_minutes(59) == "59m"
    assert format_minutes(60) == "1h"
    assert format_minutes(61) == "1h 1m"
    assert format_minutes(1440) == "1d"
    assert format_minutes(1441) == "1d 1m"
    assert format_minutes(1500) == "1d 1h"
    assert format_minutes(1501) == "1d 1h 1m"
    assert format_minutes(2880) == "2d"
    assert format_minutes(3000) == "2d 2h"
    assert format_minutes(3005) == "2d 2h 5m"
    assert format_minutes(10000) == "6d 22h 40m"


def test_speedup_submission_and_formatting(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Speedup Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # 2. Simulate submission from the form
    # The form calculates the total minutes.
    # Let's say: 1 day, 2 hours, 30 minutes = 1440 + 120 + 30 = 1590 minutes
    speedups_val = 1590

    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "12345",
            "alliance_name": "A",
            "speedups-construction": str(speedups_val),
            "truegold": "0",
            "slots-construction": "[0]",
        },
    )

    # 3. Verify in DB
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        sub = db.execute("SELECT resources, raw_data FROM submissions WHERE player_id = '12345'").fetchone()
        assert sub is not None
        # Score calculation for construction: speedups * 30 + truegold * 2000
        assert sub["resources"] == speedups_val * 30

        raw_data = json.loads(sub["raw_data"])
        assert raw_data["speedups"] == speedups_val

    # 4. Verify formatting in Admin Dashboard
    resp = client.get(f"/admin/{uid}?secret={secret}")
    assert resp.status_code == 200
    # Expected formatting: 1d 2h 30m
    assert b"Speedups: 1d 2h 30m" in resp.data


def test_speedup_multiple_units_submission(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Multi Unit Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # 2. Submit with various values
    # Construction: 10 days = 14400 minutes
    # Training: 5 hours = 300 minutes
    # Research: 45 minutes

    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P2",
            "player_id": "67890",
            "alliance_name": "B",
            "speedups-construction": "14400",
            "slots-construction": "[0]",
            "speedups-training": "300",
            "slots-training": "[0]",
            "speedups-research": "45",
            "slots-research": "[0]",
        },
    )

    resp = client.get(f"/admin/{uid}?secret={secret}")
    assert resp.status_code == 200

    # Verify formatting
    assert b"Speedups: 10d" in resp.data
    assert b"Speedups: 5h" in resp.data
    assert b"Speedups: 45m" in resp.data
