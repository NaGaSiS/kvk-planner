import io
import os
import sqlite3
from unittest.mock import patch

from app import database


def test_index_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Planner" in response.data


def test_guide_route(client):
    # 1. Success
    response = client.get("/guide")
    assert response.status_code == 200
    assert b"User Guide" in response.data or b"Tutorial" in response.data

    # 2. Not Found (Missing README.md)
    if os.path.exists("README.md"):
        os.rename("README.md", "README.md.tmp")
        try:
            response = client.get("/guide")
            assert response.status_code == 404
        finally:
            os.rename("README.md.tmp", "README.md")


def test_favicon(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"


def test_create_event(client, app):
    response = client.post(
        "/create", data={"event_name": "Test KvK"}, follow_redirects=True
    )
    assert response.status_code == 200

    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE name = 'Test KvK'").fetchone()
        assert event is not None


def test_player_form_page(client, app):
    client.post("/create", data={"event_name": "Form Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid FROM events").fetchone()
        event_uid = event["uid"]

    # Test success
    resp = client.get(f"/event/{event_uid}")
    assert resp.status_code == 200

    # Test 404
    assert client.get("/event/nonexistent").status_code == 404


def test_proxy_player_deleted(client):
    response = client.post("/api/proxy/player", json={"fid": "123"})
    assert response.status_code == 404


def test_refresh_players_deleted(client, app):
    with app.app_context():
        db = database.get_db()
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            ("ref123", "Refresh Test", '{"construction":true}', "secret"),
        )
        db.commit()

    response = client.post("/admin/ref123/refresh_players", data={"secret": "secret"})
    assert response.status_code == 404


def test_submit_valid(client, app):
    with app.app_context():
        db = database.get_db()
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            ("sub123", "Submit Test", '{"construction":true}', "secret"),
        )
        db.commit()

    response = client.post(
        "/event/sub123/submit",
        data={
            "player_id": "123456",
            "player_name": "TestPlayer",
            "alliance_name": "TEST",
            "speedups-construction": "60",
            "slots-construction": "[0]",
        },
    )
    assert response.status_code == 302
    assert "success=1" in response.headers["Location"]

    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT player_name, avatar_url FROM submissions WHERE event_uid = 'sub123' AND player_id = '123456'"
        ).fetchone()
        assert row["player_name"] == "TestPlayer"
        assert row["avatar_url"] is None


def test_submit_invalid_player_id(client, app):
    with app.app_context():
        db = database.get_db()
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            ("sub456", "Submit Test", '{"construction":true}', "secret"),
        )
        db.commit()

    response = client.post(
        "/event/sub456/submit",
        data={
            "player_id": "invalid_abc",
            "player_name": "TestPlayer",
            "alliance_name": "TEST",
        },
    )
    assert response.status_code == 400
    assert b"Must be numeric" in response.data


def test_submit_missing_player_name(client, app):
    with app.app_context():
        db = database.get_db()
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            ("sub789", "Submit Test", '{"construction":true}', "secret"),
        )
        db.commit()

    response = client.post(
        "/event/sub789/submit",
        data={
            "player_id": "123456",
            "player_name": "  ",
            "alliance_name": "TEST",
        },
    )
    assert response.status_code == 400
    assert b"Cannot be empty" in response.data


def test_full_flow(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Flow"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # 2. Submit for all days
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "12345",
            "avatar_url": "a.jpg",
            "alliance_name": "A",
            "speedups-construction": "10",
            "truegold": "1",
            "slots-construction": "[0]",
            "speedups-training": "10",
            "slots-training": "[1]",
            "speedups-research": "10",
            "truegold_dust": "1",
            "slots-research": "[2]",
        },
    )

    # 3. Distribution
    client.post(f"/admin/{uid}/distribute", data={"secret": secret})

    # 4. Admin Dashboard (Check if assignments render with names)
    resp = client.get(f"/admin/{uid}?secret={secret}")
    assert resp.status_code == 200
    assert b"P1" in resp.data

    # 5. Manual Assign (Locks by default)
    submission_id = f"{uid}_12345_construction"
    client.post(
        f"/admin/{uid}/manual_assign",
        data={"secret": secret, "submission_id": submission_id, "slot_index": "5"},
    )

    # 6. Confirm (Keep it locked)
    client.post(
        f"/admin/{uid}/confirm",
        data={"secret": secret, "day_type": "construction", "slot_index": "5"},
    )

    # 7. Public Pages (Now with locked assignments)
    assert client.get(f"/event/{uid}/schedule").status_code == 200
    resp = client.get(f"/event/{uid}/finalized")
    assert resp.status_code == 200
    assert b"CONFIRMED" in resp.data
    assert b"P1" in resp.data  # Should be visible now

    # 8. Unlock
    client.post(
        f"/admin/{uid}/unlock",
        data={"secret": secret, "day_type": "construction", "slot_index": "5"},
    )

    # 9. Delete
    client.post(
        f"/admin/{uid}/delete",
        data={"secret": secret, "submission_id": f"{uid}_12345_construction"},
    )


def test_finalized_schedule_states(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "States Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # 2. Submit for construction and training
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[0]",
            "speedups-training": "10",
            "slots-training": "[0]",
        },
    )

    # 3. Distribution (results in TENTATIVE status)
    client.post(f"/admin/{uid}/distribute", data={"secret": secret})

    # 4. Lock only construction
    client.post(
        f"/admin/{uid}/confirm",
        data={"secret": secret, "day_type": "construction", "slot_index": "0"},
    )

    # 5. Check Finalized Schedule
    resp = client.get(f"/event/{uid}/finalized")
    assert resp.status_code == 200
    assert b"CONFIRMED" in resp.data
    assert b"TENTATIVE" in resp.data


def test_export_csv(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Export Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # 2. Submit, assign and lock
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[10]",
        },
    )
    client.post(
        f"/admin/{uid}/manual_assign",
        data={
            "secret": secret,
            "submission_id": f"{uid}_123_construction",
            "slot_index": "10",
        },
    )
    # manual_assign locks by default in this app's logic

    # 3. Export
    resp = client.get(f"/admin/{uid}/export/construction?secret={secret}")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert b"Event Type,Player ID,Player Name,Appointment Slot" in resp.data
    assert b"construction,123,P1" in resp.data

    # 4. Unauthorized
    assert client.get(f"/admin/{uid}/export/construction?secret=bad").status_code == 403


def test_submit_no_slots(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "No Slots Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid = event["uid"]

    # 2. Submit with resources but NO slots
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[]",
        },
    )

    # 3. Verify NOT in DB
    with app.app_context():
        db = database.get_db()
        count = db.execute(
            "SELECT count(*) FROM submissions WHERE player_id = '123'"
        ).fetchone()[0]
        assert count == 0


def test_submit_invalid_id(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "ID Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid FROM events").fetchone()
        uid = event["uid"]

    # 2. Non-numeric ID
    resp = client.post(
        f"/event/{uid}/submit",
        data={
            "player_id": "abc",
            "player_name": "P1",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[0]",
        },
    )
    assert resp.status_code == 400
    assert b"numeric" in resp.data

    # 3. Missing name
    resp = client.post(
        f"/event/{uid}/submit",
        data={
            "player_id": "123",
            "player_name": "",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[0]",
        },
    )
    assert resp.status_code == 400
    assert b"Cannot be empty" in resp.data


def test_submit_with_backpack(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Backpack Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, _secret = event["uid"], event["admin_secret"]

    # 2. Submit with file (Mock ENABLE_SCREENSHOT_UPLOAD as True)
    data = {
        "player_name": "P1",
        "player_id": "123",
        "alliance_name": "A",
        "speedups-construction": "10",
        "slots-construction": "[0]",
        "backpack_screenshot": (io.BytesIO(b"dummy image data"), "test.jpg"),
    }

    with patch("config.Config.ENABLE_SCREENSHOT_UPLOAD", True):
        resp = client.post(
            f"/event/{uid}/submit",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    assert resp.status_code == 200

    # 3. Verify in DB
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        sub = db.execute(
            "SELECT backpack_url FROM submissions WHERE player_id = '123'"
        ).fetchone()
        assert sub["backpack_url"] is not None
        assert "/static/uploads/" in sub["backpack_url"]

        # Verify file exists on disk
        filename = sub["backpack_url"].split("/")[-1]
        filepath = os.path.join(app.static_folder, "uploads", filename)
        assert os.path.exists(filepath)

        # Cleanup
        os.remove(filepath)


def test_submit_with_backpack_disabled(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Backpack Disabled Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, _secret = event["uid"], event["admin_secret"]

    # 2. Submit with file (Mock ENABLE_SCREENSHOT_UPLOAD as False)
    data = {
        "player_name": "P1",
        "player_id": "12345",
        "alliance_name": "A",
        "speedups-construction": "10",
        "slots-construction": "[0]",
        "backpack_screenshot": (io.BytesIO(b"dummy image data"), "test.jpg"),
    }

    with patch("config.Config.ENABLE_SCREENSHOT_UPLOAD", False):
        resp = client.post(
            f"/event/{uid}/submit",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
    assert resp.status_code == 200

    # 3. Verify NOT in DB (backpack_url should be None)
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        sub = db.execute(
            "SELECT backpack_url FROM submissions WHERE player_id = '12345'"
        ).fetchone()
        assert sub["backpack_url"] is None


def test_resource_breakdown_text(client, app):
    client.post("/create", data={"event_name": "Breakdown Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # Submit for all days with different resources
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-construction": "100",
            "truegold": "5",
            "slots-construction": "[0]",
            "speedups-training": "200",
            "slots-training": "[0]",
            "speedups-research": "300",
            "truegold_dust": "10",
            "slots-research": "[0]",
        },
    )

    resp = client.get(f"/admin/{uid}?secret={secret}")
    assert resp.status_code == 200

    # Check breakdown texts in the response (they are in 'title' attributes)
    assert b"Speedups: 1h 40m | Truegold: 5" in resp.data
    assert b"Speedups: 3h 20m" in resp.data
    assert b"Speedups: 5h" in resp.data


def test_heatmap_hover_potential_players(client, app):
    client.post("/create", data={"event_name": "Hover Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # Submit for construction with slot 10
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "HoverPlayer",
            "player_id": "11111",
            "alliance_name": "HOV",
            "speedups-construction": "100",
            "slots-construction": "[10]",
        },
    )

    resp = client.get(f"/admin/{uid}?secret={secret}")
    assert resp.status_code == 200

    # Check if the player is listed in the title attribute of the slot with points
    assert b'title="Potential Players: [HOV] HoverPlayer (3.000 pts)"' in resp.data

    # Check if submission_id is in the data-slot-players attribute
    expected_id = f"{uid}_11111_construction"
    assert f"submission_id&#34;: &#34;{expected_id}&#34;".encode() in resp.data


def test_overwrite_clears_assignments(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Overwrite Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # 2. Submit for construction and training
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[0]",
            "speedups-training": "10",
            "slots-training": "[0]",
        },
    )

    # 3. Assign them
    client.post(
        f"/admin/{uid}/manual_assign",
        data={
            "secret": secret,
            "submission_id": f"{uid}_123_construction",
            "slot_index": "0",
        },
    )
    client.post(
        f"/admin/{uid}/manual_assign",
        data={
            "secret": secret,
            "submission_id": f"{uid}_123_training",
            "slot_index": "0",
        },
    )

    # 4. Verify assigned for both
    with app.app_context():
        db = database.get_db()
        count = db.execute(
            "SELECT count(*) FROM assignments WHERE event_uid = ? AND player_id = '123'",
            (uid,),
        ).fetchone()[0]
        assert count == 2

    # 5. Overwrite with ONLY training
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-training": "10",
            "slots-training": "[0]",
        },
    )

    # 6. Verify BOTH assignments are gone (since we clear all on resubmit)
    with app.app_context():
        db = database.get_db()
        count = db.execute(
            "SELECT count(*) FROM assignments WHERE event_uid = ? AND player_id = '123'",
            (uid,),
        ).fetchone()[0]
        assert count == 0


def test_tab_specific_distribute(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Tab Dist Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # Submit for construction and training in ONE call
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "12345",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[0]",
            "speedups-training": "10",
            "slots-training": "[0]",
        },
    )

    # 2. Run distribute only for construction
    client.post(
        f"/admin/{uid}/distribute", data={"secret": secret, "day_type": "construction"}
    )

    # 3. Verify construction is assigned, training is not
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row

        # Construction should be assigned
        cons_ass = db.execute(
            "SELECT * FROM assignments WHERE event_uid = ? AND day_type = ?",
            (uid, "construction"),
        ).fetchone()
        assert cons_ass is not None

        # Training should NOT be assigned
        train_ass = db.execute(
            "SELECT * FROM assignments WHERE event_uid = ? AND day_type = ?",
            (uid, "training"),
        ).fetchone()
        assert train_ass is None

        # Construction status should be Confirmed
        cons_sub = db.execute(
            "SELECT status FROM submissions WHERE event_uid = ? AND day_type = ?",
            (uid, "construction"),
        ).fetchone()
        assert cons_sub["status"] == "Confirmed"

        # Training status should still be Pending
        train_sub = db.execute(
            "SELECT status FROM submissions WHERE event_uid = ? AND day_type = ?",
            (uid, "training"),
        ).fetchone()
        assert train_sub["status"] == "Pending"


def test_unset_assignment(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Unset Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "12345",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[0]",
        },
    )
    submission_id = f"{uid}_12345_construction"

    # 2. Manually assign
    client.post(
        f"/admin/{uid}/manual_assign",
        data={"secret": secret, "submission_id": submission_id, "slot_index": "10"},
    )

    # 3. Verify assigned
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        count = db.execute(
            "SELECT count(*) FROM assignments WHERE event_uid = ? AND player_id = '12345'",
            (uid,),
        ).fetchone()[0]
        assert count == 1

    # 4. Unset
    resp = client.post(
        f"/admin/{uid}/unset",
        data={"secret": secret, "submission_id": submission_id},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # 5. Verify unassigned and status reset
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        count = db.execute(
            "SELECT count(*) FROM assignments WHERE event_uid = ? AND player_id = '12345'",
            (uid,),
        ).fetchone()[0]
        assert count == 0

        sub = db.execute(
            "SELECT status FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        assert sub["status"] == "Pending"


def test_update_alliance(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Alliance Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "12345",
            "alliance_name": "Old",
            "speedups-construction": "10",
            "slots-construction": "[0]",
        },
    )
    submission_id = f"{uid}_12345_construction"

    # 2. Success
    resp = client.post(
        f"/admin/{uid}/update_alliance",
        data={"secret": secret, "submission_id": submission_id, "alliance_name": "New"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        sub = db.execute(
            "SELECT alliance_name FROM submissions WHERE id = ?", (submission_id,)
        ).fetchone()
        assert sub["alliance_name"] == "New"


def test_error_routes(client, app):
    # 404s
    assert client.get("/event/none").status_code == 404
    assert client.get("/event/none/schedule").status_code == 404
    assert client.get("/event/none/finalized").status_code == 404
    assert client.get("/admin/none?secret=any").status_code == 404

    # POST routes 404s
    assert (
        client.post("/admin/none/manual_assign", data={"secret": "any"}).status_code
        == 404
    )
    assert (
        client.post("/admin/none/distribute", data={"secret": "any"}).status_code == 404
    )
    assert client.post("/admin/none/confirm", data={"secret": "any"}).status_code == 404
    assert client.post("/admin/none/unlock", data={"secret": "any"}).status_code == 404
    assert client.post("/admin/none/delete", data={"secret": "any"}).status_code == 404
    assert (
        client.post("/admin/none/refresh_players", data={"secret": "any"}).status_code
        == 404
    )
    assert client.post("/admin/none/unset", data={"secret": "any"}).status_code == 404
    assert (
        client.post("/admin/none/update_alliance", data={"secret": "any"}).status_code
        == 404
    )
    assert client.get("/admin/none/export/construction?secret=any").status_code == 404

    # 403s
    client.post("/create", data={"event_name": "E"})
    with app.app_context():
        db = database.get_db()
        uid = db.execute("SELECT uid FROM events").fetchone()[0]
        secret = db.execute("SELECT admin_secret FROM events").fetchone()[0]

    assert client.get(f"/admin/{uid}?secret=bad").status_code == 403
    assert (
        client.post(f"/admin/{uid}/distribute", data={"secret": "bad"}).status_code
        == 403
    )
    assert (
        client.post(f"/admin/{uid}/manual_assign", data={"secret": "bad"}).status_code
        == 403
    )
    assert (
        client.post(f"/admin/{uid}/confirm", data={"secret": "bad"}).status_code == 403
    )
    assert (
        client.post(f"/admin/{uid}/unlock", data={"secret": "bad"}).status_code == 403
    )
    assert (
        client.post(f"/admin/{uid}/delete", data={"secret": "bad"}).status_code == 403
    )
    assert client.post(f"/admin/{uid}/unset", data={"secret": "bad"}).status_code == 403
    assert (
        client.post(f"/admin/{uid}/update_alliance", data={"secret": "bad"}).status_code
        == 403
    )
    assert client.get(f"/admin/{uid}/export/construction?secret=bad").status_code == 403
    assert client.get(f"/admin/{uid}/logs?secret=bad").status_code == 403
    assert (
        client.post(
            f"/admin/{uid}/manual_assign", data={"secret": secret, "slot_index": ""}
        ).status_code
        == 302
    )


def test_view_logs(client, app):
    # 1. Setup
    client.post("/create", data={"event_name": "Log Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # 2. Trigger some logs
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[0]",
        },
    )

    # 3. View Logs (Success)
    resp = client.get(f"/admin/{uid}/logs?secret={secret}")
    assert resp.status_code == 200
    assert b"SUBMISSION" in resp.data
    assert b"123" in resp.data

    # 4. Not Found (Invalid event)
    assert client.get("/admin/none/logs?secret=any").status_code == 404


def test_view_logs_missing_file(client, app):
    client.post("/create", data={"event_name": "No Log"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    with patch("os.path.exists", return_value=False):
        resp = client.get(f"/admin/{uid}/logs?secret={secret}")
        assert resp.status_code == 404
        assert b"Log file not found" in resp.data


def test_submit_invalid_extension(client, app):
    client.post("/create", data={"event_name": "Ext Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid FROM events").fetchone()
        uid = event["uid"]

    data = {
        "player_name": "P1",
        "player_id": "123",
        "alliance_name": "A",
        "speedups-construction": "10",
        "slots-construction": "[0]",
        "backpack_screenshot": (io.BytesIO(b"data"), "test.exe"),
    }

    with patch("config.Config.ENABLE_SCREENSHOT_UPLOAD", True):
        resp = client.post(
            f"/event/{uid}/submit", data=data, content_type="multipart/form-data"
        )
        assert resp.status_code == 400
        assert b"Invalid file type" in resp.data


def test_json_and_orphans(client, app):
    client.post("/create", data={"event_name": "Orphan"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

        # Empty slots
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            ("empty", uid, "construction", "P", "1", "A", 0, "{}", ""),
        )
        # Bad JSON slots
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            ("bad_slots", uid, "training", "P", "1", "A", 0, "{}", "not-json"),
        )
        # Bad JSON raw_data
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            ("bad_raw", uid, "research", "P", "1", "A", 0, "not-json", "[]"),
        )

        # Orphaned assignment (submission missing)
        db.execute(
            "INSERT INTO assignments (event_uid, day_type, slot_index, player_id, is_locked) VALUES (?, ?, ?, ?, ?)",
            (uid, "construction", 15, "nobody", 1),
        )
        db.commit()

    resp = client.get(f"/admin/{uid}?secret={secret}")
    assert resp.status_code == 200
    assert b"Error parsing resources" in resp.data
    assert client.get(f"/event/{uid}/finalized").status_code == 200


def test_submit_creates_dir(client, app):
    client.post("/create", data={"event_name": "Dir Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid FROM events").fetchone()
        uid = event["uid"]

    data = {
        "player_name": "P1",
        "player_id": "123",
        "alliance_name": "A",
        "speedups-construction": "10",
        "slots-construction": "[0]",
        "backpack_screenshot": (io.BytesIO(b"data"), "test.jpg"),
    }

    with (
        patch("config.Config.ENABLE_SCREENSHOT_UPLOAD", True),
        patch("os.path.exists", return_value=False),
        patch("os.makedirs") as mock_makedirs,
    ):
        client.post(
            f"/event/{uid}/submit",
            data=data,
            content_type="multipart/form-data",
        )
        mock_makedirs.assert_called()


def test_override_resources(client, app):
    # Setup event
    client.post("/create", data={"event_name": "Override Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        uid, secret = event["uid"], event["admin_secret"]

    # Submit a construction submission
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-construction": "10",
            "slots-construction": "[0]",
        },
    )

    sub_id = f"{uid}_123_construction"

    # 1. Unauthorized override (wrong secret)
    resp = client.post(
        f"/admin/{uid}/override_resources",
        data={
            "secret": "wrong",
            "submission_id": sub_id,
            "speedups": "20",
            "truegold": "2",
            "tempered_truegold": "1",
        },
    )
    assert resp.status_code == 403

    # 2. Non-existent event override
    resp = client.post(
        "/admin/nonexistent/override_resources",
        data={
            "secret": secret,
            "submission_id": sub_id,
            "speedups": "20",
            "truegold": "2",
            "tempered_truegold": "1",
        },
    )
    assert resp.status_code == 404

    # 3. Successful Construction override
    resp = client.post(
        f"/admin/{uid}/override_resources",
        data={
            "secret": secret,
            "submission_id": sub_id,
            "speedups": "20",
            "truegold": "2",
            "tempered_truegold": "1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # Verify score & raw_data recalculation
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        sub = db.execute("SELECT * FROM submissions WHERE id = ?", (sub_id,)).fetchone()
        assert sub is not None
        # Construction formula: (speedups * 30) + (truegold * 2000) + (tempered_truegold * 30000)
        # 20 * 30 + 2 * 2000 + 1 * 30000 = 600 + 4000 + 30000 = 34600
        assert sub["resources"] == 34600
        import json

        raw = json.loads(sub["raw_data"])
        assert raw["speedups"] == 20
        assert raw["truegold"] == 2
        assert raw["tempered_truegold"] == 1

    # 4. Successful Training submission & override
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-training": "15",
            "slots-training": "[1]",
        },
    )
    training_sub_id = f"{uid}_123_training"
    resp = client.post(
        f"/admin/{uid}/override_resources",
        data={
            "secret": secret,
            "submission_id": training_sub_id,
            "speedups": "30",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        sub = db.execute(
            "SELECT * FROM submissions WHERE id = ?", (training_sub_id,)
        ).fetchone()
        assert sub is not None
        # Training formula: speedups * 90 = 30 * 90 = 2700
        assert sub["resources"] == 2700
        raw = json.loads(sub["raw_data"])
        assert raw["speedups"] == 30

    # 5. Successful Research submission & override
    client.post(
        f"/event/{uid}/submit",
        data={
            "player_name": "P1",
            "player_id": "123",
            "alliance_name": "A",
            "speedups-research": "5",
            "slots-research": "[2]",
        },
    )
    research_sub_id = f"{uid}_123_research"
    resp = client.post(
        f"/admin/{uid}/override_resources",
        data={
            "secret": secret,
            "submission_id": research_sub_id,
            "speedups": "10",
            "truegold_dust": "3",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        sub = db.execute(
            "SELECT * FROM submissions WHERE id = ?", (research_sub_id,)
        ).fetchone()
        assert sub is not None
        # Research formula: (speedups * 30) + (truegold_dust * 1000) = 10 * 30 + 3 * 1000 = 3300
        assert sub["resources"] == 3300
        raw = json.loads(sub["raw_data"])
        assert raw["speedups"] == 10
        assert raw["truegold_dust"] == 3

    # 6. Invalid values input handling
    resp = client.post(
        f"/admin/{uid}/override_resources",
        data={
            "secret": secret,
            "submission_id": research_sub_id,
            "speedups": "not-a-number",
            "truegold_dust": "3",
        },
    )
    assert resp.status_code == 400


def test_create_event_with_slot_count(client, app):
    for sc in [48, 49]:
        resp = client.post(
            "/create",
            data={
                "event_name": f"Event {sc}",
                "research_day": "5",
                "slot_count": str(sc),
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Check that events database has the correct slot_count
        with app.app_context():
            db = database.get_db()
            db.row_factory = sqlite3.Row
            event = db.execute(
                "SELECT slot_count FROM events WHERE name = ?", (f"Event {sc}",)
            ).fetchone()
            assert event is not None
            assert event["slot_count"] == sc


def test_null_slot_count_handling(client, app):
    # Create an event
    resp = client.post(
        "/create",
        data={
            "event_name": "Null Slot Count Event",
            "research_day": "5",
            "slot_count": "48",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # Get event UID and manually set slot_count to NULL in DB
    with app.app_context():
        db = database.get_db()
        row = db.execute(
            "SELECT uid FROM events WHERE name = ?", ("Null Slot Count Event",)
        ).fetchone()
        assert row is not None
        uid = row[0]

        db.execute("UPDATE events SET slot_count = NULL WHERE uid = ?", (uid,))
        db.commit()

    # Accessing the event form should invoke the context processor,
    # which should safely fallback to defaulting slot_count to 49 when it finds NULL (None).
    resp = client.get(f"/event/{uid}")
    assert resp.status_code == 200


def test_manual_assign_bounds_validation(client, app):
    # Create an event with 48 slots
    resp = client.post(
        "/create",
        data={
            "event_name": "Test Bounds Event",
            "research_day": "5",
            "slot_count": "48",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT uid, admin_secret FROM events WHERE name = ?",
            ("Test Bounds Event",),
        ).fetchone()
        assert row is not None
        uid = row["uid"]
        secret = row["admin_secret"]

        # Insert a submission to target for manual assignment
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sub_12345_construction",
                uid,
                "construction",
                "P1",
                "12345",
                "ALL",
                10.0,
                "{}",
                "[0]",
            ),
        )
        db.commit()

    # Try manual assignment with valid slot (0)
    resp = client.post(
        f"/admin/{uid}/manual_assign",
        data={
            "secret": secret,
            "submission_id": "sub_12345_construction",
            "slot_index": "0",
        },
    )
    assert resp.status_code == 302  # redirects on success

    # Try manual assignment with out-of-bounds slot (48) for 48-slot event
    resp = client.post(
        f"/admin/{uid}/manual_assign",
        data={
            "secret": secret,
            "submission_id": "sub_12345_construction",
            "slot_index": "48",
        },
    )
    assert resp.status_code == 400
    assert b"Invalid slot index range" in resp.data

    # Try manual assignment with negative out-of-bounds slot (-1) for 48-slot event
    resp = client.post(
        f"/admin/{uid}/manual_assign",
        data={
            "secret": secret,
            "submission_id": "sub_12345_construction",
            "slot_index": "-1",
        },
    )
    assert resp.status_code == 400
    assert b"Invalid slot index range" in resp.data

    # Try manual assignment with invalid format (abc)
    resp = client.post(
        f"/admin/{uid}/manual_assign",
        data={
            "secret": secret,
            "submission_id": "sub_12345_construction",
            "slot_index": "abc",
        },
    )
    assert resp.status_code == 400
    assert b"Invalid slot index format" in resp.data

    # Try manual assignment with malformed submission_id
    resp = client.post(
        f"/admin/{uid}/manual_assign",
        data={
            "secret": secret,
            "submission_id": "malformed_id",
            "slot_index": "0",
        },
    )
    assert resp.status_code == 400
    assert b"Invalid submission_id format" in resp.data

    # Create a 49-slot event and test boundaries
    resp = client.post(
        "/create",
        data={
            "event_name": "Test Bounds Event 49",
            "research_day": "5",
            "slot_count": "49",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT uid, admin_secret FROM events WHERE name = ?",
            ("Test Bounds Event 49",),
        ).fetchone()
        assert row is not None
        uid_49 = row["uid"]
        secret_49 = row["admin_secret"]

        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sub49_12345_construction",
                uid_49,
                "construction",
                "P1",
                "12345",
                "ALL",
                10.0,
                "{}",
                "[0]",
            ),
        )
        db.commit()

    # Try manual assignment with valid slot 48 for 49-slot event
    resp = client.post(
        f"/admin/{uid_49}/manual_assign",
        data={
            "secret": secret_49,
            "submission_id": "sub49_12345_construction",
            "slot_index": "48",
        },
    )
    assert resp.status_code == 302  # redirects on success

    # Try manual assignment with out-of-bounds slot 49 for 49-slot event
    resp = client.post(
        f"/admin/{uid_49}/manual_assign",
        data={
            "secret": secret_49,
            "submission_id": "sub49_12345_construction",
            "slot_index": "49",
        },
    )
    assert resp.status_code == 400
    assert b"Invalid slot index range" in resp.data

    # Try manual assignment with non-existent submission_id (but correct format)
    resp = client.post(
        f"/admin/{uid_49}/manual_assign",
        data={
            "secret": secret_49,
            "submission_id": "sub_99999_construction",
            "slot_index": "0",
        },
    )
    assert resp.status_code == 404
    assert b"Submission not found" in resp.data

    # Test override revert of status
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        # Insert a second player submission
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sub49_67890_construction",
                uid_49,
                "construction",
                "P2",
                "67890",
                "ALL",
                12.0,
                "{}",
                "[0]",
                "Pending",
            ),
        )
        db.commit()

    # Verify initial statuses: sub_12345 (from previous steps) is Locked, sub_67890 is Pending
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        s1 = db.execute(
            "SELECT status FROM submissions WHERE id = ?",
            ("sub49_12345_construction",),
        ).fetchone()
        s2 = db.execute(
            "SELECT status FROM submissions WHERE id = ?",
            ("sub49_67890_construction",),
        ).fetchone()
        assert s1["status"] == "Locked"
        assert s2["status"] == "Pending"

    # Overwrite slot 48 with sub_67890 (overrides sub_12345)
    resp = client.post(
        f"/admin/{uid_49}/manual_assign",
        data={
            "secret": secret_49,
            "submission_id": "sub49_67890_construction",
            "slot_index": "48",
        },
    )
    assert resp.status_code == 302

    # Verify that sub_12345's status reverted to Pending and sub_67890 is Locked
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        s1 = db.execute(
            "SELECT status FROM submissions WHERE id = ?",
            ("sub49_12345_construction",),
        ).fetchone()
        s2 = db.execute(
            "SELECT status FROM submissions WHERE id = ?",
            ("sub49_67890_construction",),
        ).fetchone()
        assert s1["status"] == "Pending"
        assert s2["status"] == "Locked"


def test_export_submissions_route(client, app):
    import json

    # Setup: Create an event and add some submissions
    client.post("/create", data={"event_name": "Export Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        event_uid = event["uid"]
        secret = event["admin_secret"]

        # Insert a submission
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, resources, raw_data, feasible_slots, status) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"{event_uid}_p1_construction",
                event_uid,
                "construction",
                "Player One",
                "p1",
                100.0,
                '{"speedups": 10}',
                '["0", "1"]',
                "Pending",
            ),
        )
        db.commit()

    # Test wrong secret
    resp = client.get(f"/admin/{event_uid}/export_submissions?secret=wrong")
    assert resp.status_code == 403

    # Test event not found
    resp = client.get(f"/admin/nonexistent/export_submissions?secret={secret}")
    assert resp.status_code == 404

    # Test successful export
    resp = client.get(f"/admin/{event_uid}/export_submissions?secret={secret}")
    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    assert "attachment" in resp.headers.get("Content-Disposition", "")

    data = json.loads(resp.data)
    assert len(data) == 1
    assert data[0]["player_name"] == "Player One"
    assert data[0]["day_type"] == "construction"
    assert data[0]["player_id"] == "p1"


def test_import_submissions_route(client, app):
    import json

    # Setup: Create an event
    client.post("/create", data={"event_name": "Import Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        event_uid = event["uid"]
        secret = event["admin_secret"]

    # Test wrong secret
    resp = client.post(
        f"/admin/{event_uid}/import_submissions",
        data={"secret": "wrong", "submissions_file": (io.BytesIO(b"[]"), "subs.json")},
        follow_redirects=True,
    )
    assert resp.status_code == 403

    # Test invalid JSON format
    resp = client.post(
        f"/admin/{event_uid}/import_submissions",
        data={
            "secret": secret,
            "submissions_file": (io.BytesIO(b"invalid-json"), "subs.json"),
        },
        follow_redirects=True,
    )
    assert b"Invalid file format" in resp.data

    # Test missing fields in JSON objects
    invalid_data = json.dumps([{"player_name": "Incomplete"}])
    resp = client.post(
        f"/admin/{event_uid}/import_submissions",
        data={
            "secret": secret,
            "submissions_file": (io.BytesIO(invalid_data.encode()), "subs.json"),
        },
        follow_redirects=True,
    )
    assert b"Missing required field" in resp.data

    # Test successful import (happy path)
    valid_data = json.dumps(
        [
            {
                "day_type": "construction",
                "player_name": "Imported Player",
                "player_id": "imported_p1",
                "avatar_url": "/static/uploads/avatar.png",
                "backpack_url": None,
                "alliance_name": "IMP",
                "resources": 200.0,
                "raw_data": '{"speedups": 20}',
                "feasible_slots": '["0", "2"]',
                "status": "Pending",
            }
        ]
    )

    resp = client.post(
        f"/admin/{event_uid}/import_submissions",
        data={
            "secret": secret,
            "submissions_file": (io.BytesIO(valid_data.encode()), "subs.json"),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Successfully imported 1 submissions for 1 players." in resp.data

    # Verify submission is in DB
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        sub = db.execute(
            "SELECT * FROM submissions WHERE player_id = 'imported_p1'"
        ).fetchone()
        assert sub is not None
        assert sub["player_name"] == "Imported Player"
        assert sub["event_uid"] == event_uid


def test_admin_dashboard_template_integration(client, app):
    # Setup: Create an event
    client.post("/create", data={"event_name": "Template Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        event_uid = event["uid"]
        secret = event["admin_secret"]

    # Fetch admin page (should not have export/import yet if failing, but let's assert to trigger RED step)
    resp = client.get(f"/admin/{event_uid}?secret={secret}")
    assert resp.status_code == 200
    assert b"Export Submissions" in resp.data
    assert b"Import Submissions" in resp.data
    assert b'name="submissions_file"' in resp.data

    # Perform an invalid import to trigger a flash message
    resp = client.post(
        f"/admin/{event_uid}/import_submissions",
        data={
            "secret": secret,
            "submissions_file": (io.BytesIO(b"invalid-json"), "subs.json"),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Invalid file format" in resp.data
    assert b"&times;" in resp.data
    assert b"this.parentElement.remove()" in resp.data


def test_import_submissions_edge_cases(client, app):
    import io
    import json

    # 1. Setup: Create an event
    client.post("/create", data={"event_name": "Edge Cases Test"})
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid, admin_secret FROM events").fetchone()
        event_uid = event["uid"]
        secret = event["admin_secret"]

        # Insert two submissions for the same player across different days
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"{event_uid}_p1_construction",
                event_uid,
                "construction",
                "Player One",
                "p1",
                "Alliance",
                10.0,
                "{}",
                "[]",
                "Pending",
            ),
        )
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                f"{event_uid}_p1_training",
                event_uid,
                "training",
                "Player One",
                "p1",
                "Alliance",
                20.0,
                "{}",
                "[]",
                "Pending",
            ),
        )
        # Add matching assignments for both
        db.execute(
            "INSERT INTO assignments (event_uid, day_type, slot_index, player_id, is_locked) VALUES (?, ?, ?, ?, ?)",
            (event_uid, "construction", 0, "p1", 0),
        )
        db.execute(
            "INSERT INTO assignments (event_uid, day_type, slot_index, player_id, is_locked) VALUES (?, ?, ?, ?, ?)",
            (event_uid, "training", 1, "p1", 0),
        )
        db.commit()

    # Now, import a file that only updates the "construction" day submission/assignment for player "p1"
    import_data = json.dumps(
        [
            {
                "day_type": "construction",
                "player_name": "Player One Updated",
                "player_id": "p1",
                "resources": "15.5",  # tests resources string parsing to float
                "raw_data": {"speedups": 5},
                "feasible_slots": [1, 2],
            }
        ]
    )

    resp = client.post(
        f"/admin/{event_uid}/import_submissions",
        data={
            "secret": secret,
            "submissions_file": (io.BytesIO(import_data.encode()), "subs.json"),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Successfully imported 1 submissions" in resp.data

    # Verify that ONLY the "construction" submission and assignment were modified/deleted and replaced
    # and the "training" submission and assignment were NOT deleted/lost!
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row
        # Check construction submission (should be updated)
        sub_const = db.execute(
            "SELECT * FROM submissions WHERE event_uid = ? AND player_id = 'p1' AND day_type = 'construction'",
            (event_uid,),
        ).fetchone()
        assert sub_const is not None
        assert sub_const["player_name"] == "Player One Updated"
        assert sub_const["resources"] == 15.5  # Verify converted to float
        assert json.loads(sub_const["feasible_slots"]) == [
            1,
            2,
        ]  # Verify list of integers
        assert json.loads(sub_const["raw_data"]) == {"speedups": 5}  # Verify dict

        # Check construction assignment (should have been deleted because of deletion logic, so it doesn't exist anymore)
        assign_const = db.execute(
            "SELECT * FROM assignments WHERE event_uid = ? AND player_id = 'p1' AND day_type = 'construction'",
            (event_uid,),
        ).fetchone()
        assert assign_const is None

        # Check training submission (should STILL exist - no data loss!)
        sub_train = db.execute(
            "SELECT * FROM submissions WHERE event_uid = ? AND player_id = 'p1' AND day_type = 'training'",
            (event_uid,),
        ).fetchone()
        assert sub_train is not None
        assert sub_train["player_name"] == "Player One"
        assert sub_train["resources"] == 20.0

        # Check training assignment (should STILL exist - no data loss!)
        assign_train = db.execute(
            "SELECT * FROM assignments WHERE event_uid = ? AND player_id = 'p1' AND day_type = 'training'",
            (event_uid,),
        ).fetchone()
        assert assign_train is not None

    # Test Non-numeric resources validation error
    bad_resources_data = json.dumps(
        [
            {
                "day_type": "construction",
                "player_name": "Player One",
                "player_id": "p1",
                "resources": "not-a-number",
                "raw_data": {},
                "feasible_slots": [1, 2],
            }
        ]
    )
    resp = client.post(
        f"/admin/{event_uid}/import_submissions",
        data={
            "secret": secret,
            "submissions_file": (io.BytesIO(bad_resources_data.encode()), "subs.json"),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Must be a number." in resp.data

    # Test Invalid feasible_slots validation error (e.g. not a list)
    bad_fs_data = json.dumps(
        [
            {
                "day_type": "construction",
                "player_name": "Player One",
                "player_id": "p1",
                "resources": 10.0,
                "raw_data": {},
                "feasible_slots": "not-a-list",
            }
        ]
    )
    resp = client.post(
        f"/admin/{event_uid}/import_submissions",
        data={
            "secret": secret,
            "submissions_file": (io.BytesIO(bad_fs_data.encode()), "subs.json"),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"feasible_slots must be a list" in resp.data

    # Test Invalid raw_data validation error (e.g. not a JSON object)
    bad_rd_data = json.dumps(
        [
            {
                "day_type": "construction",
                "player_name": "Player One",
                "player_id": "p1",
                "resources": 10.0,
                "raw_data": "not-an-object",
                "feasible_slots": [1, 2],
            }
        ]
    )
    resp = client.post(
        f"/admin/{event_uid}/import_submissions",
        data={
            "secret": secret,
            "submissions_file": (io.BytesIO(bad_rd_data.encode()), "subs.json"),
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"raw_data must be a JSON object" in resp.data
