"""
FIRE Capital Tools — Flask Application
"""

import os
from datetime import datetime

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import LoginManager, current_user, logout_user
from flask_wtf.csrf import CSRFError, CSRFProtect

from config import Config
from models import User

login_manager = LoginManager()
csrf = CSRFProtect()


def create_app(config_class: type = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_class)

    # ── Create uploads folder ──────────────────────────────────────────────
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    # ── Extensions ────────────────────────────────────────────────────────
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"          # type: ignore[assignment]
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "warning"
    csrf.init_app(app)

    def wants_json_session_response() -> bool:
        return (
            request.path.startswith("/tools/mmr-summary/upload")
            or request.path.startswith("/tools/mmr-summary/download/")
            or request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        )

    def session_expired_response():
        return jsonify({"error": "session_expired", "redirect": url_for("auth.login")}), 401

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        if wants_json_session_response():
            return session_expired_response()
        flash(login_manager.login_message, login_manager.login_message_category)
        return redirect(url_for("auth.login", next=request.full_path if request.query_string else request.path))

    # ── User loader ────────────────────────────────────────────────────────
    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        if User.matches_configured_user(user_id, app.config):
            return User(User.admin_username(app.config))
        return None

    # ── Blueprints ─────────────────────────────────────────────────────────
    from auth import auth_bp
    from tools.mmr_summary import mmr_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(mmr_bp, url_prefix="/tools/mmr-summary")

    # ── Security headers ───────────────────────────────────────────────────
    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"]        = "DENY"
        response.headers["X-XSS-Protection"]       = "1; mode=block"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        return response

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error):
        if wants_json_session_response():
            return session_expired_response()
        flash("Your session expired. Please log in again.", "warning")
        return redirect(url_for("auth.login"))

    # ── Session inactivity timeout ────────────────────────────────────────
    @app.before_request
    def check_session_timeout():
        if not current_user.is_authenticated:
            return
        session.permanent = True
        last: str | None = session.get("_last_active")
        if last:
            elapsed = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds()
            if elapsed > app.permanent_session_lifetime.total_seconds():
                logout_user()
                session.clear()
                if wants_json_session_response():
                    return session_expired_response()
                flash("Your session expired. Please log in again.", "warning")
                return redirect(url_for("auth.login"))
        session["_last_active"] = datetime.utcnow().isoformat()
        session.modified = True

    # ── Core routes ────────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return redirect(url_for("dashboard"))

    @app.route("/dashboard")
    def dashboard():
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))
        return render_template("dashboard.html")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(
        debug=app.config.get("DEBUG", False),
        host="0.0.0.0",
        port=5000,
    )
