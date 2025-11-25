# --- Standardbibliotek ---
from datetime import datetime, timedelta
from uuid import uuid4
import os
from functools import wraps

# --- Flask-bibliotek ---
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Response
from flask_migrate import Migrate

# --- Flask-Login (för användarhantering) ---
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer

from apscheduler.schedulers.background import BackgroundScheduler
from flask_rq2 import RQ

from flask_mail import Message
from twilio.rest import Client
from models import Assignment, ClassMember

# ---------- Konfiguration ----------
DATABASE = 'mvp.db'
SECRET_KEY = 'BlirDetTrueTimePåStrawberryArenaNästaÅr?'
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
migrate = Migrate(app, db)
Session(app)

app.config['RQ_REDIS_URL'] = 'redis://localhost:6379/0'
rq = RQ(app)

# Mail-konfiguration
app.config['MAIL_SERVER'] = os.environ.get('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.environ.get('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.environ.get('MAIL_USE_TLS') == 'True'
app.config['MAIL_USE_SSL'] = os.environ.get('MAIL_USE_SSL') == 'True'
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_DEFAULT_SENDER')

mail = Mail(app)

# Serializer för token
serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# ---------- Models ----------
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    email = db.Column(db.String, unique=True, nullable=False)
    phone_number = db.Column(db.String(20))
    password_hash = db.Column(db.String, nullable=False)
    confirmed = db.Column(db.Boolean, default=False)
    confirmation_token = db.Column(db.String(256), nullable=True)
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
    sent_notifications = db.Column(db.String, default="")

# ---------- Auth helpers ----------

def check_threshold(user, value):
    threshold = 100
    if value >= threshold:
        message = f"Hej {user.name}, ditt värde har nått {value}!"
        send_email_job.queue(user.id, "Threshold uppnådd", message)
        send_sms_job.queue(user.id, message)

@rq.job
def send_email_job(user_id, subject, body):
    from models import User
    user = User.query.get(user_id)
    if user:
        msg = Message(subject, recipients=[user.email], body=body)
        mail.send(msg)

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
def compute_days_left(deadline):
    now = datetime.now()
    delta = deadline - now
    return int(delta.total_seconds() // 86400)  # floor to whole days

def check_days_left_threshold(user, assignment):
    if not assignment.deadline:
        return

    thresholds = [14, 7, 3, 1]
    days_left = compute_days_left(assignment.deadline)

    if days_left not in thresholds:
        return

    sent = assignment.sent_notifications.split(",") if assignment.sent_notifications else []
    if str(days_left) in sent:
        return  # already sent

    # build email
    subject = f"Påminnelse: {assignment.title}"
    body = f"Hej {user.name}, det är nu {days_left} dagar kvar för '{assignment.title}'."

    mail.send_message(
        subject=subject,
        recipients=[user.email],
        body=body
    )

    # mark as sent
    sent.append(str(days_left))
    assignment.sent_notifications = ",".join(sent)
    db.session.commit()

def generate_join_code():
    return uuid4().hex[:6].upper()

def send_deadline_notifications():
    assignments = Assignment.query.all()

    for a in assignments:
        if not a.deadline:
            continue

        # Hämta alla användare som är med i klassen
        cls_members = a.subject.cls.members  # lista av ClassUser
        for member in cls_members:
            user = member.user  # säkerställ att det finns en relation till User
            check_days_left_threshold(user, a)

scheduler = BackgroundScheduler()
scheduler.add_job(func=send_deadline_notifications, trigger="interval", hours=1)
scheduler.start()

# ---------- Routes ----------
@app.route('/profile')
@login_required
def profile():
    user = current_user()
    return render_template_string(PROFILE_TEMPLATE, user=user)

@app.route('/profile/edit', methods=['POST'])
@login_required
def edit_profile():
    user = current_user()
    new_name = request.form.get('name', '').strip()
    new_email = request.form.get('email', '').strip()
    new_password = request.form.get('password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    new_phone = request.form.get('phone_number', '').strip()

    if not new_name or not new_email:
        flash("Fyll i både namn och e-post.")
        return redirect(url_for('profile'))

    if new_password:
        if new_password != confirm_password:
            flash("Lösenorden matchar inte.", "error")
            return redirect(url_for('profile'))
        user.password = generate_password_hash(new_password)

    user.name = new_name
    user.email = new_email
    user.phone_number = new_phone
    db.session.commit()
    flash("Dina uppgifter har uppdaterats.")
    return redirect(url_for('profile'))

@app.route('/profile/delete', methods=['POST'])
@login_required
def delete_profile():
    user = current_user()

    # Ta bort klasser där användaren är admin
    for cls in Class.query.filter_by(admin_user_id=user.id).all():
        db.session.delete(cls)
    
    db.session.delete(user)
    db.session.commit()
    logout_user()
    flash("Ditt konto har raderats.")
    return redirect(url_for('index'))

from datetime import datetime

@app.route('/')
def index():
    if not current_user():
        return render_template_string(HOME_TEMPLATE)

    user = current_user()
    
    classes = [uc.cls for uc in user.classes]
    assignments_display = []
    now = datetime.now()
    
    for cls in classes:
        for subj in cls.subjects:
            for a in subj.assignments:
                if a.type == 'Uppgift' and a.deadline and a.deadline < now:
                    continue
                if a.type == 'Prov' and a.deadline and a.deadline.date() < now.date():
                    continue
    
                # Compute days_left for threshold logic (UNCHANGED)
                if a.deadline:
                    days_left_int = compute_days_left(a.deadline)   # used for threshold only
                else:
                    days_left_int = None
                
                # Compute days_left for COLOR (MATCH view_subject)
                if a.deadline:
                    now = datetime.now()
                    delta = a.deadline - now
                    days_left = delta.days + (delta.seconds / 86400)   # float-based
                else:
                    days_left = None
                
                # Color logic (MATCH view_subject)
                if days_left is None:
                    color = "#f8f9fa"
                elif days_left > 14:
                    color = "#44ce1b"
                elif days_left > 7:
                    color = "#bbdb44"
                elif days_left > 3:
                    color = "#fad928"
                elif days_left > 1:
                    color = "#f2a134"
                elif days_left > 0:
                    color = "#e51f1f"
                elif days_left < 0:
                    color = "#6a6af7"  # same as view_subject

                assignments_display.append({
                    'id': a.id,
                    'title': a.title,
                    'type': a.type,
                    'deadline': a.deadline,
                    'subject_name': subj.name,
                    'class_name': cls.name,
                    'created_by': a.created_by,
                    'color': color
                })
    
    assignments_display.sort(key=lambda x: x['deadline'] or datetime.max)
    today = datetime.now().strftime('%Y-%m-%d')
    print("DEBUG current_user =", current_user, type(current_user))
    
    return render_template_string(DASH_TEMPLATE, user=user, classes=classes, assignments=assignments_display[:50], today=today)
@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        # Se till att sessionen är ren
        try:
            db.session.rollback()
        except Exception:
            pass

        name = request.form['name'].strip()
        email = request.form['email'].strip().lower()
        password = request.form['password']

        accept_gdpr = request.form.get('accept_gdpr')
        if not accept_gdpr:
            flash("Du måste acceptera sekretesspolicyn för att registrera dig.")
            return redirect(url_for('register'))

        if not name or not email or not password:
            flash("Fyll i alla fält.")
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash("E-post redan registrerad.")
            return redirect(url_for('register'))

        try:
            # Skapa användare med confirmed=False
            user = User(
                name=name,
                email=email,
                password_hash=generate_password_hash(password),
                confirmed=False
            )

            # Skapa token och spara direkt i objektet
            token = serializer.dumps(user.email, salt='email-confirm')
            user.confirmation_token = token

            # Lägg till och commit
            db.session.add(user)
            db.session.commit()

            # Skicka bekräftelsemail
            confirm_url = url_for('confirm_email', token=token, _external=True)
            msg = Message("Bekräfta din e-post", recipients=[user.email])
            msg.body = f"Hej {user.name},\n\nKlicka på länken för att bekräfta ditt konto:\n{confirm_url}\n\nOm du inte registrerat dig kan du ignorera detta mail."
            mail.send(msg)

            flash("Registrering lyckades! Kontrollera din e-post för att bekräfta ditt konto.")
            return redirect(url_for('login'))

        except Exception as e:
            db.session.rollback()  # viktigt!
            flash(f"Ett fel uppstod vid registrering: {str(e)}")
            return redirect(url_for('register'))

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
        
        if not user.confirmed:
            flash("Du måste bekräfta din e-post innan du kan logga in. Kontrollera din inbox.")
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


@app.route('/confirm/<token>')
def confirm_email(token):
    try:
        email = serializer.loads(token, salt='email-confirm', max_age=3600)  # giltig 1h
    except:
        flash("Bekräftelselänken är ogiltig eller har gått ut.")
        return redirect(url_for('login'))

    user = User.query.filter_by(email=email).first_or_404()
    if user.confirmed:
        flash("Kontot är redan bekräftat.")
    else:
        user.confirmed = True
        db.session.commit()
        flash("Din e-post har bekräftats! Du kan nu logga in.")

    return redirect(url_for('login'))

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

# ---------- Subject routes ----------
@app.route('/class/<int:class_id>/add_subject', methods=['POST'])
@login_required
def add_subject(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    if cls.admin_user_id != user.id:
        flash("Endast admin kan lägga till ämnen.")
        return redirect(url_for('view_class', class_id=class_id))

    name = request.form.get('subject_name', '').strip()
    if not name:
        flash("Du måste skriva ett ämnesnamn.")
        return redirect(url_for('view_class', class_id=class_id))

    subj = Subject(class_id=cls.id, name=name)
    db.session.add(subj)
    db.session.commit()

    flash(f"Ämne '{name}' har lagts till.")
    return redirect(url_for('view_class', class_id=cls.id))

# ---------- Assignment routes ----------
@app.route('/subject/<int:subject_id>/add_assignment', methods=['POST'])
@login_required
def add_assignment(subject_id):
    subj = Subject.query.get_or_404(subject_id)
    user = current_user()

    title = request.form.get('title', '').strip()
    type_ = request.form.get('type', '').strip()
    deadline_str = request.form.get('deadline', '').strip()

    if not title or not type_:
        flash("Du måste fylla i både namn och typ.")
        return redirect(url_for('view_subject', subject_id=subject_id))

    # hantera deadline
    deadline = None
    if deadline_str:
        try:
            if type_ == 'exam':
                deadline = datetime.strptime(deadline_str, '%Y-%m-%d')
            else:  # assignment
                deadline = datetime.strptime(deadline_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash("Fel datumformat.")
            return redirect(url_for('view_subject', subject_id=subject_id))

    assign = Assignment(subject_id=subject_id, title=title, type=type_, deadline=deadline, created_by=user.id)
    db.session.add(assign)
    db.session.commit()
    flash("Uppgift/prov lagt till.")
    return redirect(url_for('view_subject', subject_id=subject_id))

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
    return redirect(url_for('index', class_id=assign.subject.class_id))

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
        return redirect(url_for('index', class_id=class_id))

    return render_template_string("""
<!doctype html>
<html lang="sv">
<head>
    <link rel="icon" type="image/x-icon" href="{{ url_for('static', filename='favicon.ico') }}">
    <meta charset="UTF-8">
    <title>Ändra klass - PlugIt+</title>
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
        <h2>Ändra klass</h2>
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
            <input type="text" name="class_name" value="{{ cls.name }}" required>
            <button type="submit">Ändra klass</button>
        </form>
        <div class="back-link">
            <a href="{{ url_for('index') }}">Tillbaka till översikten</a>
        </div>
    </div>
</body>
</html>
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

from datetime import datetime, timedelta

@app.route('/subject/<int:subject_id>')
@login_required
def view_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    cls = subject.cls
    user = current_user()
    is_admin = (cls.admin_user_id == user.id)
    
    # Samla uppgifter/prov med färg baserat på deadline
    assignments_display = []
    for a in subject.assignments:
        # Standardfärg
        color = "#f8f9fa"
    
        if a.deadline:
            now = datetime.now()
            delta = a.deadline - now
            days_left = delta.days + (delta.seconds / 86400)
            if days_left > 14:
                color = "#44ce1b"  # långt borta
            elif days_left > 7:
                color = "#bbdb44"
            elif days_left > 3:
                color = "#fad928"
            elif days_left > 1:
                color = "#f2a134"
            elif days_left > 0:
                color = "#e51f1f"  # nära deadline
            elif days_left < 0:
                color = "#6a6af7"
    
        assignments_display.append({
            'id': a.id,
            'title': a.title,
            'type': a.type,
            'deadline': a.deadline,
            'created_by': a.created_by,
            'color': color
        })

    # Sortera uppgifter/prov efter deadline (närmast först)
    assignments_display.sort(key=lambda x: x['deadline'] or datetime.max)

    return render_template_string(
        SUBJECT_TEMPLATE,
        subject=subject,
        class_data=cls,
        assignments=assignments_display,
        is_admin=is_admin
    )

@app.route('/subject/<int:subject_id>/edit', methods=['GET','POST'])
@login_required
def edit_subject(subject_id):
    user = current_user()
    subject = Subject.query.get_or_404(subject_id)
    cls = subject.cls

    if cls.admin_user_id != user.id:
        flash("Endast admin kan ändra ämnen.")
        return redirect(url_for('view_class', class_id=cls.id))

    if request.method == 'POST':
        new_name = request.form['subject_name'].strip()
        if not new_name:
            flash("Skriv ett ämnesnamn.")
            return redirect(url_for('edit_subject', subject_id=subject_id))

        subject.name = new_name
        db.session.commit()
        flash("Ämnesnamnet har uppdaterats.")
        return redirect(url_for('view_class', class_id=cls.id))

    return render_template_string("""
    <!doctype html>
    <html lang="sv">
    <head>
        <meta charset="UTF-8">
        <title>Ändra ämne - PlugIt+</title>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f4f4f4; 
                   display: flex; justify-content: center; align-items: center; 
                   height: 100vh; margin:0; }
            .edit-card { background-color: #fff; padding: 30px; border-radius:8px; 
                         box-shadow:0px 4px 12px rgba(0,0,0,0.1); width:400px; 
                         text-align:center; }
            input[type="text"] { width: 100%; padding:10px; margin: 10px 0 20px 0; 
                                 border:1px solid #ccc; border-radius:4px; 
                                 box-sizing:border-box; }
            button { width:100%; padding:10px; background-color:#007bff; color:#fff; 
                     border:none; border-radius:4px; cursor:pointer; }
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
                <input type="text" name="subject_name" value="{{ subject.name }}" required>
                <button type="submit">Spara ändringar</button>
            </form>
            <div class="back-link">
                <a href="{{ url_for('view_class', class_id=cls.id) }}">Tillbaka till klassen</a>
            </div>
        </div>
    </body>
    </html>
    """, subject=subject, cls=cls)

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
    assignment = Assignment.query.get_or_404(assignment_id)
    subj = assignment.subject
    cls = subj.cls
    user = current_user()

    if cls.admin_user_id != user.id:
        flash("Endast admin kan ändra uppgifter/prov.")
        return redirect(url_for('view_class', class_id=cls.id))

    if request.method == 'POST':
        new_title = request.form['title'].strip()
        new_type = request.form['type'].strip()
        deadline_str = request.form.get('deadline')

        if not new_title:
            flash("Fyll i ett namn för uppgiften/provet.")
            return redirect(url_for('edit_assignment', assignment_id=assignment_id))

        if deadline_str:  # Kolla att strängen inte är tom
            try:
                if new_type == 'exam':
                    new_deadline = datetime.strptime(deadline_str, '%Y-%m-%d')
                else:
                    new_deadline = datetime.strptime(deadline_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash("Fel datumformat.")
                return redirect(url_for('edit_assignment', assignment_id=assignment_id))
        else:
            new_deadline = None  # Om tom, sätt deadline till None

        assignment.title = new_title
        assignment.type = new_type
        assignment.deadline = new_deadline
        db.session.commit()

        flash("Uppgiften/provet har uppdaterats.")
        return redirect(url_for('index', subject_id=subj.id))

    # GET → rendera sidan med befintliga värden
    return render_template_string(EDIT_ASSIGNMENT_TEMPLATE,
                                  assignment=assignment,
                                  subject=subj,
                                  class_data=cls,
                                  is_admin=True)

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

@app.route('/privacy')
def privacy_policy():
    return render_template_string("""
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>Sekretesspolicy - PlugIt+</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 0; }
        header { background-color: #007bff; color: #fff; padding: 15px 20px; text-align: center; }
        header h2 { margin: 0; }
        .container { max-width: 800px; margin: 30px auto; padding: 20px; background-color: #fff; border-radius: 8px; box-shadow: 0px 4px 12px rgba(0,0,0,0.1); }
        h3 { color: #007bff; margin-top: 20px; }
        p, li { line-height: 1.6; }
        ul { padding-left: 20px; }
        a { color: #007bff; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <header>
        <h2>Sekretesspolicy - PlugIt+</h2>
    </header>

    <div class="container">
        <p>PlugIt+ värnar om din integritet och följer GDPR. Nedan förklarar vi hur vi samlar in, använder och skyddar dina uppgifter.</p>

        <h3>1. Vilka uppgifter vi samlar in</h3>
        <p>Vi samlar in uppgifter som du själv anger när du registrerar dig eller använder appen: namn, e-postadress, ämnen, uppgifter/prov och klassinformation.</p>

        <h3>2. Varför vi samlar in uppgifter</h3>
        <ul>
            <li>För att du ska kunna skapa och hantera klasser och uppgifter/prov.</li>
            <li>För att hålla reda på vem som är administratör i en klass.</li>
            <li>För att skicka meddelanden om ändringar eller uppdateringar (om vi lägger till meddelandefunktion).</li>
        </ul>

        <h3>3. Hur länge uppgifterna sparas</h3>
        <p>Vi sparar uppgifterna så länge du har ett konto hos oss eller tills du väljer att radera ditt konto.</p>

        <h3>4. Din kontroll över uppgifterna</h3>
        <ul>
            <li>Du kan när som helst ändra namn och e-post i din profil.</li>
            <li>Du kan radera ditt konto, vilket tar bort alla kopplade uppgifter.</li>
            <li>Du kan ladda ner en kopia av dina uppgifter.</li>
        </ul>

        <h3>5. Säkerhet</h3>
        <p>Vi använder säker lagring av lösenord och begränsar åtkomst till uppgifter till dig som användare och administratörer av klasser.</p>

        <h3>6. Kontakt</h3>
        <p>Om du har frågor om dina uppgifter eller vår hantering av dem, kontakta oss via e-post: <a href="mailto:truetimeuf@gmail.com">truetimeuf@gmail.com</a></p>

        <div style="text-align:center; margin-top:20px;">
            <a href="{{ url_for('index') }}">Tillbaka</a>
        </div>
    </div>
</body>
</html>
""")

@app.route('/profile/download')
@login_required
def download_user_data():
    user = current_user()

    data = f"Namn: {user.name}\nE-post: {user.email}\n"

    return Response(
        data,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment;filename={user.name}_data.txt'}
    )

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
            <label>
                <input type="checkbox" name="accept_gdpr" required>
                Jag accepterar <a href="{{ url_for('privacy_policy') }}" target="_blank">sekretesspolicyn</a>.
            </label>
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
            color: green;
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
        <a href="{{ url_for('profile') }}" 
           style="padding: 8px 12px; background-color: #007bff; color: white; border-radius: 4px; text-decoration: none; margin-left: 10px;">
            Min profil
        </a>
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

            <div class="section" style="margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                    <h3 style="margin: 0;">Dina klasser</h3>
                    <span style="font-weight: bold; color: #007bff;">Dagens datum: {{ today }}</span>
                </div>
            
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
                    <li style="display: flex; justify-content: space-between; align-items: center; padding: 10px; border-radius: 6px; background-color: {{ a['color'] }};">
                        <span>
                            <strong>{{ a['title'] }}</strong> — {{ a['subject_name'] }} ({{ a['class_name'] }})
                            {% if a['deadline'] %}
                                {% if a['type'] == 'assignment' %}
                                    — deadline: {{ a['deadline'].strftime('%Y/%m/%d %H:%M') }}
                                {% elif a['type'] == 'exam' %}
                                    — datum: {{ a['deadline'].strftime('%Y/%m/%d') }}
                                {% endif %}
                            {% endif %}
                        </span>
                        {% if user['id'] == a['created_by'] %}
                        <span>
                            <a href="{{ url_for('edit_assignment', assignment_id=a['id']) }}">
                                <button style="background-color: gray; color: white; border: none; padding: 3px 8px; border-radius:4px; margin-left:5px;">Ändra</button>
                            </a>
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

            <div class="section">
                <h3>Integritet & Användarvillkor</h3>
                <a href="{{ url_for('privacy_policy') }}">Visa sekretesspolicy</a><br>
                <a href="{{ url_for('download_user_data') }}">Ladda ner dina uppgifter</a><br>
            </div>

            <div class="section">
                <h3>Följ oss på sociala medier:</h3>
                <a href="https://www.instagram.com/truetimeuf/" target="_blank">Instagram</a><br>
                <a href="https://www.tiktok.com/@truetimeuf" target="_blank">TikTok</a><br>
                <a href="https://www.youtube.com/@TrueTimeUF" target="_blank">YouTube</a><br>
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
            color: green;
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
            color: green;
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
            color: green;
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
                <input type="text" name="subject_name" placeholder="Ämnesnamn" required>
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
        .flash-message { color: green; text-align: center; margin-bottom: 10px; }
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
                <li style="background-color: {{ assignment['color'] }}; margin: 5px 0; padding: 10px; border-radius: 4px;">
                    <strong>{{ assignment['title'] }}</strong> —
                    {% if assignment['deadline'] is not none %}
                        {% if assignment['type'] == 'assignment' %}
                            deadline: {{ assignment['deadline'].strftime('%Y/%m/%d %H:%M') }}
                        {% else %}
                            datum: {{ assignment['deadline'].strftime('%Y/%m/%d') }}
                        {% endif %}
                    {% else %}
                        Ingen deadline
                    {% endif %}
                </li>
            {% else %}
                <li>Inga uppgifter tillagda ännu.</li>
            {% endfor %}
            </ul>

            {% if is_admin %}
            <form method="post" action="{{ url_for('add_assignment', subject_id=subject['id']) }}">
                <input type="text" name="title" placeholder="Uppgiftsnamn" required>
            
                <label for="type">Typ:</label>
                <select name="type" id="type" required onchange="updateDeadlineInput()">
                    <option value="assignment">Uppgift</option>
                    <option value="exam">Prov</option>
                </select>
            
                <input type="datetime-local" name="deadline" id="deadline_input" value="">
            
                <button type="submit">Lägg till uppgift</button>
            </form>
            
            <script>
            function updateDeadlineInput() {
                const typeSelect = document.getElementById('type');
                const deadlineInput = document.getElementById('deadline_input');
                if(typeSelect.value === 'exam') {
                    deadlineInput.type = 'date'; // bara datum
                } else {
                    deadlineInput.type = 'datetime-local'; // datum + tid
                }
            }
            window.onload = updateDeadlineInput;
            </script>
            {% endif %}

            <div class="back-link">
                <a href="{{ url_for('view_class', class_id=class_data['id']) }}">Tillbaka till klassen</a>
            </div>
        </div>
    </div>
</body>
</html>
"""

EDIT_ASSIGNMENT_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>{{ subject.name }} - {{ class_data.name }}</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 0; }
        header { background-color: #007bff; color: #fff; padding: 15px 20px; text-align: center; }
        header h2 { margin: 0; }
        .container { display: flex; justify-content: center; padding: 20px; }
        .subject-card { background-color: #fff; width: 600px; border-radius: 8px; box-shadow: 0px 4px 12px rgba(0,0,0,0.1); padding: 20px; }
        h3 { margin-top: 0; color: #333; }
        ul { list-style: none; padding-left: 0; }
        li { background-color: #f8f9fa; margin: 5px 0; padding: 10px; border-radius: 4px; }
        .flash-message { color: green; text-align: center; margin-bottom: 10px; }
        form input[type="text"], form input[type="date"], form input[type="datetime-local"], form select { width: 65%; padding: 8px; margin-right: 5px; border-radius: 4px; border: 1px solid #ccc; }
        form button { padding: 8px 12px; border: none; background-color: #28a745; color: #fff; border-radius: 4px; cursor: pointer; }
        form button:hover { background-color: #218838; }
        .back-link { display: block; text-align: center; margin-top: 15px; }
        .back-link a { color: #007bff; text-decoration: none; }
        .back-link a:hover { text-decoration: underline; }
        label { display: inline-block; width: 80px; }
    </style>
</head>
<body>
    <header>
        <h2>{{ subject.name }} - {{ class_data.name }}</h2>
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

            <h3>Ändra uppgift / Prov</h3>

            {% if is_admin %}
            <form method="post" action="">
                <input type="text" name="title" value="{{ assignment.title }}" required>

                <label for="type">Typ:</label>
                <select name="type" id="type" required onchange="updateDeadlineInput()">
                    <option value="assignment" {% if assignment.type == 'assignment' %}selected{% endif %}>Uppgift</option>
                    <option value="exam" {% if assignment.type == 'exam' %}selected{% endif %}>Prov</option>
                </select>

                <input 
                    type="{% if assignment.type == 'exam' %}date{% else %}datetime-local{% endif %}" 
                    name="deadline" 
                    id="deadline_input"
                    value="{% if assignment.deadline %}{% if assignment.type == 'exam' %}{{ assignment.deadline.strftime('%Y-%m-%d') }}{% else %}{{ assignment.deadline.strftime('%Y-%m-%dT%H:%M') }}{% endif %}{% else %}{% endif %}"
                >

                <button type="submit">Spara ändringar</button>
            </form>

            <script>
            function updateDeadlineInput() {
                const typeSelect = document.getElementById('type');
                const deadlineInput = document.getElementById('deadline_input');
                if(typeSelect.value === 'exam') {
                    deadlineInput.type = 'date';
                } else {
                    deadlineInput.type = 'datetime-local';
                }
            }
            window.onload = updateDeadlineInput;
            </script>
            {% endif %}

            <div class="back-link">
                <a href="{{ url_for('index', subject_id=subject.id) }}">Avbryt / Tillbaka</a>
            </div>
        </div>
    </div>
</body>
</html>
"""

PROFILE_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>Din profil</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #f4f4f4; margin: 0; padding: 0; }
        header { background-color: #007bff; color: #fff; padding: 15px 20px; text-align: center; }
        header h2 { margin: 0; }
        .container { display: flex; justify-content: center; padding: 20px; }
        .card { background-color: #fff; width: 600px; border-radius: 8px; box-shadow: 0px 4px 12px rgba(0,0,0,0.1); padding: 20px; }
        h3 { margin-top: 0; color: #333; }
        form input[type="text"], form input[type="email"], form input[type="password"] { width: 65%; padding: 8px; margin-bottom: 10px; border-radius: 4px; border: 1px solid #ccc; }
        form button { padding: 8px 12px; border: none; background-color: #28a745; color: #fff; border-radius: 4px; cursor: pointer; margin-right: 5px; }
        form button:hover { background-color: #218838; }
        .delete-btn { background-color: #dc3545; }
        .delete-btn:hover { background-color: #c82333; }
        .flash-message { color: green; text-align: center; margin-bottom: 10px; }
        .back-link { display: block; text-align: center; margin-top: 15px; }
        .back-link a { color: #007bff; text-decoration: none; }
        .back-link a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <header>
        <h2>Din profil</h2>
    </header>

    <div class="container">
        <div class="card">
            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="flash-message">
                  {% for message in messages %}
                    {{ message }}<br>
                  {% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            <h3>Ändra uppgifter</h3>
            <form method="post" action="{{ url_for('edit_profile') }}">
                <input type="text" name="name" value="{{ user.name }}" placeholder="Namn" required>
                <input type="email" name="email" value="{{ user.email }}" placeholder="E-post" required>
                
                <input type="password" name="password" placeholder="Nytt lösenord (lämna tomt om du inte vill byta)">
                <input type="password" name="confirm_password" placeholder="Bekräfta nytt lösenord">

                <button type="submit">Spara ändringar</button>
            
            </form>
            <h3>Radera konto</h3>
            <form method="post" action="{{ url_for('delete_profile') }}">
                <button type="submit" class="delete-btn" onclick="return confirm('Är du säker på att du vill radera ditt konto? Detta går inte att ångra.')">Radera konto</button>
            </form>

            <div class="back-link">
                <a href="{{ url_for('index') }}">Tillbaka till startsidan</a>
            </div>
        </div>
    </div>
</body>
</html>
"""


























































































































