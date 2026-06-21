import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Security ───────────────────────────────────────────────────────────
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-insecure-change-before-deploy")
    WTF_CSRF_ENABLED: bool = True
    WTF_CSRF_TIME_LIMIT: int = 3600          # 1-hour CSRF token validity
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"

    # ── Session ────────────────────────────────────────────────────────────
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(minutes=30)
    SESSION_PERMANENT: bool = True

    # ── Runtime ────────────────────────────────────────────────────────────
    DEBUG: bool = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    TEMPLATES_AUTO_RELOAD: bool = True

    # ── File uploads ───────────────────────────────────────────────────────
    UPLOAD_FOLDER: str = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "uploads"
    )
    MAX_CONTENT_LENGTH: int = 20 * 1024 * 1024   # 20 MB

    # ── Admin credentials (loaded from .env) ──────────────────────────────
    ADMIN_USERNAME: str = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH: str = os.environ.get("ADMIN_PASSWORD_HASH", "")
