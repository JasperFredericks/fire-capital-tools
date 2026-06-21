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
        if username != app_config.get("ADMIN_USERNAME"):
            return None
        stored_hash: str = app_config.get("ADMIN_PASSWORD_HASH", "")
        if not stored_hash:
            return None
        try:
            if bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
                return User(username)
        except Exception:
            pass
        return None
