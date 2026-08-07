"""
Unit tests for LoginService — verifying the Ticket #999 fix.

These tests ensure:
1. Login works normally when profile is complete.
2. Login works safely when profile is None (the bug fix).
3. Appropriate errors are raised for invalid credentials.
"""

import pytest
from berserker.auth.login_service import User, UserProfile, MockUserDatabase, LoginService


class TestLoginServiceFix:
    """Test suite validating the Ticket #999 fix."""

    def setup_method(self):
        """Set up a fresh database and service for each test."""
        self.db = MockUserDatabase()
        self.service = LoginService(self.db)

    # --- Happy path tests ---

    def test_login_complete_profile(self):
        """User with a complete profile logs in successfully."""
        profile = UserProfile(theme="dark", preferences={"lang": "en"})
        user = User("alice", "hash123", profile)
        self.db.add_user(user)

        result = self.service.authenticate("alice", "hash123", "192.168.1.1")

        assert result["status"] == "success"
        assert result["username"] == "alice"
        assert result["theme"] == "dark"
        assert result["preferences"] == {"lang": "en"}

    def test_login_null_profile_no_crash(self):
        """
        FIX VERIFICATION: User with None profile should NOT crash.
        Previously this raised AttributeError. Now it should succeed
        with default profile values.
        """
        # User registered but skipped profile setup
        user = User("bob", "hash456", profile=None)
        self.db.add_user(user)

        # Should NOT raise any exception
        result = self.service.authenticate("bob", "hash456", "10.0.0.1")

        assert result["status"] == "success"
        assert result["username"] == "bob"
        # Default theme should be used
        assert result["theme"] == "default"
        assert result["preferences"] == {}

    def test_null_profile_is_auto_initialized(self):
        """
        After login with None profile, the user object should have
        a default UserProfile assigned.
        """
        user = User("charlie", "hash789", profile=None)
        self.db.add_user(user)

        assert user.profile is None  # Confirm None before login

        self.service.authenticate("charlie", "hash789", "172.16.0.1")

        # Profile should now be auto-initialized
        assert user.profile is not None
        assert user.profile.theme == "default"
        assert user.profile.last_login_ip == "172.16.0.1"

    # --- Error handling tests ---

    def test_login_user_not_found(self):
        """Login should raise ValueError for unknown user."""
        with pytest.raises(ValueError, match="not found"):
            self.service.authenticate("nobody", "anything")

    def test_login_wrong_password(self):
        """Login should raise ValueError for incorrect password."""
        user = User("alice", "correct_hash", UserProfile())
        self.db.add_user(user)

        with pytest.raises(ValueError, match="Invalid password"):
            self.service.authenticate("alice", "wrong_hash")

    def test_login_missing_ip_defaults(self):
        """When client_ip is None, it should default to '0.0.0.0'."""
        user = User("dave", "hash111", profile=None)
        self.db.add_user(user)

        result = self.service.authenticate("dave", "hash111", client_ip=None)

        assert result["status"] == "success"
        # Check internal state was updated with default IP
        assert user.profile.last_login_ip == "0.0.0.0"

    def test_login_updates_ip_on_existing_profile(self):
        """Login should update the last_login_ip on an existing profile."""
        profile = UserProfile(last_login_ip="1.2.3.4")
        user = User("eve", "hash222", profile)
        self.db.add_user(user)

        self.service.authenticate("eve", "hash222", "5.6.7.8")

        assert profile.last_login_ip == "5.6.7.8"
