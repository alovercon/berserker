"""
Login Service Module for berserker.

Handles user authentication and session management.
"""


class UserProfile:
    """Represents a user's profile information."""

    def __init__(self, theme="default", last_login_ip=None, preferences=None):
        self.theme = theme
        self.last_login_ip = last_login_ip
        self.preferences = preferences or {}

    def update_last_login(self, ip):
        """Update the last login IP address."""
        self.last_login_ip = ip


class User:
    """Represents a user entity."""

    def __init__(self, username, password_hash, profile=None):
        self.username = username
        self.password_hash = password_hash
        self.profile = profile  # profile can be None if user hasn't completed setup


class MockUserDatabase:
    """Simulates a database backend for testing."""

    def __init__(self):
        self._users = {}

    def add_user(self, user):
        self._users[user.username] = user

    def get_user(self, username):
        """Retrieve a user by username. Returns None if not found."""
        return self._users.get(username)


class LoginService:
    """Service responsible for handling user login operations."""

    def __init__(self, db):
        self.db = db

    def authenticate(self, username, password, client_ip=None):
        """
        Authenticate a user and return a session token.

        Args:
            username: The username to authenticate.
            password: The plain-text password to verify.
            client_ip: The IP address of the client.

        Returns:
            dict: Session information including username and status.

        Raises:
            ValueError: If user not found or password is incorrect.
            AttributeError: If user profile is None and code attempts to access it (BUG).
        """
        # 1. Lookup user
        user = self.db.get_user(username)
        if user is None:
            raise ValueError("User '{}' not found".format(username))

        # 2. Verify password
        if user.password_hash != password:
            raise ValueError("Invalid password for user '{}'".format(username))

        # 3. Update login metadata
        # FIX: Guard against None profile — users who registered but skipped profile setup
        # will have a default profile created automatically.
        current_ip = client_ip or "0.0.0.0"
        if user.profile is None:
            user.profile = UserProfile()
        user.profile.update_last_login(current_ip)

        # 4. Generate session response
        return {
            "status": "success",
            "username": user.username,
            "theme": user.profile.theme,
            "preferences": user.profile.preferences,
        }
