from app import app, db  # Import your Flask app and database

# Ensure all database tables are created
with app.app_context():
    db.create_all()

# For local development only
if __name__ == '__main__':
    # Run Flask's built-in server with debug mode
    app.run(debug=True)
