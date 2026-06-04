from flask import Flask, request, jsonify, render_template, Response
import sqlite3
import os
import requests as http_requests

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def get_api_key():
    if not os.path.exists(ENV_PATH):
        return ""
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


app = Flask(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "leads.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                source TEXT,
                status TEXT DEFAULT 'New',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                status TEXT DEFAULT 'Draft',
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quote_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id INTEGER NOT NULL,
                product_name TEXT NOT NULL,
                product_description TEXT,
                sku TEXT,
                stock_level INTEGER DEFAULT 0,
                quantity INTEGER DEFAULT 1,
                unit_price REAL DEFAULT 0,
                cost_price REAL DEFAULT 0,
                additional_costs REAL DEFAULT 0,
                FOREIGN KEY (quote_id) REFERENCES quotes(id) ON DELETE CASCADE
            )
        """)


# ── Pages ──────────────────────────────────────────────────────────────────

@app.route("/")
def cover():
    return render_template("cover.html")


@app.route("/dashboard")
def dashboard():
    return render_template("index.html")


@app.route("/lead/<int:lead_id>")
def lead_detail(lead_id):
    return render_template("lead.html", lead_id=lead_id)


# ── Leads API ──────────────────────────────────────────────────────────────

@app.route("/api/leads", methods=["GET"])
def get_leads():
    with get_db() as conn:
        leads = conn.execute(
            "SELECT * FROM leads ORDER BY created_at DESC"
        ).fetchall()
    return jsonify([dict(row) for row in leads])


@app.route("/api/leads", methods=["POST"])
def add_lead():
    data = request.get_json()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO leads (name, email, phone, source, notes) VALUES (?, ?, ?, ?, ?)",
            (name, data.get("email", ""), data.get("phone", ""),
             data.get("source", ""), data.get("notes", "")),
        )
        lead_id = cursor.lastrowid
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()

    return jsonify(dict(lead)), 201


@app.route("/api/leads/<int:lead_id>", methods=["GET"])
def get_lead(lead_id):
    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify(dict(lead))


@app.route("/api/leads/<int:lead_id>", methods=["PUT"])
def update_lead(lead_id):
    data = request.get_json()
    with get_db() as conn:
        conn.execute(
            "UPDATE leads SET name=?, email=?, phone=?, source=?, status=?, notes=? WHERE id=?",
            (data.get("name"), data.get("email"), data.get("phone"),
             data.get("source"), data.get("status"), data.get("notes"), lead_id),
        )
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return jsonify({"error": "Lead not found"}), 404
    return jsonify(dict(lead))


@app.route("/api/leads/<int:lead_id>", methods=["DELETE"])
def delete_lead(lead_id):
    with get_db() as conn:
        conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    return jsonify({"message": "Lead deleted"})


@app.route("/api/leads/export")
def export_leads():
    import csv
    import io

    with get_db() as conn:
        leads = conn.execute(
            "SELECT name, email, phone, source, status, notes, created_at FROM leads ORDER BY created_at DESC"
        ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Email", "Phone", "Source", "Status", "Notes", "Date Added"])
    for lead in leads:
        writer.writerow([lead["name"], lead["email"], lead["phone"],
                         lead["source"], lead["status"], lead["notes"], lead["created_at"]])

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"}
    )


# ── Activities API ─────────────────────────────────────────────────────────

@app.route("/api/leads/<int:lead_id>/activities", methods=["GET"])
def get_activities(lead_id):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM activities WHERE lead_id=? ORDER BY created_at DESC", (lead_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/leads/<int:lead_id>/activities", methods=["POST"])
def add_activity(lead_id):
    data = request.get_json()
    activity_type = data.get("type", "").strip()
    description   = data.get("description", "").strip()
    if not activity_type or not description:
        return jsonify({"error": "Type and description are required"}), 400

    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO activities (lead_id, type, description) VALUES (?, ?, ?)",
            (lead_id, activity_type, description),
        )
        row = conn.execute("SELECT * FROM activities WHERE id=?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/activities/<int:activity_id>", methods=["DELETE"])
def delete_activity(activity_id):
    with get_db() as conn:
        conn.execute("DELETE FROM activities WHERE id=?", (activity_id,))
    return jsonify({"message": "Activity deleted"})


# ── Quotes API ────────────────────────────────────────────────────────────

@app.route("/api/leads/<int:lead_id>/quotes", methods=["GET"])
def get_quotes(lead_id):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM quotes WHERE lead_id=? ORDER BY created_at DESC", (lead_id,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/leads/<int:lead_id>/quotes", methods=["POST"])
def create_quote(lead_id):
    data = request.get_json()
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "Title is required"}), 400
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO quotes (lead_id, title, status, notes) VALUES (?,?,?,?)",
            (lead_id, title, data.get("status", "Draft"), data.get("notes", ""))
        )
        row = conn.execute("SELECT * FROM quotes WHERE id=?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/quotes/<int:quote_id>", methods=["GET"])
def get_quote(quote_id):
    with get_db() as conn:
        quote = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
        items = conn.execute("SELECT * FROM quote_items WHERE quote_id=? ORDER BY id", (quote_id,)).fetchall()
    if not quote:
        return jsonify({"error": "Quote not found"}), 404
    return jsonify({"quote": dict(quote), "items": [dict(i) for i in items]})


@app.route("/api/quotes/<int:quote_id>", methods=["PUT"])
def update_quote(quote_id):
    data = request.get_json()
    with get_db() as conn:
        conn.execute(
            "UPDATE quotes SET title=?, status=?, notes=? WHERE id=?",
            (data.get("title"), data.get("status"), data.get("notes"), quote_id)
        )
        row = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    return jsonify(dict(row))


@app.route("/api/quotes/<int:quote_id>", methods=["DELETE"])
def delete_quote(quote_id):
    with get_db() as conn:
        conn.execute("DELETE FROM quotes WHERE id=?", (quote_id,))
    return jsonify({"message": "Quote deleted"})


@app.route("/api/quotes/<int:quote_id>/items", methods=["POST"])
def add_quote_item(quote_id):
    data = request.get_json()
    if not data.get("product_name", "").strip():
        return jsonify({"error": "Product name is required"}), 400
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO quote_items
               (quote_id, product_name, product_description, sku, stock_level,
                quantity, unit_price, cost_price, additional_costs)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (quote_id, data.get("product_name"), data.get("product_description",""),
             data.get("sku",""), data.get("stock_level", 0), data.get("quantity", 1),
             data.get("unit_price", 0), data.get("cost_price", 0), data.get("additional_costs", 0))
        )
        row = conn.execute("SELECT * FROM quote_items WHERE id=?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/quote-items/<int:item_id>", methods=["PUT"])
def update_quote_item(item_id):
    data = request.get_json()
    with get_db() as conn:
        conn.execute(
            """UPDATE quote_items SET product_name=?, product_description=?, sku=?,
               stock_level=?, quantity=?, unit_price=?, cost_price=?, additional_costs=?
               WHERE id=?""",
            (data.get("product_name"), data.get("product_description"), data.get("sku"),
             data.get("stock_level"), data.get("quantity"), data.get("unit_price"),
             data.get("cost_price"), data.get("additional_costs"), item_id)
        )
        row = conn.execute("SELECT * FROM quote_items WHERE id=?", (item_id,)).fetchone()
    return jsonify(dict(row))


@app.route("/api/quote-items/<int:item_id>", methods=["DELETE"])
def delete_quote_item(item_id):
    with get_db() as conn:
        conn.execute("DELETE FROM quote_items WHERE id=?", (item_id,))
    return jsonify({"message": "Item deleted"})


@app.route("/quote/<int:quote_id>")
def quote_page(quote_id):
    return render_template("quote.html", quote_id=quote_id)


# ── Score Lead ────────────────────────────────────────────────────────────

@app.route("/api/leads/<int:lead_id>/score", methods=["POST"])
def score_lead(lead_id):
    import json as json_mod
    from datetime import datetime, timezone

    api_key = get_api_key()
    if not api_key or api_key == "your-api-key-here":
        return jsonify({"error": "API key not set."}), 500

    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    lead = dict(lead)
    try:
        created = datetime.fromisoformat(lead["created_at"].replace("Z", "+00:00"))
        days_ago = (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).days
    except Exception:
        days_ago = "unknown"

    prompt = (
        "You are a sales lead scoring assistant. Analyze this lead and return ONLY a JSON object "
        "with exactly two fields — no extra text, no markdown, no explanation outside the JSON.\n\n"
        f"Lead details:\n"
        f"- Name: {lead.get('name', '')}\n"
        f"- Source: {lead.get('source') or 'Unknown'}\n"
        f"- Status: {lead.get('status', '')}\n"
        f"- Notes: {lead.get('notes') or 'None'}\n"
        f"- Days since added: {days_ago}\n\n"
        "Return this exact format:\n"
        '{"rating": "Hot", "reason": "Hot — one plain-English sentence explaining why, max 25 words."}\n\n'
        'The "rating" must be exactly one of: Hot, Warm, Cold.\n'
        'The "reason" must start with the rating word followed by an em dash, e.g. "Hot — ..."'
    )

    response = http_requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 120,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )

    if response.status_code != 200:
        return jsonify({"error": f"Claude API error {response.status_code}: {response.text}"}), 500

    raw = response.json()["content"][0]["text"].strip()
    try:
        result = json_mod.loads(raw)
        if result.get("rating") not in ("Hot", "Warm", "Cold"):
            raise ValueError("Invalid rating")
    except Exception:
        return jsonify({"error": "Could not parse score. Please try again."}), 500

    return jsonify(result)


# ── Draft Email ────────────────────────────────────────────────────────────

@app.route("/api/leads/<int:lead_id>/draft-email", methods=["POST"])
def draft_email(lead_id):
    api_key = get_api_key()
    if not api_key or api_key == "your-api-key-here":
        return jsonify({"error": "API key not set. Please add your key to the .env file."}), 500

    with get_db() as conn:
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not lead:
        return jsonify({"error": "Lead not found"}), 404

    lead = dict(lead)
    prompt = (
        f"Write a short, friendly, personalised follow-up email to a lead.\n\n"
        f"Lead details:\n"
        f"- Name: {lead.get('name', '')}\n"
        f"- Source: {lead.get('source', 'unknown')}\n"
        f"- Status: {lead.get('status', '')}\n"
        f"- Notes: {lead.get('notes') or 'None'}\n\n"
        f"Instructions: Write only the email itself (subject line, then body). "
        f"Keep it under 150 words. Be warm, professional, and reference their source or notes naturally. "
        f"Do not include placeholders like [Your Name] — sign off as 'The Team'."
    )

    response = http_requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )

    if response.status_code != 200:
        return jsonify({"error": f"Claude API error {response.status_code}: {response.text}"}), 500

    email_text = response.json()["content"][0]["text"]
    return jsonify({"email": email_text})


if __name__ == "__main__":
    init_db()
    print("Lead app running at http://127.0.0.1:5000")
    app.run(debug=True)
