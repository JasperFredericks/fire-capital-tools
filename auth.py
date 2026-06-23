import os

import bcrypt
from flask import Blueprint, current_app, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user

from models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    error: str | None = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.verify(username, password, current_app.config)

        if user:
            login_user(user)
            session.permanent = True
            session["_last_active"] = _now()
            # Safe redirect — only accept relative paths to prevent open-redirect
            next_page = request.args.get("next", "")
            if next_page and next_page.startswith("/") and not next_page.startswith("//"):
                return redirect(next_page)
            return redirect(url_for("dashboard"))

        error = "Invalid username or password."

    return render_template("login.html", error=error)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    error: str | None = None
    success: bool = False

    if request.method == "POST":
        current_pw  = request.form.get("current_password", "")
        new_pw      = request.form.get("new_password", "")
        confirm_pw  = request.form.get("confirm_password", "")

        # Verify current password
        user = User.verify(current_user.id, current_pw, current_app.config)
        if not user:
            error = "Current password is incorrect."
        elif len(new_pw) < 6:
            error = "New password must be at least 6 characters."
        elif new_pw != confirm_pw:
            error = "New passwords do not match."
        elif _is_managed_runtime():
            error = (
                "Password changes must be made in the production environment "
                "variables so they survive deploys and restarts."
            )
        else:
            new_hash = bcrypt.hashpw(new_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            _write_env_hash(new_hash)
            # Update running config so the new password works immediately
            current_app.config["ADMIN_PASSWORD_HASH"] = new_hash
            success = True

    return render_template("change_password.html", error=error, success=success)


def _write_env_hash(new_hash: str) -> None:
    """Rewrite ADMIN_PASSWORD_HASH line in the .env file."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    # Look relative to this file's parent (repo root)
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        updated = []
        found = False
        for line in lines:
            if line.startswith("ADMIN_PASSWORD_HASH="):
                updated.append(f"ADMIN_PASSWORD_HASH={new_hash}\n")
                found = True
            else:
                updated.append(line)
        if not found:
            updated.append(f"ADMIN_PASSWORD_HASH={new_hash}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(updated)
    except OSError:
        pass  # If .env isn't writable (e.g. cloud deploy), the in-memory update still works this session


def _is_managed_runtime() -> bool:
    return any(
        os.environ.get(name)
        for name in (
            "RAILWAY_ENVIRONMENT",
            "RAILWAY_ENVIRONMENT_NAME",
            "RAILWAY_PROJECT_ID",
            "RAILWAY_SERVICE_ID",
        )
    )


def _now() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat()
