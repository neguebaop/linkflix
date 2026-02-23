from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager, UserMixin, login_user, login_required,
    logout_user, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from random import choice
from functools import wraps
import os
import uuid
from datetime import datetime, timedelta

# ✅ .env
from dotenv import load_dotenv
load_dotenv()

# ✅ requests (MisticPay + TMDB)
import requests

# =========================================================
# ====================== TMDB CONFIG =======================
# =========================================================
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "").strip()
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w780"


def tmdb_get(path: str, params: dict | None = None):
    """Chama TMDb API v3."""
    if not TMDB_API_KEY:
        raise RuntimeError("TMDB_API_KEY não configurada.")
    params = params or {}
    params["api_key"] = TMDB_API_KEY
    params.setdefault("language", "pt-BR")
    url = f"{TMDB_BASE_URL}{path}"
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


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
    # pega o primeiro número grande que aparecer
    import re
    m = re.search(r"/(movie|tv)/(\d+)", raw)
    if m:
        return m.group(2)
    m2 = re.search(r"(\d+)", raw)
    return m2.group(1) if m2 else raw


def tmdb_lookup_item(item_type: str, tmdb_id: str):
    """
    item_type: "movie" ou "tv"
    Retorna dict pronto pro seu admin preencher.
    """
    tmdb_id = normalize_tmdb_id(tmdb_id)
    if item_type not in ("movie", "tv"):
        raise ValueError("type inválido")

    data = tmdb_get(f"/{item_type}/{tmdb_id}", params={"append_to_response": "genres"})

    title = data.get("title") if item_type == "movie" else data.get("name")
    overview = data.get("overview") or ""
    poster = data.get("poster_path") or ""
    backdrop = data.get("backdrop_path") or ""
    # preferir backdrop, se não tiver usar poster
    image = (TMDB_IMG_BASE + backdrop) if backdrop else ((TMDB_IMG_BASE + poster) if poster else "")

    # gêneros
    genres = [g.get("name") for g in (data.get("genres") or []) if g.get("name")]
    main_category = genres[0] if genres else ""
    extra_categories = genres[1:] if len(genres) > 1 else []

    return {
        "tmdb_id": tmdb_id,
        "content_type": "Filme" if item_type == "movie" else "Serie",
        "title": title or "",
        "description": overview[:480],  # pra caber no seu limite
        "image": image,
        "category": main_category,
        "extra_categories": extra_categories,
    }


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

# ✅ UPLOAD CONFIG (avatar)
app.config["UPLOAD_FOLDER"] = os.path.join(app.root_path, "static", "uploads", "avatars")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# ✅ TMDB API KEY (adicione no Render: TMDB_API_KEY)
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")

db = SQLAlchemy(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


# =========================================================
# ✅✅✅ CRIA TABELAS NO PRIMEIRO REQUEST (SEGURO NO RENDER)
# =========================================================
_db_ready = False

@app.before_request
def _create_tables_once_safe():
    global _db_ready
    if _db_ready:
        return
    try:
        db.create_all()
        _db_ready = True
    except Exception:
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
        if not (session.get("is_admin") or getattr(current_user, "is_admin", False)):
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


# ✅ N:N categorias extras
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
    category = db.Column(db.String(100))  # pode ter várias por vírgula
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
    return User.query.get(int(user_id))


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


# =========================================================
# ========================== INDEX ==========================
# =========================================================

@app.route("/")
def index():
    if current_user.is_authenticated:
        if session.get("active_profile"):
            return redirect(url_for("home"))
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
            login_user(user)
            session["user_id"] = user.id
            session.pop("active_profile", None)
            return redirect(url_for("select_profile_page"))
        else:
            error = "Email ou senha inválidos"

    return render_template("login.html", error=error)


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


@app.route("/profile/<int:profile_id>")
@login_required
def select_profile(profile_id):
    profile = Profile.query.get_or_404(profile_id)
    if profile.user_id != current_user.id:
        return redirect(url_for("select_profile_page"))

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

        if len(current_user.profiles) >= 5:
            error = "Você só pode criar até 5 perfis"
            return render_template("create_profile.html", error=error)

        avatar_file = request.files.get("avatar_file")
        saved = save_avatar_file(avatar_file)

        avatar_url = (request.form.get("avatar_url") or "").strip()
        avatar = saved if saved else normalize_avatar(avatar_url or "/static/images/default_profile.png")

        profile = Profile(name=name, avatar=avatar, user=current_user)
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
        profile.name = (request.form.get("name") or profile.name).strip()

        avatar_file = request.files.get("avatar_file")
        saved = save_avatar_file(avatar_file)

        avatar_url = (request.form.get("avatar_url") or "").strip()
        if saved:
            profile.avatar = saved
        elif avatar_url:
            profile.avatar = normalize_avatar(avatar_url)

        db.session.commit()
        return redirect(url_for("select_profile_page"))

    return render_template("edit_profile.html", profile=profile, error=error)


@app.route("/delete_profile/<int:profile_id>")
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

    if pos < 0:
        pos = 0
    if dur < 0:
        dur = 0

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


# ✅ compatibilidade /api/progress/*
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


# =========================================================
# ============================ HOME =========================
# =========================================================

@app.route("/home")
@login_required
@require_active_profile
def home():
    search = request.args.get("search", "")
    category = request.args.get("category", "")
    content_type = request.args.get("content_type", "")

    query = Content.query

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Content.title.ilike(search_term)) |
            (Content.category.ilike(search_term))
        )

    if category:
        query = query.filter(Content.category.ilike(category))

    if content_type:
        query = query.filter(Content.content_type.ilike(content_type))

    contents = query.all()
    featured_content = choice(contents) if contents else None

    acao = Content.query.filter(Content.category.ilike("%ação%")).all()
    anime = Content.query.filter(Content.category.ilike("%anime%")).all()
    filmes = Content.query.filter(Content.content_type.ilike("%film%")).all()
    series = Content.query.filter(Content.content_type.ilike("%ser%")).all()

    profile_id = session.get("active_profile")
    favs = Favorite.query.filter_by(profile_id=profile_id).all() if profile_id else []
    favorite_ids = {f.content_id for f in favs}

    # ✅ CONTINUAR ASSISTINDO REAL
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
        progress_map=progress_map
    )


# =========================================================
# =================== BROWSE: FILMES/SÉRIES =================
# =========================================================

DEFAULT_GENRES = [
    "Ação",
    "Anime",
    "Brasileiros",
    "Clássicos",
    "Comédia stand-up",
    "Comédias",
    "Como me sinto hoje?",
    "Curtas",
    "Documentários",
    "Drama",
    "Esportes",
    "Estrangeiros",
    "Fantasia",
    "Fé e espiritualidade",
    "Ficção científica",
    "Hollywood",
    "Independentes",
    "LGBTQIA+",
    "Música e musicais",
    "Netflix no Oscar® 2026",
    "Para toda a família",
    "Policial",
    "Premiados",
    "Romance",
    "Sua playlist do zodíaco",
    "Suspense",
    "Terror",
]

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
            c = cat.strip()
            if c:
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

    return render_template(
        "browse.html",
        page_title="Filmes",
        content_type="Filme",
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

    return render_template(
        "browse.html",
        page_title="Séries",
        content_type="Serie",
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
# ===================== TMDB IMPORT (ADMIN) =================
# =========================================================

@app.route("/api/tmdb/import", methods=["GET"])
@login_required
@admin_required
def tmdb_import():
    """
    GET /api/tmdb/import?id=224372&type=tv
    type pode ser: tv ou movie
    se type não vier, tentamos primeiro movie depois tv
    """
    tmdb_id = (request.args.get("id") or "").strip()
    forced_type = (request.args.get("type") or "").strip().lower()

    if not TMDB_API_KEY:
        return jsonify({"ok": False, "error": "TMDB_API_KEY não configurada no servidor."}), 400

    if not tmdb_id or not tmdb_id.isdigit():
        return jsonify({"ok": False, "error": "ID inválido."}), 400

    def fetch(kind: str):
        url = f"https://api.themoviedb.org/3/{kind}/{tmdb_id}"
        params = {"api_key": TMDB_API_KEY, "language": "pt-BR"}
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return None
        return r.json()

    data = None
    kind = None

    if forced_type in ("movie", "tv"):
        kind = forced_type
        data = fetch(kind)
    else:
        data = fetch("movie")
        kind = "movie" if data else None
        if not data:
            data = fetch("tv")
            kind = "tv" if data else None

    if not data:
        return jsonify({"ok": False, "error": "Não encontrei no TMDB com esse ID."}), 404

    title = data.get("title") or data.get("name") or ""
    overview = data.get("overview") or ""
    poster = data.get("poster_path") or ""
    genres = [g.get("name") for g in (data.get("genres") or []) if g.get("name")]
    image_url = f"https://image.tmdb.org/t/p/w780{poster}" if poster else ""
    content_type = "Filme" if kind == "movie" else "Serie"

    return jsonify({
        "ok": True,
        "tmdb_id": int(tmdb_id),
        "title": title,
        "description": overview,
        "image": image_url,
        "content_type": content_type,
        "genres": genres,
    })


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

    return render_template(
        "watch.html",
        content=content,
        progress_pct=progress_pct,
        used_duration=used_duration or (duration or 3600)
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
# ====================== TMDB IMPORT =======================
# =========================================================

@app.route("/api/tmdb/import")
@login_required
def api_tmdb_import():
    item_type = request.args.get("type", "").strip().lower()
    tmdb_id = request.args.get("id", "").strip()

    if not tmdb_id:
        return jsonify({"ok": False, "error": "ID não informado."})

    if item_type not in ("movie", "tv"):
        item_type = "movie"

    if not TMDB_API_KEY:
        return jsonify({"ok": False, "error": "TMDB_API_KEY não configurada."})

    try:
        url = f"https://api.themoviedb.org/3/{item_type}/{tmdb_id}"
        params = {
            "api_key": TMDB_API_KEY,
            "language": "pt-BR"
        }

        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        title = data.get("title") if item_type == "movie" else data.get("name")
        overview = data.get("overview")
        poster = data.get("poster_path")
        genres = [g["name"] for g in data.get("genres", [])]

        image = f"https://image.tmdb.org/t/p/w780{poster}" if poster else ""

        return jsonify({
            "ok": True,
            "title": title,
            "description": overview,
            "image": image,
            "tmdb_id": tmdb_id,
            "content_type": "Filme" if item_type == "movie" else "Serie",
            "genres": genres
        })

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# =========================================================
# ============================ ADMIN ========================
# =========================================================

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
            tmdb_id = (request.form.get("tmdb_id") or "").strip()
            content_type = (request.form.get("content_type") or "Filme").strip()
            is_premium = ("premium" in request.form)
            duration_seconds = int(request.form.get("duration_seconds") or 0)

            if not title or not image:
                flash("Preencha pelo menos Título e Imagem.")
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
        if chave_digitada in ("22", "LINKVIP2026"):
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
        content.tmdb_id = (request.form.get("tmdb_id") or "").strip()
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

@app.route("/admin/tmdb/lookup")
@login_required
def admin_tmdb_lookup():
    # mesma regra do seu /admin
    main_account = (current_user.username == "zanagabriela26@gmail.com")
    allowed = main_account or session.get("is_admin") or session.get("admin_liberado")
    if not allowed:
        return jsonify({"ok": False, "error": "Sem permissão"}), 403

    item_type = (request.args.get("type") or "").strip().lower()  # movie | tv
    tmdb_id = (request.args.get("id") or "").strip()

    if not item_type or not tmdb_id:
        return jsonify({"ok": False, "error": "Parâmetros faltando"}), 400

    try:
        info = tmdb_lookup_item(item_type, tmdb_id)
        return jsonify({"ok": True, "data": info})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
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