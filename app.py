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

from flask_rq2 import RQ

from flask_mail import Message
from twilio.rest import Client

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
    reset_password_token = db.Column(db.String(256), nullable=True)
    reset_password_expires = db.Column(db.DateTime, nullable=True)
    classes = db.relationship('UserClass', back_populates='user', cascade="all, delete-orphan")
    notifications_enabled = db.Column(db.Boolean, default=True)

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

class AssignmentNotification(db.Model):
    __tablename__ = "assignment_notifications"
    __table_args__ = {"extend_existing": True}  # <--- allows reuse if table already exists

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(db.Integer, db.ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    days_left = db.Column(db.Integer, nullable=False)  # 14, 7, 3, 1
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Optional: unique constraint so we don't get duplicates
    __table_args__ = (
        db.UniqueConstraint('assignment_id', 'user_id', 'days_left', name='_assignment_user_days_uc'),
        {"extend_existing": True}
    )

class ClassMember(db.Model):
    __tablename__ = 'class_members'

    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='member')

    # Relationships (optional but recommended)
    user = db.relationship('User', backref='class_memberships', lazy=True)
    class_obj = db.relationship('Class', backref='memberships', lazy=True)

class Activity(db.Model):
    __tablename__ = 'activity'  # exakt namn från DBeaver

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)  # <-- FK till users
    name = db.Column(db.String(255), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)

    # Relationship
    user = db.relationship('User', backref=db.backref('activities', lazy=True))

# ---------- Auth helpers ----------
def check_days_left_threshold(user, assignment):
    if not assignment.deadline:
        return

    thresholds = [14, 7, 3, 1]
    days_left = compute_days_left(assignment.deadline)

    if days_left not in thresholds:
        return

    # Check if this user already received this threshold
    already_sent = AssignmentNotification.query.filter_by(
        assignment_id=assignment.id,
        user_id=user.id,
        days_left=days_left
    ).first()
    
    if already_sent:
        return  # already sent to this user

    # Send the email
    subject = f"Påminnelse: {assignment.title}"
    body = f"Hej {user.name}, det är nu {days_left + 1} dagar kvar för '{assignment.title}'. Länk: truetime.onrender.com"

    mail.send_message(
        subject=subject,
        recipients=[user.email],
        body=body
    )

    # Record the notification
    new_record = AssignmentNotification(
        assignment_id=assignment.id,
        user_id=user.id,
        days_left=days_left
    )
    db.session.add(new_record)
    db.session.commit()

def send_deadline_notifications():
    assignments = Assignment.query.all()  # fetch all assignments

    for a in assignments:
        if not a.deadline:
            continue

        # Loop through all class members of the assignment's subject
        for uc in a.subject.cls.members:
            user = uc.user

            # Only send notification if the user has notifications enabled
            if user.notifications_enabled:
                check_days_left_threshold(user, a)

def delete_expired_assignments():
    now = datetime.utcnow()

    expired_assignments = Assignment.query.filter(
        Assignment.deadline != None,
        Assignment.deadline < now
    ).all()

    for assignment in expired_assignments:
        db.session.delete(assignment)

    db.session.commit()

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
            flash("Du måste logga in först.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

# ---------- Utils ----------
def compute_days_left(deadline):
    now = datetime.now()
    delta = deadline - now
    return int(delta.total_seconds() // 86400)  # floor to whole days

def generate_join_code():
    return uuid4().hex[:6].upper()

# ---------- Routes ----------
@app.route('/profile')
@login_required
def profile():
    user = current_user()
    return render_template_string(PROFILE_TEMPLATE, user=user)

@app.route('/profile/edit', methods=['POST'])
@login_required
def edit_profile():
    user = current_user()  # ✅ FIXED

    new_name = request.form.get('name', '').strip()
    new_email = request.form.get('email', '').strip()
    new_password = request.form.get('password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    new_phone = request.form.get('phone_number', '').strip()

    # 🔔 notifications checkbox
    notifications_enabled = 'notifications_enabled' in request.form
    user.notifications_enabled = notifications_enabled

    if not new_name or not new_email:
        flash("Fyll i både namn och e-post.", "error")
        return redirect(url_for('profile'))

    # Check if email is already used
    existing_user = User.query.filter_by(email=new_email).first()
    if existing_user and existing_user.id != user.id:
        flash("Denna e-post används redan av en annan användare.", "warning")
        return redirect(url_for('profile'))

    # Password update
    if new_password:
        if new_password != confirm_password:
            flash("Lösenorden matchar inte.", "warning")
            return redirect(url_for('profile'))
        user.password = generate_password_hash(new_password)

    # Update fields
    user.name = new_name
    user.email = new_email
    user.phone_number = new_phone
    user.notifications_enabled = notifications_enabled  # 🔔 NEW

    db.session.commit()
    flash("Dina uppgifter har uppdaterats.", "success")
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

    # Hämta alla medlemskap för denna användare
    memberships = ClassMember.query.filter_by(user_id=user.id).all()
    classes = [m.class_obj for m in memberships if m.class_obj is not None]

    # Förbered klasser med roller
    classes_with_role = []
    for class_obj in classes:
        if not class_obj:
            continue
        membership = next((m for m in memberships if m.class_id == class_obj.id), None)
        role = membership.role if membership else 'member'
        classes_with_role.append({'class': class_obj, 'role': role})

    now = datetime.now()

    # Förbered uppgifter och prov
    combined_items = []
    for item in classes_with_role:
        cls = item['class']
        role = item['role']
        for subj in cls.subjects:
            for a in subj.assignments:
                # Hoppa över uppgifter/prov som passerat deadline
                if a.type == 'Uppgift' and a.deadline and a.deadline < now:
                    continue
                if a.type == 'Prov' and a.deadline and a.deadline.date() < now.date():
                    continue

                # Beräkna färg baserat på deadline
                if a.deadline:
                    delta = a.deadline - now
                    days_left = delta.days + (delta.seconds / 86400)
                else:
                    days_left = None

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
                else:  # overdue
                    color = "#6a6af7"

                combined_items.append({
                    'id': a.id,
                    'title': a.title,
                    'type': 'assignment',  # markera som uppgift/prov
                    'deadline': a.deadline,
                    'subject_name': subj.name,
                    'class_name': cls.name,
                    'class_id': cls.id,
                    'created_by': a.created_by,
                    'color': color,
                    'role': role
                })

    # Hämta alla aktiviteter
    activities = Activity.query.filter_by(user_id=user.id).all()
    for act in activities:
        combined_items.append({
            'id': act.id,
            'type': 'activity',
            'title': act.name,
            'start_time': act.start_time,
            'end_time': act.end_time,
            'role': 'owner',  # alltid admin för egna aktiviteter
            'color': '#cce5ff'  # ljusblå bakgrund
        })

    # Sortera allt efter datum/tid (deadline för uppgifter, starttid för aktiviteter)
    def sort_key(item):
        if item['type'] == 'assignment':
            return item['deadline'] or datetime.max
        else:
            return item['start_time']

    combined_items.sort(key=sort_key)

    delete_expired_assignments()  # radera gamla uppgifter

    today = now.strftime('%Y-%m-%d')

    return render_template_string(
        DASH_TEMPLATE,
        user=user,
        classes=classes_with_role,
        assignments=combined_items[:50],  # begränsa till 50 objekt
        today=today
    )

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
            flash("Du måste acceptera sekretesspolicyn för att registrera dig.", "error")
            return redirect(url_for('register'))

        if not name or not email or not password:
            flash("Fyll i alla fält.", "error")
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash("E-post redan registrerad.", "error")
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

            flash("Registrering lyckades! Kontrollera din e-post för att bekräfta ditt konto.", "success")
            return redirect(url_for('login'))

        except Exception as e:
            db.session.rollback()  # viktigt!
            flash(f"Ett fel uppstod vid registrering: {str(e)}", "error")
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

        # Lägg till admin som medlem med role='admin'
        if not UserClass.query.filter_by(user_id=user.id, class_id=new_class.id).first():
            membership = ClassMember(user_id=user.id, class_id=new_class.id, role='admin')
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

        # Lägg till medlem med role='member', om de inte redan finns
        if not ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first():
            db.session.add(ClassMember(user_id=user.id, class_id=cls.id, role='member'))
            db.session.commit()

        flash(f"Gick med i {cls.name}")
        return redirect(url_for('view_class', class_id=cls.id))
    
    return render_template_string(JOIN_CLASS_TEMPLATE)

@app.route('/class/<int:class_id>')
@login_required
def view_class(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    # Check membership in class_members table
    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if not membership:
        flash("Du är inte medlem i den här klassen.")
        return redirect(url_for('index'))

    subjects = cls.subjects
    is_admin = membership and membership.role == 'admin'
    return render_template_string(CLASS_TEMPLATE, class_data=cls, subjects=subjects, is_admin=is_admin)

# ---------- Subject routes ----------
@app.route('/class/<int:class_id>/add_subject', methods=['POST'])
@login_required
def add_subject(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if not membership or membership.role != 'admin':
        flash("Endast admin kan lägga till ämnen.")
        return redirect(url_for('view_class', class_id=cls.id))

    name = request.form.get('subject_name', '').strip()
    if not name:
        flash("Du måste skriva ett ämnesnamn.")
        return redirect(url_for('view_class', class_id=cls.id))

    subj = Subject(class_id=cls.id, name=name)
    db.session.add(subj)
    db.session.commit()

    flash(f"Ämne '{name}' har lagts till.")
    return redirect(url_for('view_class', class_id=cls.id))

# ---------- Assignment routes ----------
@app.route('/add_assignment/<int:subject_id>', methods=['POST'])
@login_required
def add_assignment(subject_id):
    subj = Subject.query.get_or_404(subject_id)
    cls = subj.cls
    user = current_user()

    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if not membership or membership.role != 'admin':
        flash("Endast admin kan lägga till uppgifter/prov.")
        return redirect(url_for('view_class', class_id=cls.id))

    title = request.form.get('title', '').strip()
    type_ = request.form.get('type', '').strip()
    deadline_str = request.form.get('deadline', '').strip()

    if not title or not type_:
        flash("Du måste fylla i både namn och typ.")
        return redirect(url_for('view_subject', subject_id=subject_id))

    deadline = None
    if deadline_str:
        try:
            if type_ == 'exam':
                deadline = datetime.strptime(deadline_str, '%Y-%m-%d')
            else:
                deadline = datetime.strptime(deadline_str, '%Y-%m-%dT%H:%M')
        except ValueError:
            flash("Fel datumformat.")
            return redirect(url_for('view_subject', subject_id=subject_id))

    assign = Assignment(subject_id=subject_id, title=title, type=type_, deadline=deadline, created_by=user.id)
    db.session.add(assign)
    db.session.commit()
    flash("Uppgift/prov lagt till.")
    return redirect(url_for('view_subject', subject_id=subject_id))

@app.route('/delete_assignment/<int:assignment_id>', methods=['POST'])
@login_required
def delete_assignment(assignment_id):
    assign = Assignment.query.get_or_404(assignment_id)
    cls = assign.subject.cls
    user = current_user()

    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if not membership or membership.role != 'admin':
        flash("Endast admin kan radera uppgifter.")
        return redirect(url_for('view_class', class_id=cls.id))

    db.session.delete(assign)
    db.session.commit()
    flash("Uppgift raderad.")
    return redirect(url_for('index', class_id=cls.id))

@app.route('/edit_class/<int:class_id>', methods=['GET', 'POST'])
@login_required
def edit_class(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if not membership or membership.role != 'admin':
        flash("Endast admin kan ändra klassen.")
        return redirect(url_for('index', class_id=cls.id))

    if request.method == 'POST':
        new_name = request.form['class_name'].strip()
        if not new_name:
            flash("Skriv ett klassnamn.")
            return redirect(url_for('edit_class', class_id=class_id))
        cls.name = new_name
        db.session.commit()
        flash("Klassnamnet har uppdaterats.")
        return redirect(url_for('index', class_id=cls.id))

    return render_template_string(EDIT_CLASS_TEMPLATE, cls=cls)

@app.route('/class/<int:class_id>/leave', methods=['POST'])
@login_required
def leave_class(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    # Admin cannot leave their own class
    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if membership and membership.role == 'admin':
        flash("Admin kan inte lämna sin egen klass.")
        return redirect(url_for('index'))

    if membership:
        db.session.delete(membership)
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
    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    is_admin = membership and membership.role == 'admin'
    
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

@app.route('/edit_subject/<int:subject_id>', methods=['GET', 'POST'])
@login_required
def edit_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    cls = subject.cls
    user = current_user()

    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if not membership or membership.role != 'admin':
        flash("Endast admin kan ändra ämnen.")
        return redirect(url_for('index', class_id=cls.id))

    if request.method == 'POST':
        new_name = request.form['subject_name'].strip()
        if not new_name:
            flash("Skriv ett ämnesnamn.")
            return redirect(url_for('edit_subject', subject_id=subject_id))

        subject.name = new_name
        db.session.commit()
        flash("Ämnesnamnet har uppdaterats.")
        return redirect(url_for('index', class_id=cls.id))

    return render_template_string(EDIT_SUBJECT_TEMPLATE, subject=subject, cls=cls)

@app.route('/delete_subject/<int:subject_id>', methods=['POST'])
@login_required
def delete_subject(subject_id):
    subject = Subject.query.get_or_404(subject_id)
    cls = subject.cls
    user = current_user()

    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if not membership or membership.role != 'admin':
        flash("Endast admin kan radera ämnen.")
        return redirect(url_for('view_class', class_id=cls.id))

    for assignment in subject.assignments:
        db.session.delete(assignment)
    db.session.delete(subject)
    db.session.commit()

    flash(f"Ämnet '{subject.name}' har raderats.")
    return redirect(url_for('view_class', class_id=cls.id))

@app.route('/edit_assignment/<int:assignment_id>', methods=['GET', 'POST'])
@login_required
def edit_assignment(assignment_id):
    assignment = Assignment.query.get_or_404(assignment_id)
    subj = assignment.subject
    cls = subj.cls
    user = current_user()

    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if not membership or membership.role != 'admin':
        flash("Endast admin kan ändra uppgifter/prov.")
        return redirect(url_for('index', class_id=cls.id))

    if request.method == 'POST':
        new_title = request.form['title'].strip()
        new_type = request.form['type'].strip()
        deadline_str = request.form.get('deadline')

        if not new_title:
            flash("Fyll i ett namn för uppgiften/provet.")
            return redirect(url_for('edit_assignment', assignment_id=assignment_id))

        new_deadline = None
        if deadline_str:
            try:
                if new_type == 'exam':
                    new_deadline = datetime.strptime(deadline_str, '%Y-%m-%d')
                else:
                    new_deadline = datetime.strptime(deadline_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash("Fel datumformat.")
                return redirect(url_for('index', assignment_id=assignment_id))

        assignment.title = new_title
        assignment.type = new_type
        assignment.deadline = new_deadline
        db.session.commit()
        flash("Uppgiften/provet har uppdaterats.")
        return redirect(url_for('index', subject_id=subj.id))

    return render_template_string(EDIT_ASSIGNMENT_TEMPLATE,
                                  assignment=assignment,
                                  subject=subj,
                                  class_data=cls,
                                  is_admin=True)

@app.route('/delete_class/<int:class_id>', methods=['POST'])
@login_required
def delete_class(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    membership = ClassMember.query.filter_by(user_id=user.id, class_id=cls.id).first()
    if not membership or membership.role != 'admin':
        flash("Endast admin kan radera klassen.")
        return redirect(url_for('index', class_id=cls.id))

    for subject in cls.subjects:
        for assignment in subject.assignments:
            db.session.delete(assignment)
        db.session.delete(subject)

    ClassMember.query.filter_by(class_id=cls.id).delete()
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

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #f4f4f4 0%, #e0e7ff 100%);
            margin: 0;
            padding: 0;
            min-height: 100vh;
        }
        header {
            background-color: #007bff;
            color: #fff;
            padding: 20px 0;
            text-align: center;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
        }
        header h2 {
            margin: 0;
        }
        .container {
            max-width: 900px;
            margin: 30px auto;
            padding: 25px 30px;
            background-color: #fff;
            border-radius: 12px;
            box-shadow: 0 8px 20px rgba(0,0,0,0.12);
        }
        h3 {
            color: #007bff;
            margin-top: 25px;
        }
        p, li {
            line-height: 1.7;
            color: #333;
        }
        ul {
            padding-left: 20px;
        }
        a {
            color: #007bff;
            text-decoration: none;
        }
        a:hover {
            text-decoration: underline;
        }
        .back-link {
            text-align: center;
            margin-top: 25px;
        }
        .back-link a {
            display: inline-block;
            padding: 10px 20px;
            background-color: #007bff;
            color: white;
            border-radius: 6px;
            text-decoration: none;
            transition: background-color 0.2s;
        }
        .back-link a:hover {
            background-color: #0056b3;
        }
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

        <div class="back-link">
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

@app.route('/class/<int:class_id>/add_admin', methods=['GET', 'POST'])
@login_required
def add_admin_request(class_id):
    user = current_user()
    cls = Class.query.get_or_404(class_id)

    # Check current user's role in this class
    membership = ClassMember.query.filter_by(class_id=class_id, user_id=user.id).first()
    if not membership or membership.role != 'admin':
        flash("Endast admin kan bjuda in andra admins.", "error")
        return redirect(url_for('view_class', class_id=class_id))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        target_user = User.query.filter_by(email=email).first()

        if not target_user:
            flash("Det finns ingen användare med den e-posten.", "error")
            return redirect(url_for('add_admin_request', class_id=class_id))

        target_membership = ClassMember.query.filter_by(class_id=class_id, user_id=target_user.id).first()
        if not target_membership:
            flash("Användaren är inte medlem i klassen.", "error")
            return redirect(url_for('add_admin_request', class_id=class_id))

        if target_membership.role == 'admin':
            flash("Användaren är redan admin.", "info")
            return redirect(url_for('view_class', class_id=class_id))

        # For now, directly make them admin (later you can generate a token/link)
        target_membership.role = 'admin'
        db.session.commit()
        flash(f"{target_user.name} är nu admin i klassen!", "success")
        return redirect(url_for('view_class', class_id=class_id))

    return render_template_string(INVITE_ADMIN_TEMPLATE, cls=cls)

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        user = User.query.filter_by(email=email).first()

        # Säkerhet: avslöja inte om mail finns
        if user:
            token = serializer.dumps(user.email, salt='reset-password')
            user.reset_password_token = token
            user.reset_password_expires = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()

            reset_url = url_for('reset_password', token=token, _external=True)

            msg = Message(
                "Återställ ditt lösenord",
                recipients=[user.email]
            )
            msg.body = f"""
Hej {user.name},

Du har begärt att återställa ditt lösenord.

Klicka på länken nedan (giltig i 1 timme):
{reset_url}

Om du inte begärde detta kan du ignorera mailet.
"""
            mail.send(msg)

        flash("Om kontot finns har vi skickat instruktioner till din e-post.", "info")
        return redirect(url_for('login'))

    return render_template_string(FORGOT_PASSWORD_TEMPLATE)


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt='reset-password', max_age=3600)
    except:
        flash("Länken är ogiltig eller har gått ut.", "error")
        return redirect(url_for('login'))

    user = User.query.filter_by(email=email, reset_password_token=token).first()
    if not user or user.reset_password_expires < datetime.utcnow():
        flash("Länken är ogiltig eller har gått ut.", "error")
        return redirect(url_for('login'))

    if request.method == 'POST':
        password = request.form.get('password')
        confirm = request.form.get('confirm_password')

        if not password or password != confirm:
            flash("Lösenorden matchar inte.", "warning")
            return redirect(request.url)

        user.password_hash = generate_password_hash(password)
        user.reset_password_token = None
        user.reset_password_expires = None
        db.session.commit()

        flash("Ditt lösenord har uppdaterats. Du kan nu logga in.", "success")
        return redirect(url_for('login'))

    return render_template_string(RESET_PASSWORD_TEMPLATE)

@app.route('/class/<int:class_id>/leave-admin', methods=['POST'])
@login_required
def leave_admin(class_id):
    user = current_user()

    membership = ClassMember.query.filter_by(
        class_id=class_id,
        user_id=user.id
    ).first_or_404()

    if membership.role != 'admin':
        flash("Du är inte admin i denna klass.", "error")
        return redirect(url_for('view_class', class_id=class_id))

    admin_count = ClassMember.query.filter_by(
        class_id=class_id,
        role='admin'
    ).count()

    if admin_count <= 1:
        flash("Du är sista adminen och kan inte lämna adminrollen.", "error")
        return redirect(url_for('view_class', class_id=class_id))

    membership.role = 'member'
    db.session.commit()

    flash("Du är inte längre admin i klassen.", "success")
    return redirect(url_for('view_class', class_id=class_id))

@app.route('/activity/create', methods=['GET', 'POST'])
@login_required
def create_activity():
    if request.method == 'POST':
        name = request.form.get('activity_name', '').strip()
        start_str = request.form.get('start_time')
        end_str = request.form.get('end_time')

        if not name or not start_str or not end_str:
            flash("Fyll i alla fält.", "error")
            return redirect(url_for('create_activity'))

        try:
            start_time = datetime.fromisoformat(start_str)
            end_time = datetime.fromisoformat(end_str)
        except ValueError:
            flash("Fel format på datum/tid.", "error")
            return redirect(url_for('create_activity'))

        if start_time >= end_time:
            flash("Starttid måste vara före sluttid.", "warning")
            return redirect(url_for('create_activity'))

        activity = Activity(
            user_id=current_user().id,
            name=name,
            start_time=start_time,
            end_time=end_time
        )

        db.session.add(activity)
        db.session.commit()
        flash("Aktivitet skapad!", "success")
        return redirect(url_for('index'))

    return render_template_string(CREATE_ACTIVITY_TEMPLATE)


# ---------- Templates ----------
# För enkelhet använder jag inline templates. Byt gärna till riktiga filer senare.
HOME_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <!-- SEO: Title och description -->
    <title>PlugIt+ – Digital studieplanerare för elever</title>
    <meta name="description" content="PlugIt+ hjälper elever att planera sina studier, skapa klasser och hålla koll på uppgifter, prov och deadlines – allt på ett ställe.">

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #6a11cb, #2575fc);
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            box-sizing: border-box;
            margin: 0;
        }

        .home-card {
            background-color: #ffffffee;
            padding: 45px 35px;
            border-radius: 14px;
            box-shadow: 0 12px 24px rgba(0, 0, 0, 0.2);
            width: 420px;
            text-align: center;
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }

        .home-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 16px 32px rgba(0, 0, 0, 0.25);
        }

        .home-card h1 {
            margin-bottom: 15px;
            color: #333;
            font-weight: 600;
            letter-spacing: 0.5px;
        }

        .brand {
            color: #2575fc;
            font-weight: 700;
        }

        .home-card p {
            margin: 20px 0 30px 0;
            color: #555;
            font-size: 1.05em;
            line-height: 1.5;
        }

        .home-actions {
            display: flex;
            justify-content: center;
            gap: 15px;
            flex-wrap: wrap;
        }

        .home-actions a {
            padding: 12px 22px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 500;
            font-size: 1em;
            transition: background-color 0.3s, transform 0.2s, box-shadow 0.2s;
        }

        .btn-primary {
            background-color: #2575fc;
            color: white;
        }

        .btn-primary:hover {
            background-color: #145bdc;
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.2);
        }

        .btn-secondary {
            background-color: #e9ecef;
            color: #333;
        }

        .btn-secondary:hover {
            background-color: #dfe3e7;
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(0,0,0,0.15);
        }

        .beta {
            display: inline-block;
            margin-left: 8px;
            font-size: 0.6em;
            padding: 3px 6px;
            border-radius: 6px;
            background-color: #ffc107;
            color: #333;
            vertical-align: middle;
            font-weight: 600;
        }

        @media (max-width: 600px) {
            body { padding: 20px; height: auto; }
            .home-card { width: 100%; padding: 30px 20px; }
            .home-card h1 { font-size: 1.4em; }
            .home-card p { font-size: 1em; }
            .home-actions { flex-direction: column; gap: 12px; }
            .home-actions a { width: 100%; text-align: center; padding: 14px 0; }
        }
    </style>
</head>
<body>
    <div class="home-card">
        <h1>
            Välkommen till <span class="brand">PlugIt+</span>
            <span class="beta">BETA</span>
        </h1>

        <p>
            PlugIt+ är en digital studieplanerare som hjälper elever att planera sina studier, skapa klasser och bjuda in klasskompisar. Håll koll på uppgifter, prov och deadlines – allt på ett ställe, enkelt och överskådligt.
        </p>

        <p>
            Vårt mål är att göra studier mer organiserade och att ge elever en plattform där de kan samarbeta och hålla motivationen uppe.
        </p>

        <div class="home-actions">
            <a href="{{ url_for('register') }}" class="btn-primary">Registrera</a>
            <a href="{{ url_for('login') }}" class="btn-secondary">Logga in</a>
        </div>
    </div>
</body>
</html>
"""

REGISTER_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">

    <!-- ✅ ADDED -->
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>Registrera</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">
    
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #6a11cb, #2575fc);
            display: flex;
            justify-content: center;
            align-items: center;

            /* ✅ changed for better centering on mobile */
            min-height: 100vh;
            box-sizing: border-box;

            margin: 0;
        }

        .register-card {
            background-color: #ffffffee;
            padding: 40px 30px;
            border-radius: 12px;
            box-shadow: 0 12px 24px rgba(0,0,0,0.2);
            width: 360px;
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }

        .register-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 16px 32px rgba(0,0,0,0.25);
        }

        .register-card h2 {
            text-align: center;
            margin-bottom: 25px;
            color: #333;
            font-weight: 600;
        }

        .register-card input[type="text"],
        .register-card input[type="email"],
        .register-card input[type="password"] {
            width: 100%;
            padding: 12px;
            margin: 10px 0 18px 0;
            border: 1px solid #ccc;
            border-radius: 8px;
            box-sizing: border-box;
            transition: border-color 0.3s, box-shadow 0.3s;
        }

        .register-card input:focus {
            border-color: #2575fc;
            box-shadow: 0 0 8px rgba(37, 117, 252, 0.3);
            outline: none;
        }

        .gdpr {
            font-size: 0.9em;
            margin-bottom: 18px;
        }

        .gdpr a {
            color: #2575fc;
            text-decoration: none;
            font-weight: 500;
        }

        .gdpr a:hover {
            text-decoration: underline;
        }

        .register-card button {
            width: 100%;
            padding: 12px;
            background-color: #28a745;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 500;
            transition: background-color 0.3s, transform 0.2s;
        }

        .register-card button:hover {
            background-color: #218838;
            transform: translateY(-2px);
        }

        .login-link {
            text-align: center;
            margin-top: 18px;
        }

        .login-link a {
            color: #2575fc;
            text-decoration: none;
            font-weight: 500;
        }

        .login-link a:hover {
            text-decoration: underline;
        }

        .flash-message {
            text-align: center;
            margin-bottom: 12px;
            font-weight: bold;
            padding: 8px;
            border-radius: 6px;
        }

        .flash-message.error { background-color: #f8d7da; color: #842029; }
        .flash-message.success { background-color: #d1e7dd; color: #0f5132; }
        .flash-message.warning { background-color: #fff3cd; color: #664d03; }
        .flash-message.info { background-color: #cfe2ff; color: #084298; }

        /* ✅ ADDED: Mobile improvements */
        @media (max-width: 600px) {
            body {
                padding: 20px;
            }

            .register-card {
                width: 100%;
                padding: 30px 20px;
            }

            .register-card h2 {
                font-size: 1.4em;
            }
        }
    </style>
</head>
<body>
    <div class="register-card">
        <h2>Skapa konto</h2>

        {% with messages = get_flashed_messages(with_categories=True) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash-message {{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <form method="post">
            <input type="text" name="name" placeholder="Namn" required>
            <input type="email" name="email" placeholder="E-post" required>
            <input type="password" name="password" placeholder="Lösenord" required>

            <div class="gdpr">
                <label>
                    <input type="checkbox" name="accept_gdpr" required>
                    Jag accepterar <a href="{{ url_for('privacy_policy') }}" target="_blank">sekretesspolicyn</a>
                </label>
            </div>

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

    <!-- ✅ ADDED -->
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>Logga in</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">
    
    <style>
        /* Gradient bakgrund för hela sidan */
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #6a11cb, #2575fc);
            display: flex;
            justify-content: center;
            align-items: center;

            /* ✅ Changed for proper centering on mobile */
            min-height: 100vh;
            box-sizing: border-box;

            margin: 0;
        }

        /* Kortet */
        .login-card {
            background-color: #ffffffee;
            padding: 40px 30px;
            border-radius: 12px;
            box-shadow: 0 12px 24px rgba(0, 0, 0, 0.2);
            width: 360px;
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }
        .login-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 16px 32px rgba(0, 0, 0, 0.25);
        }

        /* Rubrik */
        .login-card h2 {
            text-align: center;
            margin-bottom: 25px;
            color: #333;
            font-weight: 600;
            letter-spacing: 0.5px;
        }

        /* Inputfält */
        .login-card input[type="email"],
        .login-card input[type="password"] {
            width: 100%;
            padding: 12px;
            margin: 10px 0 20px 0;
            border: 1px solid #ccc;
            border-radius: 8px;
            box-sizing: border-box;
            transition: border-color 0.3s, box-shadow 0.3s;
        }
        .login-card input:focus {
            border-color: #2575fc;
            box-shadow: 0 0 8px rgba(37, 117, 252, 0.3);
            outline: none;
        }

        /* Logga in knapp */
        .login-card button {
            width: 100%;
            padding: 12px;
            background-color: #2575fc;
            color: #fff;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 500;
            transition: background-color 0.3s, transform 0.2s;
        }
        .login-card button:hover {
            background-color: #145bdc;
            transform: translateY(-2px);
        }

        /* Länkar */
        .login-card .register-link,
        .login-card .forgot-password {
            text-align: center;
            margin-top: 15px;
        }
        .login-card .register-link a,
        .login-card .forgot-password a {
            color: #2575fc;
            text-decoration: none;
            font-weight: 500;
            transition: color 0.2s;
        }
        .login-card .register-link a:hover,
        .login-card .forgot-password a:hover {
            color: #145bdc;
            text-decoration: underline;
        }

        /* Flash messages */
        .flash-message {
            text-align: center;
            margin-bottom: 12px;
            font-weight: bold;
            padding: 8px;
            border-radius: 6px;
        }
        .flash-message.error { background-color: #f8d7da; color: #842029; }
        .flash-message.success { background-color: #d1e7dd; color: #0f5132; }
        .flash-message.warning { background-color: #fff3cd; color: #664d03; }
        .flash-message.info { background-color: #cfe2ff; color: #084298; }

        /* ✅ ADDED: Mobile improvements */
        @media (max-width: 600px) {
            body {
                padding: 20px;
            }

            .login-card {
                width: 100%;
                padding: 30px 20px;
            }

            .login-card h2 {
                font-size: 1.4em;
            }
        }
    </style>
</head>
<body>
    <div class="login-card">
        <h2>Logga in</h2>

        {% with messages = get_flashed_messages(with_categories=True) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash-message {{ category }}">{{ message }}</div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <form method="post">
            <input type="email" name="email" placeholder="E-post" required>
            <input type="password" name="password" placeholder="Lösenord" required>
        
            <div class="forgot-password">
                <a href="{{ url_for('forgot_password') }}">Glömt lösenord?</a>
            </div>
        
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

    <!-- ✅ ADDED -->
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>PlugIt+ - Översikt</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #ffffff 0%, #0385ff 100%);
            margin: 0;
            padding: 0;
        }
        header {
            background-color: #007bff;
            color: #fff;
            padding: 20px 0;
            text-align: center;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
        }
        header h2 {
            margin: 0;
        }
        nav {
            display: flex;
            justify-content: center;
            margin: 15px 0;
            gap: 15px;
            flex-wrap: wrap; /* ✅ Added for mobile wrapping */
        }
        nav a {
            text-decoration: none;
            background-color: #28a745;
            color: #fff;
            padding: 10px 15px;
            border-radius: 6px;
            transition: 0.2s;
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
            width: 650px;
            border-radius: 12px;
            box-shadow: 0px 8px 20px rgba(0,0,0,0.12);
            padding: 25px;
        }
        .dashboard-card h3 {
            margin-top: 0;
            color: #007bff;
        }
        .section {
            margin-bottom: 30px;
        }
        ul {
            list-style: none;
            padding-left: 0;
        }
        li {
            background-color: #f8f9fa;
            margin: 8px 0;
            padding: 12px;
            border-radius: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            transition: background 0.2s, color 0.2s;
            flex-wrap: wrap; /* ✅ Added for mobile wrapping */
        }
        li a {
            color: #007bff;
            text-decoration: none;
        }
        li a:hover {
            text-decoration: underline;
        }
        .flash-message {
            text-align: center;
            margin-bottom: 15px;
            font-weight: bold;
            color: #1a7f37;
        }

        /* Hidden class styling */
        .hidden-class {
            background-color: #222 !important;
            color: #fff !important;
        }
        .hidden-class a {
            color: white !important;
        }

        .assignments li {
            background-color: #f1f3f5;
        }
        .assignments li.hidden-class {
            background-color: #222 !important;
            color: white !important;
        }

        button {
            cursor: pointer;
            border-radius: 6px;
            border: none;
            padding: 6px 12px;
            font-size: 14px;
            transition: 0.2s;
        }
        .hide-btn {
            background-color: #6c757d;
            color: white;
            margin-right: 5px;
        }
        .hide-btn:hover {
            background-color: #5a6268;
        }
        .edit-btn {
            background-color: gray;
            color: white;
            margin-left: 5px;
        }
        .edit-btn:hover {
            background-color: #555;
        }
        .delete-btn {
            background-color: red;
            color: white;
            margin-left: 3px;
        }
        .delete-btn:hover {
            background-color: #a10000;
        }
        .leave-btn {
            background-color: orange;
            color: white;
            margin-left: 5px;
        }
        .leave-btn:hover {
            background-color: #e69500;
        }

        a.button-link {
            text-decoration: none;
        }

        /* ✅ Mobile adjustments */
        @media (max-width: 700px) {
            .dashboard-card {
                width: 100%;
                padding: 20px;
            }

            li {
                flex-direction: column;
                align-items: flex-start;
            }

            li span {
                margin-bottom: 5px;
            }

            nav {
                gap: 8px;
            }
        }
    </style>
</head>
<body>
    <header>
        <h2>Hej {{ user.name }} — Din översikt</h2>
    </header>

    <nav>
        <a href="{{ url_for('logout') }}">Logga ut</a>
        <!-- Ändra gamla Skapa klass-knappen -->
        <button id="create-btn">Skapa</button>
        
        <!-- Skapa kortet som dyker upp -->
        <div id="create-card" style="display:none; position:fixed; top:50%; left:50%; transform:translate(-50%,-50%); 
             background-color:white; padding:20px; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.2); z-index:1000;">
            <h3>Välj vad du vill skapa</h3>
            <button onclick="window.location.href='{{ url_for('create_class') }}'">Skapa klass</button>
            <button onclick="window.location.href='{{ url_for('create_activity') }}'">Skapa aktivitet</button>
            <button onclick="closeCreateCard()">Avbryt</button>
        </div>
        
        <script>
            const createBtn = document.getElementById('create-btn');
            const createCard = document.getElementById('create-card');
        
            createBtn.addEventListener('click', () => {
                createCard.style.display = 'block';
            });
        
            function closeCreateCard() {
                createCard.style.display = 'none';
            }
        </script>
        <a href="{{ url_for('join_class') }}">Gå med i klass</a>
        <a href="{{ url_for('profile') }}" style="padding: 8px 12px; background-color: #007bff; color: white;">
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

            <div class="section">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                    <h3>Dina klasser</h3>
                    <span style="font-weight:bold; color:#007bff;">Dagens datum: {{ today }}</span>
                </div>

                <ul>
                {% for c in classes %}
                    <li id="class-{{ c['class'].id }}">
                        <span>
                            <a href="{{ url_for('view_class', class_id=c['class'].id) }}">{{ c['class'].name }}</a> 
                            (kod: {{ c['class'].join_code }})
                        </span>
                        <span>
                            <button class="hide-btn" data-class-id="{{ c['class'].id }}">Hide</button>

                            {% if c['role'] == 'admin' %}
                                <a class="button-link" href="{{ url_for('edit_class', class_id=c['class'].id) }}"><button class="edit-btn">Ändra</button></a>
                                <form method="post" action="{{ url_for('delete_class', class_id=c['class'].id) }}" style="display:inline;" onsubmit="return confirm('Är du säker på att du vill radera klassen?');">
                                    <button type="submit" class="delete-btn">Radera</button>
                                </form>
                            {% else %}
                                <form method="post" action="{{ url_for('leave_class', class_id=c['class'].id) }}" style="display:inline;" onsubmit="return confirm('Vill du lämna klassen?');">
                                    <button type="submit" class="leave-btn">Lämna</button>
                                </form>
                            {% endif %}
                        </span>
                    </li>
                {% else %}
                    <li>Inga klasser ännu.</li>
                {% endfor %}
                </ul>
            </div>

            <div class="section">
                <h3>Kommande uppgifter & aktiviteter</h3>
                <ul class="assignments">
                {% for a in assignments %}
                    {% if a.type == 'assignment' %}
                        <li data-class-id="{{ a['class_id'] }}" style="background-color: {{ a['color'] }};">
                            <span>
                                <strong>{{ a['title'] }}</strong> — {{ a['subject_name'] }} ({{ a['class_name'] }})
                                {% if a['deadline'] %}
                                    {% if a['type'] in ['Uppgift','assignment'] %}
                                        — deadline: {{ a['deadline'].strftime('%Y/%m/%d %H:%M') }}
                                    {% elif a['type'] in ['Prov','exam'] %}
                                        — datum: {{ a['deadline'].strftime('%Y/%m/%d') }}
                                    {% endif %}
                                {% endif %}
                            </span>
                            {% if a['role'] == 'admin' %}
                                <span>
                                    <a class="button-link" href="{{ url_for('edit_assignment', assignment_id=a['id']) }}"><button class="edit-btn">Ändra</button></a>
                                    <form method="post" action="{{ url_for('delete_assignment', assignment_id=a['id']) }}" style="display:inline;" onsubmit="return confirm('Är du säker på att du vill radera uppgiften?');">
                                        <button type="submit" class="delete-btn">Radera</button>
                                    </form>
                                </span>
                            {% endif %}
                        </li>
                    {% elif a.type == 'activity' %}
                        <li style="background-color: {{ a['color'] }}; color:#004085; font-weight:bold;">
                            <span>
                                {{ a['title'] }} — Start: {{ a['start_time'].strftime('%Y/%m/%d %H:%M') }} | Slut: {{ a['end_time'].strftime('%Y/%m/%d %H:%M') }}
                            </span>
                            <span>
                                <a class="button-link" href="{{ url_for('edit_activity', activity_id=a['id']) }}"><button class="edit-btn">Ändra</button></a>
                                <form method="post" action="{{ url_for('delete_activity', activity_id=a['id']) }}" style="display:inline;" onsubmit="return confirm('Är du säker på att du vill radera aktiviteten?');">
                                    <button type="submit" class="delete-btn">Radera</button>
                                </form>
                            </span>
                        </li>
                    {% endif %}
                {% else %}
                    <li>Inga uppgifter eller aktiviteter hittades.</li>
                {% endfor %}
                </ul>
            </div>

            <div class="section">
                <h3>Integritet & Användarvillkor</h3>
                <a href="{{ url_for('privacy_policy') }}">Visa sekretesspolicy</a><br>
                <a href="{{ url_for('download_user_data') }}">Ladda ner dina uppgifter</a>
            </div>

            <div class="section">
                <h3>Följ oss på sociala medier:</h3>
                <a href="https://www.instagram.com/truetimeuf/" target="_blank">Instagram</a><br>
                <a href="https://www.tiktok.com/@truetimeuf" target="_blank">TikTok</a><br>
                <a href="https://www.youtube.com/@TrueTimeUF" target="_blank">YouTube</a>
            </div>

        </div>
    </div>

    <script>
        document.querySelectorAll('.hide-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const classId = btn.dataset.classId;
                const classCard = document.getElementById('class-' + classId);

                classCard.classList.toggle('hidden-class');

                document.querySelectorAll(`.assignments li[data-class-id='${classId}']`).forEach(a => {
                    a.classList.toggle('hidden-class');
                });

                btn.textContent = classCard.classList.contains('hidden-class') ? 'Unhide' : 'Hide';
            });
        });
    </script>
</body>
</html>
"""

CREATE_CLASS_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Skapa klass - PlugIt+</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        * {
            box-sizing: border-box;
        }
    
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #022a4f 0%, #022a4f 100%);
            margin: 0;
    
            /* Perfekt centrering */
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
    
            padding: 20px; /* viktigt för mobil */
        }
    
        .create-card {
            background-color: #fff;
            padding: 30px;
            border-radius: 12px;
            box-shadow: 0px 8px 20px rgba(0,0,0,0.15);
    
            /* Responsiv bredd */
            width: 100%;
            max-width: 420px;
    
            text-align: center;
        }
    
        .create-card h2 {
            margin-bottom: 20px;
            color: #333;
        }
    
        .create-card input[type="text"] {
            width: 100%;
            padding: 12px;
            margin: 10px 0 20px 0;
            border: 1px solid #ccc;
            border-radius: 6px;
            font-size: 16px;
        }
    
        .create-card button {
            width: 100%;
            padding: 12px;
            background-color: #007bff;
            color: #fff;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 16px;
            transition: background 0.2s;
        }
    
        .create-card button:hover {
            background-color: #0056b3;
        }
    
        .flash-message {
            text-align: center;
            margin-bottom: 10px;
            font-weight: bold;
        }
        .flash-message.error { color: #a10000; }
        .flash-message.success { color: #1a7f37; }
        .flash-message.warning { color: #7c6f00; }
        .flash-message.info { color: #004085; }
    
        .back-link {
            margin-top: 18px;
        }
    
        .back-link a {
            color: #007bff;
            text-decoration: none;
            font-size: 14px;
        }
    
        .back-link a:hover {
            text-decoration: underline;
        }
    
        /* Extra polish för små skärmar */
        @media (max-width: 480px) {
            .create-card {
                padding: 22px;
            }
        }
    </style>
</head>
<body>
    <div class="create-card">
        <h2>Skapa ny klass</h2>

        {% with messages = get_flashed_messages(with_categories=True) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash-message {{ category }}">{{ message }}</div>
            {% endfor %}
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gå med i klass - PlugIt+</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #022a4f 0%, #022a4f 100%);
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
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .join-card:hover {
            transform: translateY(-5px);
            box-shadow: 0px 8px 20px rgba(0,0,0,0.15);
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
            transition: border-color 0.2s;
        }
        .join-card input[type="text"]:focus {
            border-color: #007bff;
            outline: none;
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
            transition: background-color 0.2s, transform 0.2s;
        }
        .join-card button:hover {
            background-color: #0056b3;
            transform: translateY(-2px);
        }
        .flash-message {
            text-align: center;
            margin-bottom: 10px;
            font-weight: bold;
        }
        .flash-message.error { color: #a10000; }
        .flash-message.success { color: #1a7f37; }
        .flash-message.warning { color: #7c6f00; }
        .flash-message.info { color: #004085; }

        .back-link {
            display: block;
            text-align: center;
            margin-top: 15px;
        }
        .back-link a {
            color: #007bff;
            text-decoration: none;
            transition: color 0.2s, transform 0.2s;
        }
        .back-link a:hover {
            color: #0056b3;
            transform: translateY(-2px);
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="join-card">
        <h2>Gå med i klass</h2>

        {% with messages = get_flashed_messages(with_categories=True) %}
          {% if messages %}
            {% for category, message in messages %}
              <div class="flash-message {{ category }}">{{ message }}</div>
            {% endfor %}
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ class_data['name'] }} - PlugIt+</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #022a4f 0%, #022a4f 100%);
            margin: 0;
            padding: 0;
        }

        header {
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
            box-shadow: 0px 4px 8px rgba(0,0,0,0.1);
        }

        nav {
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 10px;
            margin: 15px;
        }

        nav a, nav form button {
            background-color: #28a745;
            color: #fff;
            padding: 10px 14px;
            border-radius: 6px;
            border: none;
            cursor: pointer;
            text-decoration: none;
            transition: 0.2s;
        }

        nav a:hover, nav form button:hover {
            background-color: #218838;
            transform: translateY(-2px);
        }

        nav form {
            display: inline;
        }

        .container {
            display: flex;
            justify-content: center;
            padding: 20px 12px;
        }

        .class-card {
            background-color: #fff;
            width: 100%;
            max-width: 650px;
            border-radius: 10px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 22px;
        }

        .section {
            margin-bottom: 25px;
        }

        ul {
            list-style: none;
            padding: 0;
            margin: 0;
        }

        li {
            background-color: #f8f9fa;
            margin: 8px 0;
            padding: 12px;
            border-radius: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }

        li a {
            color: #007bff;
            text-decoration: none;
            font-weight: 500;
        }

        li a:hover {
            text-decoration: underline;
        }

        .flash-message {
            text-align: center;
            margin-bottom: 12px;
            font-weight: bold;
        }

        .flash-message.error { color: #a10000; }
        .flash-message.success { color: #1a7f37; }
        .flash-message.warning { color: #7c6f00; }
        .flash-message.info { color: #004085; }

        form input[type="text"] {
            width: 100%;
            max-width: 220px;
            padding: 8px;
            border-radius: 6px;
            border: 1px solid #ccc;
        }

        form button {
            padding: 8px 12px;
            border: none;
            background-color: #007bff;
            color: #fff;
            border-radius: 6px;
            cursor: pointer;
            transition: 0.2s;
        }

        .btn-admin { background-color: #dc3545; }
        .btn-gray { background-color: #6c757d; }
        .btn-warning { background-color: #ffc107; color: #222; }

        /* ---------------- MOBILE FIXES ---------------- */
        @media (max-width: 600px) {

            header h2 {
                font-size: 20px;
            }

            nav {
                flex-direction: column;
                align-items: stretch;
            }

            nav a, nav form button {
                width: 100%;
                text-align: center;
            }

            li {
                flex-direction: column;
                align-items: flex-start;
            }

            form input[type="text"] {
                max-width: 100%;
                margin-bottom: 6px;
            }
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
    <a href="{{ url_for('index') }}">Tillbaka</a>

    {% if is_admin %}
        <form method="post" action="{{ url_for('add_subject', class_id=class_data['id']) }}">
            <input type="text" name="subject_name" placeholder="Ämnesnamn" required>
            <button type="submit">Lägg till ämne</button>
        </form>

        <form action="{{ url_for('add_admin_request', class_id=class_data.id) }}" method="get">
            <button type="submit" class="btn-warning">Bjud in admin</button>
        </form>

        <form method="post" action="{{ url_for('leave_admin', class_id=class_data.id) }}"
              onsubmit="return confirm('Vill du verkligen lämna som admin?');">
            <button type="submit" class="btn-admin">Lämna som admin</button>
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
                <li>
                    <span>
                        <a href="{{ url_for('view_subject', subject_id=subject['id']) }}">{{ subject['name'] }}</a>
                    </span>

                    {% if is_admin %}
                    <span>
                        <a href="{{ url_for('edit_subject', subject_id=subject['id']) }}">
                            <button class="btn-gray">Ändra</button>
                        </a>
                        <form method="post" action="{{ url_for('delete_subject', subject_id=subject['id']) }}"
                              style="display:inline;"
                              onsubmit="return confirm('Är du säker?');">
                            <button type="submit" class="btn-admin">Radera</button>
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ subject['name'] }} - {{ class_data['name'] }}</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #022a4f 0%, #022a4f 100%);
            margin: 0;
            padding: 0;
        }

        header {
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
            box-shadow: 0px 4px 8px rgba(0,0,0,0.1);
        }

        header h2 {
            margin: 0;
            font-size: 20px;
        }

        .container {
            display: flex;
            justify-content: center;
            padding: 20px 12px;
        }

        .subject-card {
            background-color: #fff;
            width: 100%;
            max-width: 650px;
            border-radius: 10px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 22px;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .subject-card:hover {
            transform: translateY(-3px);
            box-shadow: 0px 8px 18px rgba(0,0,0,0.12);
        }

        h3 {
            margin-top: 0;
            color: #333;
        }

        ul {
            list-style: none;
            padding: 0;
            margin: 0;
        }

        li {
            margin: 6px 0;
            padding: 12px;
            border-radius: 6px;
            transition: transform 0.2s, filter 0.2s;
        }

        li:hover {
            transform: translateY(-2px);
            filter: brightness(0.95);
        }

        .flash-message {
            text-align: center;
            margin-bottom: 12px;
            font-weight: bold;
            color: #1a7f37;
        }

        form {
            margin-top: 20px;
        }

        form input[type="text"],
        form input[type="datetime-local"],
        form input[type="date"],
        form select {
            width: 100%;
            padding: 10px;
            margin: 8px 0;
            border-radius: 6px;
            border: 1px solid #ccc;
            box-sizing: border-box;
            transition: border-color 0.2s;
        }

        form input:focus,
        form select:focus {
            border-color: #007bff;
            outline: none;
        }

        form button {
            width: 100%;
            padding: 10px;
            margin-top: 10px;
            border: none;
            background-color: #28a745;
            color: #fff;
            border-radius: 6px;
            cursor: pointer;
            font-size: 16px;
            transition: transform 0.2s, background-color 0.2s;
        }

        form button:hover {
            background-color: #218838;
            transform: translateY(-2px);
        }

        .back-link {
            text-align: center;
            margin-top: 20px;
        }

        .back-link a {
            color: #007bff;
            text-decoration: none;
            font-weight: bold;
        }

        .back-link a:hover {
            text-decoration: underline;
        }

        /* --------- RESPONSIVE --------- */
        @media (max-width: 600px) {
            header h2 {
                font-size: 18px;
            }

            .subject-card {
                padding: 16px;
            }

            li {
                padding: 10px;
            }

            form input, form select, form button {
                font-size: 14px;
            }
        }
    </style>
</head>
<body>
<header>
    <h2>{{ subject['name'] }} — {{ class_data['name'] }}</h2>
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
            <li style="background-color: {{ assignment['color'] }};">
                <strong>{{ assignment['title'] }}</strong> —
                {% if assignment['deadline'] %}
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
            <li style="background-color:#f8f9fa;">Inga uppgifter tillagda ännu.</li>
        {% endfor %}
        </ul>

        {% if is_admin %}
        <form method="post" action="{{ url_for('add_assignment', subject_id=subject['id']) }}">
            <input type="text" name="title" placeholder="Uppgiftsnamn" required>

            <select name="type" id="type" required onchange="updateDeadlineInput()">
                <option value="assignment">Uppgift</option>
                <option value="exam">Prov</option>
            </select>

            <input type="datetime-local" name="deadline" id="deadline_input">

            <button type="submit">Lägg till uppgift</button>
        </form>

        <script>
            function updateDeadlineInput() {
                const typeSelect = document.getElementById('type');
                const deadlineInput = document.getElementById('deadline_input');
                deadlineInput.type = typeSelect.value === 'exam' ? 'date' : 'datetime-local';
            }
            window.onload = updateDeadlineInput;
        </script>
        {% endif %}

        <div class="back-link">
            <a href="{{ url_for('view_class', class_id=class_data['id']) }}">
                ← Tillbaka till klassen
            </a>
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ subject.name }} - {{ class_data.name }}</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #022a4f 0%, #022a4f 100%);
            margin: 0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        header {
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
            box-shadow: 0px 4px 8px rgba(0,0,0,0.1);
        }

        header h2 {
            margin: 0;
        }

        .container {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }

        .edit-card {
            background-color: #fff;
            width: 100%;
            max-width: 500px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 25px;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .edit-card:hover {
            transform: translateY(-5px);
            box-shadow: 0px 8px 20px rgba(0,0,0,0.15);
        }

        h3 {
            margin-top: 0;
            color: #333;
            text-align: center;
        }

        .flash-message {
            text-align: center;
            margin-bottom: 10px;
            font-weight: bold;
            color: #1a7f37;
        }

        form {
            margin-top: 20px;
        }

        label {
            font-weight: bold;
            display: block;
            margin-top: 10px;
        }

        form input[type="text"],
        form input[type="date"],
        form input[type="datetime-local"],
        form select {
            width: 100%;
            padding: 10px;
            margin-top: 6px;
            border-radius: 4px;
            border: 1px solid #ccc;
            box-sizing: border-box;
            transition: border-color 0.2s;
        }

        form input:focus,
        form select:focus {
            border-color: #007bff;
            outline: none;
        }

        form button {
            width: 100%;
            padding: 10px;
            margin-top: 15px;
            border: none;
            background-color: #28a745;
            color: #fff;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            transition: transform 0.2s, background-color 0.2s;
        }

        form button:hover {
            background-color: #218838;
            transform: translateY(-2px);
        }

        .back-link {
            text-align: center;
            margin-top: 20px;
        }

        .back-link a {
            color: #007bff;
            text-decoration: none;
            font-weight: bold;
        }

        .back-link a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>

<header>
    <h2>{{ subject.name }} — {{ class_data.name }}</h2>
</header>

<div class="container">
    <div class="edit-card">

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="flash-message">
              {% for message in messages %}
                {{ message }}<br>
              {% endfor %}
            </div>
          {% endif %}
        {% endwith %}

        <h3>Ändra uppgift / prov</h3>

        {% if is_admin %}
        <form method="post">

            <label for="title">Titel</label>
            <input type="text" name="title" id="title" value="{{ assignment.title }}" required>

            <label for="type">Typ</label>
            <select name="type" id="type" required onchange="updateDeadlineInput()">
                <option value="assignment" {% if assignment.type == 'assignment' %}selected{% endif %}>Uppgift</option>
                <option value="exam" {% if assignment.type == 'exam' %}selected{% endif %}>Prov</option>
            </select>

            <label for="deadline_input">Deadline / Datum</label>
            <input 
                type="{% if assignment.type == 'exam' %}date{% else %}datetime-local{% endif %}" 
                name="deadline" 
                id="deadline_input"
                value="{% if assignment.deadline %}
                    {% if assignment.type == 'exam' %}
                        {{ assignment.deadline.strftime('%Y-%m-%d') }}
                    {% else %}
                        {{ assignment.deadline.strftime('%Y-%m-%dT%H:%M') }}
                    {% endif %}
                {% endif %}"
            >

            <button type="submit">Spara ändringar</button>
        </form>

        <script>
            function updateDeadlineInput() {
                const typeSelect = document.getElementById('type');
                const deadlineInput = document.getElementById('deadline_input');
                deadlineInput.type = typeSelect.value === 'exam' ? 'date' : 'datetime-local';
            }
            window.onload = updateDeadlineInput;
        </script>
        {% endif %}

        <div class="back-link">
            <a href="{{ url_for('index') }}">← Avbryt & tillbaka</a>
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Din profil</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(180deg, #ffffff 0%, #780101 100%);
            margin: 0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        header {
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
            box-shadow: 0px 4px 8px rgba(0,0,0,0.1);
        }

        header h2 {
            margin: 0;
        }

        .container {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }

        .profile-card {
            background-color: #fff;
            width: 100%;
            max-width: 520px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 25px;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .profile-card:hover {
            transform: translateY(-5px);
            box-shadow: 0px 8px 20px rgba(0,0,0,0.15);
        }

        h3 {
            margin-top: 0;
            color: #333;
            text-align: center;
        }

        .flash-message {
            text-align: center;
            margin-bottom: 10px;
            font-weight: bold;
        }
        .flash-message.error { color: #a10000; }
        .flash-message.success { color: #1a7f37; }
        .flash-message.warning { color: #7c6f00; }
        .flash-message.info { color: #004085; }

        form {
            margin-top: 15px;
        }

        label {
            font-weight: bold;
            display: block;
            margin-top: 10px;
        }

        form input[type="text"],
        form input[type="email"],
        form input[type="password"] {
            width: 100%;
            padding: 10px;
            margin-top: 6px;
            margin-bottom: 10px;
            border-radius: 4px;
            border: 1px solid #ccc;
            box-sizing: border-box;
            transition: border-color 0.2s;
        }

        form input:focus {
            border-color: #007bff;
            outline: none;
        }

        .primary-btn {
            width: 100%;
            padding: 10px;
            border: none;
            background-color: #28a745;
            color: #fff;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            transition: transform 0.2s, background-color 0.2s;
        }

        .primary-btn:hover {
            background-color: #218838;
            transform: translateY(-2px);
        }

        .danger-zone {
            margin-top: 30px;
            padding-top: 15px;
            border-top: 1px solid #ddd;
        }

        .danger-btn {
            width: 100%;
            padding: 10px;
            border: none;
            background-color: #dc3545;
            color: #fff;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            transition: transform 0.2s, background-color 0.2s;
        }

        .danger-btn:hover {
            background-color: #c82333;
            transform: translateY(-2px);
        }

        .back-link {
            text-align: center;
            margin-top: 20px;
        }

        .back-link a {
            color: #007bff;
            text-decoration: none;
            font-weight: bold;
        }

        .back-link a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>

<header>
    <h2>Din profil</h2>
</header>

<div class="container">
    <div class="profile-card">

        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            {% for category, message in messages %}
                <div class="flash-message {{ category }}">
                    {{ message }}
                </div>
            {% endfor %}
          {% endif %}
        {% endwith %}

        <h3>Ändra uppgifter</h3>

        <form method="post" action="{{ url_for('edit_profile') }}">
            <label for="name">Namn</label>
            <input type="text" name="name" id="name" value="{{ user.name }}" required>

            <label for="email">E-post</label>
            <input type="email" name="email" id="email" value="{{ user.email }}" required>

            <label for="password">Nytt lösenord</label>
            <input type="password" name="password" placeholder="Lämna tomt om du inte vill byta">

            <label for="confirm_password">Bekräfta lösenord</label>
            <input type="password" name="confirm_password">

            <label style="display:flex; align-items:center; gap:10px; margin:15px 0;">
                <input type="checkbox" name="notifications_enabled"
                       {% if user.notifications_enabled %}checked{% endif %}>
                <span>Ta emot notiser om uppgifter & prov</span>
            </label>

            <button type="submit" class="primary-btn">Spara ändringar</button>
        </form>

        <div class="danger-zone">
            <h3>Radera konto</h3>
            <form method="post" action="{{ url_for('delete_profile') }}">
                <button type="submit"
                        class="danger-btn"
                        onclick="return confirm('Är du säker på att du vill radera ditt konto? Detta går inte att ångra.')">
                    Radera konto
                </button>
            </form>
        </div>

        <div class="back-link">
            <a href="{{ url_for('index') }}">← Tillbaka till startsidan</a>
        </div>

    </div>
</div>

</body>
</html>
"""

EDIT_CLASS_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ändra klass - PlugIt+</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #022a4f 0%, #022a4f 100%);
            margin: 0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        header {
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
            box-shadow: 0px 4px 8px rgba(0,0,0,0.1);
        }

        header h2 {
            margin: 0;
        }

        .container {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }

        .edit-card {
            background-color: #fff;
            width: 100%;
            max-width: 420px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 25px;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .edit-card:hover {
            transform: translateY(-5px);
            box-shadow: 0px 8px 20px rgba(0,0,0,0.15);
        }

        h3 {
            margin-top: 0;
            color: #333;
            text-align: center;
        }

        .flash-message {
            text-align: center;
            margin-bottom: 10px;
            font-weight: bold;
            color: #a10000;
        }

        form {
            margin-top: 15px;
        }

        label {
            font-weight: bold;
            display: block;
            margin-bottom: 6px;
        }

        form input[type="text"] {
            width: 100%;
            padding: 10px;
            margin-bottom: 15px;
            border-radius: 4px;
            border: 1px solid #ccc;
            box-sizing: border-box;
            transition: border-color 0.2s;
        }

        form input:focus {
            border-color: #007bff;
            outline: none;
        }

        .primary-btn {
            width: 100%;
            padding: 10px;
            border: none;
            background-color: #28a745;
            color: #fff;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            transition: transform 0.2s, background-color 0.2s;
        }

        .primary-btn:hover {
            background-color: #218838;
            transform: translateY(-2px);
        }

        .back-link {
            text-align: center;
            margin-top: 20px;
        }

        .back-link a {
            color: #007bff;
            text-decoration: none;
            font-weight: bold;
        }

        .back-link a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>

<header>
    <h2>Ändra klass</h2>
</header>

<div class="container">
    <div class="edit-card">

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="flash-message">
              {% for message in messages %}
                {{ message }}<br>
              {% endfor %}
            </div>
          {% endif %}
        {% endwith %}

        <h3>Klassens namn</h3>
        <form method="post">
            <label for="class_name">Namn</label>
            <input type="text" id="class_name" name="class_name" value="{{ cls.name }}" required>

            <button type="submit" class="primary-btn">Spara ändringar</button>
        </form>

        <div class="back-link">
            <a href="{{ url_for('index') }}">← Tillbaka till översikten</a>
        </div>

    </div>
</div>

</body>
</html>
"""

EDIT_SUBJECT_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ändra ämne - PlugIt+</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #022a4f 0%, #022a4f 100%);
            margin: 0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        header {
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
            box-shadow: 0px 4px 8px rgba(0,0,0,0.1);
        }

        header h2 {
            margin: 0;
        }

        .container {
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }

        .edit-card {
            background-color: #fff;
            width: 100%;
            max-width: 420px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 25px;
            text-align: center;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .edit-card:hover {
            transform: translateY(-5px);
            box-shadow: 0px 8px 20px rgba(0,0,0,0.15);
        }

        h3 {
            margin-top: 0;
            color: #333;
        }

        form {
            margin-top: 15px;
        }

        form input[type="text"] {
            width: 100%;
            padding: 10px;
            margin-bottom: 15px;
            border-radius: 4px;
            border: 1px solid #ccc;
            box-sizing: border-box;
            transition: border-color 0.2s;
        }

        form input:focus {
            border-color: #007bff;
            outline: none;
        }

        .primary-btn {
            width: 100%;
            padding: 10px;
            border: none;
            background-color: #28a745;
            color: #fff;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            transition: transform 0.2s, background-color 0.2s;
        }

        .primary-btn:hover {
            background-color: #218838;
            transform: translateY(-2px);
        }

        .back-link {
            margin-top: 20px;
            text-align: center;
        }

        .back-link a {
            color: #007bff;
            text-decoration: none;
            font-weight: bold;
        }

        .back-link a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <header>
        <h2>Ändra ämne</h2>
    </header>

    <div class="container">
        <div class="edit-card">
            <h3>Ämnesnamn</h3>
            <form method="post">
                <input type="text" name="subject_name" value="{{ subject.name }}" required>
                <button type="submit" class="primary-btn">Spara ändringar</button>
            </form>

            <div class="back-link">
                <a href="{{ url_for('view_class', class_id=cls.id) }}">← Tillbaka till klassen</a>
            </div>
        </div>
    </div>
</body>
</html>
"""

FORGOT_PASSWORD_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Glömt lösenord</title>

    <style>
        body {
            font-family: Arial, sans-serif;
            background-color: #f4f4f4;
            margin: 0;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }

        .card {
            background: #fff;
            padding: 30px;
            border-radius: 8px;
            width: 90%;
            max-width: 400px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            text-align: center;
        }

        h2 {
            margin-top: 0;
            margin-bottom: 20px;
            color: #333;
        }

        input[type="email"] {
            width: 100%;
            padding: 10px;
            margin-top: 10px;
            border-radius: 4px;
            border: 1px solid #ccc;
            box-sizing: border-box;
            transition: border-color 0.2s;
        }

        input[type="email"]:focus {
            border-color: #007bff;
            outline: none;
        }

        button {
            width: 100%;
            padding: 10px;
            margin-top: 15px;
            background-color: #007bff;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            transition: background-color 0.2s, transform 0.2s;
        }

        button:hover {
            background-color: #0056b3;
            transform: translateY(-2px);
        }

        .back {
            margin-top: 15px;
        }

        .back a {
            color: #007bff;
            text-decoration: none;
            font-weight: bold;
        }

        .back a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="card">
        <h2>Återställ lösenord</h2>

        <form method="post">
            <input type="email" name="email" placeholder="Din e-post" required>
            <button type="submit">Skicka återställningslänk</button>
        </form>

        <div class="back">
            <a href="{{ url_for('login') }}">Tillbaka till login</a>
        </div>
    </div>
</body>
</html>
"""

RESET_PASSWORD_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Återställ lösenord - PlugIt+</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #e0f0ff 0%, #ffffff 100%);
            margin: 0;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }

        header {
            width: 100%;
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
            box-shadow: 0px 4px 8px rgba(0,0,0,0.1);
            box-sizing: border-box;
        }

        header h2 {
            margin: 0;
        }

        .container {
            display: flex;
            justify-content: center;
            width: 100%;
            padding: 20px;
        }

        .card {
            background-color: #fff;
            width: 90%;
            max-width: 400px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 25px;
            text-align: center;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0px 8px 20px rgba(0,0,0,0.15);
        }

        h3 {
            margin-top: 0;
            margin-bottom: 20px;
            color: #333;
        }

        input[type="password"] {
            width: 100%;
            padding: 10px;
            margin-bottom: 15px;
            border-radius: 4px;
            border: 1px solid #ccc;
            box-sizing: border-box;
            transition: border-color 0.2s;
        }

        input[type="password"]:focus {
            border-color: #007bff;
            outline: none;
        }

        .primary-btn {
            width: 100%;
            padding: 10px;
            background-color: #28a745;
            color: #fff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            transition: transform 0.2s, background-color 0.2s;
        }

        .primary-btn:hover {
            background-color: #218838;
            transform: translateY(-2px);
        }
    </style>
</head>
<body>
    <header>
        <h2>Återställ lösenord</h2>
    </header>

    <div class="container">
        <div class="card">
            <h3>Ange nytt lösenord</h3>

            <form method="post">
                <input type="password" name="password" placeholder="Nytt lösenord" required>
                <input type="password" name="confirm_password" placeholder="Bekräfta lösenord" required>
                <button type="submit" class="primary-btn">Spara nytt lösenord</button>
            </form>
        </div>
    </div>
</body>
</html>
"""

INVITE_ADMIN_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bjud in admin – {{ cls.name }}</title>

    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="shortcut icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">
    <link rel="icon" type="image/png" sizes="32x32" href="{{ url_for('static', filename='favicon/favicon-32x32.png') }}">
    <link rel="icon" type="image/png" sizes="16x16" href="{{ url_for('static', filename='favicon/favicon-16x16.png') }}">
    <link rel="apple-touch-icon" href="{{ url_for('static', filename='favicon/apple-touch-icon.png') }}">

    <style>
        body {
            font-family: Arial, sans-serif;
            background: linear-gradient(180deg, #023a6e 0%, #023a6e 100%);
            margin: 0;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
        }

        header {
            width: 100%;
            background-color: #007bff;
            color: #fff;
            padding: 15px 20px;
            text-align: center;
            box-shadow: 0px 4px 8px rgba(0,0,0,0.1);
            box-sizing: border-box;
        }

        header h2 {
            margin: 0;
        }

        .container {
            display: flex;
            justify-content: center;
            width: 100%;
            padding: 20px;
            box-sizing: border-box;
        }

        .card {
            background-color: #fff;
            width: 90%;
            max-width: 400px;
            border-radius: 8px;
            box-shadow: 0px 4px 12px rgba(0,0,0,0.1);
            padding: 25px;
            text-align: center;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .card:hover {
            transform: translateY(-5px);
            box-shadow: 0px 8px 20px rgba(0,0,0,0.15);
        }

        h3 {
            margin-top: 0;
            margin-bottom: 20px;
            color: #333;
        }

        label {
            font-weight: bold;
            display: block;
            margin-top: 10px;
            margin-bottom: 5px;
            text-align: left;
        }

        input[type="email"] {
            width: 100%;
            padding: 10px;
            border-radius: 4px;
            border: 1px solid #ccc;
            margin-bottom: 15px;
            box-sizing: border-box;
            transition: border-color 0.2s;
        }

        input[type="email"]:focus {
            border-color: #007bff;
            outline: none;
        }

        button {
            width: 100%;
            padding: 10px;
            background-color: #28a745;
            color: #fff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            transition: transform 0.2s, background-color 0.2s;
        }

        button:hover {
            background-color: #218838;
            transform: translateY(-2px);
        }

        .back-link {
            text-align: center;
            margin-top: 15px;
        }

        .back-link a {
            color: #007bff;
            text-decoration: none;
            font-weight: bold;
        }

        .back-link a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <header>
        <h2>Bjud in admin – {{ cls.name }}</h2>
    </header>

    <div class="container">
        <div class="card">
            <h3>Lägg till en ny admin</h3>

            <form method="post">
                <label for="email">E-post till användare:</label>
                <input type="email" name="email" id="email" required>
                <button type="submit">Skicka inbjudan</button>
            </form>

            <div class="back-link">
                <a href="{{ url_for('view_class', class_id=cls.id) }}">← Tillbaka till klassen</a>
            </div>
        </div>
    </div>
</body>
</html>
"""

CREATE_ACTIVITY_TEMPLATE = """
<!doctype html>
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <title>Skapa aktivitet - PlugIt+</title>
    
    <link rel="icon" href="{{ url_for('static', filename='favicon/favicon.ico') }}" type="image/x-icon">

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
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            width: 400px;
            text-align: center;
        }
        .create-card h2 {
            margin-bottom: 20px;
            color: #333;
        }
        .create-card input[type="text"],
        .create-card input[type="datetime-local"] {
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
            background-color: #17a2ff; /* ljusblå */
            color: #003366; /* mörkare blå text */
            font-weight: bold;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
        }
        .create-card button:hover {
            background-color: #1380d3;
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
        <h2>Skapa aktivitet</h2>
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
            <input type="text" name="activity_name" placeholder="Aktivitetsnamn" required>
            <label for="start_time">Start:</label>
            <input type="datetime-local" name="start_time" required>
            <label for="end_time">Slut:</label>
            <input type="datetime-local" name="end_time" required>
            <button type="submit">Skapa aktivitet</button>
        </form>
        <div class="back-link">
            <a href="{{ url_for('index') }}">Tillbaka till dashboard</a>
        </div>
    </div>
</body>
</html>
"""












