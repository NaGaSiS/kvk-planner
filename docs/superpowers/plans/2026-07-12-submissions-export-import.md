# Submissions Export and Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build export and import functionalities for player submissions, accessible directly from the Global Actions card on the Admin Dashboard page.

**Architecture:** Add two REST endpoints in Flask (`export_submissions` GET route and `import_submissions` POST route), modify Flask imports to support `flash` messages, and update the Jinja2 admin template to render alert banners and integration buttons (using standard styling and an auto-submitting file input).

**Tech Stack:** Python 3.12, Flask, SQLite (Direct Driver), HTML, Jinja2, Tailwind CSS.

## Global Constraints

- Python code must pass `./venv/bin/ruff check .` and `./venv/bin/ruff format --check .`.
- All database modifications must be run inside database transactions and use parameterized queries.
- CSRF protection must be maintained on POST/PUT requests using `csrf_token` form variables.
- Maintain documentation integrity: do not delete unrelated comments or code.
- Clicking file links should use `file://` scheme.

---

### Task 1: Submissions Export Backend Route

**Files:**
- Modify: `app/__init__.py:15-30` (Import `flash` from `flask`)
- Modify: `app/__init__.py:890-910` (Add route inside `create_app` after `export_csv`)
- Modify: `tests/test_routes.py:1413` (Append tests at the end)

**Interfaces:**
- Produces: `/admin/<event_uid>/export_submissions` GET route returning a JSON file attachment.

- [ ] **Step 1: Write the failing test**
  Add the following test at the end of `tests/test_routes.py`:
  ```python
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
      assert b"attachment" in resp.headers.get("Content-Disposition", b"")

      data = json.loads(resp.data)
      assert len(data) == 1
      assert data[0]["player_name"] == "Player One"
      assert data[0]["day_type"] == "construction"
      assert data[0]["player_id"] == "p1"
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `./venv/bin/pytest tests/test_routes.py::test_export_submissions_route -v`
  Expected: FAIL with "404 Not Found" (or route not registered)

- [ ] **Step 3: Modify Flask imports and implement the route**
  First, edit the top of `app/__init__.py` to import `flash` from `flask`:
  ```python
  from flask import (
      Flask,
      render_template,
      request,
      redirect,
      url_for,
      jsonify,
      Response,
      send_from_directory,
      flash,
  )
  ```
  Next, add the route inside `create_app()` after the `export_csv` route:
  ```python
  @app.route("/admin/<event_uid>/export_submissions", methods=["GET"])
  def export_submissions(event_uid):
      secret = request.args.get("secret")
      db = database.get_db()
      db.row_factory = sqlite3.Row
      event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
      if event is None:
          return "Event not found", 404
      if event["admin_secret"] != secret:
          return "Forbidden", 403

      submissions = db.execute(
          """
          SELECT day_type, player_name, player_id, avatar_url, backpack_url, 
                 alliance_name, resources, raw_data, feasible_slots, status 
          FROM submissions 
          WHERE event_uid = ?
          """,
          (event_uid,),
      ).fetchall()

      # Build array of dictionaries
      sub_list = []
      for s in submissions:
          sub_list.append(
              {
                  "day_type": s["day_type"],
                  "player_name": s["player_name"],
                  "player_id": s["player_id"],
                  "avatar_url": s["avatar_url"],
                  "backpack_url": s["backpack_url"],
                  "alliance_name": s["alliance_name"],
                  "resources": s["resources"],
                  "raw_data": s["raw_data"],
                  "feasible_slots": s["feasible_slots"],
                  "status": s["status"],
              }
          )

      import datetime

      timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
      filename = f"submissions_{event_uid}_{timestamp}.json"

      return Response(
          json.dumps(sub_list, indent=2),
          mimetype="application/json",
          headers={"Content-Disposition": f"attachment; filename={filename}"},
      )
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `./venv/bin/pytest tests/test_routes.py::test_export_submissions_route -v`
  Expected: PASS

- [ ] **Step 5: Run full tests and linting check**
  Run: `./venv/bin/pytest && ./venv/bin/ruff check .`
  Expected: PASS

- [ ] **Step 6: Commit**
  Run:
  ```bash
  git add app/__init__.py tests/test_routes.py
  git commit -m "feat: implement submissions export backend route and tests"
  ```

---

### Task 2: Submissions Import Backend Route

**Files:**
- Modify: `app/__init__.py` (Add route inside `create_app` after `export_submissions`)
- Modify: `tests/test_routes.py:1413` (Append tests at the end)

**Interfaces:**
- Produces: `/admin/<event_uid>/import_submissions` POST route processing file uploads.

- [ ] **Step 1: Write the failing tests**
  Add the following test at the end of `tests/test_routes.py`:
  ```python
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
          sub = db.execute("SELECT * FROM submissions WHERE player_id = 'imported_p1'").fetchone()
          assert sub is not None
          assert sub["player_name"] == "Imported Player"
          assert sub["event_uid"] == event_uid
  ```

- [ ] **Step 2: Run test to verify it fails**
  Run: `./venv/bin/pytest tests/test_routes.py::test_import_submissions_route -v`
  Expected: FAIL with "404 Not Found" (or route not registered)

- [ ] **Step 3: Implement the import route**
  Add the route inside `create_app()` in `app/__init__.py` after `export_submissions`:
  ```python
  @app.route("/admin/<event_uid>/import_submissions", methods=["POST"])
  def import_submissions(event_uid):
      secret = request.form.get("secret")
      db = database.get_db()
      db.row_factory = sqlite3.Row
      event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
      if event is None:
          return "Event not found", 404
      if event["admin_secret"] != secret:
          return "Forbidden", 403

      file = request.files.get("submissions_file")
      if not file or file.filename == "":
          flash("No file selected.", "error")
          return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

      try:
          data = json.load(file)
      except Exception:
          flash("Invalid file format. Please upload a valid JSON file.", "error")
          return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

      if not isinstance(data, list):
          flash("Invalid JSON schema. Submissions must be formatted as an array.", "error")
          return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

      required_fields = [
          "day_type",
          "player_name",
          "player_id",
          "resources",
          "raw_data",
          "feasible_slots",
      ]
      for idx, item in enumerate(data):
          if not isinstance(item, dict):
              flash(f"Item at index {idx} is not a valid submission object.", "error")
              return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))
          for field in required_fields:
              if field not in item:
                  flash(
                      f"Missing required field '{field}' at submission index {idx}.",
                      "error",
                  )
                  return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

      # Process upserts inside transaction
      unique_players = list(set(item["player_id"] for item in data))

      # Delete existing matching records
      for player_id in unique_players:
          db.execute(
              "DELETE FROM submissions WHERE event_uid = ? AND player_id = ?",
              (event_uid, player_id),
          )
          db.execute(
              "DELETE FROM assignments WHERE event_uid = ? AND player_id = ?",
              (event_uid, player_id),
          )

      # Insert the imported submissions
      for item in data:
          sub_id = f"{event_uid}_{item['player_id']}_{item['day_type']}"
          # Ensure values are safely parsed (re-encode json strings if they were parsed as dicts/lists)
          raw_data_str = item["raw_data"] if isinstance(item["raw_data"], str) else json.dumps(item["raw_data"])
          feasible_slots_str = item["feasible_slots"] if isinstance(item["feasible_slots"], str) else json.dumps(item["feasible_slots"])

          db.execute(
              """
              INSERT INTO submissions (
                  id, event_uid, day_type, player_name, player_id, 
                  avatar_url, backpack_url, alliance_name, resources, 
                  raw_data, feasible_slots, status
              ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
              """,
              (
                  sub_id,
                  event_uid,
                  item["day_type"],
                  item["player_name"],
                  item["player_id"],
                  item.get("avatar_url"),
                  item.get("backpack_url"),
                  item.get("alliance_name"),
                  item["resources"],
                  raw_data_str,
                  feasible_slots_str,
                  item.get("status", "Pending"),
              ),
          )

      db.commit()
      app.audit_logger.info(f"ADMIN: Imported {len(data)} submissions for {len(unique_players)} players in event {event_uid}")
      flash(
          f"Successfully imported {len(data)} submissions for {len(unique_players)} players.",
          "success",
      )

      return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))
  ```

- [ ] **Step 4: Run test to verify it passes**
  Run: `./venv/bin/pytest tests/test_routes.py::test_import_submissions_route -v`
  Expected: PASS

- [ ] **Step 5: Run full tests and linting check**
  Run: `./venv/bin/pytest && ./venv/bin/ruff check .`
  Expected: PASS

- [ ] **Step 6: Commit**
  Run:
  ```bash
  git add app/__init__.py tests/test_routes.py
  git commit -m "feat: implement submissions import backend route and tests"
  ```

---

### Task 3: Admin Dashboard Template Integrations

**Files:**
- Modify: `app/templates/admin_dashboard.html:75-86` (Add export & import buttons next to refresh button)
- Modify: `app/templates/admin_dashboard.html:70-71` (Insert Flash Alerts Container below header)

**Interfaces:**
- Produces: Visual buttons and alert messages on the Admin Dashboard page.

- [ ] **Step 1: Add Flash Alerts display block**
  Open `app/templates/admin_dashboard.html` and locate line 71:
  ```html
          <div id="admin-dynamic-content">
  ```
  Insert the following alert template logic right *above* that tag (under the `<header>` block):
  ```html
          <!-- Flash Messages -->
          {% with messages = get_flashed_messages(with_categories=true) %}
              {% if messages %}
                  <div class="mb-6 space-y-2 max-w-4xl mx-auto">
                      {% for category, message in messages %}
                          <div class="p-4 rounded-md {% if category == 'error' %}bg-red-900/40 border border-red-500/30 text-red-200{% else %}bg-green-900/40 border border-green-500/30 text-green-200{% endif %} flex justify-between items-center shadow-md">
                              <span>{{ message }}</span>
                              <button onclick="this.parentElement.remove()" class="text-gray-400 hover:text-gray-200 font-bold px-2">&times;</button>
                          </div>
                      {% endfor %}
                  </div>
              {% endif %}
          {% endwith %}
  ```

- [ ] **Step 2: Add Export & Import controls**
  Locate lines 75-86 in `app/templates/admin_dashboard.html`:
  ```html
                  <div>
                      <h2 class="text-xl font-bold mb-2 text-gray-100">Global Actions</h2>
                      <div class="flex gap-2">
                          <form action="{{ url_for('refresh_players', event_uid=event.uid) }}" method="POST" class="inline">
                              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                              <input type="hidden" name="secret" value="{{ secret }}">
                              <button type="submit" class="bg-kvk-blue text-white font-bold py-2 px-4 rounded-md hover:bg-blue-600 transition duration-300">Refresh Player Data</button>
                          </form>
                      </div>
                  </div>
  ```
  Modify the `div` containing `class="flex gap-2"` to include the Export/Import controls:
  ```html
                  <div>
                      <h2 class="text-xl font-bold mb-2 text-gray-100">Global Actions</h2>
                      <div class="flex flex-wrap gap-2 items-center">
                          <form action="{{ url_for('refresh_players', event_uid=event.uid) }}" method="POST" class="inline">
                              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                              <input type="hidden" name="secret" value="{{ secret }}">
                              <button type="submit" class="bg-kvk-blue text-white font-bold py-2 px-4 rounded-md hover:bg-blue-600 transition duration-300">Refresh Player Data</button>
                          </form>

                          <a href="{{ url_for('export_submissions', event_uid=event.uid, secret=secret) }}" 
                             class="bg-kvk-gray-700 text-white font-bold py-2 px-4 rounded-md hover:bg-kvk-gray-600 transition duration-300 flex items-center gap-1 border border-kvk-gray-600">
                              <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-kvk-gold" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                              </svg>
                              Export Submissions
                          </a>

                          <form action="{{ url_for('import_submissions', event_uid=event.uid) }}" 
                                method="POST" 
                                enctype="multipart/form-data" 
                                class="inline-block">
                              <input type="hidden" name="csrf_token" value="{{ csrf_token() }}">
                              <input type="hidden" name="secret" value="{{ secret }}">
                              <label class="bg-kvk-gray-700 text-white font-bold py-2 px-4 rounded-md hover:bg-kvk-gray-600 cursor-pointer transition duration-300 flex items-center gap-1 border border-kvk-gray-600 inline-flex">
                                  <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5 text-kvk-gold" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                                  </svg>
                                  <span>Import Submissions</span>
                                  <input type="file" name="submissions_file" accept=".json" class="hidden" onchange="this.form.submit()">
                              </label>
                          </form>
                      </div>
                  </div>
  ```

- [ ] **Step 3: Run full tests to make sure layout changes do not break test assertions**
  Run: `./venv/bin/pytest`
  Expected: PASS

- [ ] **Step 4: Check formatting and style guide compliance**
  Run: `./venv/bin/ruff check .`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add app/templates/admin_dashboard.html
  git commit -m "feat: add export and import controls and flash alerts to dashboard template"
  ```
