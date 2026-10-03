import os
import shutil
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g

from app import database, generate_slot_labels


def test_generate_slot_labels():
    # Test 49 slots (Legacy format starting at 23:45)
    labels_49 = generate_slot_labels(49)
    assert len(labels_49) == 49
    assert labels_49[0] == "23:45-\u200b00:15"
    assert labels_49[1] == "00:15-\u200b00:45"
    assert labels_49[48].endswith("23:45") or "23:45" in labels_49[48]

    # Test 48 slots (Standard format starting at 00:00)
    labels_48 = generate_slot_labels(48)
    assert len(labels_48) == 48
    assert labels_48[0] == "00:00-\u200b00:30"
    assert labels_48[1] == "00:30-\u200b01:00"
    assert labels_48[47].endswith("00:00") or "00:00" in labels_48[47]


def test_init_db_creates_directory():
    # Create a path in a new temp directory
    tmpdir = tempfile.mkdtemp()
    new_dir = os.path.join(tmpdir, "subdir")
    db_path = os.path.join(new_dir, "test.db")

    # Ensure subdir doesn't exist
    assert not os.path.exists(new_dir)

    app = Flask(__name__)
    database.DATABASE_PATH = db_path
    database.init_app(app)

    # Verify subdir was created
    assert os.path.exists(new_dir)
    assert os.path.exists(db_path)

    # Cleanup
    shutil.rmtree(tmpdir)


def test_create_app_creates_log_dir():
    # Use a temp directory for the root
    with tempfile.TemporaryDirectory() as tmpdir:
        # The logic we want to test:
        # log_dir = os.path.join(app.root_path, "..", "logs")
        # if not os.path.exists(log_dir): os.makedirs(log_dir)

        mock_app = MagicMock()
        mock_app.root_path = os.path.join(tmpdir, "app")
        os.makedirs(mock_app.root_path)

        expected_log_dir = os.path.join(tmpdir, "logs")
        assert not os.path.exists(expected_log_dir)

        # We can't easily call create_app() due to its global nature and side effects,
        # but we can test the specific logic by calling a helper or mocking what it uses.
        # Actually, let's just mock os.makedirs and os.path.exists within the context of create_app
        with (
            patch("os.path.exists") as mock_exists,
            patch("os.makedirs") as mock_mkdir,
        ):
            # Setup exists to return False for the logs dir
            def exists_side_effect(path):
                return "logs" not in path

            mock_exists.side_effect = exists_side_effect

            from app import create_app

            with patch("app.database.init_app"):
                create_app()

            # Check if it tried to create a logs directory
            made_logs = any("logs" in str(args[0]) for args, kwargs in mock_mkdir.call_args_list)
            assert made_logs


def test_database_migrations():
    # Create a database with an OLD schema
    db_fd, db_path = tempfile.mkstemp()
    try:
        conn = sqlite3.connect(db_path)
        # Create tables missing the new columns
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, uid TEXT UNIQUE, name TEXT, active_days TEXT, admin_secret TEXT)")
        conn.execute(
            "CREATE TABLE submissions (id TEXT PRIMARY KEY, event_uid TEXT, day_type TEXT, player_name TEXT, player_id TEXT, alliance_name TEXT, resources REAL, raw_data TEXT, feasible_slots TEXT)"
        )
        conn.execute(
            "CREATE TABLE assignments (event_uid TEXT, slot_index INTEGER, player_id TEXT, is_locked BOOLEAN, PRIMARY KEY (event_uid, slot_index))"
        )
        conn.commit()
        conn.close()

        # Run init_db with this existing database
        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()

            # Verify columns AND Primary Key structure
            db = database.get_db()

            # Check assignments table info
            cursor = db.execute("PRAGMA table_info(assignments)")
            info = cursor.fetchall()
            cols = [c[1] for c in info]
            pk_cols = [c[1] for c in info if c[5] > 0]

            assert "day_type" in cols
            assert "event_uid" in pk_cols
            assert "day_type" in pk_cols
            assert "slot_index" in pk_cols
            assert len(pk_cols) == 3

            # Check submissions table info
            cursor = db.execute("PRAGMA table_info(submissions)")
            cols = [c[1] for c in cursor.fetchall()]
            assert "avatar_url" in cols
            assert "backpack_url" in cols

            # Create an event first to satisfy the FOREIGN KEY constraint now that PRAGMA foreign_keys = ON is enforced
            db.execute("INSERT INTO events (uid, name, active_days, admin_secret) VALUES ('e1', 'Test Event', '[\"construction\"]', 'secret')")

            # FUNCTIONAL TEST: Try to insert same slot for different days
            db.execute("INSERT INTO assignments (event_uid, day_type, slot_index, player_id) VALUES ('e1', 'construction', 10, 'p1')")
            db.execute("INSERT INTO assignments (event_uid, day_type, slot_index, player_id) VALUES ('e1', 'training', 10, 'p2')")
            db.commit()

            # Verify both exist
            count = db.execute("SELECT COUNT(*) FROM assignments WHERE event_uid='e1' AND slot_index=10").fetchone()[0]
            assert count == 2

            # Test calling it again should be fine (idempotency)
            database.init_db()
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_migration_with_column_present():
    # Create a database where day_type is present as a column but NOT in PK
    db_fd, db_path = tempfile.mkstemp()
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, uid TEXT UNIQUE, name TEXT, active_days TEXT, admin_secret TEXT)")
        conn.execute(
            "CREATE TABLE submissions (id TEXT PRIMARY KEY, event_uid TEXT, day_type TEXT, player_name TEXT, player_id TEXT, alliance_name TEXT, resources REAL, raw_data TEXT, feasible_slots TEXT, avatar_url TEXT)"
        )
        # day_type is here, but PK is only (event_uid, slot_index)
        conn.execute(
            "CREATE TABLE assignments (event_uid TEXT, day_type TEXT, slot_index INTEGER, player_id TEXT, is_locked BOOLEAN, PRIMARY KEY (event_uid, slot_index))"
        )
        conn.commit()
        conn.close()

        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()  # Should trigger migration and use Line 105

            db = database.get_db()
            cursor = db.execute("PRAGMA table_info(assignments)")
            pk_cols = [c[1] for c in cursor.fetchall() if c[5] > 0]
            assert "day_type" in pk_cols
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_init_submissions_race():
    db_fd, db_path = tempfile.mkstemp()
    try:
        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()
            if hasattr(g, "_database"):
                del g._database
            with patch("app.database.get_db") as mock_get_db:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_get_db.return_value = mock_conn
                mock_conn.cursor.return_value = mock_cursor
                mock_cursor.fetchone.return_value = ("exists",)

                def fetchall_side_effect():
                    sql = str(mock_cursor.execute.call_args_list[-1]).upper()
                    if "SUBMISSIONS" in sql:
                        return [(0, "id", "TEXT", 1, None, 1)]
                    return [
                        (0, "id", "T", 1, None, 1),
                        (1, "day_type", "T", 1, None, 2),
                        (2, "slot_index", "T", 1, None, 3),
                    ]

                mock_cursor.fetchall.side_effect = fetchall_side_effect

                def side_effect(sql, *args):
                    if "ALTER TABLE SUBMISSIONS" in sql.upper():
                        raise sqlite3.OperationalError("duplicate column name")
                    return MagicMock()

                mock_cursor.execute.side_effect = side_effect

                database.init_db()
                assert mock_cursor.execute.called
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_init_backpack_race():
    db_fd, db_path = tempfile.mkstemp()
    try:
        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()
            if hasattr(g, "_database"):
                del g._database
            with patch("app.database.get_db") as mock_get_db:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_get_db.return_value = mock_conn
                mock_conn.cursor.return_value = mock_cursor
                mock_cursor.fetchone.return_value = ("exists",)

                def fetchall_side_effect():
                    sql = str(mock_cursor.execute.call_args_list[-1]).upper()
                    if "SUBMISSIONS" in sql:
                        # Missing backpack_url
                        return [
                            (0, "id", "T", 1, None, 1),
                            (1, "avatar_url", "T", 0, None, 0),
                        ]
                    return [
                        (0, "id", "T", 1, None, 1),
                        (1, "day_type", "T", 1, None, 2),
                        (2, "slot_index", "T", 1, None, 3),
                    ]

                mock_cursor.fetchall.side_effect = fetchall_side_effect

                def side_effect(sql, *args):
                    if "ALTER TABLE SUBMISSIONS" in sql.upper() and "BACKPACK_URL" in sql.upper():
                        raise sqlite3.OperationalError("duplicate column name")
                    return MagicMock()

                mock_cursor.execute.side_effect = side_effect

                database.init_db()
                assert mock_cursor.execute.called
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_init_backpack_error():
    db_fd, db_path = tempfile.mkstemp()
    try:
        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()
            if hasattr(g, "_database"):
                del g._database
            with patch("app.database.get_db") as mock_get_db:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_get_db.return_value = mock_conn
                mock_conn.cursor.return_value = mock_cursor
                mock_cursor.fetchone.return_value = ("exists",)

                def fetchall_side_effect():
                    sql = str(mock_cursor.execute.call_args_list[-1]).upper()
                    if "SUBMISSIONS" in sql:
                        # Missing backpack_url
                        return [
                            (0, "id", "T", 1, None, 1),
                            (1, "avatar_url", "T", 0, None, 0),
                        ]
                    return [
                        (0, "id", "T", 1, None, 1),
                        (1, "day_type", "T", 1, None, 2),
                        (2, "slot_index", "T", 1, None, 3),
                    ]

                mock_cursor.fetchall.side_effect = fetchall_side_effect

                def side_effect(sql, *args):
                    if "ALTER TABLE SUBMISSIONS" in sql.upper() and "BACKPACK_URL" in sql.upper():
                        raise sqlite3.OperationalError("other error")
                    return MagicMock()

                mock_cursor.execute.side_effect = side_effect

                with pytest.raises(sqlite3.OperationalError):
                    database.init_db()
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_migration_failed_verification():
    db_fd, db_path = tempfile.mkstemp()
    try:
        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()
            if hasattr(g, "_database"):
                del g._database
            with patch("app.database.get_db") as mock_get_db:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_get_db.return_value = mock_conn
                mock_conn.cursor.return_value = mock_cursor
                mock_cursor.fetchone.return_value = ("exists",)

                def fetchall_side_effect():
                    sql = str(mock_cursor.execute.call_args_list[-1]).upper()
                    if "SUBMISSIONS" in sql:
                        return [
                            (0, "id", "T", 1, None, 1),
                            (1, "avatar_url", "T", 0, None, 0),
                        ]
                    # Return schema containing day_type but NOT in PK
                    return [
                        (0, "event_uid", "T", 1, None, 1),
                        (1, "day_type", "T", 1, None, 0),
                        (2, "slot_index", "T", 1, None, 0),
                    ]

                mock_cursor.fetchall.side_effect = fetchall_side_effect

                def side_effect(sql, *args):
                    if "RENAME" in sql.upper():
                        raise sqlite3.OperationalError("already exists")
                    return MagicMock()

                mock_cursor.execute.side_effect = side_effect

                with pytest.raises(sqlite3.OperationalError):
                    database.init_db()
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_migration_worker_halfway():
    db_fd, db_path = tempfile.mkstemp()
    try:
        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()
            if hasattr(g, "_database"):
                del g._database
            with patch("app.database.get_db") as mock_get_db:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_get_db.return_value = mock_conn
                mock_conn.cursor.return_value = mock_cursor
                mock_cursor.fetchone.return_value = ("exists",)

                def fetchall_side_effect():
                    sql = str(mock_cursor.execute.call_args_list[-1]).upper()
                    if "SUBMISSIONS" in sql:
                        return [
                            (0, "id", "T", 1, None, 1),
                            (1, "avatar_url", "T", 0, None, 0),
                        ]
                    # assignments initial: incomplete, verification: complete
                    if mock_cursor.fetchall.call_count <= 2:  # 1 sub, 1 ass initial
                        return [(0, "id", "T", 1, None, 1)]
                    return [
                        (0, "id", "T", 1, None, 1),
                        (1, "day_type", "T", 1, None, 2),
                        (2, "slot_index", "T", 1, None, 3),
                    ]

                mock_cursor.fetchall.side_effect = fetchall_side_effect

                def side_effect(sql, *args):
                    if "RENAME" in sql.upper():
                        raise sqlite3.OperationalError("already exists")
                    return MagicMock()

                mock_cursor.execute.side_effect = side_effect

                database.init_db()
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_migration_already_dropped():
    db_fd, db_path = tempfile.mkstemp()
    try:
        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()
            if hasattr(g, "_database"):
                del g._database
            with patch("app.database.get_db") as mock_get_db:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_get_db.return_value = mock_conn
                mock_conn.cursor.return_value = mock_cursor
                mock_cursor.fetchone.return_value = ("exists",)

                def fetchall_side_effect():
                    if "SUBMISSIONS" in str(mock_cursor.execute.call_args_list[-1]).upper():
                        return [
                            (0, "id", "T", 1, None, 1),
                            (1, "avatar_url", "T", 0, None, 0),
                        ]
                    return [(0, "id", "T", 1, None, 1)]

                mock_cursor.fetchall.side_effect = fetchall_side_effect

                def side_effect(sql, *args):
                    if "RENAME" in sql.upper():
                        raise sqlite3.OperationalError("no such table: assignments_old")
                    return MagicMock()

                mock_cursor.execute.side_effect = side_effect

                database.init_db()
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_concurrency_error_re_raised_submissions():
    db_fd, db_path = tempfile.mkstemp()
    try:
        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()
            if hasattr(g, "_database"):
                del g._database
            with patch("app.database.get_db") as mock_get_db:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_get_db.return_value = mock_conn
                mock_conn.cursor.return_value = mock_cursor
                mock_cursor.fetchone.return_value = ("exists",)

                def fetchall_side_effect():
                    sql = str(mock_cursor.execute.call_args_list[-1]).upper()
                    if "SUBMISSIONS" in sql:
                        return [(0, "id", "T", 1, None, 1)]  # Incomplete
                    return [
                        (0, "id", "T", 1, None, 1),
                        (1, "day_type", "T", 1, None, 2),
                        (2, "slot_index", "T", 1, None, 3),
                    ]  # Complete

                mock_cursor.fetchall.side_effect = fetchall_side_effect

                def side_effect(sql, *args):
                    if "ALTER TABLE SUBMISSIONS" in sql.upper():
                        raise sqlite3.OperationalError("submissions error")
                    return MagicMock()

                mock_cursor.execute.side_effect = side_effect

                with pytest.raises(sqlite3.OperationalError):
                    database.init_db()
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_concurrency_error_re_raised_assignments():
    db_fd, db_path = tempfile.mkstemp()
    try:
        app = Flask(__name__)
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()
            if hasattr(g, "_database"):
                del g._database
            with patch("app.database.get_db") as mock_get_db:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_get_db.return_value = mock_conn
                mock_conn.cursor.return_value = mock_cursor
                mock_cursor.fetchone.return_value = ("exists",)

                def fetchall_side_effect():
                    sql = str(mock_cursor.execute.call_args_list[-1]).upper()
                    if "SUBMISSIONS" in sql:
                        return [
                            (0, "id", "T", 1, None, 1),
                            (1, "avatar_url", "T", 0, None, 0),
                        ]
                    return [(0, "id", "T", 1, None, 1)]

                mock_cursor.fetchall.side_effect = fetchall_side_effect

                def side_effect(sql, *args):
                    if "RENAME" in sql.upper():
                        raise sqlite3.OperationalError("other error")
                    return MagicMock()

                mock_cursor.execute.side_effect = side_effect

                with pytest.raises(sqlite3.OperationalError):
                    database.init_db()
    finally:
        os.close(db_fd)
        os.unlink(db_path)


def test_database_migration_slot_count(app):
    old_db_path = database.DATABASE_PATH
    db_fd, db_path = tempfile.mkstemp()
    try:
        conn = sqlite3.connect(db_path)
        # Create events table with legacy schema (missing slot_count)
        conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, uid TEXT UNIQUE, name TEXT, active_days TEXT, admin_secret TEXT)")
        conn.commit()
        conn.close()

        # Run init_db with this existing database
        database.DATABASE_PATH = db_path
        with app.app_context():
            database.init_db()

            # Verify slot_count column exists
            db = database.get_db()
            cursor = db.execute("PRAGMA table_info(events)")
            cols = [c[1] for c in cursor.fetchall()]
            assert "slot_count" in cols
    finally:
        database.DATABASE_PATH = old_db_path
        os.close(db_fd)
        os.unlink(db_path)


def test_event_slot_count_constraint_and_defaults(app):
    with app.app_context():
        db = database.get_db()
        db.row_factory = sqlite3.Row

        # 1. Verify default value is 49 when slot_count is not provided
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret) VALUES (?, ?, ?, ?)",
            ("event_default", "Default Event", "{}", "secret_default"),
        )
        db.commit()

        row = db.execute("SELECT slot_count FROM events WHERE uid = ?", ("event_default",)).fetchone()
        assert row is not None
        assert row["slot_count"] == 49

        # 2. Verify we can store and retrieve slot_count of 48
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret, slot_count) VALUES (?, ?, ?, ?, ?)",
            ("event_48", "Slot 48 Event", "{}", "secret_48", 48),
        )
        db.commit()

        row = db.execute("SELECT slot_count FROM events WHERE uid = ?", ("event_48",)).fetchone()
        assert row is not None
        assert row["slot_count"] == 48

        # 3. Verify we can store and retrieve slot_count of 49
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret, slot_count) VALUES (?, ?, ?, ?, ?)",
            ("event_49", "Slot 49 Event", "{}", "secret_49", 49),
        )
        db.commit()

        row = db.execute("SELECT slot_count FROM events WHERE uid = ?", ("event_49",)).fetchone()
        assert row is not None
        assert row["slot_count"] == 49

        # 4. Verify updating slot_count (from 49 to 48 and vice-versa) persists correctly
        # Update event_48 (48 -> 49)
        db.execute("UPDATE events SET slot_count = ? WHERE uid = ?", (49, "event_48"))
        db.commit()

        row = db.execute("SELECT slot_count FROM events WHERE uid = ?", ("event_48",)).fetchone()
        assert row is not None
        assert row["slot_count"] == 49

        # Update event_49 (49 -> 48)
        db.execute("UPDATE events SET slot_count = ? WHERE uid = ?", (48, "event_49"))
        db.commit()

        row = db.execute("SELECT slot_count FROM events WHERE uid = ?", ("event_49",)).fetchone()
        assert row is not None
        assert row["slot_count"] == 48
