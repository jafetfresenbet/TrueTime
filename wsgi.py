from app import app, db
import os

# Skapa databasen om den inte finns
with app.app_context():
    db.create_all()

# Det här behövs för att Gunicorn/Render ska hitta "application"
application = app 

if __name__ == '__main__':
    # Hämta port från miljövariabler (viktigt för servrar)
    port = int(os.environ.get("PORT", 5000))
    # host="0.0.0.0" gör att den lyssnar på externa anrop
    app.run(host="0.0.0.0", port=port, debug=True)True)
