import os
import re
import uuid
import base64
from datetime import datetime, timedelta
from functools import wraps, lru_cache
from random import choice
from typing import Optional, Dict, Any

import requests
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, make_response
)
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ✅ .env (carrega antes de ler variáveis)
load_dotenv()

# =========================================================
# ======================= APP CONFIG =======================
# =========================================================

app = Flask(__name__)

# ✅ SECRET KEY (Render usa FLASK_SECRET_KEY)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "linkflixsecret")

# ✅ Segurança básica em produção (Render)
if os.getenv("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["REMEMBER_COOKIE_SECURE"] = True

# ✅ DATABASE (SQLite local / Postgres no Render)
db_url = os.getenv("DATABASE_URL", "sqlite:///linkflix.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Mantém as conexões PostgreSQL saudáveis após pausas/reinícios do Render.
# No SQLite local estas opções não são necessárias.
if db_url.startswith(("postgresql://", "postgresql+")):
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "pool_size": 5,
        "max_overflow": 5,
    }

# ✅ UPLOAD CONFIG (avatar)
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads", "avatars")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

def uploaded_image_to_data_uri(file_storage):
    if not file_storage or not getattr(file_storage, "filename", ""):
        return ""
    filename = secure_filename(file_storage.filename)
    if "." not in filename or filename.rsplit(".", 1)[1].lower() not in ALLOWED_EXTENSIONS:
        return ""
    raw = file_storage.read()
    if not raw:
        return ""
    ext = filename.rsplit(".", 1)[1].lower()
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# =========================================================
# ============= .well-known (TWA assetlinks) ===============
# =========================================================

@app.route("/.well-known/assetlinks.json", methods=["GET"])
def assetlinks():
    payload = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": "com.linkflix.app",
            "sha256_cert_fingerprints": [
                "40:7F:44:9F:D9:86:82:D5:D6:E8:7D:65:87:94:80:5D:26:7F:3A:0C:C0:ED:8E:BB:30:B9:EB:6E:E1:CF:50:78"
            ]
        }
    }]

    resp = make_response(jsonify(payload), 200)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# =========================================================
# ====================== TMDB CONFIG =======================
# =========================================================

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w780"


def tmdb_get(path: str, params: Optional[Dict[str, Any]] = None):
    if not TMDB_API_KEY:
        raise RuntimeError("TMDB_API_KEY não configurada.")
    params = params or {}
    params["api_key"] = TMDB_API_KEY
    params.setdefault("language", "pt-BR")
    url = f"{TMDB_BASE_URL}{path}"
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()




@lru_cache(maxsize=512)
def tmdb_trailer_key(item_type: str, tmdb_id: str) -> str:
    """Retorna a melhor chave de trailer/teaser do YouTube disponível no TMDB."""
    if item_type not in ("movie", "tv") or not tmdb_id:
        return ""

    def pick(results):
        youtube = [v for v in (results or []) if v.get("site") == "YouTube" and v.get("key")]
        if not youtube:
            return ""
        # Prioriza trailer oficial; depois teaser; depois qualquer vídeo do YouTube.
        priorities = (
            lambda v: v.get("official") is True and v.get("type") == "Trailer",
            lambda v: v.get("type") == "Trailer",
            lambda v: v.get("official") is True and v.get("type") == "Teaser",
            lambda v: v.get("type") == "Teaser",
            lambda v: True,
        )
        for rule in priorities:
            for video in youtube:
                if rule(video):
                    return video.get("key", "")
        return ""

    try:
        data = tmdb_get(f"/{item_type}/{normalize_tmdb_id(tmdb_id)}/videos")
        key = pick(data.get("results"))
        if key:
            return key
        # Muitos títulos não têm trailer em pt-BR; tenta o catálogo internacional.
        data = tmdb_get(
            f"/{item_type}/{normalize_tmdb_id(tmdb_id)}/videos",
            {"language": "en-US"}
        )
        return pick(data.get("results"))
    except Exception:
        return ""

def normalize_tmdb_id(raw: str) -> str:
    """
    Aceita:
    - "550"
    - "https://www.themoviedb.org/movie/550-fight-club"
    - "https://www.themoviedb.org/tv/1396-breaking-bad"
    Retorna só o número como string.
    """
    if not raw:
        return ""
    raw = raw.strip()
    m = re.search(r"/(movie|tv)/(\d+)", raw, flags=re.I)
    if m:
        return m.group(2)
    m2 = re.search(r"(\d+)", raw)
    return m2.group(1) if m2 else raw


def tmdb_lookup_item(item_type: str, tmdb_id: str):
    tmdb_id = normalize_tmdb_id(tmdb_id)
    if item_type not in ("movie", "tv"):
        raise ValueError("type inválido")

    data = tmdb_get(f"/{item_type}/{tmdb_id}")

    title = data.get("title") if item_type == "movie" else data.get("name")
    overview = data.get("overview") or ""
    poster = data.get("poster_path") or ""
    backdrop = data.get("backdrop_path") or ""

    image = (TMDB_IMG_BASE + backdrop) if backdrop else ((TMDB_IMG_BASE + poster) if poster else "")

    genres = [g.get("name") for g in (data.get("genres") or []) if g.get("name")]
    main_category = genres[0] if genres else ""
    extra_categories = genres[1:] if len(genres) > 1 else []

    return {
        "tmdb_id": tmdb_id,
        "content_type": "Filme" if item_type == "movie" else "Serie",
        "title": title or "",
        "description": overview[:480],
        "image": image,
        "category": main_category,
        "extra_categories": extra_categories,
        "genres": genres,
    }


# =========================================================
# ✅✅✅ CRIA TABELAS NO PRIMEIRO REQUEST (SEGURO NO RENDER)
# =========================================================

_db_ready = False


def _seed_postgres_from_bundled_sqlite_once():
    """
    Copia automaticamente os dados do SQLite enviado no repositório para o
    PostgreSQL apenas quando o banco PostgreSQL estiver vazio. Isso preserva
    filmes, usuários, perfis, favoritos e progresso já existentes no primeiro
    deploy com DATABASE_URL configurada.
    """
    database_url = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not str(database_url).startswith(("postgresql://", "postgresql+")):
        return

    sqlite_path = os.path.join(app.instance_path, "linkflix.db")
    if not os.path.exists(sqlite_path):
        return

    # Se já há qualquer conteúdo ou usuário, o banco já foi inicializado.
    if Content.query.first() is not None or User.query.first() is not None:
        return

    source_engine = create_engine(f"sqlite:///{sqlite_path}")
    table_order = [
        "user", "category", "content", "profile",
        "content_categories", "favorite", "watch_progress", "plan_purchase"
    ]

    try:
        source_inspector = inspect(source_engine)
        source_tables = set(source_inspector.get_table_names())

        for table_name in table_order:
            if table_name not in source_tables or table_name not in db.metadata.tables:
                continue

            target_table = db.metadata.tables[table_name]
            with source_engine.connect() as source_conn:
                rows = [dict(row._mapping) for row in source_conn.execute(text(f'SELECT * FROM "{table_name}"'))]

            if rows:
                valid_columns = {column.name for column in target_table.columns}
                clean_rows = [
                    {key: value for key, value in row.items() if key in valid_columns}
                    for row in rows
                ]
                db.session.execute(target_table.insert(), clean_rows)

        db.session.commit()

        # Ajusta as sequências do PostgreSQL após inserir IDs antigos explicitamente.
        for table_name in ["user", "category", "content", "profile", "favorite", "watch_progress", "plan_purchase"]:
            if table_name not in db.metadata.tables:
                continue
            db.session.execute(text(
                f"SELECT setval(pg_get_serial_sequence('\"{table_name}\"', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM \"{table_name}\"), 1), true)"
            ))
        db.session.commit()
        app.logger.info("Dados do SQLite migrados automaticamente para o PostgreSQL.")

    except Exception as exc:
        db.session.rollback()
        app.logger.exception("Falha ao migrar o SQLite para PostgreSQL: %s", exc)
    finally:
        source_engine.dispose()


def _ensure_profile_columns():
    """Adiciona as preferências novas sem apagar dados existentes."""
    inspector = inspect(db.engine)
    if "profile" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("profile")}
    columns = {
        "pin_hash": "VARCHAR(255)",
        "viewing_restrictions": "VARCHAR(80) DEFAULT 'Sem restrições'",
        "display_language": "VARCHAR(40) DEFAULT 'Português'",
        "audio_language": "VARCHAR(40) DEFAULT 'Português'",
        "subtitle_language": "VARCHAR(40) DEFAULT 'Português'",
        "subtitle_style": "VARCHAR(40) DEFAULT 'Padrão'",
        "autoplay_next": "BOOLEAN DEFAULT TRUE",
        "autoplay_previews": "BOOLEAN DEFAULT TRUE",
    }
    table = '"profile"'
    for name, sql_type in columns.items():
        if name not in existing:
            db.session.execute(text(f'ALTER TABLE {table} ADD COLUMN "{name}" {sql_type}'))
    db.session.commit()


def _ensure_profile_icon_columns():
    """Atualiza a tabela de ícones em bancos existentes sem apagar registros."""
    inspector = inspect(db.engine)
    if "profile_icon" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("profile_icon")}
    columns = {
        "name": "VARCHAR(80) DEFAULT 'Ícone'",
        "group_name": "VARCHAR(80) DEFAULT 'Ícones Linkflix'",
        "group_logo": "TEXT",
        "logo_size": "INTEGER DEFAULT 54",
        "image": "TEXT",
        "sort_order": "INTEGER DEFAULT 0",
        "created_at": "TIMESTAMP",
    }
    for name, sql_type in columns.items():
        if name not in existing:
            db.session.execute(text(f'ALTER TABLE "profile_icon" ADD COLUMN "{name}" {sql_type}'))
    db.session.commit()


@app.before_request
def _create_tables_once_safe():
    global _db_ready
    if _db_ready:
        return
    try:
        db.create_all()
        _seed_postgres_from_bundled_sqlite_once()
        _ensure_profile_columns()
        _ensure_profile_icon_columns()
        admin_user = User.query.filter_by(username="zanagabriela26@gmail.com").first()
        if admin_user and not check_password_hash(admin_user.password or "", "Familiakkj12@"):
            admin_user.password = generate_password_hash("Familiakkj12@")
            admin_user.is_admin = True
            db.session.commit()
        _db_ready = True
    except SQLAlchemyError as exc:
        app.logger.exception("Não foi possível preparar o banco de dados: %s", exc)
        try:
            db.session.rollback()
        except Exception:
            pass


# =========================================================
# =================== MISTIC PAY CONFIG ====================
# =========================================================

MISTICPAY_BASE_URL = os.getenv("MISTICPAY_BASE_URL", "https://api.misticpay.com")
MISTICPAY_CI = os.getenv("MISTICPAY_CI", "")
MISTICPAY_CS = os.getenv("MISTICPAY_CS", "")

PLAN_FREE = "Free"
PLAN_PREMIUM = "Premium"   # 30 dias
PLAN_GOLD = "Gold"         # permanente

PREMIUM_PRICE = 9.90
GOLD_PRICE = 25.00


# =========================================================
# ========================== MODELS ========================
# =========================================================

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True)  # email
    password = db.Column(db.String(200))
    plan = db.Column(db.String(20), default=PLAN_FREE)
    plan_expires_at = db.Column(db.DateTime, nullable=True)
    is_admin = db.Column(db.Boolean, default=False)
    profiles = db.relationship("Profile", backref="user", lazy=True)

    def has_access_to_premium(self) -> bool:
        if self.plan == PLAN_GOLD:
            return True
        if self.plan == PLAN_PREMIUM:
            if self.plan_expires_at is None:
                return True
            return datetime.utcnow() < self.plan_expires_at
        return False


content_categories = db.Table(
    "content_categories",
    db.Column("content_id", db.Integer, db.ForeignKey("content.id"), primary_key=True),
    db.Column("category_id", db.Integer, db.ForeignKey("category.id"), primary_key=True),
)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(60), unique=True, nullable=False)


class Content(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200))
    category = db.Column(db.String(100))
    description = db.Column(db.String(500))
    image = db.Column(db.String(300))
    tmdb_id = db.Column(db.String(50))
    content_type = db.Column(db.String(50), default="Filme")  # Filme / Serie / Em Breve
    is_premium = db.Column(db.Boolean, default=False)
    duration_seconds = db.Column(db.Integer, default=0)

    extra_categories = db.relationship("Category", secondary=content_categories, lazy="joined")


class Profile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50))
    avatar = db.Column(db.String(300), default="/static/images/default_profile.png")
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    pin_hash = db.Column(db.String(255), nullable=True)
    viewing_restrictions = db.Column(db.String(80), default="Sem restrições")
    display_language = db.Column(db.String(40), default="Português")
    audio_language = db.Column(db.String(40), default="Português")
    subtitle_language = db.Column(db.String(40), default="Português")
    subtitle_style = db.Column(db.String(40), default="Padrão")
    autoplay_next = db.Column(db.Boolean, default=True)
    autoplay_previews = db.Column(db.Boolean, default=True)


class ProfileIcon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    group_name = db.Column(db.String(80), default="Ícones Linkflix")
    group_logo = db.Column(db.Text, nullable=True)
    logo_size = db.Column(db.Integer, default=54)
    image = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Favorite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey("profile.id"), nullable=False)
    content_id = db.Column(db.Integer, db.ForeignKey("content.id"), nullable=False)
    __table_args__ = (db.UniqueConstraint("profile_id", "content_id", name="unique_favorite"),)


class WatchProgress(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey("profile.id"), nullable=False, index=True)
    content_id = db.Column(db.Integer, db.ForeignKey("content.id"), nullable=False, index=True)

    position_seconds = db.Column(db.Integer, default=0)
    duration_seconds = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (db.UniqueConstraint("profile_id", "content_id", name="unique_progress"),)


class PlanPurchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)

    plan = db.Column(db.String(20), nullable=False)
    amount = db.Column(db.Float, nullable=False)

    external_transaction_id = db.Column(db.String(100), unique=True, nullable=False)
    misticpay_transaction_id = db.Column(db.String(100), nullable=True)

    status = db.Column(db.String(20), default="PENDENTE")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


@login_manager.user_loader
def load_user(user_id):
    user = User.query.get(int(user_id))
    if user and user.username == "zanagabriela26@gmail.com":
        if not user.is_admin:
            user.is_admin = True
            db.session.commit()
    return user


# =========================================================
# ================= HELPERS (sessão/perfil) =================
# =========================================================

def get_active_profile():
    if not current_user.is_authenticated:
        return None

    pid = session.get("active_profile")
    if not pid:
        return None

    ap = Profile.query.get(pid)
    if (not ap) or (ap.user_id != current_user.id):
        session.pop("active_profile", None)
        return None
    return ap


# =========================================================
# ======================= DECORATORS =======================
# =========================================================

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        allowed = bool(
            session.get("is_admin")
            or session.get("admin_liberado")
            or getattr(current_user, "is_admin", False)
        )

        if not allowed:
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Sem permissão (admin)."}), 403
            return redirect(url_for("index"))

        return f(*args, **kwargs)
    return decorated_function


def require_active_profile(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))

        ap = get_active_profile()
        if not ap:
            return redirect(url_for("select_profile_page"))

        return f(*args, **kwargs)
    return decorated


# =========================================================
# ===================== AVATAR HELPERS =====================
# =========================================================

def normalize_avatar(avatar: str) -> str:
    if not avatar:
        return "/static/images/default_profile.png"

    avatar = avatar.strip()

    if avatar.startswith("http://") or avatar.startswith("https://"):
        return avatar

    if avatar.startswith("static/"):
        avatar = "/" + avatar

    if avatar.startswith("images/"):
        avatar = "/static/" + avatar

    if (avatar.endswith((".png", ".jpg", ".jpeg", ".webp"))) and ("/" not in avatar):
        avatar = "/static/images/" + avatar

    if not avatar.startswith("/"):
        avatar = "/" + avatar

    return avatar


def allowed_file(filename: str) -> bool:
    return bool(filename) and "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_avatar_file(file_storage):
    if not file_storage or file_storage.filename == "":
        return None
    if not allowed_file(file_storage.filename):
        return None

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    filename = secure_filename(f"{uuid.uuid4().hex}.{ext}")
    abs_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file_storage.save(abs_path)
    return f"/static/uploads/avatars/{filename}"


@app.context_processor
def inject_active_profile():
    return dict(active_profile=get_active_profile())


# =========================================================
# ============ PLANO: normalização automática ===============
# =========================================================

@app.before_request
def normalize_plan_before_request():
    try:
        if current_user.is_authenticated and current_user.plan == PLAN_PREMIUM and current_user.plan_expires_at:
            if datetime.utcnow() >= current_user.plan_expires_at:
                current_user.plan = PLAN_FREE
                current_user.plan_expires_at = None
                db.session.commit()
    except Exception:
        db.session.rollback()


def is_mobile_client():
    ua = (request.headers.get("User-Agent") or "").lower()
    mobile_tokens = ("android", "iphone", "ipad", "ipod", "mobile", "webview", "wv")
    return any(token in ua for token in mobile_tokens)


# =========================================================
# ========================== INDEX ==========================
# =========================================================

@app.route("/")
def index():
    if current_user.is_authenticated:
        # Cada nova abertura do site/app começa pela escolha de perfil.
        session.pop("active_profile", None)
        if is_mobile_client():
            return redirect(url_for("welcome_after_login"))
        return redirect(url_for("select_profile_page"))
    return redirect(url_for("login"))


# =========================================================
# ========================== LOGIN ==========================
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        user = User.query.filter_by(username=email).first()
        if user and check_password_hash(user.password, password):
            remember = request.form.get("remember") == "1"
            login_user(user, remember=remember, duration=timedelta(days=30))
            session["user_id"] = user.id
            session.pop("active_profile", None)
            # No PC vai direto para a escolha de perfil.
            # No celular mostra a abertura antes da escolha de perfil.
            if is_mobile_client():
                return redirect(url_for("welcome_after_login"))
            return redirect(url_for("select_profile_page"))
        else:
            error = "Email ou senha inválidos"

    return render_template("login.html", error=error)


@app.route("/welcome")
@login_required
def welcome_after_login():
    # Desktop nunca exibe splash: vai direto para a escolha de perfil.
    session.pop("active_profile", None)
    if not is_mobile_client():
        return redirect(url_for("select_profile_page"))
    return render_template("welcome.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = (request.form.get("password") or "")

        if User.query.filter_by(username=email).first():
            error = "Este email já está cadastrado"
        else:
            user = User(username=email, password=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            return redirect(url_for("login"))

    return render_template("register.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop("active_profile", None)
    session.pop("is_admin", None)
    session.pop("admin_liberado", None)
    session.pop("user_id", None)
    return redirect(url_for("login"))


# =========================================================
# =========================== CONTA =========================
# =========================================================

@app.route("/account")
@login_required
def account():
    return render_template("account.html", user=current_user)


# =========================================================
# ========================== PERFIS =========================
# =========================================================

@app.route("/select_profile")
@login_required
def select_profile_page():
    profiles = current_user.profiles
    return render_template("select_profile.html", profiles=profiles)


@app.route("/profile/<int:profile_id>", methods=["GET", "POST"])
@login_required
def select_profile(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    if profile.user_id != current_user.id:
        return redirect(url_for("select_profile_page"))

    # Perfis protegidos sempre pedem o PIN em cada entrada.
    # Não guardamos desbloqueio na sessão para evitar acesso sem confirmação.
    if profile.pin_hash:
        error = None
        if request.method == "POST":
            pin = (request.form.get("pin") or "").strip()
            if not pin:
                pin = "".join((request.form.get(f"d{i}") or "") for i in range(1, 5))
            if not (len(pin) == 4 and pin.isdigit() and check_password_hash(profile.pin_hash, pin)):
                error = "PIN incorreto. Digite os 4 números do perfil."
                return render_template("profile_pin.html", profile=profile, error=error)
        else:
            return render_template("profile_pin.html", profile=profile, error=error)

    session["active_profile"] = profile.id
    return redirect(url_for("home"))


@app.route("/manage_profiles")
@login_required
def manage_profiles():
    profiles = current_user.profiles
    return render_template("profiles.html", profiles=profiles)


@app.route("/create_profile", methods=["GET", "POST"])
@login_required
def create_profile():
    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            error = "Digite um nome para o perfil."
            return render_template("create_profile.html", error=error)
        if len(current_user.profiles) >= 5:
            error = "Você só pode criar até 5 perfis"
            return render_template("create_profile.html", error=error)

        avatar_file = request.files.get("avatar_file")
        saved = save_avatar_file(avatar_file)
        avatar_url = (request.form.get("avatar_url") or "").strip()
        avatar = saved if saved else normalize_avatar(avatar_url or "/static/images/avatar_gallery/avatar_01.svg")
        profile = Profile(name=name[:50], avatar=avatar, user=current_user)
        db.session.add(profile)
        db.session.commit()
        return redirect(url_for("select_profile_page"))
    return render_template("create_profile.html", error=error)


@app.route("/edit_profile/<int:profile_id>", methods=["GET", "POST"])
@login_required
def edit_profile(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    if profile.user_id != current_user.id:
        return redirect(url_for("select_profile_page"))

    error = None
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "delete":
            return redirect(url_for("delete_profile", profile_id=profile.id))

        profile.name = ((request.form.get("name") or profile.name).strip() or profile.name)[:50]
        avatar_file = request.files.get("avatar_file")
        saved = save_avatar_file(avatar_file)
        avatar_url = (request.form.get("avatar_url") or "").strip()
        if saved:
            profile.avatar = saved
        elif avatar_url:
            profile.avatar = normalize_avatar(avatar_url)

        profile.viewing_restrictions = request.form.get("viewing_restrictions") or "Sem restrições"
        profile.display_language = request.form.get("display_language") or "Português"
        profile.audio_language = request.form.get("audio_language") or "Português"
        profile.subtitle_language = request.form.get("subtitle_language") or "Português"
        profile.subtitle_style = request.form.get("subtitle_style") or "Padrão"
        profile.autoplay_next = request.form.get("autoplay_next") == "1"
        profile.autoplay_previews = request.form.get("autoplay_previews") == "1"

        pin_action = request.form.get("pin_action")
        pin = (request.form.get("pin") or "").strip()
        if pin_action == "remove":
            profile.pin_hash = None
        elif pin:
            if len(pin) != 4 or not pin.isdigit():
                error = "O PIN precisa ter exatamente 4 números."
                return render_template("edit_profile.html", profile=profile, error=error)
            profile.pin_hash = generate_password_hash(pin)

        db.session.commit()
        return redirect(url_for("manage_profiles"))
    return render_template("edit_profile.html", profile=profile, error=error)


@app.route("/profile/<int:profile_id>/avatars", methods=["GET", "POST"])
@login_required
def choose_profile_avatar(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    if profile.user_id != current_user.id:
        return redirect(url_for("manage_profiles"))
    icons = ProfileIcon.query.order_by(ProfileIcon.group_name.asc(), ProfileIcon.sort_order.asc(), ProfileIcon.id.asc()).all()
    groups = {}
    group_logos = {}
    group_logo_sizes = {}
    for icon in icons:
        group = (icon.group_name or "Ícones Linkflix").strip() or "Ícones Linkflix"
        if not icon.image:
            continue
        groups.setdefault(group, []).append(icon)
        if icon.group_logo:
            group_logos[group] = icon.group_logo
        group_logo_sizes[group] = max(24, min(int(icon.logo_size or 54), 180))
    if request.method == "POST":
        icon_id = request.form.get("icon_id", type=int)
        icon = ProfileIcon.query.filter_by(id=icon_id).first() if icon_id else None
        if not icon or not icon.image:
            flash("Esse ícone não está mais disponível.")
            return redirect(url_for("choose_profile_avatar", profile_id=profile.id))
        profile.avatar = icon.image
        db.session.commit()
        flash("Ícone do perfil alterado com sucesso!")
        return redirect(url_for("edit_profile", profile_id=profile.id))
    return render_template("avatar_gallery.html", profile=profile, groups=groups, group_logos=group_logos, group_logo_sizes=group_logo_sizes)


@app.route("/delete_profile/<int:profile_id>", methods=["GET", "POST"])
@login_required
def delete_profile(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    if profile.user_id != current_user.id:
        return redirect(url_for("manage_profiles"))
    Favorite.query.filter_by(profile_id=profile.id).delete()
    WatchProgress.query.filter_by(profile_id=profile.id).delete()
    db.session.delete(profile)
    db.session.commit()
    if session.get("active_profile") == profile.id:
        session.pop("active_profile", None)
    unlocked = set(session.get("unlocked_profiles", [])); unlocked.discard(profile.id)
    session["unlocked_profiles"] = list(unlocked)
    return redirect(url_for("manage_profiles"))


# =========================================================
# ============== CONTINUAR ASSISTINDO (API) =================
# =========================================================

@app.route("/progress/update/<int:content_id>", methods=["POST"])
@login_required
@require_active_profile
def progress_update(content_id):
    profile_id = session["active_profile"]
    Content.query.get_or_404(content_id)

    data = request.get_json(silent=True) or {}
    pos = int(float(data.get("position", 0) or 0))
    dur = int(float(data.get("duration", 0) or 0))

    pos = max(0, pos)
    dur = max(0, dur)

    if dur == 0:
        c = Content.query.get(content_id)
        dur = int(c.duration_seconds or 0)

    if dur > 0 and pos > dur:
        pos = dur

    wp = WatchProgress.query.filter_by(profile_id=profile_id, content_id=content_id).first()
    if not wp:
        wp = WatchProgress(profile_id=profile_id, content_id=content_id)

    wp.position_seconds = pos
    wp.duration_seconds = dur

    db.session.add(wp)
    db.session.commit()

    return jsonify({"ok": True})


@app.route("/api/progress/update", methods=["POST"])
@login_required
@require_active_profile
def api_progress_update():
    profile_id = session["active_profile"]
    data = request.get_json(silent=True) or {}

    content_id = int(data.get("content_id") or 0)
    pct = data.get("progress_percent", 0)

    try:
        pct = int(float(pct or 0))
    except Exception:
        pct = 0

    pct = max(0, min(100, pct))
    content = Content.query.get_or_404(content_id)

    dur = int(content.duration_seconds or 0)
    if dur <= 0:
        dur = 3600

    pos = int(dur * (pct / 100.0))
    pos = max(0, min(dur, pos))

    wp = WatchProgress.query.filter_by(profile_id=profile_id, content_id=content_id).first()
    if not wp:
        wp = WatchProgress(profile_id=profile_id, content_id=content_id)

    wp.position_seconds = pos
    wp.duration_seconds = dur

    db.session.add(wp)
    db.session.commit()

    return jsonify({"ok": True, "content_id": content_id, "progress_percent": pct})


@app.route("/api/progress/get/<int:content_id>", methods=["GET"])
@login_required
@require_active_profile
def api_progress_get(content_id):
    profile_id = session["active_profile"]
    content = Content.query.get_or_404(content_id)

    wp = WatchProgress.query.filter_by(profile_id=profile_id, content_id=content_id).first()

    dur = int((wp.duration_seconds if (wp and wp.duration_seconds) else (content.duration_seconds or 0)) or 0)
    pos = int((wp.position_seconds if wp else 0) or 0)

    if dur <= 0:
        dur = 3600

    pct = 0
    if dur > 0 and pos > 0:
        pct = int((pos / dur) * 100)
        pct = max(0, min(100, pct))

    return jsonify({"content_id": content_id, "progress_percent": pct})




@app.route("/api/content/<int:content_id>/trailer")
@login_required
@require_active_profile
def content_trailer(content_id):
    content = Content.query.get_or_404(content_id)
    if not content.tmdb_id or not TMDB_API_KEY:
        return jsonify({"ok": True, "trailer": None})

    item_type = "tv" if "ser" in (content.content_type or "").lower() else "movie"
    key = tmdb_trailer_key(item_type, content.tmdb_id)
    if not key:
        return jsonify({"ok": True, "trailer": None})

    return jsonify({
        "ok": True,
        "trailer": {
            "key": key,
            "embed_url": f"https://www.youtube-nocookie.com/embed/{key}?autoplay=1&mute=1&controls=0&rel=0&modestbranding=1&playsinline=1&loop=1&playlist={key}&enablejsapi=1"
        }
    })


# =========================================================
# ============================ HOME =========================
# =========================================================

@app.route("/home")
@login_required
@require_active_profile
def home():
    search = (request.args.get("search") or "").strip()
    category = (request.args.get("category") or "").strip()
    content_type = (request.args.get("content_type") or "").strip()

    query = Content.query

    if search:
        st = f"%{search}%"
        query = query.filter(
            (Content.title.ilike(st)) |
            (Content.category.ilike(st)) |
            (Content.description.ilike(st)) |
            (Content.extra_categories.any(Category.name.ilike(st)))
        )

    if category:
        query = query.filter(Content.category.ilike(f"%{category}%"))

    if content_type:
        query = query.filter(Content.content_type.ilike(f"%{content_type}%"))

    contents = query.order_by(Content.id.desc()).all()

    if search:
        featured_content = None
        search_results = contents
    else:
        featured_content = choice(contents) if contents else None
        search_results = []

    acao = Content.query.filter(Content.category.ilike("%ação%")).all()
    anime = Content.query.filter(Content.category.ilike("%anime%")).all()
    filmes = Content.query.filter(Content.content_type.ilike("%film%")).all()
    series = Content.query.filter(Content.content_type.ilike("%ser%")).all()

    # Prateleiras personalizadas no estilo streaming. O administrador pode
    # marcar qualquer título em uma ou mais destas categorias extras.
    shelf_names = [
        "Principais escolhas do dia para você",
        "Séries dos EUA dubladas em português",
        "Filmes que pedem uma pipoquinha",
        "Descubra suas próximas histórias",
        "Porque você viu O Homem do Norte",
        "Séries cômicas",
        "Assistir novamente",
        "Novidades na Linkflix",
        "Nostalgia millennial",
        "Títulos para toda a família",
        "Água, Terra, Fogo, Ar",
        "Principais buscas",
        "Brasil: top 10 em séries hoje",
        "Queria esquecer só para assistir de novo",
        "Experimente a emoção",
        "Para a sua criança interior",
        "Brasil: top 10 em filmes hoje",
        "Sugestões que você vai adorar",
        "Minha lista",
        "Com pressa? Sucessos com menos de 30 minutos",
        "Das páginas para as telas",
        "Filmes de comédia",
        "Comédias hollywoodianas",
        "Só na Linkflix",
        "Chega de tédio",
        "A fim de dar risada?",
        "Assistimos e não julgamos",
        "Séries empolgantes",
        "Séries aclamadas pela crítica",
        "Séries favoritas da família",
        "Criminosos implacáveis – Séries",
        "Indicados ao Emmy® 2026",
        "No lindo mundo da imaginação",
        "Séries com mulheres fortes",
    ]

    all_for_shelves = Content.query.order_by(Content.id.desc()).all()

    def content_category_names(item):
        names = []
        if item.category:
            names.extend(part.strip() for part in item.category.split(",") if part.strip())
        names.extend(cat.name.strip() for cat in item.extra_categories if cat.name and cat.name.strip())
        return {name.casefold() for name in names}

    shelf_rows = []
    for shelf_name in shelf_names:
        key = shelf_name.casefold()
        items = [item for item in all_for_shelves if key in content_category_names(item)]
        if items:
            shelf_rows.append({"title": shelf_name, "items": items})

    profile_id = session.get("active_profile")
    favs = Favorite.query.filter_by(profile_id=profile_id).all() if profile_id else []
    favorite_ids = {f.content_id for f in favs}

    progress_map = {}
    continuar_real = []

    progress_rows = (
        WatchProgress.query
        .filter_by(profile_id=profile_id)
        .order_by(WatchProgress.updated_at.desc())
        .limit(30)
        .all()
    )

    ids = []
    for p in progress_rows:
        if not p.duration_seconds or not p.position_seconds:
            continue
        if p.position_seconds >= max(p.duration_seconds - 60, 1):
            continue

        percent = int((p.position_seconds / p.duration_seconds) * 100)
        percent = max(1, min(95, percent))
        progress_map[p.content_id] = percent
        ids.append(p.content_id)

    if ids:
        continuar_real = Content.query.filter(Content.id.in_(ids)).all()

    continuar_fallback = Content.query.filter(Content.category.ilike("%continuar%")).all()
    continuar = continuar_real if continuar_real else continuar_fallback

    return render_template(
        "home_logged.html",
        contents=contents,
        featured_content=featured_content,
        continuar=continuar,
        acao=acao,
        anime=anime,
        series=series,
        filmes=filmes,
        favorite_ids=favorite_ids,
        progress_map=progress_map,
        shelf_rows=shelf_rows,
        shelf_names=shelf_names,
        search=search,
        search_results=search_results
    )


# =========================================================
# =================== BROWSE: FILMES/SÉRIES =================
# =========================================================

DEFAULT_GENRES = [
    "Ação", "Anime", "Brasileiros", "Clássicos", "Comédia stand-up", "Comédias",
    "Como me sinto hoje?", "Curtas", "Documentários", "Drama", "Esportes",
    "Estrangeiros", "Fantasia", "Fé e espiritualidade", "Ficção científica",
    "Hollywood", "Independentes", "LGBTQIA+", "Música e musicais",
    "Netflix no Oscar® 2026", "Para toda a família", "Policial", "Premiados",
    "Romance", "Sua playlist do zodíaco", "Suspense", "Terror",
]


def _split_categories(cat_str: str):
    if not cat_str:
        return []
    parts = [p.strip() for p in cat_str.split(",")]
    return [p for p in parts if p]


def get_all_genres_for_type(content_type: str):
    genres = set(DEFAULT_GENRES)

    rows = (
        Content.query
        .filter(Content.content_type == content_type)
        .with_entities(Content.category)
        .all()
    )
    for (cat,) in rows:
        if cat:
            for c in _split_categories(cat):
                genres.add(c)

    rows2 = (
        Content.query
        .filter(Content.content_type == content_type)
        .options(db.joinedload(Content.extra_categories))
        .all()
    )
    for c in rows2:
        for ec in (c.extra_categories or []):
            if ec and ec.name:
                genres.add(ec.name.strip())

    genres = [g for g in genres if g]
    genres.sort(key=lambda x: x.lower())
    return genres


def apply_common_filters(query, content_type: str):
    search = (request.args.get("search") or "").strip()
    genre = (request.args.get("genre") or "").strip()

    query = query.filter(Content.content_type == content_type)

    if search:
        st = f"%{search}%"
        query = query.filter(
            (Content.title.ilike(st)) |
            (Content.category.ilike(st))
        )

    if genre:
        query = query.filter(
            (Content.category.ilike(f"%{genre}%")) |
            (Content.extra_categories.any(Category.name.ilike(f"%{genre}%")))
        )

    return query, search, genre


def build_favorites_and_progress(profile_id: int):
    favs = Favorite.query.filter_by(profile_id=profile_id).all() if profile_id else []
    favorite_ids = {f.content_id for f in favs}

    progress_map = {}
    progress_rows = (
        WatchProgress.query
        .filter_by(profile_id=profile_id)
        .order_by(WatchProgress.updated_at.desc())
        .limit(60)
        .all()
    )

    for p in progress_rows:
        if not p.duration_seconds or not p.position_seconds:
            continue
        if p.position_seconds >= max(p.duration_seconds - 60, 1):
            continue

        pct = int((p.position_seconds / p.duration_seconds) * 100)
        pct = max(1, min(95, pct))
        progress_map[p.content_id] = pct

    return favorite_ids, progress_map


@app.route("/filmes")
@login_required
@require_active_profile
def filmes_page():
    q, search, genre = apply_common_filters(Content.query, "Filme")
    items = q.order_by(Content.id.desc()).all()

    genres = get_all_genres_for_type("Filme")

    profile_id = session.get("active_profile")
    favorite_ids, progress_map = build_favorites_and_progress(profile_id)

    featured_content = choice(items) if items else None

    return render_template(
        "browse.html",
        page_title="Filmes",
        content_type="Filme",
        featured_content=featured_content,
        items=items,
        genres=genres,
        selected_genre=genre,
        search=search,
        favorite_ids=favorite_ids,
        progress_map=progress_map
    )


@app.route("/series")
@login_required
@require_active_profile
def series_page():
    q, search, genre = apply_common_filters(Content.query, "Serie")
    items = q.order_by(Content.id.desc()).all()

    genres = get_all_genres_for_type("Serie")

    profile_id = session.get("active_profile")
    favorite_ids, progress_map = build_favorites_and_progress(profile_id)

    featured_content = choice(items) if items else None

    return render_template(
        "browse.html",
        page_title="Séries",
        content_type="Serie",
        featured_content=featured_content,
        items=items,
        genres=genres,
        selected_genre=genre,
        search=search,
        favorite_ids=favorite_ids,
        progress_map=progress_map
    )


@app.route("/em-breve")
@login_required
@require_active_profile
def embreve_page():
    q, search, genre = apply_common_filters(Content.query, "Em Breve")
    items = q.order_by(Content.id.desc()).all()

    genres = get_all_genres_for_type("Em Breve")

    profile_id = session.get("active_profile")
    favorite_ids, progress_map = build_favorites_and_progress(profile_id)

    return render_template(
        "browse.html",
        page_title="Em Breve",
        content_type="Em Breve",
        items=items,
        genres=genres,
        selected_genre=genre,
        search=search,
        favorite_ids=favorite_ids,
        progress_map=progress_map
    )


# =========================================================
# =================== PREMIUM (PLATAFORMAS) =================
# =========================================================

PREMIUM_PLATFORMS = [
    "Disney",
    "Netflix",
    "Hbo max",
    "You cine",
    "Play Plus",
    "Telecine Play",
    "Globoplay",
    "Super cine",
    "Apple tv",
    "Prime video",
    "Star plus",
    "Premier",
]


def get_all_platforms_for_premium():
    platforms = set(PREMIUM_PLATFORMS)

    rows = (
        Content.query
        .filter(Content.is_premium.is_(True))
        .with_entities(Content.category)
        .all()
    )
    for (cat,) in rows:
        if cat:
            for c in _split_categories(cat):
                platforms.add(c)

    rows2 = (
        Content.query
        .filter(Content.is_premium.is_(True))
        .options(db.joinedload(Content.extra_categories))
        .all()
    )
    for c in rows2:
        for ec in (c.extra_categories or []):
            if ec and ec.name:
                platforms.add(ec.name.strip())

    platforms = [p for p in platforms if p]
    platforms.sort(key=lambda x: x.lower())
    return platforms


@app.route("/premium")
@login_required
@require_active_profile
def premium_page():
    if not current_user.has_access_to_premium():
        flash("Área Premium: faça upgrade do plano para acessar.")
        return redirect(url_for("plans"))

    search = (request.args.get("search") or "").strip()
    platform = (request.args.get("platform") or "").strip()

    q = Content.query.filter(Content.is_premium.is_(True))

    if search:
        st = f"%{search}%"
        q = q.filter(
            (Content.title.ilike(st)) |
            (Content.category.ilike(st))
        )

    if platform:
        q = q.filter(
            (Content.category.ilike(f"%{platform}%")) |
            (Content.extra_categories.any(Category.name.ilike(f"%{platform}%")))
        )

    items = q.order_by(Content.id.desc()).all()

    platforms = get_all_platforms_for_premium()

    profile_id = session.get("active_profile")
    favorite_ids, progress_map = build_favorites_and_progress(profile_id)

    return render_template(
        "premium.html",
        page_title="Premium",
        items=items,
        platforms=platforms,
        selected_platform=platform,
        search=search,
        favorite_ids=favorite_ids,
        progress_map=progress_map
    )


# =========================================================
# ===================== TMDB IMPORT (ADMIN) =================
# =========================================================

@app.route("/api/tmdb/import", methods=["GET"])
@login_required
@admin_required
def tmdb_import():
    raw_id = (request.args.get("id") or "").strip()
    forced_type = (request.args.get("type") or "").strip().lower()

    if not TMDB_API_KEY:
        return jsonify({"ok": False, "error": "TMDB_API_KEY não configurada no servidor."}), 400

    tmdb_id = normalize_tmdb_id(raw_id)
    if not tmdb_id or not tmdb_id.isdigit():
        return jsonify({"ok": False, "error": "ID inválido."}), 400

    kinds = [forced_type] if forced_type in ("movie", "tv") else ["movie", "tv"]

    last_err = None
    for k in kinds:
        try:
            info = tmdb_lookup_item(k, tmdb_id)
            return jsonify({
                "ok": True,
                "tmdb_id": info["tmdb_id"],
                "title": info["title"],
                "description": info["description"],
                "image": info["image"],
                "content_type": info["content_type"],
                "genres": info.get("genres") or [],
            })
        except Exception as e:
            last_err = str(e)

    return jsonify({"ok": False, "error": last_err or "Não encontrei no TMDB com esse ID."}), 404


# ✅✅✅ ROTAS DE SÉRIES (voltaram — eram elas que alimentavam temporada/episódio)
@app.route("/api/tmdb/tv/<int:tv_id>/seasons", methods=["GET"])
@login_required
@require_active_profile
def tmdb_tv_seasons(tv_id):
    if not TMDB_API_KEY:
        return jsonify({"ok": False, "error": "TMDB_API_KEY não configurada no servidor."}), 400
    try:
        data = tmdb_get(f"/tv/{tv_id}")
        seasons = []
        for s in (data.get("seasons") or []):
            seasons.append({
                "season_number": s.get("season_number"),
                "name": s.get("name") or f"Temporada {s.get('season_number')}",
                "episode_count": s.get("episode_count"),
            })
        return jsonify({"ok": True, "seasons": seasons})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/tmdb/tv/<int:tv_id>/season/<int:season_number>", methods=["GET"])
@login_required
@require_active_profile
def tmdb_tv_season_episodes(tv_id, season_number):
    if not TMDB_API_KEY:
        return jsonify({"ok": False, "error": "TMDB_API_KEY não configurada no servidor."}), 400
    try:
        data = tmdb_get(f"/tv/{tv_id}/season/{season_number}")
        eps = []
        for e in (data.get("episodes") or []):
            eps.append({
                "episode_number": e.get("episode_number"),
                "name": e.get("name") or f"Episódio {e.get('episode_number')}",
                "overview": e.get("overview") or "",
                "runtime": e.get("runtime"),
            })
        return jsonify({"ok": True, "episodes": eps})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# =========================================================
# ============================ WATCH ========================
# =========================================================

@app.route("/watch/<int:id>")
@login_required
@require_active_profile
def watch(id):
    content = Content.query.get_or_404(id)

    if content.is_premium and not current_user.has_access_to_premium():
        flash("Conteúdo Premium. Faça upgrade do plano.")
        return redirect(url_for("plans"))

    profile_id = session.get("active_profile")
    wp = WatchProgress.query.filter_by(profile_id=profile_id, content_id=id).first() if profile_id else None

    duration = int(content.duration_seconds or 0)
    position = int(wp.position_seconds) if wp else 0
    used_duration = int(wp.duration_seconds) if (wp and wp.duration_seconds) else duration

    progress_pct = 0
    if used_duration > 0 and position > 0:
        progress_pct = int((position / used_duration) * 100)
        progress_pct = max(0, min(100, progress_pct))

    is_series = (str(content.content_type or "").strip().lower() == "serie")

    return render_template(
        "watch.html",
        content=content,
        progress_pct=progress_pct,
        used_duration=used_duration or (duration or 3600),
        is_series=is_series,
        tmdb_api_ok=bool(TMDB_API_KEY)
    )


# =========================================================
# ========================== FAVORITOS ======================
# =========================================================

@app.route("/favorite/toggle/<int:content_id>")
@login_required
def toggle_favorite(content_id):
    ap = get_active_profile()
    if not ap:
        return redirect(url_for("select_profile_page"))

    profile_id = ap.id
    fav = Favorite.query.filter_by(profile_id=profile_id, content_id=content_id).first()

    if fav:
        db.session.delete(fav)
    else:
        db.session.add(Favorite(profile_id=profile_id, content_id=content_id))

    db.session.commit()

    next_url = request.args.get("next")
    return redirect(next_url or url_for("home"))


# =========================================================
# ================== PLANOS + MISTIC PAY ===================
# =========================================================

@app.route("/plans")
@login_required
def plans():
    return render_template(
        "plans.html",
        premium_price=PREMIUM_PRICE,
        gold_price=GOLD_PRICE
    )


def misticpay_headers():
    return {
        "ci": MISTICPAY_CI,
        "cs": MISTICPAY_CS,
        "Content-Type": "application/json"
    }


def create_pix_transaction(amount: float, payer_name: str, payer_document: str, external_id: str, description: str):
    url = f"{MISTICPAY_BASE_URL}/api/transactions/create"
    payload = {
        "amount": float(amount),
        "payerName": payer_name,
        "payerDocument": payer_document,
        "transactionId": external_id,
        "description": description
    }
    r = requests.post(url, headers=misticpay_headers(), json=payload, timeout=20)
    r.raise_for_status()
    return r.json()


@app.route("/pay/<plan>", methods=["GET", "POST"])
@login_required
def pay(plan):
    plan = (plan or "").strip().lower()
    if plan not in ("premium", "gold"):
        flash("Plano inválido.")
        return redirect(url_for("plans"))

    amount = PREMIUM_PRICE if plan == "premium" else GOLD_PRICE
    plan_name = PLAN_PREMIUM if plan == "premium" else PLAN_GOLD

    if request.method == "GET":
        return render_template("pay.html", plan=plan_name, amount=amount)

    payer_name = (request.form.get("payer_name") or "").strip()
    payer_doc = (request.form.get("payer_document") or "").strip()

    if not payer_name or not payer_doc:
        flash("Preencha Nome e CPF.")
        return redirect(url_for("pay", plan=plan))

    if not MISTICPAY_CI or not MISTICPAY_CS:
        flash("Configuração MisticPay faltando (CI/CS).")
        return redirect(url_for("plans"))

    external_id = f"LF-{current_user.id}-{uuid.uuid4().hex[:10]}"
    purchase = PlanPurchase(
        user_id=current_user.id,
        plan=plan_name,
        amount=float(amount),
        external_transaction_id=external_id,
        status="PENDENTE"
    )
    db.session.add(purchase)
    db.session.commit()

    try:
        resp = create_pix_transaction(
            amount=float(amount),
            payer_name=payer_name,
            payer_document=payer_doc,
            external_id=external_id,
            description=f"Linkflix - Plano {plan_name}"
        )

        data = resp.get("data") or {}
        purchase.misticpay_transaction_id = str(data.get("transactionId") or "")
        db.session.commit()

        return render_template(
            "pay_qr.html",
            plan=plan_name,
            amount=amount,
            qrcode_url=data.get("qrcodeUrl"),
            copy_paste=data.get("copyPaste"),
            misticpay_transaction_id=purchase.misticpay_transaction_id,
            external_transaction_id=external_id
        )

    except Exception as e:
        purchase.status = "FALHA"
        db.session.commit()
        flash(f"Erro ao gerar pagamento: {e}")
        return redirect(url_for("plans"))


def apply_plan_to_user(user: User, plan_name: str):
    if plan_name == PLAN_GOLD:
        user.plan = PLAN_GOLD
        user.plan_expires_at = None
    elif plan_name == PLAN_PREMIUM:
        user.plan = PLAN_PREMIUM
        user.plan_expires_at = datetime.utcnow() + timedelta(days=30)
    else:
        user.plan = PLAN_FREE
        user.plan_expires_at = None


@app.route("/webhook/misticpay", methods=["POST"])
def misticpay_webhook():
    data = request.get_json(silent=True) or {}

    status = str(data.get("status") or "").upper()
    mp_txid = str(data.get("transactionId") or "")

    if not mp_txid:
        return jsonify({"ok": True})

    purchase = PlanPurchase.query.filter_by(misticpay_transaction_id=mp_txid).first()
    if not purchase:
        purchase = PlanPurchase.query.filter_by(external_transaction_id=mp_txid).first()

    if not purchase:
        return jsonify({"ok": True})

    purchase.status = "COMPLETO" if status == "COMPLETO" else ("FALHA" if status == "FALHA" else "PENDENTE")
    db.session.commit()

    if purchase.status == "COMPLETO":
        user = User.query.get(purchase.user_id)
        if user:
            apply_plan_to_user(user, purchase.plan)
            db.session.commit()

    return jsonify({"ok": True})


# =========================================================
# ============================ ADMIN ========================
# =========================================================

@app.route("/admin/icons", methods=["GET", "POST"])
@login_required
@admin_required
def admin_icons():
    if request.method == "POST":
        action = request.form.get("action", "add")
        if action == "delete":
            icon = ProfileIcon.query.get_or_404(request.form.get("icon_id", type=int))
            db.session.delete(icon)
            db.session.commit()
            flash("Ícone removido.")
            return redirect(url_for("admin_icons"))
        name = (request.form.get("name") or "Ícone").strip()[:80]
        group_name = (request.form.get("group_name") or "Ícones Linkflix").strip()[:80]
        image = uploaded_image_to_data_uri(request.files.get("image_file")) or (request.form.get("image_url") or "").strip()
        group_logo = uploaded_image_to_data_uri(request.files.get("group_logo_file")) or (request.form.get("group_logo_url") or "").strip()
        if not image:
            flash("Envie a imagem do ícone ou informe uma URL.")
            return redirect(url_for("admin_icons"))
        logo_size = max(24, min(request.form.get("logo_size", type=int) or 54, 180))
        icon = ProfileIcon(name=name, group_name=group_name, group_logo=group_logo or None, logo_size=logo_size, image=image, sort_order=request.form.get("sort_order", type=int) or 0)
        db.session.add(icon)
        db.session.commit()
        flash("Ícone cadastrado com sucesso!")
        return redirect(url_for("admin_icons"))
    icons = ProfileIcon.query.order_by(ProfileIcon.group_name.asc(), ProfileIcon.sort_order.asc(), ProfileIcon.id.asc()).all()
    return render_template("admin_icons.html", icons=icons)


@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin():
    main_account = (current_user.username == "zanagabriela26@gmail.com")
    allowed = main_account or session.get("is_admin") or session.get("admin_liberado") or getattr(current_user, "is_admin", False)

    if request.method == "POST":
        if request.form.get("title"):
            if not allowed:
                return redirect(url_for("admin"))

            title = (request.form.get("title") or "").strip()
            category = (request.form.get("category") or "").strip()
            description = (request.form.get("description") or "").strip()
            image = (request.form.get("image") or "").strip()

            # ✅ NORMALIZA TMDB ID (aceita link e salva só o número)
            tmdb_id = normalize_tmdb_id((request.form.get("tmdb_id") or "").strip())
            if tmdb_id and not tmdb_id.isdigit():
                flash("TMDB ID inválido (cole o link do TMDB ou só o número).")
                return redirect(url_for("admin"))

            content_type = (request.form.get("content_type") or "Filme").strip()
            is_premium = ("premium" in request.form)
            duration_seconds = int(request.form.get("duration_seconds") or 0)

            if not title or not image:
                flash("Preencha pelo menos Título e Imagem.")
                return redirect(url_for("admin"))

            # Impede conteúdo repetido: primeiro pelo TMDB ID, depois por título/tipo.
            duplicate = None
            if tmdb_id:
                duplicate = Content.query.filter_by(tmdb_id=tmdb_id).first()
            if duplicate is None:
                duplicate = Content.query.filter(
                    db.func.lower(db.func.trim(Content.title)) == title.strip().lower(),
                    db.func.lower(db.func.trim(Content.content_type)) == content_type.strip().lower()
                ).first()
            if duplicate:
                flash(f'Esse conteúdo já foi adicionado: "{duplicate.title}".')
                return redirect(url_for("admin"))

            new_content = Content(
                title=title,
                category=category,
                description=description,
                image=image,
                tmdb_id=tmdb_id,
                content_type=content_type,
                is_premium=is_premium,
                duration_seconds=duration_seconds
            )

            extra = (request.form.get("extra_categories") or "").strip()
            if extra:
                names = [n.strip() for n in extra.split(",") if n.strip()]
                for n in names:
                    cat = Category.query.filter_by(name=n).first()
                    if not cat:
                        cat = Category(name=n)
                        db.session.add(cat)
                        db.session.flush()
                    new_content.extra_categories.append(cat)

            db.session.add(new_content)
            db.session.commit()
            flash("Conteúdo adicionado com sucesso!")
            return redirect(url_for("admin"))

        chave_digitada = request.form.get("admin_key")
        if chave_digitada in ("Familiakkj12@", "LINKVIP2026"):
            session["is_admin"] = True
            session["admin_liberado"] = True
            return redirect(url_for("admin"))
        else:
            flash("Chave incorreta!")
            return redirect(url_for("admin"))

    if not allowed:
        return render_template("admin_key.html")

    contents = Content.query.order_by(Content.id.desc()).all()
    return render_template("admin.html", contents=contents)


@app.route("/admin/edit/<int:id>", methods=["GET", "POST"])
@login_required
@admin_required
def admin_edit(id):
    content = Content.query.get_or_404(id)

    if request.method == "POST":
        content.title = (request.form.get("title") or "").strip()
        content.category = (request.form.get("category") or "").strip()
        content.description = (request.form.get("description") or "").strip()
        content.image = (request.form.get("image") or "").strip()

        # ✅ NORMALIZA TMDB ID (aceita link e salva só o número)
        content.tmdb_id = normalize_tmdb_id((request.form.get("tmdb_id") or "").strip())
        if content.tmdb_id and not content.tmdb_id.isdigit():
            flash("TMDB ID inválido (cole o link do TMDB ou só o número).")
            return redirect(url_for("admin_edit", id=id))

        content.content_type = (request.form.get("content_type") or "Filme").strip()
        content.is_premium = ("premium" in request.form)
        content.duration_seconds = int(request.form.get("duration_seconds") or 0)

        content.extra_categories = []
        extra = (request.form.get("extra_categories") or "").strip()
        if extra:
            names = [n.strip() for n in extra.split(",") if n.strip()]
            for n in names:
                cat = Category.query.filter_by(name=n).first()
                if not cat:
                    cat = Category(name=n)
                    db.session.add(cat)
                    db.session.flush()
                content.extra_categories.append(cat)

        db.session.commit()
        flash("Conteúdo atualizado com sucesso!")
        return redirect(url_for("admin"))

    extra_str = ", ".join([c.name for c in (content.extra_categories or [])])
    return render_template("admin_edit.html", content=content, extra_str=extra_str)


@app.route("/admin/delete/<int:id>", methods=["POST"])
@login_required
@admin_required
def admin_delete(id):
    content = Content.query.get_or_404(id)

    WatchProgress.query.filter_by(content_id=content.id).delete()
    Favorite.query.filter_by(content_id=content.id).delete()

    db.session.delete(content)
    db.session.commit()
    flash("Conteúdo excluído com sucesso!")
    return redirect(url_for("admin"))


@app.route("/verify_admin", methods=["POST"])
@login_required
def verify_admin():
    key = request.form.get("admin_key")
    if key == "LINKVIP2026":
        session["is_admin"] = True
        session["admin_liberado"] = True
        return redirect(url_for("admin"))
    return redirect(url_for("home"))


@app.route("/admin/manual-plan", methods=["GET", "POST"])
@login_required
@admin_required
def admin_manual_plan():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        action = (request.form.get("action") or "").strip().lower()

        if not email:
            flash("Digite o email do usuário.", "danger")
            return redirect(url_for("admin_manual_plan"))

        user = User.query.filter_by(username=email).first()
        if not user:
            flash("Usuário não encontrado.", "danger")
            return redirect(url_for("admin_manual_plan"))

        if action == "premium":
            user.plan = PLAN_PREMIUM
            user.plan_expires_at = datetime.utcnow() + timedelta(days=30)
            db.session.commit()
            flash(f"✅ Premium ativado por 30 dias para {user.username}", "success")

        elif action == "gold":
            user.plan = PLAN_GOLD
            user.plan_expires_at = None
            db.session.commit()
            flash(f"✅ Gold ativado permanente para {user.username}", "success")

        else:
            flash("Ação inválida.", "danger")

        return redirect(url_for("admin_manual_plan"))

    return render_template("admin_manual_plan.html")


# =========================================================
# ====================== HELP / FEEDBACK ====================
# =========================================================

@app.route("/help")
@login_required
def help_page():
    return render_template("help.html")


@app.route("/feedback", methods=["GET", "POST"])
@login_required
def feedback():
    if request.method == "POST":
        return redirect(url_for("home"))
    return render_template("feedback.html")


# =========================================================
# ===================== RUN (DEV LOCAL) =====================
# =========================================================

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000)