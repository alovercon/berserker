"""Tests for login_service module to demonstrate the bug."""
import pytest
from berserker.auth.login_service import User, UserProfile, MockUserDatabase, LoginService


class TestLoginServiceBug:
    """Test cases that reproduce the bug described in Ticket #999."""

    def test_login_with_complete_profile(self):
        """Happy path: User has a complete profile."""
        profile = UserProfile(theme="dark", preferences={"lang": "en"})
        user = User("alice", "hash123", profile)

        db = MockUserDatabase()
        db.add_user(user)

        service = LoginService(db)
        result = service.authenticate("alice", "hash123", "192.168.1.1")

        assert result["status"] == "success"
        assert result["theme"] == "dark"

    def test_login_with_missing_profile_triggers_bug(self):
        """
        Bug reproduction: User profile is None.
        This should trigger an AttributeError (Python equivalent of NPE).
        """
        # Simulate a user who registered but skipped profile setup
        user = User("bob", "hash456", profile=None)

        db = MockUserDatabase()
        db.add_user(user)

        service = LoginService(db)
        
        # This raises: AttributeError: 'NoneType' object has no attribute 'update_last_login'
        with pytest.raises(AttributeError, match="'NoneType'"):
            service.authenticate("bob", "hash456", "192.168.1.2")
