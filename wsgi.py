from app import app, db

# cria tabelas no primeiro start (não quebra se já existir)
with app.app_context():
    db.create_all()