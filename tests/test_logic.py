import json

from app import database, logic


def test_algorithm_prioritization(app):
    with app.app_context():
        db = database.get_db()
        event_uid = "test-event"
        active_days = json.dumps({"construction": True, "training": True, "research": True})
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            (event_uid, "Test Event", active_days, "secret"),
        )

        # Player 1 (Higher Score)
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "sub1",
                event_uid,
                "construction",
                "High Score",
                "player1",
                "ALL1",
                10000,
                "{}",
                "[5, 6]",
            ),
        )

        # Player 2 (Lower Score)
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "sub2",
                event_uid,
                "construction",
                "Low Score",
                "player2",
                "ALL1",
                5000,
                "{}",
                "[5, 6]",
            ),
        )

        db.commit()

        logic.run_distribution_algorithm(event_uid)

        # Verify Player 1 got slot 5 (their first choice) and Player 2 got slot 6 (their second choice)
        res1 = db.execute("SELECT slot_index FROM assignments WHERE player_id = 'player1' AND day_type = 'construction'").fetchone()
        res2 = db.execute("SELECT slot_index FROM assignments WHERE player_id = 'player2' AND day_type = 'construction'").fetchone()

        assert res1[0] == 5
        assert res2[0] == 6


def test_algorithm_lock_protection(app):
    with app.app_context():
        db = database.get_db()
        event_uid = "lock-test"
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            (event_uid, "Lock Test", json.dumps({"construction": True}), "secret"),
        )

        # Player 1: Has a locked assignment in slot 5
        db.execute(
            "INSERT INTO assignments (event_uid, day_type, slot_index, player_id, is_locked) VALUES (?, ?, ?, ?, ?)",
            (event_uid, "construction", 5, "player1", 1),
        )

        # Player 2: Very high score, wants slot 5
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "sub2",
                event_uid,
                "construction",
                "High Score",
                "player2",
                "ALL1",
                99999,
                "{}",
                "[5, 6]",
            ),
        )

        db.commit()

        logic.run_distribution_algorithm(event_uid)

        # Player 2 should be bumped to slot 6 because slot 5 is locked
        res = db.execute("SELECT slot_index FROM assignments WHERE player_id = 'player2'").fetchone()
        assert res[0] == 6


def test_algorithm_waitlist(app):
    with app.app_context():
        db = database.get_db()
        event_uid = "waitlist-test"
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            (event_uid, "Waitlist Test", json.dumps({"construction": True}), "secret"),
        )

        # Player 1 takes slot 5
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "sub1",
                event_uid,
                "construction",
                "P1",
                "player1",
                "ALL1",
                1000,
                "{}",
                "[5]",
            ),
        )

        # Player 2 ONLY wants slot 5
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "sub2",
                event_uid,
                "construction",
                "P2",
                "player2",
                "ALL1",
                500,
                "{}",
                "[5]",
            ),
        )

        db.commit()

        logic.run_distribution_algorithm(event_uid)

        # Player 2 should be waitlisted
        res = db.execute("SELECT status FROM submissions WHERE id = 'sub2'").fetchone()
        assert res[0] == "Waitlisted"


def test_algorithm_non_existent_event(app):
    with app.app_context():
        # Running algorithm on a non-existent UID should simply return (no crash)
        logic.run_distribution_algorithm("none")


def test_algorithm_empty_slots(app):
    with app.app_context():
        db = database.get_db()
        event_uid = "empty-slots-test"
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            (event_uid, "Empty Test", json.dumps({"construction": True}), "secret"),
        )

        # Player with empty slots
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "sub1",
                event_uid,
                "construction",
                "P1",
                "player1",
                "ALL1",
                1000,
                "{}",
                "[]",
            ),
        )

        db.commit()
        logic.run_distribution_algorithm(event_uid)

        # Should not be in assignments
        a = db.execute("SELECT * FROM assignments WHERE player_id = 'player1'").fetchone()
        assert a is None


def test_algorithm_smart_spread(app):
    with app.app_context():
        db = database.get_db()
        event_uid = "smart-spread"
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            (event_uid, "Smart Spread", json.dumps({"construction": True}), "secret"),
        )

        # Player A: Score 100, Slots [1, 2]
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "subA",
                event_uid,
                "construction",
                "Player A",
                "playerA",
                "ALL1",
                100,
                "{}",
                "[1, 2]",
            ),
        )

        # Player B: Score 50, Slots [1]
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "subB",
                event_uid,
                "construction",
                "Player B",
                "playerB",
                "ALL1",
                50,
                "{}",
                "[1]",
            ),
        )

        db.commit()

        logic.run_distribution_algorithm(event_uid)

        # Player A should have picked Slot 2 because Slot 1 has higher demand (2 players requested it).
        # This leaves Slot 1 for Player B.
        resA = db.execute("SELECT slot_index FROM assignments WHERE player_id = 'playerA'").fetchone()
        resB = db.execute("SELECT slot_index FROM assignments WHERE player_id = 'playerB'").fetchone()

        assert resA[0] == 2
        assert resB[0] == 1


def test_algorithm_no_double_assign(app):
    with app.app_context():
        db = database.get_db()
        event_uid = "double-assign-test"
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            (event_uid, "Double Test", json.dumps({"construction": True}), "secret"),
        )

        # Player 1 has a LOCKED assignment in slot 10
        db.execute(
            "INSERT INTO assignments (event_uid, day_type, slot_index, player_id, is_locked) VALUES (?, ?, ?, ?, ?)",
            (event_uid, "construction", 10, "player1", 1),
        )

        # Player 1 ALSO has a submission wanting slot 11
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "sub1",
                event_uid,
                "construction",
                "P1",
                "player1",
                "ALL1",
                1000,
                "{}",
                "[11]",
            ),
        )

        db.commit()
        logic.run_distribution_algorithm(event_uid)

        # Player 1 should STILL only have ONE assignment (the locked one)
        count = db.execute(
            "SELECT COUNT(*) FROM assignments WHERE event_uid = ? AND player_id = 'player1'",
            (event_uid,),
        ).fetchone()[0]
        assert count == 1

        # The submission should be Locked (since they have a locked assignment)
        status = db.execute("SELECT status FROM submissions WHERE id = 'sub1'").fetchone()[0]
        assert status == "Locked"


def test_algorithm_bad_json(app):
    with app.app_context():
        db = database.get_db()
        event_uid = "bad-json-test"
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            (event_uid, "Bad JSON", json.dumps({"construction": True}), "secret"),
        )

        # Submission with invalid JSON in feasible_slots
        db.execute(
            "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
            ("sub1", event_uid, "construction", "P1", "p1", "A", 100, "{}", "not-json"),
        )

        db.commit()
        logic.run_distribution_algorithm(event_uid)

        # Should be waitlisted
        status = db.execute("SELECT status FROM submissions WHERE id = 'sub1'").fetchone()[0]
        assert status == "Waitlisted"


def test_format_minutes():
    from app.logic import format_minutes

    assert format_minutes(0) == "0m"
    assert format_minutes(30) == "30m"
    assert format_minutes(60) == "1h"
    assert format_minutes(90) == "1h 30m"
    assert format_minutes(1440) == "1d"
    assert format_minutes(1530) == "1d 1h 30m"


def test_distribution_algorithm_both_slot_lengths(app):
    with app.app_context():
        db = database.get_db()

        for sc in [48, 49]:
            event_uid = f"event-test-{sc}"
            db.execute(
                "INSERT INTO events (uid, name, active_days, admin_secret, slot_count) VALUES (?, ?, ?, ?, ?)",
                (
                    event_uid,
                    f"Test {sc}",
                    json.dumps({"construction": True}),
                    "secret",
                    sc,
                ),
            )
            # Player 1 wants the first slot (0)
            db.execute(
                "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"sub1-{sc}",
                    event_uid,
                    "construction",
                    "Player 1",
                    f"p1-{sc}",
                    "Alliance",
                    100.0,
                    "{}",
                    json.dumps([0]),
                ),
            )
            # Player 2 wants the 49th slot (index 48) - which is only valid if sc == 49
            db.execute(
                "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, alliance_name, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"sub2-{sc}",
                    event_uid,
                    "construction",
                    "Player 2",
                    f"p2-{sc}",
                    "Alliance",
                    90.0,
                    "{}",
                    json.dumps([48]),
                ),
            )
            # Run algorithm
            logic.run_distribution_algorithm(event_uid)

            # Check assignment Player 1
            assignment1 = db.execute(
                "SELECT slot_index FROM assignments WHERE event_uid = ? AND player_id = ?",
                (event_uid, f"p1-{sc}"),
            ).fetchone()
            assert assignment1 is not None
            assert assignment1["slot_index"] == 0

            # Check assignment Player 2
            assignment2 = db.execute(
                "SELECT slot_index FROM assignments WHERE event_uid = ? AND player_id = ?",
                (event_uid, f"p2-{sc}"),
            ).fetchone()

            sub2_status = db.execute(
                "SELECT status FROM submissions WHERE id = ?",
                (f"sub2-{sc}",),
            ).fetchone()["status"]

            if sc == 48:
                assert assignment2 is None
                assert sub2_status == "Waitlisted"
            else:
                assert assignment2 is not None
                assert assignment2["slot_index"] == 48
                assert sub2_status == "Confirmed"
