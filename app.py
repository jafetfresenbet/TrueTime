from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
from uuid import uuid4
import os

# ---------- Konfiguration ----------
DATABASE = 'mvp.db'
SECRET_KEY = 'byt-denna-till-nagot-hemligt-i-produkt'
DEBUG = True

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', f'sqlite:///{DATABASE}')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Session
app.config['SESSION_TYPE'] = 'sqlalchemy'
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

db = SQLAlchemy(app)
app.config['SESSION_SQLALCHEMY'] = db
Session(app)

# ---------- Models ----------
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    email = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)
    classes = db.relationship('UserClass', back_populates='user', cascade="all, delete-orphan")

class Class(db.Model):
    __tablename__ = 'classes'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    join_code = db.Column(db.String, unique=True, nullable=False)
    admin_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    subjects = db.relationship('Subject', back_populates='cls', cascade="all, delete-orphan")
    members = db.relationship('UserClass', back_populates='cls', cascade="all, delete-orphan")

class UserClass(db.Model):
    __tablename__ = 'user_classes'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id', ondelete='CASCADE'), primary_key=True)
    user = db.relationship('User', back_populates='classes')
    cls = db.relationship('Class', back_populates='members')

class Subject(db.Model):
    __tablename__ = 'subjects'
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)
    name = db.Column(db.String, nullable=False)
    cls = db.relationship('Class', back_populates='subjects')
    assignments = db.relationship('Assignment', back_populates='subject', cascade="all, delete-orphan")

class Assignment(db.Model):
    __tablename__ = 'assignments'
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    title = db.Column(db.String, nullable=False)
    type = db.Column(db.String, nullable=False)  # ex: 'uppgift' eller 'prov'
    deadline = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    subject = db.relationship('Subject', back_populates='assignments')

# ---------- Auth helpers ----------
def current_user():
    uid = session.get('user_id')
    if uid:
        return User.query.get(uid)
    return None

def login_user(user):
    session.permanent = True
    session['user_id'] = user.id
    session['user_name'] = user.name

def logout_user():
    session.pop('user_id', None)
    session.pop('user_name', None)

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            flash("Du måste logga in först.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ---------- Utils ----------
def generate_join_code():
    return uuid4().hex[:6].upper()

# ---------- Routes ----------
@app.route('/')
def index():
    user = current_user()
    if not user:
        return render_template_string(HOME_TEMPLATE)

    classes = [uc.cls for uc in user.classes]
    assignments = []
    for cls in classes:
        for subj in cls.subjects:
            assignments.extend(subj.assignments)
    assignments.sort(key=lambda x: x.deadline or datetime.max)

    return render_template_string(DASH_TEMPLATE, user=user, classes=classes, assignments=assignments[:50])

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']
        if not name or not email or not password:
            flash("Fyll i alla fält.")
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash("E-post redan registrerad.")
            return redirect(url_for('register'))

        user = User(name=name, email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        login_user(user)
        flash("Registrerad och inloggad!")
        return redirect(url_for('index'))

    return render_template_string(REGISTER_TEMPLATE)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
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

# ---------- Klass-routes ----------
@app.route('/create_class', methods=['GET','POST'])
@login_required
def create_class():
    user = current_user()
    if request.method == 'POST':
        name = request.form['class_name'].strip()
        if not name:
            flash("Skriv ett klassnamn.")
            return redirect(url_for('create_class'))
        join_code = generate_join_code()
        new_class = Class(name=name, join_code=join_code, admin_user_id=user.id)
        db.session.add(new_class)
        db.session.commit()
        # Lägg till admin som medlem
        membership = UserClass(user_id=user.id, class_id=new_class.id)
        db.session.add(membership)
        db.session.commit()
        flash(f"Klass skapad! Join-kod: {join_code}")
        return redirect(url_for('view_class', class_id=new_class.id))
    return render_template_string(CREATE_CLASS_TEMPLATE)

@app.route('/join_class', methods=['GET','POST'])
@login_required
def join_class():
    user = current_user()
    if request.method == 'POST':
        join_code = request.form['join_code'].strip().upper()
        cls = Class.query.filter_by(join_code=join_code).first()
        if not cls:
            flash("Ogiltig kod.")
            return redirect(url_for('join_class'))

        if not UserClass.query.filter_by(user_id=user.id, class_id=cls.id).first():
            db.session.add(UserClass(user_id=user.id, class_id=cls.id))
            db.session.commit()
        flash(f"Gick med i {cls.name}")
        return redirect(url_for('view_class', class_id=cls.id))
    return render_template_string(JOIN_CLASS_TEMPLATE)

@app.route('/class/<int:class_id>')
@login_required
def view_class(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)
    if not any(uc.cls.id == cls.id for uc in user.classes):
        flash("Du är inte medlem i den här klassen.")
        return redirect(url_for('index'))
    subjects = cls.subjects
    is_admin = (cls.admin_user_id == user.id)
    return render_template_string(CLASS_TEMPLATE, class_data=cls, subjects=subjects, is_admin=is_admin)

@app.route('/class/<int:class_id>/delete', methods=['POST'])
@login_required
def delete_class(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)
    if cls.admin_user_id != user.id:
        flash("Endast admin kan radera klassen.")
        return redirect(url_for('view_class', class_id=class_id))
    db.session.delete(cls)
    db.session.commit()
    flash("Klass raderad.")
    return redirect(url_for('index'))

# ---------- Subject routes ----------
@app.route('/class/<int:class_id>/add_subject', methods=['POST'])
@login_required
def add_subject(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)
    if cls.admin_user_id != user.id:
        flash("Endast admin kan lägga till ämnen.")
        return redirect(url_for('view_class', class_id=class_id))
    name = request.form['subject_name'].strip()
    if name:
        subj = Subject(class_id=cls.id, name=name)
        db.session.add(subj)
        db.session.commit()
        flash(f"Ämne '{name}' lagt till.")
    return redirect(url_for('view_class', class_id=cls.id))

# ---------- Assignment routes ----------
@app.route('/subject/<int:subject_id>/add_assignment', methods=['POST'])
@login_required
def add_assignment(subject_id):
    subj = Subject.query.get_or_404(subject_id)
    user = current_user()
    title = request.form['title'].strip()
    type_ = request.form['type'].strip()
    deadline_str = request.form.get('deadline')
    deadline = datetime.strptime(deadline_str, '%Y-%m-%d') if deadline_str else None
    if title and type_:
        assign = Assignment(subject_id=subject_id, title=title, type=type_, deadline=deadline, created_by=user.id)
        db.session.add(assign)
        db.session.commit()
        flash("Uppgift/prov lagt till.")
    return redirect(url_for('view_class', class_id=subj.class_id))

@app.route('/assignment/<int:assignment_id>/delete', methods=['POST'])
@login_required
def delete_assignment(assignment_id):
    assign = Assignment.query.get_or_404(assignment_id)
    user = current_user()
    if assign.subject.cls.admin_user_id != user.id:
        flash("Endast admin kan radera uppgifter.")
        return redirect(url_for('view_class', class_id=assign.subject.class_id))
    db.session.delete(assign)
    db.session.commit()
    flash("Uppgift raderad.")
    return redirect(url_for('view_class', class_id=assign.subject.class_id))

@app.route('/class/<int:class_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_class(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    if cls.admin_user_id != user.id:
        flash("Endast admin kan ändra klassen.")
        return redirect(url_for('view_class', class_id=class_id))

    if request.method == 'POST':
        new_name = request.form['class_name'].strip()
        if not new_name:
            flash("Skriv ett klassnamn.")
            return redirect(url_for('edit_class', class_id=class_id))

        cls.name = new_name
        db.session.commit()
        flash("Klassnamnet har uppdaterats.")
        return redirect(url_for('view_class', class_id=class_id))

    return render_template_string("""
    <h2>Ändra klassnamn</h2>
    <form method="post">
        <input type="text" name="class_name" value="{{ cls.name }}" required>
        <button type="submit">Spara ändringar</button>
    </form>
    """, cls=cls)

@app.route('/class/<int:class_id>/leave', methods=['POST'])
@login_required
def leave_class(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    if cls.admin_user_id == user.id:
        flash("Admin kan inte lämna sin egen klass.")
        return redirect(url_for('index'))

    uc = UserClass.query.filter_by(class_id=class_id, user_id=user.id).first()
    if uc:
        db.session.delete(uc)
        db.session.commit()
        flash("Du har lämnat klassen.")
    else:
        flash("Du är inte medlem i den här klassen.")

    return redirect(url_for('index'))

@app.route('/subject/<int:subject_id>')
@login_required
def view_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    cls = subject.cls
    user = current_user()
    is_admin = (cls.admin_user_id == user.id)
    assignments = subject.assignments
    return render_template_string(SUBJECT_TEMPLATE, subject=subject, class_data=cls, assignments=assignments, is_admin=is_admin)

@app.route('/subject/<int:subject_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_subject(subject_id):
    user = current_user()
    subject = Subject.query.get_or_404(subject_id)
    cls = subject.cls

    if cls.admin_user_id != user.id:
        flash("Endast admin kan ändra ämnen.")
        return redirect(url_for('view_class', class_id=cls.id))

    if request.method == 'POST':
        new_name = request.form['name'].strip()
        if not new_name:
            flash("Fyll i ämnesnamn.")
            return redirect(url_for('edit_subject', subject_id=subject_id))

        subject.name = new_name
        db.session.commit()
        flash("Ämnet har uppdaterats.")
        return redirect(url_for('view_class', class_id=cls.id))

    return render_template_string("""
    <h2>Ändra ämnesnamn</h2>
    <form method="post">
        <input type="text" name="name" value="{{ subject.name }}" required>
        <button type="submit">Spara ändringar</button>
    </form>
    """, subject=subject)

@app.route('/subject/<int:subject_id>/delete', methods=['POST'])
@login_required
def delete_subject(subject_id):
    user = current_user()
    subject = Subject.query.get_or_404(subject_id)
    cls = subject.cls

    if cls.admin_user_id != user.id:
        flash("Endast admin kan radera ämnen.")
        return redirect(url_for('view_class', class_id=cls.id))

    # Delete all assignments in this subject first
    for a in subject.assignments:
        db.session.delete(a)
    db.session.delete(subject)
    db.session.commit()

    flash(f"Ämnet '{subject.name}' har raderats.")
    return redirect(url_for('view_class', class_id=cls.id))

@app.route('/assignment/<int:assignment_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_assignment(assignment_id):
    user = current_user()
    assignment = Assignment.query.get_or_404(assignment_id)
    subject = assignment.subject
    cls = subject.cls

    if cls.admin_user_id != user.id:
        flash("Endast admin kan ändra uppgifter.")
        return redirect(url_for('view_subject', subject_id=subject.id))

    if request.method == 'POST':
        assignment.title = request.form['title'].strip()
        assignment.description = request.form['description'].strip()
        assignment.deadline = datetime.strptime(request.form['deadline'], '%Y-%m-%d').date()
        db.session.commit()
        flash("Uppgiften har uppdaterats.")
        return redirect(url_for('view_subject', subject_id=subject.id))

    return render_template_string("""
    <h2>Ändra uppgift</h2>
    <form method="post">
        <input type="text" name="title" value="{{ assignment.title }}" required><br>
        <textarea name="description">{{ assignment.description }}</textarea><br>
        <input type="date" name="deadline" value="{{ assignment.deadline }}"><br>
        <button type="submit">Spara</button>
    </form>
    """, assignment=assignment)

@app.route('/class/<int:class_id>/delete', methods=['POST'])
@login_required
def delete_class(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    if cls.admin_user_id != user.id:
        flash("Endast admin kan radera klassen.")
        return redirect(url_for('view_class', class_id=class_id))

    # Delete related subjects + assignments
    for subject in cls.subjects:
        for assignment in subject.assignments:
            db.session.delete(assignment)
        db.session.delete(subject)

    # Delete memberships
    UserClass.query.filter_by(class_id=class_id).delete()

    db.session.delete(cls)
    db.session.commit()

    flash(f"Klassen '{cls.name}' har raderats.")
    return redirect(url_for('index'))

# ---------- Templates ----------
# För enkelhet använder jag inline templates. Byt gärna till riktiga filer senare.
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
        <h2>Hej {{ user['name'] }} — Din översikt</h2>
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
                        {% if user['id'] == c['admin_user_id'] %}
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
                        {% if user['id'] == a['created_by'] %}
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




