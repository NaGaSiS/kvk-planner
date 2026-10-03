# Manual Player Info Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace automatic player lookup via external Century Games API with manual input of Player ID and Player Name, removing all obsolete lookup code, endpoints, and admin refresh features.

**Architecture:** Update Flask routes to receive `player_id` and `player_name` directly from standard HTML inputs, removing `fetch_player_info` and `/api/proxy/player` and `/admin/<event_uid>/refresh_players` routes. Update `player_form.html` to render two text fields, and remove the "Refresh Player Data" button from `admin_dashboard.html`.

**Tech Stack:** Python 3.12, Flask, Jinja2, Tailwind CSS, pytest, ruff.

## Global Constraints

- Python code must pass `./venv/bin/ruff check .` and `./venv/bin/ruff format --check .`.
- All automated tests must pass via `./venv/bin/pytest`.
- Maintain CSRF protection and form validation integrity across all endpoints.

---

### Task 1: Update Backend Routes and Remove Obsolete Lookup Code

**Files:**
- Modify: `app/__init__.py:59-225`
- Modify: `tests/test_routes.py`

**Interfaces:**
- Consumes: Form POST data with `player_id` and `player_name`
- Produces: Updated `/event/<event_uid>/submit` route, removed `/api/proxy/player` & `/admin/<event_uid>/refresh_players` routes

- [ ] **Step 1: Write failing/updated tests in `tests/test_routes.py`**

Replace obsolete tests (`test_proxy_player_*` and `test_refresh_players`) with updated tests for manual player submit and missing routes:

```python
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
    assert "/submission-success" in response.headers["Location"]

    with app.app_context():
        db = database.get_db()
        row = db.execute("SELECT player_name, avatar_url FROM submissions WHERE event_uid = 'sub123' AND player_id = '123456'").fetchone()
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
```

- [ ] **Step 2: Run pytest to verify tests fail on missing routes or assertions**

Run: `./venv/bin/pytest tests/test_routes.py`
Expected: Failures due to `/api/proxy/player` and `/admin/<event_uid>/refresh_players` currently returning 200/302 instead of 404.

- [ ] **Step 3: Remove lookup functions/routes and update submit logic in `app/__init__.py`**

1. Delete `fetch_player_info(fid)` function.
2. Delete `@app.route("/api/proxy/player")` route function.
3. Delete `@app.route("/admin/<event_uid>/refresh_players")` route function.
4. Update `@app.route("/event/<event_uid>/submit")`:
```python
@app.route("/event/<event_uid>/submit", methods=["POST"])
def submit(event_uid):
    db = database.get_db()
    player_id = request.form.get("player_id", "").strip()
    player_name = request.form.get("player_name", "").strip()
    alliance_name = request.form.get("alliance_name", "").strip()

    # Server-side validation
    if not player_id.isdigit():
        return "Invalid Player ID: Must be numeric", 400

    if not player_name:
        return "Invalid Player Name: Cannot be empty", 400

    app.audit_logger.info(f"SUBMISSION: Player {player_name} ({player_id}) submitted resources for event {event_uid}")
    ...
    avatar_url = request.form.get("avatar_url") or None
```

- [ ] **Step 4: Run pytest and ruff check to verify tests pass**

Run: `./venv/bin/pytest`
Run: `./venv/bin/ruff check .`
Expected: ALL tests pass cleanly, ruff checks pass.

- [ ] **Step 5: Commit**

```bash
git add app/__init__.py tests/test_routes.py
git commit -m "feat: remove player lookup API & refresh routes, update submit validation"
```

---

### Task 2: Update Templates (`player_form.html` and `admin_dashboard.html`)

**Files:**
- Modify: `app/templates/player_form.html`
- Modify: `app/templates/admin_dashboard.html`

**Interfaces:**
- Consumes: Updated submit route expectations
- Produces: Clean UI with manual Player ID and Player Name inputs; no obsolete avatar fetching or refresh buttons

- [ ] **Step 1: Update `app/templates/player_form.html`**

1. Replace the "Your Information" grid section (lines 73-101):
```html
            <div class="p-4 border-b border-kvk-gray-700">
                <h2 class="text-2xl font-bold text-gray-100 mb-4">Your Information</h2>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-6 items-end">
                    <div>
                        <label for="player_id" class="block text-sm font-medium text-gray-300 mb-1">Player ID (Numeric)</label>
                        <input type="text" name="player_id" id="player_id" required 
                               pattern="\d*" inputmode="numeric" placeholder="e.g. 12345678"
                               class="block w-full bg-kvk-gray-700 border-kvk-gray-700 rounded-md shadow-sm text-gray-200 focus:border-kvk-gold focus:ring focus:ring-kvk-gold focus:ring-opacity-50">
                    </div>
                    <div>
                        <label for="player_name" class="block text-sm font-medium text-gray-300 mb-1">Player Name</label>
                        <input type="text" name="player_name" id="player_name" required placeholder="e.g. KingArthur"
                               class="block w-full bg-kvk-gray-700 border-kvk-gray-700 rounded-md shadow-sm text-gray-200 focus:border-kvk-gold focus:ring focus:ring-kvk-gold focus:ring-opacity-50">
                    </div>
                    <div>
                        <label for="alliance_name" class="block text-sm font-medium text-gray-300 mb-1">Alliance</label>
                        <input type="text" name="alliance_name" id="alliance_name" placeholder="e.g. KVK" class="block w-full bg-kvk-gray-700 border-kvk-gray-700 rounded-md shadow-sm text-gray-200 focus:border-kvk-gold focus:ring focus:ring-kvk-gold focus:ring-opacity-50">
                    </div>
                </div>
                ...
```

2. Remove JS `idInput` blur fetch logic (lines 376-437) and update submit validation in JS:
```javascript
            // Form Validation
            const form = document.querySelector('form');
            form.addEventListener('submit', function(e) {
                // 1. Validate Player ID & Name
                const player_id = document.getElementById('player_id').value.trim();
                const player_name = document.getElementById('player_name').value.trim();

                if (!/^\d+$/.test(player_id)) {
                    e.preventDefault();
                    alert("Player ID must be numeric.");
                    return;
                }

                if (!player_name) {
                    e.preventDefault();
                    alert("Please enter a valid Player Name.");
                    return;
                }
```

- [ ] **Step 2: Update `app/templates/admin_dashboard.html`**

Remove the "Refresh Player Data" form (lines 92-96 in `app/templates/admin_dashboard.html`):
```html
                    <div class="flex flex-wrap gap-2 items-center">
                        <a href="{{ url_for('export_submissions', event_uid=event.uid, secret=secret) }}" 
...
```

- [ ] **Step 3: Run pytest and ruff check**

Run: `./venv/bin/pytest`
Run: `./venv/bin/ruff check .`
Expected: ALL tests pass cleanly, no syntax or lint errors.

- [ ] **Step 4: Commit**

```bash
git add app/templates/player_form.html app/templates/admin_dashboard.html
git commit -m "feat: update player submission form and admin dashboard templates"
```
