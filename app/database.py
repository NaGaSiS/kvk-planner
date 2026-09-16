import os
import sqlite3

from flask import g

DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/planner.db")


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE_PATH, timeout=30.0)
        # Enable WAL mode for significantly better concurrency
        db.execute("PRAGMA journal_mode=WAL")
        # Enforce foreign key constraints
        db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    db = get_db()
    cursor = db.cursor()

    # 1. Ensure 'events' table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            active_days TEXT NOT NULL,
            admin_secret TEXT NOT NULL,
            slot_count INTEGER DEFAULT 49,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Check if 'slot_count' column exists in existing database
    cursor.execute("PRAGMA table_info(events)")
    columns = [column[1] for column in cursor.fetchall()]
    if "slot_count" not in columns:
        try:
            cursor.execute(
                "ALTER TABLE events ADD COLUMN slot_count INTEGER DEFAULT 49"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise

    # 2. Ensure 'submissions' table exists and has the correct schema
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id TEXT PRIMARY KEY,
            event_uid TEXT NOT NULL,
            day_type TEXT NOT NULL,
            player_name TEXT NOT NULL,
            player_id TEXT NOT NULL,
            avatar_url TEXT,
            backpack_url TEXT,
            accelerators_url TEXT,
            city_level INTEGER,
            alliance_name TEXT,
            resources REAL NOT NULL,
            raw_data TEXT NOT NULL,
            feasible_slots TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'Pending',
            FOREIGN KEY (event_uid) REFERENCES events (uid)
        )
    """)

    # Table exists (just created or already there), check if columns exist
    cursor.execute("PRAGMA table_info(submissions)")
    columns = [column[1] for column in cursor.fetchall()]
    if "avatar_url" not in columns:
        try:
            cursor.execute("ALTER TABLE submissions ADD COLUMN avatar_url TEXT")
        except sqlite3.OperationalError as e:
            # If another worker added it at the same time, ignore the error
            if "duplicate column name" not in str(e):
                raise

    if "backpack_url" not in columns:
        try:
            cursor.execute("ALTER TABLE submissions ADD COLUMN backpack_url TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise

    if "accelerators_url" not in columns:
        try:
            cursor.execute("ALTER TABLE submissions ADD COLUMN accelerators_url TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise

    if "city_level" not in columns:
        try:
            cursor.execute("ALTER TABLE submissions ADD COLUMN city_level INTEGER")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise


    # 3. Ensure 'assignments' table exists and has the correct schema
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assignments (
            event_uid TEXT NOT NULL,
            day_type TEXT NOT NULL,
            slot_index INTEGER NOT NULL,
            player_id TEXT,
            is_locked BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (event_uid, day_type, slot_index),
            FOREIGN KEY (event_uid) REFERENCES events (uid)
        )
    """)

    # Table exists, check if 'day_type' column exists AND is part of the primary key
    cursor.execute("PRAGMA table_info(assignments)")
    columns_info = cursor.fetchall()
    columns = [c[1] for c in columns_info]
    pk_columns = [c[1] for c in columns_info if c[5] > 0]

    # We need day_type to be in the columns AND in the primary key
    if "day_type" not in columns or "day_type" not in pk_columns:
        print("DEBUG: Migrating assignments table to new primary key...")
        try:
            # 0. Drop any old indexes that might be enforcing the old unique constraint
            cursor.execute("DROP INDEX IF EXISTS idx_assignments_unique")

            # 1. Rename existing table
            cursor.execute("ALTER TABLE assignments RENAME TO assignments_old")
            print("DEBUG: Renamed assignments to assignments_old")

            # 2. Create new table with correct PK
            cursor.execute("""
                CREATE TABLE assignments (
                    event_uid TEXT NOT NULL,
                    day_type TEXT NOT NULL,
                    slot_index INTEGER NOT NULL,
                    player_id TEXT,
                    is_locked BOOLEAN DEFAULT FALSE,
                    PRIMARY KEY (event_uid, day_type, slot_index),
                    FOREIGN KEY (event_uid) REFERENCES events (uid)
                )
            """)
            print("DEBUG: Created new assignments table with composite primary key")

            # 3. Copy data
            if "day_type" in columns:
                cursor.execute(
                    "INSERT INTO assignments (event_uid, day_type, slot_index, player_id, is_locked) SELECT event_uid, day_type, slot_index, player_id, is_locked FROM assignments_old"
                )
            else:
                # Legacy data is assumed to be construction
                cursor.execute(
                    "INSERT INTO assignments (event_uid, day_type, slot_index, player_id, is_locked) SELECT event_uid, 'construction', slot_index, player_id, is_locked FROM assignments_old"
                )
            print("DEBUG: Copied data from assignments_old to assignments")

            # 4. Drop old table
            cursor.execute("DROP TABLE assignments_old")
            print("DEBUG: Dropped assignments_old table")
        except sqlite3.OperationalError as e:
            print(f"DEBUG: Migration error: {e}")
            # Handle concurrency (another worker might be doing this)
            if "already exists" in str(e) or "duplicate column name" in str(e):
                # Check if the new table is actually correct now
                cursor.execute("PRAGMA table_info(assignments)")
                new_cols = cursor.fetchall()
                if any(c[1] == "day_type" and c[5] > 0 for c in new_cols):
                    print("DEBUG: Migration already completed by another worker.")
                else:
                    raise
            elif "no such table" in str(e) and "assignments_old" in str(e):
                print(
                    "DEBUG: assignments_old already dropped, migration likely finished."
                )
            else:
                raise

    db.commit()


def init_app(app):
    @app.teardown_appcontext
    def close_connection(exception):
        db = getattr(g, "_database", None)
        if db is not None:
            db.close()

    with app.app_context():
        # Ensure the data directory exists
        data_dir = os.path.dirname(DATABASE_PATH)
        os.makedirs(data_dir, exist_ok=True)
        init_db()
