from flask import Flask, g, render_template_string, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from uuid import uuid4
from datetime import datetime
import os

# ---------- Konfiguration ----------
DATABASE = 'mvp.db'
SECRET_KEY = 'byt-denna-till-nagot-hemligt-i-produkt'  # byt senare
DEBUG = True

app = Flask(__name__)
app.config.from_object(__name__)
app.secret_key = app.config['SECRET_KEY']

from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from datetime import timedelta
import os

# ---------- Database configuration ----------
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')  # Render internal Postgres URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ---------- Session configuration ----------
app.config['SESSION_TYPE'] = 'sqlalchemy'
app.config['SESSION_SQLALCHEMY'] = db
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)
Session(app)

# ---------- Models ----------
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    email = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)

class Class(db.Model):
    __tablename__ = 'classes'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    join_code = db.Column(db.String, unique=True, nullable=False)
    admin_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

class UserClass(db.Model):
    __tablename__ = 'user_classes'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id', ondelete='CASCADE'), primary_key=True)

class Subject(db.Model):
    __tablename__ = 'subjects'
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)
    name = db.Column(db.String, nullable=False)

class Assignment(db.Model):
    __tablename__ = 'assignments'
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    title = db.Column(db.String, nullable=False)
    type = db.Column(db.String, nullable=False)
    deadline = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))

# ---------- Auth hjälpare ----------
def current_user():
    uid = session.get('user_id')
    if not uid:
        return None
    return User.query.get(uid)

def login_user(user):
    session.permanent = True
    session['user_id'] = user.id
    session['user_name'] = user.name

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
    classes = db.session.query(Class).join(UserClass).filter(UserClass.user_id == user.id).all()
    # samla assignments
    assignments = []
    for c in classes:
        a_rows = query_db('''
            SELECT a.*, s.name as subject_name, c.name as class_name FROM assignments a
            JOIN subjects s ON a.subject_id = s.id
            JOIN classes c ON s.class_id = c.id
            WHERE c.id = ?
            order_by(Assignment.deadline.asc())
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
        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("E-post redan registrerad.")
            return redirect(url_for('register'))
        pw_hash = generate_password_hash(pw)
        new_user = User(name=name, email=email, password_hash=pw_hash)
        db.session.add(new_user)
        db.session.commit()
        user = User.query.filter_by(email=email).first()
        login_user(user)
        flash("Registrerad och inloggad!")
        return redirect(url_for('index'))
    return render_template_string(REGISTER_TEMPLATE)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        email = request.form['email'].strip().lower()
        pw = request.form['password']
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, pw):
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
        name = request.form['class_name'].strip()
        if not name:
            flash("Skriv ett klassnamn.")
            return redirect(url_for('create_class'))
        join_code = generate_join_code()
        new_class = Class(name=name, join_code=join_code, admin_user_id=user.id)
        db.session.add(new_class)
        db.session.commit()

        cls = Class.query.filter_by(join_code=join_code).first()
        # add creator as member
        db.session.add(user); db.session.commit()
        flash(f"Klass skapad! Join-kod: {join_code}")
        return redirect(url_for('view_class', class_id=cls['id']))
    return render_template_string(CREATE_CLASS_TEMPLATE)

@app.route('/join_class', methods=['GET','POST'])
@login_required
def join_class():
    user = current_user()
    if request.method=='POST':
        join_code = request.form['join_code'].strip()
        cls = Class.query.filter_by(join_code=join_code).first()
        if not cls:
            flash("Ogiltig kod.")
            return redirect(url_for('join_class'))
        # insert membership if not already
        existing = query_db('SELECT * FROM user_classes WHERE user_id = ? AND class_id = ?', (user.id, cls['id']), one=True)
        if not existing:
            db.session.add(user); db.session.commit()
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
    membership = query_db('SELECT * FROM user_classes WHERE class_id = ? AND user_id = ?', (class_id, user.id), one=True)
    if not membership:
        flash("Du är inte medlem i den här klassen.")
        return redirect(url_for('index'))
    subjects = query_db('SELECT * FROM subjects WHERE class_id = ?', (class_id,))
    # assignments grouped
    assignments = query_db('''
        SELECT a.*, s.name as subject_name FROM assignments a
        JOIN subjects s ON a.subject_id = s.id
        WHERE s.class_id = ?
        order_by(Assignment.deadline.asc())
    ''', (class_id,))
    is_admin = (cls['admin_user_id'] == user.id)
    return render_template_string(CLASS_TEMPLATE, class_data=cls, subjects=subjects, assignments=assignments, is_admin=is_admin)

@app.route('/class/<int:class_id>/delete', methods=['POST'])
@login_required
def delete_class(class_id):
    user = current_user()
    cls = query_db('SELECT * FROM classes WHERE id = ?', (class_id,), one=True)
    if not cls:
        flash("Klass hittades inte.")
        return redirect(url_for('index'))

    if cls['admin_user_id'] != user.id:
        flash("Endast admin kan radera klassen.")
        return redirect(url_for('view_class', class_id=class_id))

    # Radera kopplade ämnen och uppgifter först
    subjects = query_db('SELECT id FROM subjects WHERE class_id = ?', (class_id,))
    for sub in subjects:
        Assignment.query.filter_by(subject_id=sub['id']).delete()
    Subject.query.filter_by(class_id=class_id).delete()
    db.session.commit()

    # Radera medlemskap
    execute_db('DELETE FROM user_classes WHERE class_id = ?', (class_id,))

    # Radera själva klassen
    # Hämta klassen först
    cls = Class.query.get(class_id)
    if cls:
        db.session.delete(cls)
        db.session.commit()


    flash(f"Klassen '{cls['name']}' har raderats.")
    return redirect(url_for('index'))

@app.route('/class/<int:class_id>/edit', methods=['GET','POST'])
@login_required
def edit_class(class_id):
    user = current_user()
    cls = query_db('SELECT * FROM classes WHERE id = ?', (class_id,), one=True)
    if not cls:
        flash("Klass hittades inte.")
        return redirect(url_for('index'))

    if cls['admin_user_id'] != user.id:
        flash("Endast admin kan ändra klassen.")
        return redirect(url_for('view_class', class_id=class_id))

    if request.method == 'POST':
        new_name = request.form['class_name'].strip()
        if not new_name:
            flash("Skriv ett klassnamn.")
            return redirect(url_for('edit_class', class_id=class_id))
        execute_db('UPDATE classes SET name = ? WHERE id = ?', (new_name, class_id))
        flash("Klassnamnet har uppdaterats.")
        return redirect(url_for('view_class', class_id=class_id))

    # GET: visa formulär med förifyllt namn
    return render_template_string("""
    <!doctype html>
    <html lang="sv">
    <head>
        <meta charset="UTF-8">
        <title>Ändra klass - PlugIt+</title>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f4f4f4; display: flex; justify-content: center; align-items: center; height: 100vh; margin:0; }
            .edit-card { background-color: #fff; padding: 30px; border-radius:8px; box-shadow:0px 4px 12px rgba(0,0,0,0.1); width:400px; text-align:center; }
            input[type="text"] { width: 100%; padding:10px; margin: 10px 0 20px 0; border:1px solid #ccc; border-radius:4px; box-sizing:border-box; }
            button { width:100%; padding:10px; background-color:#007bff; color:#fff; border:none; border-radius:4px; cursor:pointer; }
            button:hover { background-color:#0056b3; }
            .back-link { margin-top:15px; display:block; }
            .back-link a { color:#007bff; text-decoration:none; }
            .back-link a:hover { text-decoration:underline; }
        </style>
    </head>
    <body>
        <div class="edit-card">
            <h2>Ändra klassnamn</h2>
            <form method="post">
                <input type="text" name="class_name" value="{{ cls['name'] }}" required>
                <button type="submit">Spara ändringar</button>
            </form>
            <div class="back-link"><a href="{{ url_for('view_class', class_id=cls['id']) }}">Tillbaka till klassen</a></div>
        </div>
    </body>
    </html>
    """, cls=cls)

@app.route('/class/<int:class_id>/leave', methods=['POST'])
@login_required
def leave_class(class_id):
    user = current_user()

    # Hämta klassen
    class_data = query_db('SELECT * FROM classes WHERE id = ?', (class_id,), one=True)
    if not class_data:
        flash("Klassen hittades inte.")
        return redirect(url_for('index'))

    # Admin får inte lämna sin egen klass
    if class_data['admin_user_id'] == user.id:
        flash("Admin kan inte lämna sin egen klass.")
        return redirect(url_for('index'))

    # Ta bort användaren från klassen
    execute_db('DELETE FROM user_classes WHERE class_id = ? AND user_id = ?', (class_id, user.id))
    flash("Du har lämnat klassen.")

    return redirect(url_for('index'))

@app.route('/subject/<int:subject_id>/delete', methods=['POST'])
@login_required
def delete_subject(subject_id):
    user = current_user()
    subject = query_db('SELECT * FROM subjects WHERE id = ?', (subject_id,), one=True)
    if not subject:
        flash("Ämnet hittades inte.")
        return redirect(url_for('index'))

    cls = query_db('SELECT * FROM classes WHERE id = ?', (subject['class_id'],), one=True)
    if cls['admin_user_id'] != user.id:
        flash("Endast admin kan radera ämnen.")
        return redirect(url_for('view_class', class_id=cls['id']))

    # Ta bort tillhörande uppgifter först
    Assignment.query.filter_by(id=assignment_id).delete()
    execute_db('DELETE FROM subjects WHERE id = ?', (subject_id,))
    flash(f"Ämnet '{subject['name']}' har raderats.")
    return redirect(url_for('view_class', class_id=cls['id']))

@app.route('/subject/<int:subject_id>/edit', methods=['GET','POST'])
@login_required
def edit_subject(subject_id):
    user = current_user()
    subject = query_db('SELECT * FROM subjects WHERE id = ?', (subject_id,), one=True)
    if not subject:
        flash("Ämnet hittades inte.")
        return redirect(url_for('index'))

    cls = query_db('SELECT * FROM classes WHERE id = ?', (subject['class_id'],), one=True)
    if cls['admin_user_id'] != user.id:
        flash("Endast admin kan ändra ämnen.")
        return redirect(url_for('view_class', class_id=cls['id']))

    if request.method == 'POST':
        new_name = request.form['name'].strip()
        if not new_name:
            flash("Fyll i ämnesnamn.")
            return redirect(url_for('edit_subject', subject_id=subject_id))
        execute_db('UPDATE subjects SET name = ? WHERE id = ?', (new_name, subject_id))
        flash("Ämnet har uppdaterats.")
        return redirect(url_for('view_class', class_id=cls['id']))

    return render_template_string("""
    <!doctype html>
    <html lang="sv">
    <head>
        <meta charset="UTF-8">
        <title>Ändra ämne - PlugIt+</title>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f4f4f4; display: flex; justify-content: center; align-items: center; height:100vh; margin:0; }
            .edit-card { background-color: #fff; padding: 30px; border-radius:8px; box-shadow:0px 4px 12px rgba(0,0,0,0.1); width:400px; text-align:center; }
            input[type="text"] { width: 100%; padding:10px; margin:10px 0 20px 0; border:1px solid #ccc; border-radius:4px; box-sizing:border-box; }
            button { width:100%; padding:10px; background-color:#007bff; color:#fff; border:none; border-radius:4px; cursor:pointer; }
            button:hover { background-color:#0056b3; }
            .back-link { margin-top:15px; display:block; }
            .back-link a { color:#007bff; text-decoration:none; }
            .back-link a:hover { text-decoration:underline; }
        </style>
    </head>
    <body>
        <div class="edit-card">
            <h2>Ändra ämnesnamn</h2>
            <form method="post">
                <input type="text" name="name" value="{{ subject['name'] }}" required>
                <button type="submit">Spara ändringar</button>
            </form>
            <div class="back-link"><a href="{{ url_for('view_class', class_id=cls['id']) }}">Tillbaka till klassen</a></div>
        </div>
    </body>
    </html>
    """, subject=subject, cls=cls)

@app.route('/assignment/<int:assignment_id>/delete', methods=['POST'])
@login_required
def delete_assignment(assignment_id):
    user = current_user()
    # Hämta uppgiften
    assignment = query_db('SELECT * FROM assignments WHERE id = ?', (assignment_id,), one=True)
    if not assignment:
        flash("Uppgiften hittades inte.")
        return redirect(url_for('index'))
    # Hämta ämnet för kontroll
    subject = query_db('SELECT * FROM subjects WHERE id = ?', (assignment['subject_id'],), one=True)
    class_data = query_db('SELECT * FROM classes WHERE id = ?', (subject['class_id'],), one=True)
    if class_data['admin_user_id'] != user.id:
        flash("Endast admin kan radera uppgifter.")
        return redirect(url_for('index'))
    # Radera
    Assignment.query.filter_by(id=assignment_id).delete()
    flash("Uppgiften har raderats.")
    return redirect(url_for('index'))


@app.route('/assignment/<int:assignment_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_assignment(assignment_id):
    user = current_user()
    assignment = query_db('SELECT * FROM assignments WHERE id = ?', (assignment_id,), one=True)
    if not assignment:
        flash("Uppgiften hittades inte.")
        return redirect(url_for('index'))

    subject = query_db('SELECT * FROM subjects WHERE id = ?', (assignment['subject_id'],), one=True)
    class_data = query_db('SELECT * FROM classes WHERE id = ?', (subject['class_id'],), one=True)
    if class_data['admin_user_id'] != user.id:
        flash("Endast admin kan ändra uppgifter.")
        return redirect(url_for('index'))

    if request.method == 'POST':
        title = request.form['name'].strip()
        deadline = request.form['deadline']
        type_ = request.form['type']
        if not title or not deadline or not type_:
            flash("Fyll i alla fält.")
            return redirect(url_for('edit_assignment', assignment_id=assignment_id))

        execute_db('UPDATE assignments SET title = ?, deadline = ?, type = ? WHERE id = ?',
                   (title, deadline, type_, assignment_id))
        flash("Uppgiften har uppdaterats.")
        return redirect(url_for('view_subject', subject_id=subject['id']))

    # GET: visa samma layout som view_subject, fast med förifyllda värden för denna uppgift
    assignments = query_db('SELECT * FROM assignments WHERE subject_id = ?', (subject['id'],))
    is_admin = class_data['admin_user_id'] == user.id

    return render_template_string("""
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>{{ subject['name'] }} - {{ class_data['name'] }}</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 0; }
        header { background-color: #007bff; color: #fff; padding: 15px 20px; text-align: center; }
        header h2 { margin: 0; }
        .container { display: flex; justify-content: center; padding: 20px; }
        .subject-card { background-color: #fff; width: 600px; border-radius: 8px; box-shadow: 0px 4px 12px rgba(0,0,0,0.1); padding: 20px; }
        h3 { margin-top: 0; color: #333; }
        ul { list-style: none; padding-left: 0; }
        li { background-color: #f8f9fa; margin: 5px 0; padding: 10px; border-radius: 4px; }
        .flash-message { color: red; text-align: center; margin-bottom: 10px; }
        form input[type="text"], form input[type="date"], form select { width: 65%; padding: 8px; margin-right: 5px; border-radius: 4px; border: 1px solid #ccc; }
        form button { padding: 8px 12px; border: none; background-color: #28a745; color: #fff; border-radius: 4px; cursor: pointer; }
        form button:hover { background-color: #218838; }
        .back-link { display: block; text-align: center; margin-top: 15px; }
        .back-link a { color: #007bff; text-decoration: none; }
        .back-link a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <header>
        <h2>{{ subject['name'] }} - {{ class_data['name'] }}</h2>
    </header>

    <div class="container">
        <div class="subject-card">
            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="flash-message">
                  {% for message in messages %}
                    {{ message }}<br>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            <h3>Uppgifter / Inlämningar / Prov</h3>
            <ul>
            {% for a in assignments %}
                <li>{{ a['title'] }} – Deadline: {{ a['deadline'] }} – Typ: {{ a['type'] }}</li>
            {% else %}
                <li>Inga uppgifter tillagda ännu.</li>
            {% endfor %}
            </ul>

            {% if is_admin %}
            <form method="post">
                <input type="text" name="name" value="{{ assignment['title'] }}" required>
                <input type="date" name="deadline" value="{{ assignment['deadline'] }}" required>
                <select name="type" required>
                    <option value="uppgift" {% if assignment['type'] == 'uppgift' %}selected{% endif %}>Uppgift</option>
                    <option value="prov" {% if assignment['type'] == 'prov' %}selected{% endif %}>Prov</option>
                </select>
                <button type="submit">Spara ändringar</button>
            </form>
            {% endif %}

            <div class="back-link">
                <a href="{{ url_for('index', subject_id=subject['id']) }}">Avbryt / Tillbaka</a>
            </div>
        </div>
    </div>
</body>
</html>
    """, assignment=assignment, assignments=assignments, subject=subject, class_data=class_data, is_admin=is_admin)

@app.route('/class/<int:class_id>/add_subject', methods=['POST'])
@login_required
def add_subject(class_id):
    user = current_user()
    cls = query_db('SELECT * FROM classes WHERE id = ?', (class_id,), one=True)
    if not cls:
        flash("Klass finns inte.")
        return redirect(url_for('index'))
    if cls['admin_user_id'] != user.id:
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
    subject = query_db('''
        SELECT s.*, c.name as class_name, c.id as class_id 
        FROM subjects s 
        JOIN classes c ON s.class_id = c.id 
        WHERE s.id = ?
    ''', (subject_id,), one=True)

    if not subject:
        flash("Ämne hittades ej.")
        return redirect(url_for('index'))

    membership = query_db('SELECT * FROM user_classes WHERE class_id = ? AND user_id = ?',
                          (subject['class_id'], user.id), one=True)
    if not membership:
        flash("Du är inte medlem i den här klassen.")
        return redirect(url_for('index'))

    assignments = Assignment.query.filter(
        Assignment.subject_id == subject_id,           # same filter as before
        Assignment.deadline >= datetime.utcnow()       # compare with current time
    ).order_by(Assignment.deadline.asc()).all()

    class_data = {'id': subject['class_id'], 'name': subject['class_name']}
    is_admin = (query_db('SELECT * FROM classes WHERE id = ?', (subject['class_id'],), one=True)['admin_user_id'] ==
                user.id)

    return render_template_string(SUBJECT_TEMPLATE,
                                  subject=subject,
                                  class_data=class_data,
                                  assignments=assignments,
                                  is_admin=is_admin)


@app.route('/subject/<int:subject_id>/add_assignment', methods=['POST'])
@login_required
def add_assignment(subject_id):
    user = current_user()

    # Hämta ämnet
    subject = query_db('SELECT * FROM subjects WHERE id = ?', (subject_id,), one=True)
    if not subject:
        flash("Ämne hittades ej.")
        return redirect(url_for('index'))

    # Hämta klassdata
    class_data = query_db('SELECT * FROM classes WHERE id = ?', (subject['class_id'],), one=True)
    if class_data['admin_user_id'] != user.id:
        flash("Endast admin kan lägga till uppgifter.")
        return redirect(url_for('view_subject', subject_id=subject_id))

    # Läs formulärdata
    title = request.form['name'].strip()  # matchar input name="name"
    deadline = request.form['deadline']
    type_ = request.form['type']

    if not title or not deadline or not type_:
        flash("Fyll i namn, deadline och typ för uppgiften.")
        return redirect(url_for('view_subject', subject_id=subject_id))

    # Lägg till uppgift i databasen
    execute_db('INSERT INTO assignments (subject_id, title, deadline, type, created_by) VALUES (?, ?, ?, ?, ?)',
               (subject_id, title, deadline, type_, user.id))

    flash("Uppgift tillagd.")
    return redirect(url_for('view_subject', subject_id=subject_id))

# ---------- Utils ----------
def generate_join_code():
    return uuid4().hex[:6].upper()

# ---------- Templates (enkla) ----------
HOME_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>PlugIt+ - Hem</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .home-card {
            background-color: #fff;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            width: 400px;
            text-align: center;
        }
        .home-card h1 {
            margin-bottom: 20px;
            color: #333;
        }
        .home-card p {
            margin: 15px 0;
        }
        .home-card a {
            display: inline-block;
            padding: 10px 20px;
            margin: 10px;
            background-color: #007bff;
            color: #fff;
            text-decoration: none;
            border-radius: 4px;
        }
        .home-card a:hover {
            background-color: #0056b3;
        }
    </style>
</head>
<body>
    <div class="home-card">
        <h1>Välkommen till <span style="color:#007bff;">PlugIt+</span> (Beta)</h1>
        <p>Skapa en klass, bjud in klasskompisar och håll koll på uppgifter!</p>
        <a href="{{ url_for('register') }}">Registrera</a>
        <a href="{{ url_for('login') }}">Logga in</a>
    </div>
</body>
</html>
"""


REGISTER_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>Registrera</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .register-card {
            background-color: #fff;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            width: 350px;
        }
        .register-card h2 {
            text-align: center;
            margin-bottom: 20px;
            color: #333;
        }
        .register-card input[type="text"],
        .register-card input[type="email"],
        .register-card input[type="password"] {
            width: 100%;
            padding: 10px;
            margin: 10px 0 20px 0;
            border: 1px solid #ccc;
            border-radius: 4px;
            box-sizing: border-box;
        }
        .register-card button {
            width: 100%;
            padding: 10px;
            background-color: #28a745;
            color: #fff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        .register-card button:hover {
            background-color: #218838;
        }
        .register-card .login-link {
            text-align: center;
            margin-top: 15px;
        }
        .register-card .login-link a {
            color: #007bff;
            text-decoration: none;
        }
        .register-card .login-link a:hover {
            text-decoration: underline;
        }
        .flash-message {
            color: red;
            text-align: center;
            margin-bottom: 10px;
        }
    </style>
</head>
<body>
    <div class="register-card">
        <h2>Registrera</h2>
        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="flash-message">
              {% for message in messages %}
                {{ message }}<br>
              {% endfor %}
            </div>
          {% endif %}
        {% endwith %}
        <form method="post">
            <input type="text" name="name" placeholder="Namn" required>
            <input type="email" name="email" placeholder="E-post" required>
            <input type="password" name="password" placeholder="Lösenord" required>
            <button type="submit">Registrera</button>
        </form>
        <div class="login-link">
            Har du redan konto? <a href="{{ url_for('login') }}">Logga in här</a>
        </div>
    </div>
</body>
</html>
"""


LOGIN_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>Logga in</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .login-card {
            background-color: #fff;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            width: 350px;
        }
        .login-card h2 {
            text-align: center;
            margin-bottom: 20px;
            color: #333;
        }
        .login-card input[type="email"],
        .login-card input[type="password"] {
            width: 100%;
            padding: 10px;
            margin: 10px 0 20px 0;
            border: 1px solid #ccc;
            border-radius: 4px;
            box-sizing: border-box;
        }
        .login-card button {
            width: 100%;
            padding: 10px;
            background-color: #007bff;
            color: #fff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        .login-card button:hover {
            background-color: #0056b3;
        }
        .login-card .register-link {
            text-align: center;
            margin-top: 15px;
        }
        .login-card .register-link a {
            color: #007bff;
            text-decoration: none;
        }
        .login-card .register-link a:hover {
            text-decoration: underline;
        }
        .flash-message {
            color: red;
            text-align: center;
            margin-bottom: 10px;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <h2>Logga in</h2>
        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="flash-message">
              {% for message in messages %}
                {{ message }}<br>
              {% endfor %}
            </div>
          {% endif %}
        {% endwith %}
        <form method="post">
            <input type="email" name="email" placeholder="E-post" required>
            <input type="password" name="password" placeholder="Lösenord" required>
            <button type="submit">Logga in</button>
        </form>
        <div class="register-link">
            Har du inget konto? <a href="{{ url_for('register') }}">Registrera här</a>
        </div>
    </div>
</body>
</html>
"""

DASH_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>PlugIt+ - Översikt</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            margin: 0;
            padding: 0;
        }
        header {
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
        }
        header h2 {
            margin: 0;
        }
        nav {
            display: flex;
            justify-content: center;
            margin: 15px 0;
            gap: 15px;
        }
        nav a {
            text-decoration: none;
            background-color: #28a745;
            color: #fff;
            padding: 10px 15px;
            border-radius: 4px;
        }
        nav a:hover {
            background-color: #218838;
        }
        .container {
            display: flex;
            justify-content: center;
            padding: 20px;
        }
        .dashboard-card {
            background-color: #fff;
            width: 600px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 20px;
        }
        .dashboard-card h3 {
            margin-top: 0;
            color: #333;
        }
        .section {
            margin-bottom: 25px;
        }
        ul {
            list-style: none;
            padding-left: 0;
        }
        li {
            background-color: #f8f9fa;
            margin: 5px 0;
            padding: 10px;
            border-radius: 4px;
        }
        .flash-message {
            color: red;
            text-align: center;
            margin-bottom: 10px;
        }
    </style>
</head>
<body>
    <header>
        <h2>Hej {{ user.name }} — Din översikt</h2>
    </header>
    <nav>
        <a href="{{ url_for('logout') }}">Logga ut</a>
        <a href="{{ url_for('create_class') }}">Skapa klass</a>
        <a href="{{ url_for('join_class') }}">Gå med i klass</a>
    </nav>

    <div class="container">
        <div class="dashboard-card">
            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="flash-message">
                  {% for message in messages %}
                    {{ message }}<br>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            <div class="section">
                <h3>Dina klasser</h3>
                <ul>
                {% for c in classes %}
                    <li style="display: flex; justify-content: space-between; align-items: center; padding: 5px 0;">
                        <span>
                            <a href="{{ url_for('view_class', class_id=c['id']) }}">{{ c['name'] }}</a> 
                            (kod: {{ c['join_code'] }})
                        </span>
                        {% if user.id == c['admin_user_id'] %}
                            <span>
                                <a href="{{ url_for('edit_class', class_id=c['id']) }}">
                                    <button style="background-color: gray; color: white; border: none; padding: 3px 8px; border-radius:4px; margin-left:5px;">Ändra</button>
                                </a>
                                <form method="post" action="{{ url_for('delete_class', class_id=c['id']) }}" style="display:inline;" onsubmit="return confirm('Är du säker på att du vill radera klassen?');">
                                    <button type="submit" style="background-color: red; color: white; border: none; padding: 3px 8px; border-radius:4px; margin-left:3px;">Radera</button>
                                </form>
                            </span>
                        {% else %}
                            <span>
                                <form method="post" action="{{ url_for('leave_class', class_id=c['id']) }}" style="display:inline;" onsubmit="return confirm('Vill du lämna klassen?');">
                                    <button type="submit" style="background-color: orange; color: white; border: none; padding: 3px 8px; border-radius:4px; margin-left:5px;">
                                        Lämna
                                    </button>
                                </form>
                            </span>
                        {% endif %}
                    </li>
                {% else %}
                    <li>Inga klasser ännu.</li>
                {% endfor %}
                </ul>
            </div>

            <div class="section">
                <h3>Kommande uppgifter</h3>
                <ul>
                {% for a in assignments %}
                    <li style="display: flex; justify-content: space-between; align-items: center; padding: 5px 0;">
                        <span>
                            <strong>{{ a['title'] }}</strong> — {{ a['subject_name'] }} ({{ a['class_name'] }})
                            {% if a['deadline'] %} — deadline: {{ a['deadline']|replace('-', '/') }}{% endif %}
                        </span>
                        {% if user.id == a['created_by'] %}
                        <span>
                            <!-- Ändra-knapp -->
                            <a href="{{ url_for('edit_assignment', assignment_id=a['id']) }}">
                                <button style="background-color: gray; color: white; border: none; padding: 3px 8px; border-radius:4px; margin-left:5px;">Ändra</button>
                            </a>
                        
                            <!-- Radera-knapp -->
                            <form method="post" action="{{ url_for('delete_assignment', assignment_id=a['id']) }}" style="display:inline;" onsubmit="return confirm('Är du säker på att du vill radera uppgiften?');">
                                <button type="submit" style="background-color: red; color: white; border: none; padding: 3px 8px; border-radius:4px; margin-left:3px;">Radera</button>
                            </form>
                        </span>
                        {% endif %}
                    </li>
                {% else %}
                    <li>Inga uppgifter hittades.</li>
                {% endfor %}
                </ul>
            </div>
        </div>
    </div>
</body>
</html>
"""


CREATE_CLASS_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>Skapa klass - PlugIt+</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .create-card {
            background-color: #fff;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            width: 400px;
            text-align: center;
        }
        .create-card h2 {
            margin-bottom: 20px;
            color: #333;
        }
        .create-card input[type="text"] {
            width: 100%;
            padding: 10px;
            margin: 10px 0 20px 0;
            border: 1px solid #ccc;
            border-radius: 4px;
            box-sizing: border-box;
        }
        .create-card button {
            width: 100%;
            padding: 10px;
            background-color: #28a745;
            color: #fff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        .create-card button:hover {
            background-color: #218838;
        }
        .flash-message {
            color: red;
            text-align: center;
            margin-bottom: 10px;
        }
        .back-link {
            display: block;
            text-align: center;
            margin-top: 15px;
        }
        .back-link a {
            color: #007bff;
            text-decoration: none;
        }
        .back-link a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="create-card">
        <h2>Skapa ny klass</h2>
        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="flash-message">
              {% for message in messages %}
                {{ message }}<br>
              {% endfor %}
            </div>
          {% endif %}
        {% endwith %}
        <form method="post">
            <input type="text" name="class_name" placeholder="Klassnamn" required>
            <button type="submit">Skapa klass</button>
        </form>
        <div class="back-link">
            <a href="{{ url_for('index') }}">Tillbaka till översikten</a>
        </div>
    </div>
</body>
</html>
"""


JOIN_CLASS_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>Gå med i klass - PlugIt+</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
            margin: 0;
        }
        .join-card {
            background-color: #fff;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            width: 400px;
            text-align: center;
        }
        .join-card h2 {
            margin-bottom: 20px;
            color: #333;
        }
        .join-card input[type="text"] {
            width: 100%;
            padding: 10px;
            margin: 10px 0 20px 0;
            border: 1px solid #ccc;
            border-radius: 4px;
            box-sizing: border-box;
        }
        .join-card button {
            width: 100%;
            padding: 10px;
            background-color: #007bff;
            color: #fff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        .join-card button:hover {
            background-color: #0056b3;
        }
        .flash-message {
            color: red;
            text-align: center;
            margin-bottom: 10px;
        }
        .back-link {
            display: block;
            text-align: center;
            margin-top: 15px;
        }
        .back-link a {
            color: #007bff;
            text-decoration: none;
        }
        .back-link a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="join-card">
        <h2>Gå med i klass</h2>
        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="flash-message">
              {% for message in messages %}
                {{ message }}<br>
              {% endfor %}
            </div>
          {% endif %}
        {% endwith %}
        <form method="post">
            <input type="text" name="join_code" placeholder="Ange join-kod" required>
            <button type="submit">Gå med</button>
        </form>
        <div class="back-link">
            <a href="{{ url_for('index') }}">Tillbaka till översikten</a>
        </div>
    </div>
</body>
</html>
"""


CLASS_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>{{ class_data['name'] }} - PlugIt+</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            margin: 0;
            padding: 0;
        }
        header {
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
        }
        header h2 {
            margin: 0;
        }
        nav {
            display: flex;
            justify-content: center;
            margin: 15px 0;
            gap: 15px;
        }
        nav a, nav form button {
            text-decoration: none;
            background-color: #28a745;
            color: #fff;
            padding: 10px 15px;
            border-radius: 4px;
            border: none;
            cursor: pointer;
        }
        nav a:hover, nav form button:hover {
            background-color: #218838;
        }
        nav form {
            display: inline;
        }
        .container {
            display: flex;
            justify-content: center;
            padding: 20px;
        }
        .class-card {
            background-color: #fff;
            width: 600px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 20px;
        }
        .class-card h3 {
            margin-top: 0;
            color: #333;
        }
        .section {
            margin-bottom: 25px;
        }
        ul {
            list-style: none;
            padding-left: 0;
        }
        li {
            background-color: #f8f9fa;
            margin: 5px 0;
            padding: 10px;
            border-radius: 4px;
        }
        .flash-message {
            color: red;
            text-align: center;
            margin-bottom: 10px;
        }
        form input[type="text"] {
            width: 70%;
            padding: 8px;
            margin-right: 5px;
            border-radius: 4px;
            border: 1px solid #ccc;
        }
        form button {
            padding: 8px 12px;
            border: none;
            background-color: #007bff;
            color: #fff;
            border-radius: 4px;
            cursor: pointer;
        }
        form button:hover {
            background-color: #0056b3;
        }
    </style>
</head>
<body>
    <header>
        <h2>{{ class_data['name'] }}</h2>
        {% if is_admin %}
            <p>Join-kod: {{ class_data['join_code'] }}</p>
        {% endif %}
    </header>

    <nav>
        <a href="{{ url_for('index') }}">Tillbaka till översikten</a>
        {% if is_admin %}
            <!-- Byt ut länk mot formulär med POST -->
            <form method="post" action="{{ url_for('add_subject', class_id=class_data['id']) }}">
                <input type="text" name="name" placeholder="Ämnesnamn" required>
                <button type="submit">Lägg till ämne</button>
            </form>
        {% endif %}
    </nav>

    <div class="container">
        <div class="class-card">
            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="flash-message">
                  {% for message in messages %}
                    {{ message }}<br>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            <div class="section">
                <h3>Ämnen / Kurser</h3>
                <ul>
                {% for subject in subjects %}
                    <li style="display: flex; justify-content: space-between; align-items: center; padding: 5px 0;">
                        <span>
                            <a href="{{ url_for('view_subject', subject_id=subject['id']) }}">{{ subject['name'] }}</a>
                        </span>
                        {% if is_admin %}
                        <span>
                            <a href="{{ url_for('edit_subject', subject_id=subject['id']) }}">
                                <button style="background-color: gray; color: white; border: none; padding: 3px 8px; border-radius:4px;">Ändra</button>
                            </a>
                            <form method="post" action="{{ url_for('delete_subject', subject_id=subject['id']) }}" style="display:inline;" onsubmit="return confirm('Är du säker på att du vill radera ämnet?');">
                                <button type="submit" style="background-color: red; color: white; border: none; padding: 3px 8px; border-radius:4px;">Radera</button>
                            </form>
                        </span>
                        {% endif %}
                    </li>
                {% else %}
                    <li>Inga ämnen tillagda ännu.</li>
                {% endfor %}
                </ul>
            </div>
        </div>
    </div>
</body>
</html>
"""



SUBJECT_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>{{ subject['name'] }} - {{ class_data['name'] }}</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 0; }
        header { background-color: #007bff; color: #fff; padding: 15px 20px; text-align: center; }
        header h2 { margin: 0; }
        .container { display: flex; justify-content: center; padding: 20px; }
        .subject-card { background-color: #fff; width: 600px; border-radius: 8px; box-shadow: 0px 4px 12px rgba(0,0,0,0.1); padding: 20px; }
        h3 { margin-top: 0; color: #333; }
        ul { list-style: none; padding-left: 0; }
        li { background-color: #f8f9fa; margin: 5px 0; padding: 10px; border-radius: 4px; }
        .flash-message { color: red; text-align: center; margin-bottom: 10px; }
        form input[type="text"], form input[type="datetime-local"], form select { width: 65%; padding: 8px; margin-right: 5px; border-radius: 4px; border: 1px solid #ccc; }
        form button { padding: 8px 12px; border: none; background-color: #28a745; color: #fff; border-radius: 4px; cursor: pointer; }
        form button:hover { background-color: #218838; }
        .back-link { display: block; text-align: center; margin-top: 15px; }
        .back-link a { color: #007bff; text-decoration: none; }
        .back-link a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <header>
        <h2>{{ subject['name'] }} - {{ class_data['name'] }}</h2>
    </header>

    <div class="container">
        <div class="subject-card">
            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="flash-message">
                  {% for message in messages %}
                    {{ message }}<br>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            <h3>Uppgifter / Inlämningar / Prov</h3>
            <ul>
            {% for assignment in assignments %}
                <li>{{ assignment['title'] }} – Deadline: {{ assignment['deadline'] }} – Typ: {{ assignment['type'] }}</li>
            {% else %}
                <li>Inga uppgifter tillagda ännu.</li>
            {% endfor %}
            </ul>

            {% if is_admin %}
            <form method="post" action="{{ url_for('add_assignment', subject_id=subject['id']) }}">
                <input type="text" name="name" placeholder="Uppgiftsnamn" required>
                <input type="date" name="deadline" required>
                <select name="type" required>
                    <option value="assignment">Uppgift</option>
                    <option value="exam">Prov</option>
                </select>
                <button type="submit">Lägg till uppgift</button>
            </form>
            {% endif %}

            <div class="back-link">
                <a href="{{ url_for('view_class', class_id=class_data['id']) }}">Tillbaka till klassen</a>
            </div>
        </div>
    </div>
</body>
</html>
"""










