from flask import Flask, g, render_template_string, request, redirect, url_for, session, flash
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
from datetime import datetime

# ---------- Konfiguration ----------
DATABASE = 'mvp.db'
SECRET_KEY = 'byt-denna-till-nagot-hemligt-i-produkt'  # byt senare
DEBUG = True

app = Flask(__name__)
app.config.from_object(__name__)
app.secret_key = app.config['SECRET_KEY']

# ---------- DB-hjälpare ----------
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=()):
    db = get_db()
    cur = db.execute(query, args)
    db.commit()
    cur.close()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# ---------- Init DB ----------
def init_db():
    with app.app_context():
        db = get_db()
        c = db.cursor()
        # Users
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE,
            password_hash TEXT
        )''')
        # Classes
        c.execute('''CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            join_code TEXT UNIQUE,
            admin_user_id INTEGER
        )''')
        # Memberships
        c.execute('''CREATE TABLE IF NOT EXISTS user_classes (
            user_id INTEGER,
            class_id INTEGER,
            PRIMARY KEY (user_id, class_id)
        )''')
        # Subjects
        c.execute('''CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id INTEGER,
            name TEXT
        )''')
        # Assignments
        c.execute('''CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER,
            title TEXT,
            type TEXT,
            deadline TEXT,
            created_by INTEGER
        )''')
        db.commit()

# ---------- Auth hjälpare ----------
def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return query_db('SELECT * FROM users WHERE id = ?', (uid,), one=True)

def login_user(user_row):
    session['user_id'] = user_row['id']
    session['user_name'] = user_row['name']

def logout_user():
    session.pop('user_id', None)
    session.pop('user_name', None)

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            flash("Du måste logga in först.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ---------- Routes ----------
@app.route('/')
def index():
    user = current_user()
    if not user:
        return render_template_string(HOME_TEMPLATE)
    # Visa kommande uppgifter för användarens klasser
    classes = query_db('''
        SELECT c.* FROM classes c
        JOIN user_classes uc ON uc.class_id = c.id
        WHERE uc.user_id = ?
    ''', (user['id'],))
    # samla assignments
    assignments = []
    for c in classes:
        a_rows = query_db('''
            SELECT a.*, s.name as subject_name, c.name as class_name FROM assignments a
            JOIN subjects s ON a.subject_id = s.id
            JOIN classes c ON s.class_id = c.id
            WHERE c.id = ?
            ORDER BY datetime(a.deadline) ASC
            LIMIT 50
        ''', (c['id'],))
        for a in a_rows:
            assignments.append(a)
    return render_template_string(DASH_TEMPLATE, user=user, classes=classes, assignments=assignments)

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        pw = request.form['password']
        if not name or not email or not pw:
            flash("Fyll i alla fält.")
            return redirect(url_for('register'))
        existing = query_db('SELECT * FROM users WHERE email = ?', (email,), one=True)
        if existing:
            flash("E-post redan registrerad.")
            return redirect(url_for('register'))
        pw_hash = generate_password_hash(pw)
        execute_db('INSERT INTO users (name,email,password_hash) VALUES (?,?,?)', (name,email,pw_hash))
        user = query_db('SELECT * FROM users WHERE email = ?', (email,), one=True)
        login_user(user)
        flash("Registrerad och inloggad!")
        return redirect(url_for('index'))
    return render_template_string(REGISTER_TEMPLATE)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        email = request.form['email'].strip().lower()
        pw = request.form['password']
        user = query_db('SELECT * FROM users WHERE email = ?', (email,), one=True)
        if not user or not check_password_hash(user['password_hash'], pw):
            flash("Fel e-post eller lösenord.")
            return redirect(url_for('login'))
        login_user(user)
        flash("Inloggad.")
        return redirect(url_for('index'))
    return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    logout_user()
    flash("Utloggad.")
    return redirect(url_for('index'))

@app.route('/create_class', methods=['GET','POST'])
@login_required
def create_class():
    user = current_user()
    if request.method=='POST':
        name = request.form['name'].strip()
        if not name:
            flash("Skriv ett klassnamn.")
            return redirect(url_for('create_class'))
        join_code = generate_join_code()
        execute_db('INSERT INTO classes (name, join_code, admin_user_id) VALUES (?,?,?)', (name,join_code,user['id']))
        cls = query_db('SELECT * FROM classes WHERE join_code = ?', (join_code,), one=True)
        # add creator as member
        execute_db('INSERT INTO user_classes (user_id,class_id) VALUES (?,?)', (user['id'], cls['id']))
        flash(f"Klass skapad! Join-kod: {join_code}")
        return redirect(url_for('view_class', class_id=cls['id']))
    return render_template_string(CREATE_CLASS_TEMPLATE)

@app.route('/join_class', methods=['GET','POST'])
@login_required
def join_class():
    user = current_user()
    if request.method=='POST':
        code = request.form['code'].strip()
        cls = query_db('SELECT * FROM classes WHERE join_code = ?', (code,), one=True)
        if not cls:
            flash("Ogiltig kod.")
            return redirect(url_for('join_class'))
        # insert membership if not already
        existing = query_db('SELECT * FROM user_classes WHERE user_id = ? AND class_id = ?', (user['id'], cls['id']), one=True)
        if not existing:
            execute_db('INSERT INTO user_classes (user_id,class_id) VALUES (?,?)', (user['id'], cls['id']))
        flash(f"Gick med i {cls['name']}")
        return redirect(url_for('view_class', class_id=cls['id']))
    return render_template_string(JOIN_CLASS_TEMPLATE)

@app.route('/class/<int:class_id>')
@login_required
def view_class(class_id):
    user = current_user()
    cls = query_db('SELECT * FROM classes WHERE id = ?', (class_id,), one=True)
    if not cls:
        flash("Klass hittades ej.")
        return redirect(url_for('index'))
    # kontrollera medlemskap
    membership = query_db('SELECT * FROM user_classes WHERE class_id = ? AND user_id = ?', (class_id, user['id']), one=True)
    if not membership:
        flash("Du är inte medlem i den här klassen.")
        return redirect(url_for('index'))
    subjects = query_db('SELECT * FROM subjects WHERE class_id = ?', (class_id,))
    # assignments grouped
    assignments = query_db('''
        SELECT a.*, s.name as subject_name FROM assignments a
        JOIN subjects s ON a.subject_id = s.id
        WHERE s.class_id = ?
        ORDER BY datetime(a.deadline) ASC
    ''', (class_id,))
    is_admin = (cls['admin_user_id'] == user['id'])
    return render_template_string(CLASS_TEMPLATE, cls=cls, subjects=subjects, assignments=assignments, is_admin=is_admin)

@app.route('/class/<int:class_id>/add_subject', methods=['POST'])
@login_required
def add_subject(class_id):
    user = current_user()
    cls = query_db('SELECT * FROM classes WHERE id = ?', (class_id,), one=True)
    if not cls:
        flash("Klass finns inte.")
        return redirect(url_for('index'))
    if cls['admin_user_id'] != user['id']:
        flash("Endast admin kan lägga till ämnen.")
        return redirect(url_for('view_class', class_id=class_id))
    name = request.form['name'].strip()
    if not name:
        flash("Fyll i ämnesnamn.")
        return redirect(url_for('view_class', class_id=class_id))
    execute_db('INSERT INTO subjects (class_id, name) VALUES (?,?)', (class_id, name))
    flash("Ämne lagt till.")
    return redirect(url_for('view_class', class_id=class_id))

@app.route('/subject/<int:subject_id>')
@login_required
def view_subject(subject_id):
    user = current_user()
    subject = query_db('SELECT s.*, c.name as class_name, c.id as class_id FROM subjects s JOIN classes c ON s.class_id = c.id WHERE s.id = ?', (subject_id,), one=True)
    if not subject:
        flash("Ämne hittades ej.")
        return redirect(url_for('index'))
    membership = query_db('SELECT * FROM user_classes WHERE class_id = ? AND user_id = ?', (subject['class_id'], user['id']), one=True)
    if not membership:
        flash("Du är inte medlem i den här klassen.")
        return redirect(url_for('index'))
    assignments = query_db('SELECT * FROM assignments WHERE subject_id = ? ORDER BY datetime(deadline) ASC', (subject_id,))
    is_admin = (query_db('SELECT * FROM classes WHERE id = ?', (subject['class_id'],), one=True)['admin_user_id'] == user['id'])
    return render_template_string(SUBJECT_TEMPLATE, subject=subject, assignments=assignments, is_admin=is_admin)

@app.route('/subject/<int:subject_id>/add_assignment', methods=['POST'])
@login_required
def add_assignment(subject_id):
    user = current_user()
    subject = query_db('SELECT * FROM subjects WHERE id = ?', (subject_id,), one=True)
    if not subject:
        flash("Ämne finns inte.")
        return redirect(url_for('index'))
    cls = query_db('SELECT * FROM classes WHERE id = ?', (subject['class_id'],), one=True)
    if cls['admin_user_id'] != user['id']:
        flash("Endast admin kan lägga till uppgifter.")
        return redirect(url_for('view_subject', subject_id=subject_id))
    title = request.form['title'].strip()
    type_ = request.form['type'].strip()
    deadline = request.form['deadline'].strip()
    # enkel validering av datum
    try:
        if deadline:
            # ISO-format expected yyyy-mm-dd HH:MM (optional)
            dt = datetime.fromisoformat(deadline)
            ds = dt.isoformat()
        else:
            ds = None
    except Exception:
        flash("Ogiltigt datumformat. Använd ISO-format: YYYY-MM-DD eller YYYY-MM-DDTHH:MM")
        return redirect(url_for('view_subject', subject_id=subject_id))
    execute_db('INSERT INTO assignments (subject_id, title, type, deadline, created_by) VALUES (?,?,?,?,?)', (subject_id, title, type_, ds, user['id']))
    flash("Uppgift skapad.")
    return redirect(url_for('view_subject', subject_id=subject_id))

# ---------- Utils ----------
def generate_join_code():
    return uuid4().hex[:6].upper()

# ---------- Templates (enkla) ----------
HOME_TEMPLATE = """
<!doctype html>
<title>ClassBoard - Hem</title>
<h1>Välkommen till ClassBoard (MVP)</h1>
<p><a href="{{ url_for('register') }}">Registrera</a> eller <a href="{{ url_for('login') }}">Logga in</a>.</p>
<p>Skapa en klass, bjud in klasskompisar med join-kod och börja lägga in uppgifter.</p>
"""

REGISTER_TEMPLATE = """
<!doctype html>
<title>Registrera</title>
<h2>Registrera</h2>
<form method=post>
  Namn: <input name=name><br>
  E-post: <input name=email><br>
  Lösenord: <input type=password name=password><br>
  <button type=submit>Registrera</button>
</form>
<p><a href="{{ url_for('login') }}">Har du redan konto?</a></p>
"""

LOGIN_TEMPLATE = """
<!doctype html>
<title>Login</title>
<h2>Logga in</h2>
<form method=post>
  E-post: <input name=email><br>
  Lösenord: <input type=password name=password><br>
  <button type=submit>Logga in</button>
</form>
<p><a href="{{ url_for('register') }}">Registrera</a></p>
"""

DASH_TEMPLATE = """
<!doctype html>
<title>Översikt</title>
<h2>Hej {{ user['name'] }} — Din översikt</h2>
<p><a href="{{ url_for('logout') }}">Logga ut</a> | <a href="{{ url_for('create_class') }}">Skapa klass</a> | <a href="{{ url_for('join_class') }}">Gå med i klass</a></p>

<h3>Dina klasser</h3>
<ul>
{% for c in classes %}
  <li><a href="{{ url_for('view_class', class_id=c['id']) }}">{{ c['name'] }}</a> (kod: {{ c['join_code'] }})</li>
{% else %}
  <li>Inga klasser ännu.</li>
{% endfor %}
</ul>

<h3>Kommande uppgifter</h3>
<ul>
{% for a in assignments %}
  <li><strong>{{ a['title'] }}</strong> — {{ a['subject_name'] }} ({{ a['class_name'] }}) {% if a['deadline'] %} — deadline: {{ a['deadline'] }}{% endif %}</li>
{% else %}
  <li>Inga uppgifter hittades.</li>
{% endfor %}
</ul>
"""

CREATE_CLASS_TEMPLATE = """
<!doctype html>
<title>Skapa klass</title>
<h2>Skapa klass</h2>
<form method=post>
  Klassnamn: <input name=name><br>
  <button type=submit>Skapa</button>
</form>
<p><a href="{{ url_for('index') }}">Tillbaka</a></p>
"""

JOIN_CLASS_TEMPLATE = """
<!doctype html>
<title>Gå med</title>
<h2>Gå med i klass</h2>
<form method=post>
  Join-kod: <input name=code><br>
  <button type=submit>Gå med</button>
</form>
<p><a href="{{ url_for('index') }}">Tillbaka</a></p>
"""

CLASS_TEMPLATE = """
<!doctype html>
<title>Klass</title>
<h2>Klass: {{ cls['name'] }} (kod: {{ cls['join_code'] }})</h2>
<p><a href="{{ url_for('index') }}">Tillbaka</a></p>
{% if is_admin %}
  <h3>Lägg till ämne</h3>
  <form method=post action="{{ url_for('add_subject', class_id=cls['id']) }}">
    Ämnesnamn: <input name=name><button type=submit>Lägg till</button>
  </form>
{% endif %}

<h3>Ämnen</h3>
<ul>
{% for s in subjects %}
  <li><a href="{{ url_for('view_subject', subject_id=s['id']) }}">{{ s['name'] }}</a></li>
{% else %}
  <li>Inga ämnen ännu.</li>
{% endfor %}
</ul>

<h3>Alla uppgifter</h3>
<ul>
{% for a in assignments %}
  <li>{{ a['title'] }} — {{ a['type'] }} — {{ a['deadline'] }} ({{ a['subject_name'] }})</li>
{% else %}
  <li>Inga uppgifter.</li>
{% endfor %}
</ul>
"""

SUBJECT_TEMPLATE = """
<!doctype html>
<title>Ämne</title>
<h2>Ämne: {{ subject['name'] }} (Klass: {{ subject['class_name'] }})</h2>
<p><a href="{{ url_for('view_class', class_id=subject['class_id']) }}">Tillbaka till klass</a></p>
{% if is_admin %}
  <h3>Lägg till uppgift</h3>
  <form method=post action="{{ url_for('add_assignment', subject_id=subject['id']) }}">
    Titel: <input name=title><br>
    Typ: <input name=type placeholder="prov / inlämning"><br>
    Deadline (ISO t.ex. 2025-10-01T18:00 eller 2025-10-01): <input name=deadline><br>
    <button type=submit>Skapa</button>
  </form>
{% endif %}

<h3>Uppgifter</h3>
<ul>
{% for a in assignments %}
  <li><strong>{{ a['title'] }}</strong> — {{ a['type'] }} {% if a['deadline'] %}— deadline: {{ a['deadline'] }}{% endif %}</li>
{% else %}
  <li>Inga uppgifter.</li>
{% endfor %}
</ul>
"""

# ---------- Starta app ----------
if __name__ == '__main__':
    init_db()
    app.run(debug=DEBUG)