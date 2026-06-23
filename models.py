import bcrypt
from flask_login import UserMixin


class User(UserMixin):
    """Single-admin user backed by .env credentials — no database."""

    def __init__(self, username: str) -> None:
        self.id = username

    def get_id(self) -> str:
        return self.id

    @staticmethod
    def verify(username: str, password: str, app_config: dict) -> "User | None":
        """
        Check supplied credentials against the hashed password in app config.
        Returns a User on success, None on failure.
        """
        admin_username = User.admin_username(app_config)
        if User.normalize_username(username) != User.normalize_username(admin_username):
            return None
        stored_hash = User.clean_config_value(app_config.get("ADMIN_PASSWORD_HASH", ""))
        if not stored_hash:
            return None
        try:
            if bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
                return User(admin_username)
        except Exception:
            pass
        return None

    @staticmethod
    def clean_config_value(value) -> str:
        return str(value or "").strip().strip('"').strip("'").strip()

    @staticmethod
    def admin_username(app_config: dict) -> str:
        return User.clean_config_value(app_config.get("ADMIN_USERNAME", ""))

    @staticmethod
    def normalize_username(username: str) -> str:
        return User.clean_config_value(username).casefold()

    @staticmethod
    def matches_configured_user(user_id: str, app_config: dict) -> bool:
        return User.normalize_username(user_id) == User.normalize_username(User.admin_username(app_config))
