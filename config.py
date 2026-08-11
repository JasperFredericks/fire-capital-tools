import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Security ───────────────────────────────────────────────────────────
    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-insecure-change-before-deploy")
    WTF_CSRF_ENABLED: bool = True
    WTF_CSRF_TIME_LIMIT: int = 4 * 60 * 60   # 4-hour CSRF token validity
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"

    # ── Session ────────────────────────────────────────────────────────────
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(hours=4)
    SESSION_PERMANENT: bool = True

    # ── Runtime ────────────────────────────────────────────────────────────
    DEBUG: bool = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    TEMPLATES_AUTO_RELOAD: bool = True
    GOOGLE_MAPS_API_KEY: str = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    GOOGLE_MAPS_MAP_ID: str = os.environ.get("GOOGLE_MAPS_MAP_ID", "")
    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
    FIRE_METRICS_SUMMARY_MODEL: str = os.environ.get("FIRE_METRICS_SUMMARY_MODEL", "")
    FIRE_METRICS_CRE_MODEL: str = os.environ.get("FIRE_METRICS_CRE_MODEL", "gpt-4o-mini")
    FIRE_METRICS_AI_SUMMARIES_ENABLED: bool = os.environ.get("FIRE_METRICS_AI_SUMMARIES_ENABLED", "false").lower() == "true"

    # ── File uploads ───────────────────────────────────────────────────────
    # Env-var-overridable with a repo-relative fallback, the same shape as
    # USER_STORE_PATH below and every *_DB_PATH in tools/. On Railway this
    # must point at the persistent volume: the container filesystem is
    # ephemeral, so anything written under the repo is destroyed on each
    # deploy. Deal Dive is the reason this matters -- its uploads are
    # tracked in deal_files and downloadable indefinitely, unlike Scorecard
    # Pro's and MMR's, which are session scratch on a 4-hour TTL.
    UPLOAD_FOLDER: str = os.environ.get(
        "UPLOAD_FOLDER_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads"),
    )
    MAX_CONTENT_LENGTH: int = 20 * 1024 * 1024   # 20 MB

    # ── Admin credentials (loaded from .env) ──────────────────────────────
    ADMIN_USERNAME: str = os.environ.get("ADMIN_USERNAME", "michelle")
    ADMIN_PASSWORD_HASH: str = os.environ.get("ADMIN_PASSWORD_HASH", "")
    USER_STORE_PATH: str = os.environ.get(
        "USER_STORE_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.json"),
    )
