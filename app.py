from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import sqlite3
import os
import csv
import io
from datetime import datetime, timedelta
import threading
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'leadflow-crm-secret-2026')

DATABASE = 'leads.db'


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            company TEXT,
            title TEXT,
            source TEXT DEFAULT 'manual',
            status TEXT DEFAULT 'new',
            pipeline_stage TEXT DEFAULT 'prospect',
            pipeline_value REAL DEFAULT 0,
            notes TEXT,
            sequence_active INTEGER DEFAULT 0,
            sequence_step INTEGER DEFAULT 0,
            sequence_start_date TEXT,
            last_contacted TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS email_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            sequence_step INTEGER DEFAULT 1,
            delay_days INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS outreach_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            template_id INTEGER,
            subject TEXT,
            body TEXT,
            status TEXT DEFAULT 'sent',
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id),
            FOREIGN KEY (template_id) REFERENCES email_templates(id)
        );

        CREATE TABLE IF NOT EXISTS scheduled_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            template_id INTEGER,
            scheduled_for TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id),
            FOREIGN KEY (template_id) REFERENCES email_templates(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vin TEXT,
            stock_number TEXT,
            year INTEGER,
            make TEXT DEFAULT 'Hyundai',
            model TEXT,
            trim TEXT,
            variant TEXT,
            color_exterior TEXT,
            color_interior TEXT,
            mileage INTEGER DEFAULT 0,
            cost_price REAL DEFAULT 0,
            rrp REAL DEFAULT 0,
            status TEXT DEFAULT 'available',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_number TEXT UNIQUE,
            lead_id INTEGER,
            vehicle_id INTEGER,
            status TEXT DEFAULT 'draft',
            selling_price REAL DEFAULT 0,
            discount REAL DEFAULT 0,
            cost_price REAL DEFAULT 0,
            accessories_cost REAL DEFAULT 0,
            accessories_price REAL DEFAULT 0,
            trade_in_description TEXT,
            trade_in_value REAL DEFAULT 0,
            trade_in_outstanding REAL DEFAULT 0,
            deposit REAL DEFAULT 0,
            finance_amount REAL DEFAULT 0,
            interest_rate REAL DEFAULT 9.99,
            term_months INTEGER DEFAULT 60,
            monthly_payment REAL DEFAULT 0,
            balloon_payment REAL DEFAULT 0,
            notes TEXT,
            internal_notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT,
            expires_at TEXT,
            FOREIGN KEY (lead_id) REFERENCES leads(id),
            FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
        );

        CREATE TABLE IF NOT EXISTS test_drives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            vehicle_id INTEGER,
            scheduled_date TEXT,
            scheduled_time TEXT DEFAULT '10:00',
            status TEXT DEFAULT 'scheduled',
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id),
            FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
        );

        CREATE TABLE IF NOT EXISTS finance_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            quote_id INTEGER,
            bank TEXT,
            status TEXT DEFAULT 'pending',
            application_date TEXT,
            approved_amount REAL DEFAULT 0,
            interest_rate_offered REAL DEFAULT 0,
            term_offered INTEGER DEFAULT 0,
            monthly_offered REAL DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (lead_id) REFERENCES leads(id),
            FOREIGN KEY (quote_id) REFERENCES quotes(id)
        );
    ''')

    defaults = [
        ('smtp_host', 'smtp.gmail.com'),
        ('smtp_port', '587'),
        ('smtp_email', ''),
        ('smtp_password', ''),
        ('smtp_name', 'Lead Tracker'),
        ('sequence_enabled', 'true'),
    ]
    for key, value in defaults:
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))

    default_templates = [
        (
            'Initial Outreach',
            'Quick question about {{company}}',
            'Hi {{name}},\n\nI hope this message finds you well. I came across {{company}} and wanted to reach out personally.\n\nI\'d love to schedule a quick 15-minute call to introduce myself and learn more about what you\'re working on.\n\nWould you be open to a brief chat this week?\n\nBest regards,\n{{sender_name}}',
            1, 0
        ),
        (
            'Follow-Up #1',
            'Following up - {{company}}',
            'Hi {{name}},\n\nI wanted to follow up on my previous message. I know you\'re busy, so I\'ll keep this short.\n\nI genuinely believe we could help {{company}} and would love 15 minutes of your time to explain how.\n\nAre you available for a quick call this week?\n\nBest,\n{{sender_name}}',
            2, 3
        ),
        (
            'Follow-Up #2',
            'Last touch - {{company}}',
            'Hi {{name}},\n\nI don\'t want to be a bother, so this will be my last outreach for now.\n\nIf the timing isn\'t right, I completely understand. Feel free to reach out whenever it makes sense.\n\nWishing you all the best with {{company}}!\n\n{{sender_name}}',
            3, 7
        ),
    ]
    for name, subject, body, step, delay in default_templates:
        cursor.execute(
            'INSERT OR IGNORE INTO email_templates (name, subject, body, sequence_step, delay_days) VALUES (?, ?, ?, ?, ?)',
            (name, subject, body, step, delay)
        )

    conn.commit()
    conn.close()


def get_setting(key):
    conn = get_db()
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else ''


def generate_quote_number():
    conn = get_db()
    year = datetime.now().year
    count = conn.execute(
        "SELECT COUNT(*) as c FROM quotes WHERE quote_number LIKE ?",
        (f'Q-{year}-%',)
    ).fetchone()['c']
    conn.close()
    return f'Q-{year}-{count + 1:04d}'


def personalize(subject, body, lead, sender_name):
    first_name = lead['name'].split()[0] if lead['name'] else ''
    replacements = {
        '{{name}}': first_name,
        '{{full_name}}': lead['name'] or '',
        '{{company}}': lead['company'] or 'your company',
        '{{email}}': lead['email'] or '',
        '{{title}}': lead['title'] or '',
        '{{sender_name}}': sender_name,
    }
    for k, v in replacements.items():
        subject = subject.replace(k, v)
        body = body.replace(k, v)
    return subject, body


def send_email(to_email, to_name, subject, body, lead_id=None, template_id=None):
    smtp_host = get_setting('smtp_host')
    smtp_port = int(get_setting('smtp_port') or 587)
    smtp_email = get_setting('smtp_email')
    smtp_password = get_setting('smtp_password')
    smtp_name = get_setting('smtp_name')

    if not smtp_email or not smtp_password:
        return False, 'SMTP not configured. Go to Settings to add your email credentials.'

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f'{smtp_name} <{smtp_email}>'
        msg['To'] = f'{to_name} <{to_email}>'
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_email, smtp_password)
            server.send_message(msg)

        if lead_id:
            conn = get_db()
            conn.execute(
                'INSERT INTO outreach_log (lead_id, template_id, subject, body, status) VALUES (?, ?, ?, ?, ?)',
                (lead_id, template_id, subject, body, 'sent')
            )
            conn.execute(
                'UPDATE leads SET last_contacted = ?, status = "contacted", updated_at = ? WHERE id = ?',
                (datetime.now().isoformat(), datetime.now().isoformat(), lead_id)
            )
            conn.commit()
            conn.close()

        return True, 'Email sent successfully'
    except Exception as e:
        return False, str(e)


def process_scheduled_emails():
    conn = get_db()
    now = datetime.now().isoformat()
    pending = conn.execute('''
        SELECT se.*, l.name, l.email, l.company, l.title,
               et.subject, et.body
        FROM scheduled_emails se
        JOIN leads l ON se.lead_id = l.id
        JOIN email_templates et ON se.template_id = et.id
        WHERE se.status = "pending" AND se.scheduled_for <= ?
    ''', (now,)).fetchall()

    sender_name = get_setting('smtp_name')
    for job in pending:
        subject, body = personalize(job['subject'], job['body'], job, sender_name)
        success, _ = send_email(
            job['email'], job['name'], subject, body,
            lead_id=job['lead_id'], template_id=job['template_id']
        )
        status = 'sent' if success else 'failed'
        conn.execute('UPDATE scheduled_emails SET status = ? WHERE id = ?', (status, job['id']))
        if success:
            conn.execute(
                'UPDATE leads SET sequence_step = sequence_step + 1 WHERE id = ?',
                (job['lead_id'],)
            )

    conn.commit()
    conn.close()


def scheduler_loop():
    while True:
        try:
            if get_setting('sequence_enabled') == 'true':
                process_scheduled_emails()
        except Exception as e:
            print(f'Scheduler error: {e}')
        time.sleep(60)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def dashboard():
    conn = get_db()
    total_leads = conn.execute('SELECT COUNT(*) as c FROM leads').fetchone()['c']
    active_sequences = conn.execute('SELECT COUNT(*) as c FROM leads WHERE sequence_active = 1').fetchone()['c']
    emails_sent = conn.execute('SELECT COUNT(*) as c FROM outreach_log').fetchone()['c']
    pipeline_stages = conn.execute(
        'SELECT pipeline_stage, COUNT(*) as count, SUM(pipeline_value) as total_value FROM leads GROUP BY pipeline_stage'
    ).fetchall()
    recent_leads = conn.execute('SELECT * FROM leads ORDER BY created_at DESC LIMIT 10').fetchall()
    upcoming_emails = conn.execute('''
        SELECT se.*, l.name, et.name as template_name
        FROM scheduled_emails se
        JOIN leads l ON se.lead_id = l.id
        JOIN email_templates et ON se.template_id = et.id
        WHERE se.status = "pending"
        ORDER BY se.scheduled_for ASC LIMIT 10
    ''').fetchall()
    conn.close()
    return render_template('dashboard.html',
        total_leads=total_leads, active_sequences=active_sequences,
        emails_sent=emails_sent, pipeline_stages=pipeline_stages,
        recent_leads=recent_leads, upcoming_emails=upcoming_emails)


@app.route('/leads')
def leads():
    conn = get_db()
    search = request.args.get('search', '')
    status = request.args.get('status', '')
    stage = request.args.get('stage', '')
    query = 'SELECT * FROM leads WHERE 1=1'
    params = []
    if search:
        query += ' AND (name LIKE ? OR email LIKE ? OR company LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])
    if status:
        query += ' AND status = ?'
        params.append(status)
    if stage:
        query += ' AND pipeline_stage = ?'
        params.append(stage)
    query += ' ORDER BY created_at DESC'
    all_leads = conn.execute(query, params).fetchall()
    conn.close()
    return render_template('leads.html', leads=all_leads, search=search, status=status, stage=stage)


@app.route('/leads/add', methods=['POST'])
def add_lead():
    conn = get_db()
    conn.execute('''
        INSERT INTO leads (name, email, phone, company, title, source, pipeline_value, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        request.form['name'], request.form['email'],
        request.form.get('phone', ''), request.form.get('company', ''),
        request.form.get('title', ''), request.form.get('source', 'manual'),
        float(request.form.get('pipeline_value', 0) or 0),
        request.form.get('notes', '')
    ))
    conn.commit()
    conn.close()
    flash('Lead added successfully!', 'success')
    return redirect(url_for('leads'))


@app.route('/leads/<int:lead_id>')
def lead_detail(lead_id):
    conn = get_db()
    lead = conn.execute('SELECT * FROM leads WHERE id = ?', (lead_id,)).fetchone()
    if not lead:
        flash('Lead not found.', 'error')
        return redirect(url_for('leads'))
    history = conn.execute('''
        SELECT ol.*, et.name as template_name FROM outreach_log ol
        LEFT JOIN email_templates et ON ol.template_id = et.id
        WHERE ol.lead_id = ? ORDER BY ol.sent_at DESC
    ''', (lead_id,)).fetchall()
    scheduled = conn.execute('''
        SELECT se.*, et.name as template_name FROM scheduled_emails se
        JOIN email_templates et ON se.template_id = et.id
        WHERE se.lead_id = ? AND se.status = "pending" ORDER BY se.scheduled_for ASC
    ''', (lead_id,)).fetchall()
    templates = conn.execute('SELECT * FROM email_templates ORDER BY sequence_step').fetchall()
    lead_test_drives = conn.execute('''
        SELECT td.*, v.year as v_year, v.make as v_make, v.model as v_model,
               v.trim as v_trim, v.stock_number as v_stock
        FROM test_drives td
        LEFT JOIN vehicles v ON td.vehicle_id = v.id
        WHERE td.lead_id = ? ORDER BY td.scheduled_date DESC, td.scheduled_time DESC LIMIT 10
    ''', (lead_id,)).fetchall()
    lead_finance = conn.execute('''
        SELECT fa.*, q.quote_number FROM finance_applications fa
        LEFT JOIN quotes q ON fa.quote_id = q.id
        WHERE fa.lead_id = ? ORDER BY fa.created_at DESC LIMIT 5
    ''', (lead_id,)).fetchall()
    available_vehicles = conn.execute(
        "SELECT * FROM vehicles WHERE status = 'available' ORDER BY year DESC, model"
    ).fetchall()
    lead_quotes = conn.execute('''
        SELECT q.*, v.model as v_model, v.trim as v_trim FROM quotes q
        LEFT JOIN vehicles v ON q.vehicle_id = v.id
        WHERE q.lead_id = ? ORDER BY q.created_at DESC LIMIT 5
    ''', (lead_id,)).fetchall()
    conn.close()
    return render_template('lead_detail.html', lead=lead, history=history,
                           scheduled=scheduled, templates=templates,
                           lead_test_drives=lead_test_drives, lead_finance=lead_finance,
                           available_vehicles=available_vehicles, lead_quotes=lead_quotes)


@app.route('/leads/<int:lead_id>/update', methods=['POST'])
def update_lead(lead_id):
    conn = get_db()
    conn.execute('''
        UPDATE leads SET name=?, email=?, phone=?, company=?, title=?, source=?,
        status=?, pipeline_stage=?, pipeline_value=?, notes=?, updated_at=? WHERE id=?
    ''', (
        request.form['name'], request.form['email'],
        request.form.get('phone', ''), request.form.get('company', ''),
        request.form.get('title', ''), request.form.get('source', 'manual'),
        request.form.get('status', 'new'), request.form.get('pipeline_stage', 'prospect'),
        float(request.form.get('pipeline_value', 0) or 0),
        request.form.get('notes', ''), datetime.now().isoformat(), lead_id
    ))
    conn.commit()
    conn.close()
    flash('Lead updated!', 'success')
    return redirect(url_for('lead_detail', lead_id=lead_id))


@app.route('/leads/<int:lead_id>/delete', methods=['POST'])
def delete_lead(lead_id):
    conn = get_db()
    conn.execute('DELETE FROM leads WHERE id = ?', (lead_id,))
    conn.execute('DELETE FROM outreach_log WHERE lead_id = ?', (lead_id,))
    conn.execute('DELETE FROM scheduled_emails WHERE lead_id = ?', (lead_id,))
    conn.commit()
    conn.close()
    flash('Lead deleted.', 'success')
    return redirect(url_for('leads'))


@app.route('/leads/<int:lead_id>/start_sequence', methods=['POST'])
def start_sequence(lead_id):
    conn = get_db()
    lead = conn.execute('SELECT * FROM leads WHERE id = ?', (lead_id,)).fetchone()
    if not lead:
        flash('Lead not found.', 'error')
        return redirect(url_for('leads'))
    templates = conn.execute('SELECT * FROM email_templates ORDER BY sequence_step').fetchall()
    if not templates:
        flash('No email templates found. Add templates first in the Templates section.', 'error')
        return redirect(url_for('lead_detail', lead_id=lead_id))
    conn.execute(
        'UPDATE scheduled_emails SET status = "cancelled" WHERE lead_id = ? AND status = "pending"',
        (lead_id,)
    )
    now = datetime.now()
    for t in templates:
        send_time = now + timedelta(days=t['delay_days'])
        conn.execute(
            'INSERT INTO scheduled_emails (lead_id, template_id, scheduled_for) VALUES (?, ?, ?)',
            (lead_id, t['id'], send_time.isoformat())
        )
    conn.execute(
        'UPDATE leads SET sequence_active=1, sequence_step=0, sequence_start_date=?, updated_at=? WHERE id=?',
        (now.isoformat(), now.isoformat(), lead_id)
    )
    conn.commit()
    conn.close()
    flash('Outreach sequence started! Emails will be sent automatically.', 'success')
    return redirect(url_for('lead_detail', lead_id=lead_id))


@app.route('/leads/<int:lead_id>/stop_sequence', methods=['POST'])
def stop_sequence(lead_id):
    conn = get_db()
    conn.execute(
        'UPDATE scheduled_emails SET status = "cancelled" WHERE lead_id = ? AND status = "pending"',
        (lead_id,)
    )
    conn.execute(
        'UPDATE leads SET sequence_active=0, updated_at=? WHERE id=?',
        (datetime.now().isoformat(), lead_id)
    )
    conn.commit()
    conn.close()
    flash('Sequence stopped.', 'info')
    return redirect(url_for('lead_detail', lead_id=lead_id))


@app.route('/leads/<int:lead_id>/send_email', methods=['POST'])
def send_manual_email(lead_id):
    conn = get_db()
    lead = conn.execute('SELECT * FROM leads WHERE id = ?', (lead_id,)).fetchone()
    template_id = request.form.get('template_id') or None
    sender_name = get_setting('smtp_name')

    if template_id:
        t = conn.execute('SELECT * FROM email_templates WHERE id = ?', (template_id,)).fetchone()
        subject, body = personalize(t['subject'], t['body'], lead, sender_name)
    else:
        subject = request.form.get('custom_subject', '')
        body = request.form.get('custom_body', '')

    conn.close()
    success, message = send_email(lead['email'], lead['name'], subject, body,
                                   lead_id=lead_id, template_id=template_id)
    flash(message, 'success' if success else 'error')
    return redirect(url_for('lead_detail', lead_id=lead_id))


@app.route('/templates')
def email_templates():
    conn = get_db()
    templates = conn.execute('SELECT * FROM email_templates ORDER BY sequence_step').fetchall()
    conn.close()
    return render_template('templates.html', templates=templates)


@app.route('/templates/add', methods=['POST'])
def add_template():
    conn = get_db()
    conn.execute(
        'INSERT INTO email_templates (name, subject, body, sequence_step, delay_days) VALUES (?, ?, ?, ?, ?)',
        (request.form['name'], request.form['subject'], request.form['body'],
         int(request.form.get('sequence_step', 1)), int(request.form.get('delay_days', 0)))
    )
    conn.commit()
    conn.close()
    flash('Template added!', 'success')
    return redirect(url_for('email_templates'))


@app.route('/templates/<int:tid>/update', methods=['POST'])
def update_template(tid):
    conn = get_db()
    conn.execute(
        'UPDATE email_templates SET name=?, subject=?, body=?, sequence_step=?, delay_days=? WHERE id=?',
        (request.form['name'], request.form['subject'], request.form['body'],
         int(request.form.get('sequence_step', 1)), int(request.form.get('delay_days', 0)), tid)
    )
    conn.commit()
    conn.close()
    flash('Template updated!', 'success')
    return redirect(url_for('email_templates'))


@app.route('/templates/<int:tid>/delete', methods=['POST'])
def delete_template(tid):
    conn = get_db()
    conn.execute('DELETE FROM email_templates WHERE id = ?', (tid,))
    conn.commit()
    conn.close()
    flash('Template deleted.', 'success')
    return redirect(url_for('email_templates'))


@app.route('/outreach')
def outreach():
    conn = get_db()
    log = conn.execute('''
        SELECT ol.*, l.name as lead_name, l.company,
               et.name as template_name
        FROM outreach_log ol
        JOIN leads l ON ol.lead_id = l.id
        LEFT JOIN email_templates et ON ol.template_id = et.id
        ORDER BY ol.sent_at DESC LIMIT 100
    ''').fetchall()
    scheduled = conn.execute('''
        SELECT se.*, l.name as lead_name,
               et.name as template_name, et.subject
        FROM scheduled_emails se
        JOIN leads l ON se.lead_id = l.id
        JOIN email_templates et ON se.template_id = et.id
        WHERE se.status = "pending"
        ORDER BY se.scheduled_for ASC
    ''').fetchall()
    stats = conn.execute(
        'SELECT COUNT(*) as total_sent, COUNT(DISTINCT lead_id) as leads_contacted FROM outreach_log'
    ).fetchone()
    active_sequences = conn.execute(
        'SELECT COUNT(*) as c FROM leads WHERE sequence_active = 1'
    ).fetchone()['c']
    conn.close()
    return render_template('outreach.html', log=log, scheduled=scheduled,
                           stats=stats, active_sequences=active_sequences)


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        conn = get_db()
        for key in ['smtp_host', 'smtp_port', 'smtp_email', 'smtp_password', 'smtp_name']:
            if key in request.form:
                conn.execute('UPDATE settings SET value = ? WHERE key = ?', (request.form[key], key))
        seq_val = 'true' if request.form.get('sequence_enabled') else 'false'
        conn.execute('UPDATE settings SET value = ? WHERE key = "sequence_enabled"', (seq_val,))
        conn.commit()
        conn.close()
        flash('Settings saved!', 'success')
        return redirect(url_for('settings'))

    conn = get_db()
    all_settings = {row['key']: row['value'] for row in conn.execute('SELECT * FROM settings').fetchall()}
    conn.close()
    return render_template('settings.html', settings=all_settings)


@app.route('/api/template/<int:tid>')
def api_template(tid):
    conn = get_db()
    t = conn.execute('SELECT * FROM email_templates WHERE id = ?', (tid,)).fetchone()
    conn.close()
    if t:
        return jsonify(dict(t))
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/vehicle/<int:vid>')
def api_vehicle(vid):
    conn = get_db()
    v = conn.execute('SELECT * FROM vehicles WHERE id = ?', (vid,)).fetchone()
    conn.close()
    if v:
        return jsonify(dict(v))
    return jsonify({'error': 'Not found'}), 404


@app.route('/api/inventory/suggest')
def inventory_suggest():
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify([])
    conn = get_db()
    results = conn.execute('''
        SELECT id, year, make, model, trim, variant, stock_number, vin, color_exterior, status
        FROM vehicles
        WHERE model LIKE ? OR trim LIKE ? OR vin LIKE ? OR stock_number LIKE ? OR variant LIKE ?
        ORDER BY
            CASE WHEN model LIKE ? THEN 0
                 WHEN stock_number LIKE ? THEN 1
                 ELSE 2 END,
            model ASC
        LIMIT 8
    ''', [f'%{q}%'] * 5 + [f'{q}%', f'{q}%']).fetchall()
    conn.close()
    return jsonify([dict(r) for r in results])


# ── Inventory ─────────────────────────────────────────────────────────────────

@app.route('/inventory')
def inventory():
    conn = get_db()
    status_filter = request.args.get('status', '')
    search = request.args.get('search', '')
    query = 'SELECT *, CAST((julianday("now") - julianday(created_at)) AS INTEGER) as age_days FROM vehicles WHERE 1=1'
    params = []
    if status_filter:
        query += ' AND status = ?'
        params.append(status_filter)
    if search:
        query += ' AND (model LIKE ? OR trim LIKE ? OR vin LIKE ? OR stock_number LIKE ? OR color_exterior LIKE ?)'
        params.extend([f'%{search}%'] * 5)
    query += ' ORDER BY created_at DESC'
    vehicles_list = conn.execute(query, params).fetchall()
    counts = conn.execute('SELECT status, COUNT(*) as c FROM vehicles GROUP BY status').fetchall()
    stock_counts = {row['status']: row['c'] for row in counts}
    conn.close()
    return render_template('inventory.html', vehicles=vehicles_list,
                           status_filter=status_filter, search=search,
                           stock_counts=stock_counts, now_year=datetime.now().year)


@app.route('/inventory/add', methods=['POST'])
def add_vehicle():
    conn = get_db()
    conn.execute('''
        INSERT INTO vehicles (vin, stock_number, year, make, model, trim, variant,
            color_exterior, color_interior, mileage, cost_price, rrp, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        request.form.get('vin', '').upper().strip(),
        request.form.get('stock_number', '').upper().strip(),
        int(request.form.get('year', datetime.now().year) or datetime.now().year),
        request.form.get('make', 'Hyundai').strip() or 'Hyundai',
        request.form.get('model', '').strip(),
        request.form.get('trim', '').strip(),
        request.form.get('variant', '').strip(),
        request.form.get('color_exterior', '').strip(),
        request.form.get('color_interior', '').strip(),
        int(request.form.get('mileage', 0) or 0),
        float(request.form.get('cost_price', 0) or 0),
        float(request.form.get('rrp', 0) or 0),
        request.form.get('status', 'available'),
        request.form.get('notes', '').strip()
    ))
    conn.commit()
    conn.close()
    flash('Vehicle added to inventory!', 'success')
    return redirect(url_for('inventory'))


@app.route('/inventory/import', methods=['POST'])
def import_vehicles():
    if 'csv_file' not in request.files:
        flash('No file selected.', 'error')
        return redirect(url_for('inventory'))
    f = request.files['csv_file']
    if not f.filename.lower().endswith('.csv'):
        flash('Please upload a .csv file.', 'error')
        return redirect(url_for('inventory'))
    stream = io.StringIO(f.stream.read().decode('utf-8-sig'), newline=None)
    reader = csv.DictReader(stream)
    added, errors = 0, []
    conn = get_db()
    for i, row in enumerate(reader, 1):
        try:
            def g(k1, k2, default=''):
                return str(row.get(k1, row.get(k2, default)) or default).strip()
            conn.execute('''
                INSERT INTO vehicles (vin, stock_number, year, make, model, trim, variant,
                    color_exterior, color_interior, mileage, cost_price, rrp, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                g('VIN', 'vin').upper(),
                g('Stock Number', 'stock_number').upper(),
                int(float(g('Year', 'year', str(datetime.now().year))) or datetime.now().year),
                g('Make', 'make', 'Hyundai') or 'Hyundai',
                g('Model', 'model'),
                g('Trim', 'trim'),
                g('Variant', 'variant'),
                g('Exterior Color', 'color_exterior'),
                g('Interior Color', 'color_interior'),
                int(float(g('Mileage', 'mileage', '0')) or 0),
                float(g('Cost Price', 'cost_price', '0') or 0),
                float(g('RRP', 'rrp', '0') or 0),
                g('Status', 'status', 'available').lower() or 'available',
                g('Notes', 'notes')
            ))
            added += 1
        except Exception as e:
            errors.append(f'Row {i}: {e}')
    conn.commit()
    conn.close()
    if added:
        flash(f'{added} vehicle(s) imported successfully!', 'success')
    if errors:
        flash(f'{len(errors)} row(s) skipped due to errors.', 'error')
    return redirect(url_for('inventory'))


@app.route('/inventory/<int:vid>/update', methods=['POST'])
def update_vehicle(vid):
    conn = get_db()
    conn.execute('''
        UPDATE vehicles SET vin=?, stock_number=?, year=?, make=?, model=?, trim=?, variant=?,
            color_exterior=?, color_interior=?, mileage=?, cost_price=?, rrp=?, status=?, notes=?, updated_at=?
        WHERE id=?
    ''', (
        request.form.get('vin', '').upper().strip(),
        request.form.get('stock_number', '').upper().strip(),
        int(request.form.get('year', datetime.now().year) or datetime.now().year),
        request.form.get('make', 'Hyundai').strip() or 'Hyundai',
        request.form.get('model', '').strip(),
        request.form.get('trim', '').strip(),
        request.form.get('variant', '').strip(),
        request.form.get('color_exterior', '').strip(),
        request.form.get('color_interior', '').strip(),
        int(request.form.get('mileage', 0) or 0),
        float(request.form.get('cost_price', 0) or 0),
        float(request.form.get('rrp', 0) or 0),
        request.form.get('status', 'available'),
        request.form.get('notes', '').strip(),
        datetime.now().isoformat(), vid
    ))
    conn.commit()
    conn.close()
    flash('Vehicle updated!', 'success')
    return redirect(url_for('inventory'))


@app.route('/inventory/<int:vid>/delete', methods=['POST'])
def delete_vehicle(vid):
    conn = get_db()
    conn.execute('DELETE FROM vehicles WHERE id = ?', (vid,))
    conn.commit()
    conn.close()
    flash('Vehicle removed from inventory.', 'success')
    return redirect(url_for('inventory'))


# ── Quotes ────────────────────────────────────────────────────────────────────

@app.route('/quotes')
def quotes():
    conn = get_db()
    status_filter = request.args.get('status', '')
    query = '''
        SELECT q.*, l.name as lead_name, l.email as lead_email,
               v.year as v_year, v.make as v_make, v.model as v_model,
               v.trim as v_trim, v.stock_number as v_stock
        FROM quotes q
        LEFT JOIN leads l ON q.lead_id = l.id
        LEFT JOIN vehicles v ON q.vehicle_id = v.id
        WHERE 1=1
    '''
    params = []
    if status_filter:
        query += ' AND q.status = ?'
        params.append(status_filter)
    query += ' ORDER BY q.created_at DESC'
    all_quotes = conn.execute(query, params).fetchall()
    stats = conn.execute('''
        SELECT COUNT(*) as total,
               SUM(CASE WHEN status="sent" THEN 1 ELSE 0 END) as sent,
               SUM(CASE WHEN status="accepted" THEN 1 ELSE 0 END) as accepted,
               SUM(CASE WHEN status="accepted" THEN selling_price + COALESCE(accessories_price,0) ELSE 0 END) as accepted_value
        FROM quotes
    ''').fetchone()
    all_leads = conn.execute('SELECT id, name, email FROM leads ORDER BY name').fetchall()
    available_vehicles = conn.execute(
        "SELECT * FROM vehicles WHERE status = 'available' ORDER BY year DESC, model"
    ).fetchall()
    conn.close()
    return render_template('quotes.html', quotes=all_quotes, status_filter=status_filter,
                           stats=stats, all_leads=all_leads, available_vehicles=available_vehicles)


@app.route('/quotes/new', methods=['POST'])
def new_quote():
    conn = get_db()
    qnum = generate_quote_number()
    vehicle_id = int(request.form['vehicle_id']) if request.form.get('vehicle_id') else None
    selling_price, cost_price = 0.0, 0.0
    if vehicle_id:
        v = conn.execute('SELECT * FROM vehicles WHERE id = ?', (vehicle_id,)).fetchone()
        if v:
            selling_price = v['rrp'] or 0
            cost_price = v['cost_price'] or 0
    conn.execute('''
        INSERT INTO quotes (quote_number, lead_id, vehicle_id, status,
            selling_price, cost_price, interest_rate, term_months)
        VALUES (?, ?, ?, 'draft', ?, ?, 9.99, 60)
    ''', (qnum, int(request.form['lead_id']) if request.form.get('lead_id') else None,
          vehicle_id, selling_price, cost_price))
    conn.commit()
    new_id = conn.execute('SELECT id FROM quotes WHERE quote_number = ?', (qnum,)).fetchone()['id']
    conn.close()
    flash(f'Quote {qnum} created!', 'success')
    return redirect(url_for('quote_detail', qid=new_id))


@app.route('/quotes/<int:qid>')
def quote_detail(qid):
    conn = get_db()
    quote = conn.execute('''
        SELECT q.*, l.name as lead_name, l.email as lead_email, l.phone as lead_phone,
               v.year as v_year, v.make as v_make, v.model as v_model, v.trim as v_trim,
               v.variant as v_variant, v.stock_number as v_stock, v.vin as v_vin,
               v.color_exterior as v_color_ext, v.color_interior as v_color_int,
               v.rrp as v_rrp, v.mileage as v_mileage
        FROM quotes q
        LEFT JOIN leads l ON q.lead_id = l.id
        LEFT JOIN vehicles v ON q.vehicle_id = v.id
        WHERE q.id = ?
    ''', (qid,)).fetchone()
    if not quote:
        flash('Quote not found.', 'error')
        return redirect(url_for('quotes'))
    all_leads = conn.execute('SELECT id, name, email FROM leads ORDER BY name').fetchall()
    all_vehicles = conn.execute(
        "SELECT * FROM vehicles WHERE status IN ('available','reserved') ORDER BY year DESC, model"
    ).fetchall()
    conn.close()
    return render_template('quote_detail.html', quote=quote, all_leads=all_leads, all_vehicles=all_vehicles)


@app.route('/quotes/<int:qid>/update', methods=['POST'])
def update_quote(qid):
    conn = get_db()
    conn.execute('''
        UPDATE quotes SET
            lead_id=?, vehicle_id=?,
            selling_price=?, discount=?, cost_price=?,
            accessories_cost=?, accessories_price=?,
            trade_in_description=?, trade_in_value=?, trade_in_outstanding=?,
            deposit=?, finance_amount=?, interest_rate=?, term_months=?,
            monthly_payment=?, balloon_payment=?,
            notes=?, internal_notes=?, expires_at=?, updated_at=?
        WHERE id=?
    ''', (
        int(request.form['lead_id']) if request.form.get('lead_id') else None,
        int(request.form['vehicle_id']) if request.form.get('vehicle_id') else None,
        float(request.form.get('selling_price', 0) or 0),
        float(request.form.get('discount', 0) or 0),
        float(request.form.get('cost_price', 0) or 0),
        float(request.form.get('accessories_cost', 0) or 0),
        float(request.form.get('accessories_price', 0) or 0),
        request.form.get('trade_in_description', ''),
        float(request.form.get('trade_in_value', 0) or 0),
        float(request.form.get('trade_in_outstanding', 0) or 0),
        float(request.form.get('deposit', 0) or 0),
        float(request.form.get('finance_amount', 0) or 0),
        float(request.form.get('interest_rate', 9.99) or 9.99),
        int(request.form.get('term_months', 60) or 60),
        float(request.form.get('monthly_payment', 0) or 0),
        float(request.form.get('balloon_payment', 0) or 0),
        request.form.get('notes', ''),
        request.form.get('internal_notes', ''),
        request.form.get('expires_at', ''),
        datetime.now().isoformat(), qid
    ))
    conn.commit()
    conn.close()
    flash('Quote saved!', 'success')
    return redirect(url_for('quote_detail', qid=qid))


@app.route('/quotes/<int:qid>/status', methods=['POST'])
def update_quote_status(qid):
    new_status = request.form.get('status', '')
    if new_status not in ('draft', 'sent', 'accepted', 'declined', 'expired'):
        flash('Invalid status.', 'error')
        return redirect(url_for('quote_detail', qid=qid))
    conn = get_db()
    quote = conn.execute('SELECT * FROM quotes WHERE id = ?', (qid,)).fetchone()
    if not quote:
        conn.close()
        flash('Quote not found.', 'error')
        return redirect(url_for('quotes'))
    now = datetime.now().isoformat()
    sent_at = quote['sent_at'] or (now if new_status == 'sent' else None)
    conn.execute(
        'UPDATE quotes SET status=?, sent_at=?, updated_at=? WHERE id=?',
        (new_status, sent_at, now, qid)
    )
    if quote['lead_id']:
        if new_status == 'sent':
            conn.execute(
                "UPDATE leads SET status='proposal', pipeline_stage='opportunity', updated_at=? "
                "WHERE id=? AND status NOT IN ('closed_won','closed_lost')",
                (now, quote['lead_id'])
            )
        elif new_status == 'accepted':
            conn.execute(
                "UPDATE leads SET status='closed_won', pipeline_stage='customer', updated_at=? WHERE id=?",
                (now, quote['lead_id'])
            )
            if quote['vehicle_id']:
                conn.execute(
                    "UPDATE vehicles SET status='sold', updated_at=? WHERE id=?",
                    (now, quote['vehicle_id'])
                )
        elif new_status == 'declined':
            conn.execute(
                "UPDATE leads SET status='closed_lost', updated_at=? "
                "WHERE id=? AND status NOT IN ('closed_won')",
                (now, quote['lead_id'])
            )
    conn.commit()
    conn.close()
    flash(f'Quote marked as {new_status}.', 'success')
    return redirect(url_for('quote_detail', qid=qid))


@app.route('/quotes/<int:qid>/delete', methods=['POST'])
def delete_quote(qid):
    conn = get_db()
    conn.execute('DELETE FROM quotes WHERE id = ?', (qid,))
    conn.commit()
    conn.close()
    flash('Quote deleted.', 'success')
    return redirect(url_for('quotes'))


@app.route('/quotes/<int:qid>/print')
def print_quote(qid):
    conn = get_db()
    quote = conn.execute('''
        SELECT q.*, l.name as lead_name, l.email as lead_email, l.phone as lead_phone,
               v.year as v_year, v.make as v_make, v.model as v_model, v.trim as v_trim,
               v.variant as v_variant, v.stock_number as v_stock, v.vin as v_vin,
               v.color_exterior as v_color_ext, v.color_interior as v_color_int,
               v.rrp as v_rrp, v.mileage as v_mileage
        FROM quotes q
        LEFT JOIN leads l ON q.lead_id = l.id
        LEFT JOIN vehicles v ON q.vehicle_id = v.id
        WHERE q.id = ?
    ''', (qid,)).fetchone()
    conn.close()
    if not quote:
        return redirect(url_for('quotes'))
    dealer_name = get_setting('smtp_name') or 'Hyundai'
    return render_template('quote_print.html', quote=quote, dealer_name=dealer_name,
                           print_date=datetime.now().strftime('%d %B %Y'))


# ── Test Drives ───────────────────────────────────────────────────────────────

@app.route('/test-drives')
def test_drives():
    conn = get_db()
    upcoming = conn.execute('''
        SELECT td.*, l.name as lead_name, l.phone as lead_phone,
               v.year as v_year, v.make as v_make, v.model as v_model,
               v.trim as v_trim, v.stock_number as v_stock
        FROM test_drives td
        LEFT JOIN leads l ON td.lead_id = l.id
        LEFT JOIN vehicles v ON td.vehicle_id = v.id
        WHERE td.status = "scheduled" AND td.scheduled_date >= date("now")
        ORDER BY td.scheduled_date ASC, td.scheduled_time ASC
    ''').fetchall()
    past = conn.execute('''
        SELECT td.*, l.name as lead_name,
               v.year as v_year, v.make as v_make, v.model as v_model,
               v.trim as v_trim, v.stock_number as v_stock
        FROM test_drives td
        LEFT JOIN leads l ON td.lead_id = l.id
        LEFT JOIN vehicles v ON td.vehicle_id = v.id
        WHERE td.status != "scheduled" OR td.scheduled_date < date("now")
        ORDER BY td.scheduled_date DESC LIMIT 50
    ''').fetchall()
    all_leads = conn.execute('SELECT id, name FROM leads ORDER BY name').fetchall()
    available_vehicles = conn.execute(
        "SELECT * FROM vehicles WHERE status = 'available' ORDER BY year DESC, model"
    ).fetchall()
    today_count = conn.execute(
        'SELECT COUNT(*) as c FROM test_drives WHERE scheduled_date = date("now") AND status = "scheduled"'
    ).fetchone()['c']
    week_count = conn.execute(
        'SELECT COUNT(*) as c FROM test_drives WHERE scheduled_date >= date("now") AND scheduled_date < date("now", "+7 days") AND status = "scheduled"'
    ).fetchone()['c']
    conn.close()
    return render_template('test_drives.html', upcoming=upcoming, past=past,
                           all_leads=all_leads, available_vehicles=available_vehicles,
                           today_count=today_count, week_count=week_count,
                           today=datetime.now().strftime('%Y-%m-%d'))


@app.route('/test-drives/add', methods=['POST'])
def add_test_drive():
    conn = get_db()
    conn.execute('''
        INSERT INTO test_drives (lead_id, vehicle_id, scheduled_date, scheduled_time, notes)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        int(request.form['lead_id']) if request.form.get('lead_id') else None,
        int(request.form['vehicle_id']) if request.form.get('vehicle_id') else None,
        request.form.get('scheduled_date', ''),
        request.form.get('scheduled_time', '10:00'),
        request.form.get('notes', '')
    ))
    conn.commit()
    conn.close()
    flash('Test drive scheduled!', 'success')
    redirect_to = request.form.get('redirect_to', '')
    if redirect_to:
        return redirect(redirect_to)
    return redirect(url_for('test_drives'))


@app.route('/test-drives/<int:tdid>/status', methods=['POST'])
def update_test_drive_status(tdid):
    new_status = request.form.get('status', '')
    if new_status not in ('scheduled', 'completed', 'cancelled', 'no_show'):
        flash('Invalid status.', 'error')
        return redirect(url_for('test_drives'))
    conn = get_db()
    td = conn.execute('SELECT * FROM test_drives WHERE id = ?', (tdid,)).fetchone()
    conn.execute('UPDATE test_drives SET status=?, updated_at=? WHERE id=?',
                 (new_status, datetime.now().isoformat(), tdid))
    if new_status == 'completed' and td and td['lead_id']:
        conn.execute(
            "UPDATE leads SET status='qualified', updated_at=? WHERE id=? AND status IN ('new','contacted')",
            (datetime.now().isoformat(), td['lead_id'])
        )
    conn.commit()
    conn.close()
    flash(f'Test drive marked as {new_status.replace("_", " ")}.', 'success')
    return redirect(request.referrer or url_for('test_drives'))


@app.route('/test-drives/<int:tdid>/delete', methods=['POST'])
def delete_test_drive(tdid):
    conn = get_db()
    conn.execute('DELETE FROM test_drives WHERE id = ?', (tdid,))
    conn.commit()
    conn.close()
    flash('Test drive removed.', 'success')
    return redirect(request.referrer or url_for('test_drives'))


# ── Finance Applications ───────────────────────────────────────────────────────

@app.route('/finance')
def finance():
    conn = get_db()
    status_filter = request.args.get('status', '')
    query = '''
        SELECT fa.*, l.name as lead_name, q.quote_number
        FROM finance_applications fa
        LEFT JOIN leads l ON fa.lead_id = l.id
        LEFT JOIN quotes q ON fa.quote_id = q.id
        WHERE 1=1
    '''
    params = []
    if status_filter:
        query += ' AND fa.status = ?'
        params.append(status_filter)
    query += ' ORDER BY fa.created_at DESC'
    applications = conn.execute(query, params).fetchall()
    stats = conn.execute('''
        SELECT COUNT(*) as total,
               SUM(CASE WHEN status="approved" THEN 1 ELSE 0 END) as approved,
               SUM(CASE WHEN status IN ("pending","submitted") THEN 1 ELSE 0 END) as pending,
               SUM(CASE WHEN status="declined" THEN 1 ELSE 0 END) as declined,
               SUM(CASE WHEN status="approved" THEN approved_amount ELSE 0 END) as total_approved
        FROM finance_applications
    ''').fetchone()
    all_leads = conn.execute('SELECT id, name FROM leads ORDER BY name').fetchall()
    all_quotes = conn.execute('''
        SELECT q.id, q.quote_number, l.name as lead_name
        FROM quotes q LEFT JOIN leads l ON q.lead_id = l.id
        ORDER BY q.created_at DESC
    ''').fetchall()
    conn.close()
    return render_template('finance.html', applications=applications, stats=stats,
                           status_filter=status_filter, all_leads=all_leads, all_quotes=all_quotes)


@app.route('/finance/add', methods=['POST'])
def add_finance_application():
    conn = get_db()
    conn.execute('''
        INSERT INTO finance_applications (lead_id, quote_id, bank, status, application_date,
            approved_amount, interest_rate_offered, term_offered, monthly_offered, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        int(request.form['lead_id']) if request.form.get('lead_id') else None,
        int(request.form['quote_id']) if request.form.get('quote_id') else None,
        request.form.get('bank', ''),
        request.form.get('status', 'pending'),
        request.form.get('application_date', datetime.now().strftime('%Y-%m-%d')),
        float(request.form.get('approved_amount', 0) or 0),
        float(request.form.get('interest_rate_offered', 0) or 0),
        int(request.form.get('term_offered', 0) or 0),
        float(request.form.get('monthly_offered', 0) or 0),
        request.form.get('notes', '')
    ))
    conn.commit()
    conn.close()
    flash('Finance application logged!', 'success')
    return redirect(request.referrer or url_for('finance'))


@app.route('/finance/<int:faid>/update', methods=['POST'])
def update_finance_application(faid):
    conn = get_db()
    conn.execute('''
        UPDATE finance_applications SET bank=?, status=?, application_date=?,
            approved_amount=?, interest_rate_offered=?, term_offered=?,
            monthly_offered=?, notes=?, updated_at=?
        WHERE id=?
    ''', (
        request.form.get('bank', ''),
        request.form.get('status', 'pending'),
        request.form.get('application_date', ''),
        float(request.form.get('approved_amount', 0) or 0),
        float(request.form.get('interest_rate_offered', 0) or 0),
        int(request.form.get('term_offered', 0) or 0),
        float(request.form.get('monthly_offered', 0) or 0),
        request.form.get('notes', ''),
        datetime.now().isoformat(), faid
    ))
    conn.commit()
    conn.close()
    flash('Application updated!', 'success')
    return redirect(request.referrer or url_for('finance'))


@app.route('/finance/<int:faid>/delete', methods=['POST'])
def delete_finance_application(faid):
    conn = get_db()
    conn.execute('DELETE FROM finance_applications WHERE id = ?', (faid,))
    conn.commit()
    conn.close()
    flash('Application removed.', 'success')
    return redirect(request.referrer or url_for('finance'))


if __name__ == '__main__':
    init_db()
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()
    print('\n  LeadFlow CRM is running!')
    print('  Open your browser: http://localhost:5000\n')
    app.run(debug=False, port=5000)
