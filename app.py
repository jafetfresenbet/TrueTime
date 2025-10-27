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

@app.route('/subject/<int:subject_id>/delete', methods=['POST'])
@login_required
def delete_subject(subject_id):
    subj = Subject.query.get_or_404(subject_id)
    user = current_user()
    if subj.cls.admin_user_id != user.id:
        flash("Endast admin kan radera ämnen.")
        return redirect(url_for('view_class', class_id=subj.class_id))
    db.session.delete(subj)
    db.session.commit()
    flash("Ämne raderat.")
    return redirect(url_for('view_class', class_id=subj.class_id))

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
<h1>Registrera</h1>
<form method="post">
  Namn: <input type="text" name="name"><br>
  E-post: <input type="email" name="email"><br>
  Lösenord: <input type="password" name="password"><br>
  <button type="submit">Registrera</button>
</form>
<a href="{{ url_for('login') }}">Redan registrerad? Logga in</a>
"""

LOGIN_TEMPLATE = """
<h1>Logga in</h1>
<form method="post">
  E-post: <input type="email" name="email"><br>
  Lösenord: <input type="password" name="password"><br>
  <button type="submit">Logga in</button>
</form>
<a href="{{ url_for('register') }}">Registrera</a>
"""

DASH_TEMPLATE = """
<h1>Hej {{ user.name }}</h1>
<a href="{{ url_for('logout') }}">Logga ut</a><br>
<h2>Dina klasser</h2>
<ul>
{% for cls in classes %}
  <li><a href="{{ url_for('view_class', class_id=cls.id) }}">{{ cls.name }}</a></li>
{% endfor %}
</ul>
<h2>Kommande uppgifter</h2>
<ul>
{% for a in assignments %}
  <li>{{ a.title }} - {{ a.type }} - {{ a.deadline }}</li>
{% endfor %}
</ul>
<a href="{{ url_for('create_class') }}">Skapa klass</a> | <a href="{{ url_for('join_class') }}">Gå med i klass</a>
"""

CREATE_CLASS_TEMPLATE = """
<h1>Skapa klass</h1>
<form method="post">
  Klassnamn: <input type="text" name="class_name"><br>
  <button type="submit">Skapa</button>
</form>
"""

JOIN_CLASS_TEMPLATE = """
<h1>Gå med i klass</h1>
<form method="post">
  Join-kod: <input type="text" name="join_code"><br>
  <button type="submit">Gå med</button>
</form>
"""

CLASS_TEMPLATE = """
<h1>{{ class_data.name }}</h1>
{% if is_admin %}<form method="post" action="{{ url_for('delete_class', class_id=class_data.id) }}"><button type="submit">Radera klass</button></form>{% endif %}
<h2>Ämnen</h2>
<ul>
{% for s in subjects %}
  <li>{{ s.name }}
      {% if is_admin %}
      <form method="post" action="{{ url_for('delete_subject', subject_id=s.id) }}" style="display:inline">
        <button type="submit">Radera ämne</button>
      </form>
      {% endif %}
  </li>
{% endfor %}
</ul>
{% if is_admin %}
<form method="post" action="{{ url_for('add_subject', class_id=class_data.id) }}">
  Lägg till ämne: <input type="text" name="subject_name"><button type="submit">Lägg till</button>
</form>
{% endif %}
"""

