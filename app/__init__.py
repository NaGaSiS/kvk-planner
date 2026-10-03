import csv
import io
import json
import logging
import mimetypes
import os
import sqlite3
import time
import uuid
from logging.handlers import RotatingFileHandler

import markdown
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename

from config import Config

from . import database, image_analyzer, kingshot_scraper, logic
from .logic import format_minutes

# Ensure .js files are served with the correct MIME type
mimetypes.add_type("application/javascript", ".js")


def generate_slot_labels(slot_count=49):
    labels = []
    for i in range(slot_count):
        if slot_count == 48:
            start_total_minutes = i * 30
        else:
            start_total_minutes = (i * 30) - 15

        if start_total_minutes < 0:
            start_total_minutes += 24 * 60

        start_hour = start_total_minutes // 60
        start_min = start_total_minutes % 60

        end_total_minutes = start_total_minutes + 30
        end_hour = (end_total_minutes // 60) % 24
        end_min = end_total_minutes % 60

        labels.append(f"{start_hour:02d}:{start_min:02d}-\u200b{end_hour:02d}:{end_min:02d}")
    return labels


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    CSRFProtect(app)
    database.init_app(app)

    # Setup Audit Logging
    log_dir = os.path.join(app.root_path, "..", "logs")
    os.makedirs(log_dir, exist_ok=True)

    audit_handler = RotatingFileHandler(os.path.join(log_dir, "audit.log"), maxBytes=1000000, backupCount=5)
    audit_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    audit_logger = logging.getLogger("audit")
    audit_logger.setLevel(logging.INFO)
    audit_logger.addHandler(audit_handler)
    app.audit_logger = audit_logger

    # Make the label generator available to all templates
    @app.context_processor
    def inject_global_config():
        slot_count = 49
        try:
            event_uid = request.view_args.get("event_uid") if request.view_args else None
            if event_uid:
                db = database.get_db()
                row = db.execute("SELECT slot_count FROM events WHERE uid = ?", (event_uid,)).fetchone()
                if row and row[0] is not None:
                    slot_count = row[0]
        except (RuntimeError, Exception):
            pass

        return {
            "slot_labels": generate_slot_labels(slot_count),
            "enable_screenshot_upload": Config.ENABLE_SCREENSHOT_UPLOAD,
            "ga_measurement_id": Config.GA_MEASUREMENT_ID,
        }

    @app.template_filter("thousands")
    def thousands_filter(value):
        """Format a number with dot as thousands separator: 1234567 → 1.234.567"""
        try:
            return f"{float(value):,.0f}".replace(",", ".")
        except (TypeError, ValueError):
            return value

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://www.google-analytics.com;"
        )
        response.headers["Content-Security-Policy"] = csp
        return response

    @app.route("/")
    def index():
        is_admin = session.get("is_admin", False)
        return render_template("index.html", is_admin=is_admin)

    @app.route("/admin/verify-pin", methods=["POST"])
    def verify_admin_pin():
        """Verifica el PIN del administrador y crea sesión."""
        pin = request.form.get("pin", "").strip()

        # Check for superadmin
        if Config.SUPERADMIN_SECRET and pin == Config.SUPERADMIN_SECRET:
            return jsonify({"success": True, "redirect": url_for("superadmin", secret=pin)})

        if pin == Config.ADMIN_PIN:
            session["is_admin"] = True
            session.permanent = True
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "PIN incorrecto"}), 403

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        """Cierra sesión de administrador."""
        session.pop("is_admin", None)
        return redirect(url_for("index"))

    @app.route("/guide")
    def guide():
        try:
            with open("README.md", "r", encoding="utf-8") as f:
                lines = f.readlines()

            # Filter out technical badges for the in-app guide
            filtered_lines = [line for line in lines if not line.strip().startswith("[![")]
            content = "".join(filtered_lines)

            # Replace local file paths with web-accessible static paths for the in-app guide
            content = content.replace("app/static/images/", "/static/images/")
            html_content = markdown.markdown(content, extensions=["extra", "toc", "fenced_code"])
            return render_template("guide.html", content=html_content)
        except FileNotFoundError:
            return "Guide not found", 404

    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(
            os.path.join(app.root_path, "static"),
            "favicon.svg",
            mimetype="image/svg+xml",
        )

    @app.route("/create", methods=["POST"])
    def create_event():
        # Only admin can create events
        if not session.get("is_admin", False):
            return "Forbidden: Solo el administrador puede crear eventos.", 403

        event_name = request.form.get("event_name", "").strip()
        if not event_name:
            return "El nombre del evento es requerido.", 400

        research_day = request.form.get("research_day", "5")
        try:
            slot_count = int(request.form.get("slot_count", "49"))
            if slot_count not in [48, 49]:
                slot_count = 49
        except ValueError:
            slot_count = 49

        # All events will now have all 3 days active by default.
        # Store the research day preference in the JSON
        active_days = {
            "construction": True,
            "training": True,
            "research": True,
            "research_day": int(research_day),
        }

        uid = str(uuid.uuid4())
        admin_secret = str(uuid.uuid4())

        db = database.get_db()
        db.execute(
            "INSERT INTO events (uid, name, active_days, admin_secret, slot_count) VALUES (?, ?, ?, ?, ?)",
            (uid, event_name, json.dumps(active_days), admin_secret, slot_count),
        )
        db.commit()

        app.audit_logger.info(f"ADMIN: Created event '{event_name}' with uid {uid}")

        return redirect(url_for("success", event_uid=uid, secret=admin_secret))

    @app.route("/success/<event_uid>")
    def success(event_uid):
        secret = request.args.get("secret")

        player_url = url_for("player_form", event_uid=event_uid, _external=True)
        admin_url = url_for("admin_dashboard", event_uid=event_uid, secret=secret, _external=True)
        finalized_url = url_for("locked_appointments", event_uid=event_uid, _external=True)

        return render_template(
            "success.html",
            player_url=player_url,
            admin_url=admin_url,
            finalized_url=finalized_url,
        )

    def _build_back_url(req, event_uid):
        """Compute the 'back' URL for the calendar page based on ?ref= param."""
        ref = req.args.get("ref", "home")
        secret = req.args.get("secret", "")
        player_id = req.args.get("player_id", "")
        if ref == "admin" and secret:
            return url_for("admin_dashboard", event_uid=event_uid) + f"?secret={secret}"
        if ref == "player":
            base = url_for("player_form", event_uid=event_uid)
            return f"{base}?player_id={player_id}" if player_id else base
        return url_for("index")

    def _build_back_label(req):
        ref = req.args.get("ref", "home")
        labels = {
            "admin": "← Panel de Admin",
            "player": "← Mi Formulario",
            "home": "← Inicio",
        }
        return labels.get(ref, "← Inicio")

    @app.route("/event/<event_uid>/finalized")
    def locked_appointments(event_uid):

        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()

        if event is None:
            return "Event not found", 404

        active_days_config = json.loads(event["active_days"])
        active_days = [day for day in ["construction", "training", "research"] if active_days_config.get(day)]

        # Create a dictionary from the database row for the template
        event_dict = {
            "uid": event["uid"],
            "name": event["name"],
            "active_days": active_days_config,
        }

        # Fetch all assignments
        assignments_raw = db.execute(
            "SELECT * FROM assignments WHERE event_uid = ?",
            (event_uid,),
        ).fetchall()

        # Fetch submissions to get player/alliance names
        submissions_raw = db.execute("SELECT * FROM submissions WHERE event_uid = ?", (event_uid,)).fetchall()
        submissions_map = {(sub["day_type"], sub["player_id"]): sub for sub in submissions_raw}

        # Group rich assignments by day_type
        all_assignments = {day: {} for day in active_days}
        for a in assignments_raw:
            day_type = a["day_type"]
            player_id = a["player_id"]
            if day_type in all_assignments:
                submission = submissions_map.get((day_type, player_id))
                if submission:
                    all_assignments[day_type][a["slot_index"]] = {
                        "player_id": player_id,
                        "player_name": submission["player_name"],
                        "alliance_name": submission["alliance_name"],
                        "avatar_url": submission["avatar_url"],
                        "is_locked": bool(a["is_locked"]),
                    }

        return render_template(
            "locked_appointments.html",
            event=event_dict,
            active_days=active_days,
            assignments=all_assignments,
            back_url=_build_back_url(request, event_uid),
            back_label=_build_back_label(request),
            back_ref=request.args.get("ref", "home"),
        )

    @app.route("/event/<event_uid>")
    def player_form(event_uid):
        db = database.get_db()
        # Use a dictionary cursor for easier row access
        db.row_factory = sqlite3.Row
        event_cursor = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,))
        event = event_cursor.fetchone()

        if event is None:
            return "Event not found", 404

        # Create a dictionary from the database row
        event_dict = {
            "uid": event["uid"],
            "name": event["name"],
            "active_days": json.loads(event["active_days"]),
        }

        # Check for success message after submission
        show_success = request.args.get("success") == "1"
        player_id_prefill = request.args.get("player_id", "")

        return render_template(
            "player_form.html",
            event=event_dict,
            show_success=show_success,
            player_id_prefill=player_id_prefill,
        )

    @app.route("/event/<event_uid>/lookup_player")
    def lookup_player(event_uid):
        """API para identificar un jugador por su ID y cargar datos previos."""
        player_id = request.args.get("player_id", "").strip()
        kingdom = request.args.get("kingdom", "").strip()

        if not player_id or not player_id.isdigit():
            return jsonify({"found": False, "error": "ID inválido"}), 400

        # Kingdom is required for the external search; without it we can still
        # check the local DB but cannot scrape kingshot.com.br reliably.
        kingdom_missing = not kingdom or not kingdom.isdigit()

        db = database.get_db()
        db.row_factory = sqlite3.Row

        # Check if event exists
        event = db.execute("SELECT uid FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return jsonify({"found": False, "error": "Evento no encontrado"}), 404

        # Fetch all previous submissions for this player in this event
        submissions_raw = db.execute(
            "SELECT * FROM submissions WHERE event_uid = ? AND player_id = ?",
            (event_uid, player_id),
        ).fetchall()

        if submissions_raw:
            # Player found in local DB
            first_sub = dict(submissions_raw[0])
            player_name = first_sub["player_name"]
            avatar_url = first_sub["avatar_url"]
            alliance_name = first_sub["alliance_name"]
            city_level = first_sub["city_level"]

            # Always try to fetch fresh/real data from Kingshot to prevent fake data
            if not kingdom_missing:
                try:
                    scraped = kingshot_scraper.lookup_player(player_id, kingdom)
                    print(f"SCRAPED: {scraped}")
                    if scraped.get("found"):
                        player_name = scraped.get("player_name") or player_name
                        avatar_url = scraped.get("avatar_url") or avatar_url
                        alliance_name = scraped.get("alliance_name") or alliance_name
                        city_level = scraped.get("city_level") or city_level
                        city_label = scraped.get("city_label")
                        print(f"UPDATED: {player_name}, {alliance_name}")
                except Exception as e:
                    print(f"EXCEPTION IN SCRAPE: {e}")

            result = {
                "found": True,
                "player_id": player_id,
                "player_name": player_name,
                "avatar_url": avatar_url,
                "city_level": city_level,
                "city_label": locals().get("city_label") or (f"TC {city_level}" if city_level else None),
                "alliance_name": alliance_name,
                "kingdom": kingdom or first_sub.get("kingdom"),
                "source": "local_db",
                "submissions": {},
            }

            for sub in submissions_raw:
                day_type = sub["day_type"]
                try:
                    raw_data = json.loads(sub["raw_data"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    raw_data = {}
                try:
                    feasible_slots = json.loads(sub["feasible_slots"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    feasible_slots = []

                result["submissions"][day_type] = {
                    "raw_data": raw_data,
                    "feasible_slots": feasible_slots,
                    "status": sub["status"],
                }

            return jsonify(result)

        # Player NOT in local DB — kingdom is required to scrape kingshot.com.br
        if kingdom_missing:
            return jsonify(
                {
                    "found": False,
                    "requires_kingdom": True,
                    "player_id": player_id,
                    "error": "Kingdom number is required to search external profiles.",
                }
            )

        scraped = kingshot_scraper.lookup_player(player_id, kingdom)

        if scraped.get("found"):
            return jsonify(
                {
                    "found": True,
                    "player_id": player_id,
                    "player_name": scraped.get("player_name"),
                    "avatar_url": scraped.get("avatar_url"),
                    "city_level": scraped.get("city_level"),
                    "city_label": scraped.get("city_label"),
                    "alliance_name": scraped.get("alliance_name"),
                    "power": scraped.get("power"),
                    "kills": scraped.get("kills"),
                    "kingdom": scraped.get("kingdom"),
                    "source": "kingshot.com.br",
                    "submissions": {},
                }
            )

        return jsonify({"found": False, "player_id": player_id})

    @app.route("/event/<event_uid>/analyze_image", methods=["POST"])
    def analyze_image(event_uid):
        """API para analizar imágenes de mochila o aceleradores con IA."""
        # Check event exists
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT uid FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return jsonify({"success": False, "error": "Evento no encontrado"}), 404

        image_type = request.form.get("type", "")  # 'backpack' or 'speedups'
        if image_type not in ("backpack", "speedups"):
            return jsonify({"success": False, "error": "Tipo de imagen inválido. Use 'backpack' o 'speedups'"}), 400

        if "image" not in request.files:
            return jsonify({"success": False, "error": "No se encontró el archivo de imagen"}), 400

        file = request.files["image"]
        if not file or not file.filename:
            return jsonify({"success": False, "error": "Archivo vacío"}), 400

        allowed_extensions = {"png", "jpg", "jpeg", "webp"}
        extension = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
        if extension not in allowed_extensions:
            return jsonify({"success": False, "error": "Tipo de archivo no válido"}), 400

        image_bytes = file.read()
        api_key = Config.GROQ_API_KEY

        if image_type == "speedups":
            result = image_analyzer.analyze_speedups_image(image_bytes, api_key)
        else:
            result = image_analyzer.analyze_backpack_image(image_bytes, api_key)

        return jsonify(result)

    @app.route("/event/<event_uid>/submit", methods=["POST"])
    def submit(event_uid):
        db = database.get_db()
        db.row_factory = sqlite3.Row

        # Verify event exists
        event = db.execute("SELECT uid FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404

        player_id = request.form.get("player_id", "").strip()
        kingdom = request.form.get("kingdom_num", "").strip()
        player_name = request.form.get("player_name", "").strip()
        alliance_name = request.form.get("alliance_name", "").strip()
        city_level_str = request.form.get("city_level", "").strip()
        city_level = int(city_level_str) if city_level_str.isdigit() else None
        avatar_url = request.form.get("avatar_url") or None

        # Always try to fetch from kingshot to prevent users from faking their names/alliances
        if player_id and kingdom:
            try:
                scraped = kingshot_scraper.lookup_player(player_id, kingdom)
                if scraped and scraped.get("found"):
                    avatar_url = scraped.get("avatar_url") or avatar_url
                    player_name = scraped.get("player_name") or player_name
                    alliance_name = scraped.get("alliance_name") or alliance_name
                    if scraped.get("city_level"):
                        city_level = scraped.get("city_level")
            except Exception as e:
                app.audit_logger.error(f"Error auto-fetching player info on submit: {e}")

        # Server-side validation
        if not player_id.isdigit():
            return "Invalid Player ID: Must be numeric", 400

        if not player_name:
            return "Invalid Player Name: Cannot be empty", 400

        app.audit_logger.info(f"SUBMISSION: Player {player_name} ({player_id}) submitted resources for event {event_uid}")

        # Handle backpack screenshot upload
        backpack_url = None
        if Config.ENABLE_SCREENSHOT_UPLOAD and "backpack_screenshot" in request.files:
            file = request.files["backpack_screenshot"]
            if file and file.filename:
                allowed_extensions = {"png", "jpg", "jpeg", "gif", "webp"}
                extension = file.filename.rsplit(".", 1)[-1].lower()
                if extension not in allowed_extensions:
                    return "Invalid file type. Only images are allowed.", 400
                upload_dir = os.path.join(app.static_folder, "uploads")
                os.makedirs(upload_dir, exist_ok=True)
                filename = secure_filename(f"{event_uid}_{player_id}_{int(time.time())}_backpack_{file.filename}")
                file.save(os.path.join(upload_dir, filename))
                backpack_url = url_for("static", filename=f"uploads/{filename}")

        # Handle accelerators screenshot upload
        accelerators_url = None
        if Config.ENABLE_SCREENSHOT_UPLOAD and "accelerators_screenshot" in request.files:
            file = request.files["accelerators_screenshot"]
            if file and file.filename:
                allowed_extensions = {"png", "jpg", "jpeg", "gif", "webp"}
                extension = file.filename.rsplit(".", 1)[-1].lower()
                if extension not in allowed_extensions:
                    return "Invalid file type. Only images are allowed.", 400
                upload_dir = os.path.join(app.static_folder, "uploads")
                os.makedirs(upload_dir, exist_ok=True)
                filename = secure_filename(f"{event_uid}_{player_id}_{int(time.time())}_accel_{file.filename}")
                file.save(os.path.join(upload_dir, filename))
                accelerators_url = url_for("static", filename=f"uploads/{filename}")

        # First, delete all previous submissions and assignments for this player and event.
        db.execute(
            "DELETE FROM submissions WHERE event_uid = ? AND player_id = ?",
            (event_uid, player_id),
        )
        db.execute(
            "DELETE FROM assignments WHERE event_uid = ? AND player_id = ?",
            (event_uid, player_id),
        )

        # Then, insert the new submissions from the form.

        # --- Process Construction Submission ---
        construction_speedups = int(request.form.get("speedups-construction") or 0)
        truegold = int(request.form.get("truegold") or 0)
        tempered_truegold = int(request.form.get("tempered_truegold") or 0)
        feasible_slots = request.form.get("slots-construction", "[]")
        if (construction_speedups > 0 or truegold > 0 or tempered_truegold > 0) and feasible_slots != "[]":
            day_type = "construction"
            score = (construction_speedups * 30) + (truegold * 2000) + (tempered_truegold * 30000)
            raw_data = {
                "speedups": construction_speedups,
                "truegold": truegold,
                "tempered_truegold": tempered_truegold,
            }
            submission_id = f"{event_uid}_{player_id}_{day_type}"
            db.execute(
                "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, avatar_url, backpack_url, accelerators_url, city_level, alliance_name, kingdom, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    submission_id,
                    event_uid,
                    day_type,
                    player_name,
                    player_id,
                    avatar_url,
                    backpack_url,
                    accelerators_url,
                    city_level,
                    alliance_name,
                    kingdom,
                    score,
                    json.dumps(raw_data),
                    feasible_slots,
                ),
            )

        # --- Process Training Submission ---
        training_speedups = int(request.form.get("speedups-training") or 0)
        feasible_slots = request.form.get("slots-training", "[]")
        if training_speedups > 0 and feasible_slots != "[]":
            day_type = "training"
            score = training_speedups * 90
            raw_data = {"speedups": training_speedups}
            submission_id = f"{event_uid}_{player_id}_{day_type}"
            db.execute(
                "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, avatar_url, backpack_url, accelerators_url, city_level, alliance_name, kingdom, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    submission_id,
                    event_uid,
                    day_type,
                    player_name,
                    player_id,
                    avatar_url,
                    backpack_url,
                    accelerators_url,
                    city_level,
                    alliance_name,
                    kingdom,
                    score,
                    json.dumps(raw_data),
                    feasible_slots,
                ),
            )

        # --- Process Research Submission ---
        research_speedups = int(request.form.get("speedups-research") or 0)
        truegold_dust = int(request.form.get("truegold_dust") or 0)
        feasible_slots = request.form.get("slots-research", "[]")
        if (research_speedups > 0 or truegold_dust > 0) and feasible_slots != "[]":
            day_type = "research"
            score = (research_speedups * 30) + (truegold_dust * 1000)
            raw_data = {"speedups": research_speedups, "truegold_dust": truegold_dust}
            submission_id = f"{event_uid}_{player_id}_{day_type}"
            db.execute(
                "INSERT INTO submissions (id, event_uid, day_type, player_name, player_id, avatar_url, backpack_url, accelerators_url, city_level, alliance_name, kingdom, resources, raw_data, feasible_slots) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    submission_id,
                    event_uid,
                    day_type,
                    player_name,
                    player_id,
                    avatar_url,
                    backpack_url,
                    accelerators_url,
                    city_level,
                    alliance_name,
                    kingdom,
                    score,
                    json.dumps(raw_data),
                    feasible_slots,
                ),
            )

        db.commit()

        # Redirect back to the player form with success flag so they can view the calendar
        return redirect(url_for("player_form", event_uid=event_uid, success="1", player_id=player_id))

    @app.route("/admin/<event_uid>")
    def admin_dashboard(event_uid):
        db = database.get_db()
        db.row_factory = sqlite3.Row

        secret = request.args.get("secret")
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()

        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        active_days_config = json.loads(event["active_days"])
        active_days = [day for day in ["construction", "training", "research"] if active_days_config.get(day)]

        # Create a dictionary from the database row for the template
        event_dict = {
            "uid": event["uid"],
            "name": event["name"],
            "active_days": active_days_config,
        }

        # 1. Group submissions by day_type
        submissions_raw = db.execute(
            "SELECT * FROM submissions WHERE event_uid = ? ORDER BY resources DESC",
            (event_uid,),
        ).fetchall()
        submissions_by_day = {day: [] for day in active_days}
        for row in submissions_raw:
            if row["day_type"] in submissions_by_day:
                # Convert sqlite3.Row to a dictionary to allow item assignment
                sub_dict = dict(row)
                try:
                    sub_dict["raw_resources"] = json.loads(row["raw_data"])
                except (json.JSONDecodeError, TypeError):
                    sub_dict["raw_resources"] = {}
                submissions_by_day[row["day_type"]].append(sub_dict)

        # 2. Group assignments and related data by day_type
        assignments_raw = db.execute("SELECT * FROM assignments WHERE event_uid = ?", (event_uid,)).fetchall()
        rich_assignments = {day: {} for day in active_days}
        assignments_by_sub_id = {}
        submissions_map = {(sub["day_type"], sub["player_id"]): sub for sub in submissions_raw}

        for a in assignments_raw:
            day_type = a["day_type"]
            player_id = a["player_id"]
            if day_type in rich_assignments:
                submission = submissions_map.get((day_type, player_id))
                if submission:
                    rich_assignments[day_type][a["slot_index"]] = {
                        "player_id": player_id,
                        "player_name": submission["player_name"],
                        "alliance_name": submission["alliance_name"],
                        "avatar_url": submission["avatar_url"],
                        "is_locked": a["is_locked"],
                    }
            assignments_by_sub_id[(a["day_type"], a["player_id"])] = a

        # 3. Group everything else by day_type
        slot_count = event["slot_count"] if event["slot_count"] is not None else 49
        slot_density = {day: [0] * slot_count for day in active_days}
        slot_players = {day: {i: [] for i in range(slot_count)} for day in active_days}
        max_density = {day: 1 for day in active_days}
        available_slots = {day: [] for day in active_days}
        alliance_summary = {day: {} for day in active_days}

        slot_labels = generate_slot_labels(slot_count)

        for day in active_days:
            # Heatmap & Requested Slots Text
            for sub in submissions_by_day[day]:
                if not sub["feasible_slots"]:
                    sub["requested_slots_text"] = "No slots selected"
                    continue
                try:
                    feasible_slots = json.loads(sub["feasible_slots"])
                    # Create human readable labels for hover text
                    requested_labels = [slot_labels[i] for i in feasible_slots if 0 <= i < slot_count]
                    sub["requested_slots_text"] = ", ".join(requested_labels) if requested_labels else "No slots selected"

                    for slot_index in feasible_slots:
                        if 0 <= slot_index < slot_count:
                            slot_density[day][slot_index] += 1
                            slot_players[day][slot_index].append(
                                {
                                    "player_name": sub["player_name"],
                                    "alliance_name": sub["alliance_name"],
                                    "resources": sub["resources"],
                                    "submission_id": sub["id"],
                                }
                            )

                except (json.JSONDecodeError, TypeError, KeyError):
                    sub["requested_slots_text"] = "Error parsing slots"

                # Resources Hover Text
                try:
                    raw_resources = json.loads(sub["raw_data"])
                    parts = []
                    if day == "construction":
                        if raw_resources.get("speedups"):
                            parts.append(f"Speedups: {format_minutes(raw_resources['speedups'])}")
                        if raw_resources.get("truegold"):
                            parts.append(f"Truegold: {int(raw_resources['truegold']):,}".replace(",", "."))
                        if raw_resources.get("tempered_truegold"):
                            parts.append(f"Tempered Gold: {int(raw_resources['tempered_truegold']):,}".replace(",", "."))
                    elif day == "training":
                        if raw_resources.get("speedups"):
                            parts.append(f"Speedups: {format_minutes(raw_resources['speedups'])}")
                    elif day == "research":
                        if raw_resources.get("speedups"):
                            parts.append(f"Speedups: {format_minutes(raw_resources['speedups'])}")
                        if raw_resources.get("truegold_dust"):
                            parts.append(f"Dust: {int(raw_resources['truegold_dust']):,}".replace(",", "."))
                    sub["resources_text"] = " | ".join(parts) if parts else "No raw data"
                except (json.JSONDecodeError, TypeError):
                    sub["resources_text"] = "Error parsing resources"

            max_density[day] = max(slot_density[day]) if any(slot_density[day]) else 1

            # Available Slots
            assigned_slots_for_day = rich_assignments[day].keys()
            available_slots[day] = [i for i in range(slot_count) if i not in assigned_slots_for_day]

            # Alliance Summary
            day_summary = {}
            for sub in submissions_by_day[day]:
                alliance_name = sub["alliance_name"] or "No Alliance"
                if alliance_name not in day_summary:
                    day_summary[alliance_name] = {
                        "total_resources": 0,
                        "submissions_count": 0,
                        "assigned_count": 0,
                    }
                day_summary[alliance_name]["total_resources"] += sub["resources"]
                day_summary[alliance_name]["submissions_count"] += 1

            for assignment in rich_assignments[day].values():
                alliance_name = assignment["alliance_name"] or "No Alliance"
                if alliance_name in day_summary:
                    day_summary[alliance_name]["assigned_count"] += 1
            alliance_summary[day] = day_summary

        # Generate URLs for the admin dashboard links
        player_url = url_for("player_form", event_uid=event_uid, _external=True)
        finalized_url = url_for("locked_appointments", event_uid=event_uid, _external=True)

        return render_template(
            "admin_dashboard.html",
            event=event_dict,
            active_days=active_days,
            submissions_by_day=submissions_by_day,
            assignments=rich_assignments,
            assignments_by_sub_id=assignments_by_sub_id,
            available_slots=available_slots,
            secret=secret,
            slot_density=slot_density,
            slot_players=slot_players,
            max_density=max_density,
            alliance_summary=alliance_summary,
            player_url=player_url,
            finalized_url=finalized_url,
        )

    @app.route("/event/<event_uid>/schedule")
    def public_schedule(event_uid):
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()

        if event is None:
            return "Event not found", 404

        active_days_config = json.loads(event["active_days"])
        active_days = [day for day in ["construction", "training", "research"] if active_days_config.get(day)]

        # Create a dictionary from the database row for the template
        event_dict = {
            "uid": event["uid"],
            "name": event["name"],
            "active_days": active_days_config,
        }

        assignments_raw = db.execute("SELECT * FROM assignments WHERE event_uid = ?", (event_uid,)).fetchall()

        # Group assignments by day_type
        assignments = {day: {} for day in active_days}
        for a in assignments_raw:
            if a["day_type"] in assignments:
                assignments[a["day_type"]][a["slot_index"]] = a

        return render_template(
            "public_schedule.html",
            event=event_dict,
            active_days=active_days,
            assignments=assignments,
        )

    @app.route("/admin/<event_uid>/manual_assign", methods=["POST"])
    def manual_assign(event_uid):
        secret = request.form.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        submission_id = request.form.get("submission_id")
        slot_index = request.form.get("slot_index")

        if not slot_index:  # Don't do anything if the slot is empty
            return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

        try:
            slot_idx_val = int(slot_index)
            slot_count = event["slot_count"] if event["slot_count"] is not None else 49
            if not (0 <= slot_idx_val < slot_count):
                return "Invalid slot index range", 400
        except (ValueError, TypeError):
            return "Invalid slot index format", 400

        if not submission_id:
            return "Missing submission_id", 400
        try:
            _, player_id, day_type = submission_id.split("_", 2)
        except (ValueError, AttributeError):
            return "Invalid submission_id format", 400

        sub = db.execute(
            "SELECT 1 FROM submissions WHERE id = ? AND event_uid = ?",
            (submission_id, event_uid),
        ).fetchone()
        if not sub:
            return "Submission not found", 404

        app.audit_logger.info(f"ADMIN: Manual assign - Player {player_id} to slot {slot_idx_val} for day {day_type} in event {event_uid}")

        # Check if there is an existing assignment in this slot that will be overridden
        existing_assignment = db.execute(
            "SELECT player_id FROM assignments WHERE event_uid = ? AND day_type = ? AND slot_index = ?",
            (event_uid, day_type, slot_idx_val),
        ).fetchone()
        if existing_assignment and existing_assignment["player_id"] != player_id:
            db.execute(
                "UPDATE submissions SET status = 'Pending' WHERE event_uid = ? AND player_id = ? AND day_type = ?",
                (event_uid, existing_assignment["player_id"], day_type),
            )

        # Delete any pre-existing assignment for this player on this day
        db.execute(
            "DELETE FROM assignments WHERE event_uid = ? AND player_id = ? AND day_type = ?",
            (event_uid, player_id, day_type),
        )

        # Overwrite whatever was in the target slot and lock it
        db.execute(
            "REPLACE INTO assignments (event_uid, day_type, slot_index, player_id, is_locked) VALUES (?, ?, ?, ?, ?)",
            (event_uid, day_type, slot_idx_val, player_id, 1),
        )

        # Update submission status to 'Locked'
        db.execute(
            "UPDATE submissions SET status = 'Locked' WHERE event_uid = ? AND player_id = ? AND day_type = ?",
            (event_uid, player_id, day_type),
        )

        db.commit()

        return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

    @app.route("/admin/<event_uid>/distribute", methods=["POST"])
    def distribute(event_uid):
        secret = request.form.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        day_type = request.form.get("day_type")
        app.audit_logger.info(f"ADMIN: Automatic distribution triggered for event {event_uid}, day {day_type or 'all'}")
        logic.run_distribution_algorithm(event_uid, day_type)

        return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

    @app.route("/admin/<event_uid>/export/<day_type>", methods=["GET"])
    def export_csv(event_uid, day_type):
        secret = request.args.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        # Fetch locked assignments joined with submissions to get player name
        assignments = db.execute(
            """
            SELECT a.day_type, a.player_id, s.player_name, a.slot_index
            FROM assignments a
            JOIN submissions s ON a.event_uid = s.event_uid AND a.day_type = s.day_type AND a.player_id = s.player_id
            WHERE a.event_uid = ? AND a.day_type = ? AND a.is_locked = 1
            ORDER BY a.slot_index ASC
            """,
            (event_uid, day_type),
        ).fetchall()

        slot_count = event["slot_count"] if event["slot_count"] is not None else 49
        slot_labels = generate_slot_labels(slot_count)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["Event Type", "Player ID", "Player Name", "Appointment Slot"])

        for a in assignments:
            writer.writerow(
                [
                    a["day_type"],
                    a["player_id"],
                    a["player_name"],
                    slot_labels[a["slot_index"]],
                ]
            )

        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=schedule_{event['uid']}_{day_type}.csv"},
        )

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

        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"submissions_{event_uid}_{timestamp}.json"

        return Response(
            json.dumps(sub_list, indent=2),
            mimetype="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

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
            flash(
                "Invalid JSON schema. Submissions must be formatted as an array.",
                "error",
            )
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

            # Validate resources can be parsed to float, convert and save as float
            try:
                item["resources"] = float(item["resources"])
            except (ValueError, TypeError):
                flash("Must be a number.", "error")
                return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

            # Validate and normalize feasible_slots to JSON string of list of integers
            fs_val = item["feasible_slots"]
            if isinstance(fs_val, str):
                try:
                    fs_val = json.loads(fs_val)
                except Exception:
                    flash("feasible_slots must be a list.", "error")
                    return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))
            if not isinstance(fs_val, list):
                flash("feasible_slots must be a list.", "error")
                return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))
            try:
                fs_val = [int(x) for x in fs_val]
            except (ValueError, TypeError):
                flash("feasible_slots must be a list of integers.", "error")
                return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))
            item["feasible_slots"] = json.dumps(fs_val)

            # Validate and normalize raw_data to JSON string of a dictionary/object
            rd_val = item["raw_data"]
            if isinstance(rd_val, str):
                try:
                    rd_val = json.loads(rd_val)
                except Exception:
                    flash("raw_data must be a JSON object.", "error")
                    return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))
            if not isinstance(rd_val, dict):
                flash("raw_data must be a JSON object.", "error")
                return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))
            item["raw_data"] = json.dumps(rd_val)

        # Process upserts inside transaction
        unique_players = list({item["player_id"] for item in data})

        # Delete existing matching records (for the specific player_id and day_type)
        for item in data:
            db.execute(
                "DELETE FROM submissions WHERE event_uid = ? AND player_id = ? AND day_type = ?",
                (event_uid, item["player_id"], item["day_type"]),
            )
            db.execute(
                "DELETE FROM assignments WHERE event_uid = ? AND player_id = ? AND day_type = ?",
                (event_uid, item["player_id"], item["day_type"]),
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
                    avatar_url, backpack_url, alliance_name, kingdom, resources, 
                    raw_data, feasible_slots, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    @app.route("/admin/<event_uid>/confirm", methods=["POST"])
    def confirm(event_uid):
        secret = request.form.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        slot_index = request.form.get("slot_index")
        day_type = request.form.get("day_type")

        # Get the player_id for this assignment
        assignment = db.execute(
            "SELECT player_id FROM assignments WHERE event_uid = ? AND day_type = ? AND slot_index = ?",
            (event_uid, day_type, slot_index),
        ).fetchone()

        db.execute(
            "UPDATE assignments SET is_locked = 1 WHERE event_uid = ? AND day_type = ? AND slot_index = ?",
            (event_uid, day_type, slot_index),
        )

        app.audit_logger.info(f"ADMIN: Lock - Slot {slot_index} for day {day_type} in event {event_uid}")

        if assignment:
            db.execute(
                "UPDATE submissions SET status = 'Locked' WHERE event_uid = ? AND day_type = ? AND player_id = ?",
                (event_uid, day_type, assignment["player_id"]),
            )

        db.commit()

        return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

    @app.route("/admin/<event_uid>/unlock", methods=["POST"])
    def unlock(event_uid):
        secret = request.form.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        slot_index = request.form.get("slot_index")
        day_type = request.form.get("day_type")

        # Get the player_id for this assignment
        assignment = db.execute(
            "SELECT player_id FROM assignments WHERE event_uid = ? AND day_type = ? AND slot_index = ?",
            (event_uid, day_type, slot_index),
        ).fetchone()

        db.execute(
            "UPDATE assignments SET is_locked = 0 WHERE event_uid = ? AND day_type = ? AND slot_index = ?",
            (event_uid, day_type, slot_index),
        )

        app.audit_logger.info(f"ADMIN: Unlock - Slot {slot_index} for day {day_type} in event {event_uid}")

        if assignment:
            db.execute(
                "UPDATE submissions SET status = 'Confirmed' WHERE event_uid = ? AND day_type = ? AND player_id = ?",
                (event_uid, day_type, assignment["player_id"]),
            )

        db.commit()

        return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

    @app.route("/admin/<event_uid>/delete", methods=["POST"])
    def delete(event_uid):
        secret = request.form.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        submission_id = request.form.get("submission_id")

        app.audit_logger.info(f"ADMIN: Delete submission {submission_id} in event {event_uid}")

        # Find player_id and day_type from submission_id
        _, player_id, day_type = submission_id.split("_", 2)

        # First, clear the specific assignment for this player and day
        db.execute(
            "DELETE FROM assignments WHERE event_uid = ? AND player_id = ? AND day_type = ?",
            (event_uid, player_id, day_type),
        )

        # Then, delete the submission
        db.execute("DELETE FROM submissions WHERE id = ?", (submission_id,))

        db.commit()

        return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

    @app.route("/admin/<event_uid>/update_alliance", methods=["POST"])
    def update_alliance(event_uid):
        secret = request.form.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        submission_id = request.form.get("submission_id")
        new_alliance_name = request.form.get("alliance_name").strip()

        app.audit_logger.info(f"ADMIN: Update alliance for submission {submission_id} to {new_alliance_name} in event {event_uid}")

        db.execute(
            "UPDATE submissions SET alliance_name = ? WHERE id = ? AND event_uid = ?",
            (new_alliance_name, submission_id, event_uid),
        )
        db.commit()

        return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

    @app.route("/admin/<event_uid>/override_resources", methods=["POST"])
    def override_resources(event_uid):
        secret = request.form.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        submission_id = request.form.get("submission_id")
        submission = db.execute(
            "SELECT * FROM submissions WHERE id = ? AND event_uid = ?",
            (submission_id, event_uid),
        ).fetchone()

        if submission is None:
            return "Submission not found", 404

        day_type = submission["day_type"]

        try:
            if day_type == "construction":
                speedups = int(request.form.get("speedups") or 0)
                truegold = int(request.form.get("truegold") or 0)
                tempered_truegold = int(request.form.get("tempered_truegold") or 0)
                score = (speedups * 30) + (truegold * 2000) + (tempered_truegold * 30000)
                raw_data = {
                    "speedups": speedups,
                    "truegold": truegold,
                    "tempered_truegold": tempered_truegold,
                }
            elif day_type == "training":
                speedups = int(request.form.get("speedups") or 0)
                score = speedups * 90
                raw_data = {"speedups": speedups}
            elif day_type == "research":
                speedups = int(request.form.get("speedups") or 0)
                truegold_dust = int(request.form.get("truegold_dust") or 0)
                score = (speedups * 30) + (truegold_dust * 1000)
                raw_data = {"speedups": speedups, "truegold_dust": truegold_dust}
            else:
                return "Invalid day type", 400
        except ValueError:
            return "Invalid resource values", 400

        app.audit_logger.info(
            f"ADMIN: Override resources for submission {submission_id} (day_type={day_type}) - score={score}, raw_data={raw_data} in event {event_uid}"
        )

        db.execute(
            "UPDATE submissions SET resources = ?, raw_data = ? WHERE id = ? AND event_uid = ?",
            (score, json.dumps(raw_data), submission_id, event_uid),
        )
        db.commit()

        return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

    @app.route("/admin/<event_uid>/unset", methods=["POST"])
    def unset_assignment(event_uid):
        secret = request.form.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        submission_id = request.form.get("submission_id")
        _, player_id, day_type = submission_id.split("_", 2)

        app.audit_logger.info(f"ADMIN: Unset assignment for Player {player_id} on day {day_type} in event {event_uid}")

        # Delete the assignment for this player on this day
        db.execute(
            "DELETE FROM assignments WHERE event_uid = ? AND player_id = ? AND day_type = ?",
            (event_uid, player_id, day_type),
        )

        # Update submission status back to 'Pending'
        db.execute(
            "UPDATE submissions SET status = 'Pending' WHERE event_uid = ? AND player_id = ? AND day_type = ?",
            (event_uid, player_id, day_type),
        )

        db.commit()

        return redirect(url_for("admin_dashboard", event_uid=event_uid, secret=secret))

    @app.route("/admin/<event_uid>/logs")
    def view_logs(event_uid):
        secret = request.args.get("secret")
        db = database.get_db()
        db.row_factory = sqlite3.Row
        event = db.execute("SELECT * FROM events WHERE uid = ?", (event_uid,)).fetchone()
        if event is None:
            return "Event not found", 404
        if event["admin_secret"] != secret:
            return "Forbidden", 403

        log_path = os.path.join(app.root_path, "..", "logs", "audit.log")
        if not os.path.exists(log_path):
            return "Log file not found", 404

        with open(log_path, "r") as f:
            # Read last 1000 lines
            lines = f.readlines()[-1000:]
            content = "".join(lines)

        return Response(content, mimetype="text/plain")

    @app.route("/superadmin")
    def superadmin():
        import hmac as _hmac

        secret_param = request.args.get("secret", "")
        expected = app.config.get("SUPERADMIN_SECRET", "")

        # First visit with ?secret=... — authenticate and redirect
        if secret_param:
            if expected and _hmac.compare_digest(secret_param, expected):
                session["is_superadmin"] = True
                return redirect(url_for("superadmin"))
            return "Forbidden — wrong secret", 403

        # Already authenticated
        if not session.get("is_superadmin"):
            return "Forbidden — not authenticated. Use /superadmin?secret=YOUR_SECRET", 403

        from .database import get_db
        from .logic import get_superadmin_metrics

        time_range = request.args.get("range", "all")
        db = get_db()
        metrics = get_superadmin_metrics(db, time_range)
        return render_template("superadmin.html", metrics=metrics, current_range=time_range)

    @app.route("/superadmin/delete_event", methods=["POST"])
    def superadmin_delete_event():
        import hmac as _hmac

        secret_param = request.form.get("superadmin_secret", "")
        expected = app.config.get("SUPERADMIN_SECRET", "")
        if not expected or not _hmac.compare_digest(secret_param, expected):
            return "Forbidden — wrong superadmin secret", 403

        event_uid = request.form.get("event_uid")
        if not event_uid:
            return "Missing event_uid", 400

        db = database.get_db()
        db.execute("DELETE FROM assignments WHERE event_uid = ?", (event_uid,))
        db.execute("DELETE FROM submissions WHERE event_uid = ?", (event_uid,))
        db.execute("DELETE FROM events WHERE uid = ?", (event_uid,))
        db.commit()

        app.audit_logger.info(f"SUPERADMIN: Deleted event {event_uid} and all associated data.")
        return redirect(url_for("superadmin"))

    @app.route("/superadmin/logout")
    def superadmin_logout():
        session.pop("is_superadmin", None)
        return redirect(url_for("index"))

    return app


app = create_app()
