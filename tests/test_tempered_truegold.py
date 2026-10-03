import json
import sqlite3

from app import database


def test_tempered_truegold_submission(client, app):
    # 1. Setup: Create an event
    client.post("/create", data={"event_name": "Tempered Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # 2. Submit with Tempered TrueGold
    # Construction: 100 minutes speedups, 10 truegold, 5 tempered truegold
    # Expected score: (100 * 30) + (10 * 2000) + (5 * 30000)
    # 3000 + 20000 + 150000 = 173000

    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "TemperedPlayer",
            "player_id": "99999",
            "alliance_name": "T",
            "speedups-construction": "100",
            "truegold": "10",
            "tempered_truegold": "5",
            "slots-construction": "[0]",
        },
    )

    # 3. Verify in DB
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        sub = db.execute("SELECT resources, raw_data FROM submissions WHERE player_id = '99999'").fetchone()
        assert sub is not None
        assert sub["resources"] == 173000

        raw_data = json.loads(sub["raw_data"])
        assert raw_data["speedups"] == 100
        assert raw_data["truegold"] == 10
        assert raw_data["tempered_truegold"] == 5

    # 4. Verify in Admin Dashboard
    resp = client.get(f"/admin/{uid}?secret={secret}")
    assert resp.status_code == 200
    assert b"Tempered Gold: 5" in resp.data
    assert b"Truegold: 10" in resp.data
    assert b"Speedups: 1h 40m" in resp.data
