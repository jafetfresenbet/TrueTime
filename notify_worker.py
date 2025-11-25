from app import app, db
from app import send_deadline_notifications

# Create an application context so DB / Mail / config work
with app.app_context():
    print("Running scheduled notification check...")
    send_deadline_notifications()
    print("Done.")
