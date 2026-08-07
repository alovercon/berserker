"""
Tests for berserker.agent.registry module.

Covers:
- AgentRegistry CRUD operations (register, unregister, get, has, clear, count).
- Listing operations (list_all, list_by_mode, list_primary, list_subagents).
- Thread safety (concurrent register, concurrent get, mixed read/write).
- Error handling (duplicate registration, not found errors).
"""

import threading
import pytest

from berserker.agent.registry import AgentRegistry
from berserker.agent.base import BaseAgent
from berserker.agent.schema import AgentSchema
from berserker.agent.exceptions import AgentNotFoundError, AgentRegistrationError


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


class ConcreteTestAgent(BaseAgent):
    """Concrete BaseAgent subclass for testing purposes."""

    def execute(self, messages, session_id, tool_registry, **kwargs):
        # type: (list, str, object, object) -> dict
        return {"content": "test", "usage": None}


def make_agent(name, mode="primary", model="gpt-4o"):
    # type: (str, str, str) -> ConcreteTestAgent
    """Factory function to create a ConcreteTestAgent with the given parameters."""
    schema = AgentSchema(
        name=name,
        description="Test agent: {}".format(name),
        mode=mode,
        model=model,
        prompt="You are {}.".format(name),
    )
    return ConcreteTestAgent(schema)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_registry():
    """Return an empty AgentRegistry instance."""
    return AgentRegistry()


@pytest.fixture
def populated_registry():
    """Return an AgentRegistry with sample agents of different modes."""
    registry = AgentRegistry()
    registry.register(make_agent("alpha", mode="primary"))
    registry.register(make_agent("beta", mode="subagent"))
    registry.register(make_agent("gamma", mode="subagent"))
    registry.register(make_agent("delta", mode="hidden"))
    return registry


# ---------------------------------------------------------------------------
# Registration Tests
# ---------------------------------------------------------------------------


class TestRegisterAgent:
    """Tests for AgentRegistry.register()."""

    def test_register_agent(self, empty_registry):
        """Register a BaseAgent and verify it is accessible via get()."""
        agent = make_agent("test-agent")
        empty_registry.register(agent)
        retrieved = empty_registry.get("test-agent")
        assert retrieved is agent
        assert retrieved.name == "test-agent"

    def test_register_duplicate_raises(self, empty_registry):
        """Registering the same name twice should raise AgentRegistrationError."""
        agent = make_agent("duplicate-agent")
        empty_registry.register(agent)
        with pytest.raises(AgentRegistrationError) as exc_info:
            empty_registry.register(make_agent("duplicate-agent"))
        assert exc_info.value.name == "duplicate-agent"
        assert "already registered" in str(exc_info.value)

    def test_register_non_baseagent_raises(self, empty_registry):
        """Registering a non-BaseAgent should raise TypeError."""
        with pytest.raises(TypeError) as exc_info:
            empty_registry.register("not an agent")  # type: ignore
        assert "Expected BaseAgent" in str(exc_info.value)

    def test_register_none_raises(self, empty_registry):
        """Registering None should raise TypeError."""
        with pytest.raises(TypeError):
            empty_registry.register(None)  # type: ignore

    def test_register_multiple_agents(self, empty_registry):
        """Register multiple agents and verify all are accessible."""
        agents = [
            make_agent("agent-1"),
            make_agent("agent-2"),
            make_agent("agent-3"),
        ]
        for agent in agents:
            empty_registry.register(agent)
        for agent in agents:
            assert empty_registry.get(agent.name) is agent

    def test_register_increases_count(self, empty_registry):
        """Registering an agent should increase the count."""
        assert empty_registry.count() == 0
        empty_registry.register(make_agent("agent-a"))
        assert empty_registry.count() == 1
        empty_registry.register(make_agent("agent-b"))
        assert empty_registry.count() == 2


# ---------------------------------------------------------------------------
# Unregistration Tests
# ---------------------------------------------------------------------------


class TestUnregisterAgent:
    """Tests for AgentRegistry.unregister()."""

    def test_unregister_agent(self, populated_registry):
        """Unregister an agent and verify has() returns False."""
        assert populated_registry.has("alpha") is True
        populated_registry.unregister("alpha")
        assert populated_registry.has("alpha") is False

    def test_unregister_not_found_raises(self, empty_registry):
        """Unregistering a non-existent agent should raise AgentNotFoundError."""
        with pytest.raises(AgentNotFoundError) as exc_info:
            empty_registry.unregister("non-existent")
        assert exc_info.value.name == "non-existent"

    def test_unregister_decreases_count(self, populated_registry):
        """Unregistering an agent should decrease the count."""
        initial_count = populated_registry.count()
        populated_registry.unregister("beta")
        assert populated_registry.count() == initial_count - 1

    def test_unregister_removes_correct_agent(self, populated_registry):
        """Unregistering one agent should not affect others."""
        populated_registry.unregister("alpha")
        assert populated_registry.has("beta") is True
        assert populated_registry.has("gamma") is True
        assert populated_registry.has("delta") is True


# ---------------------------------------------------------------------------
# Get Agent Tests
# ---------------------------------------------------------------------------


class TestGetAgent:
    """Tests for AgentRegistry.get()."""

    def test_get_agent(self, populated_registry):
        """Get a registered agent by name."""
        agent = populated_registry.get("alpha")
        assert agent.name == "alpha"
        assert agent.mode == "primary"

    def test_get_not_found_raises(self, empty_registry):
        """Getting a non-existent agent should raise AgentNotFoundError."""
        with pytest.raises(AgentNotFoundError) as exc_info:
            empty_registry.get("missing-agent")
        assert exc_info.value.name == "missing-agent"

    def test_get_returns_same_instance(self, empty_registry):
        """get() should return the exact same instance that was registered."""
        agent = make_agent("singleton-test")
        empty_registry.register(agent)
        retrieved = empty_registry.get("singleton-test")
        assert retrieved is agent


# ---------------------------------------------------------------------------
# Has Agent Tests
# ---------------------------------------------------------------------------


class TestHasAgent:
    """Tests for AgentRegistry.has()."""

    def test_has_agent(self, populated_registry):
        """Check if a registered agent exists."""
        assert populated_registry.has("alpha") is True
        assert populated_registry.has("beta") is True

    def test_has_not_found(self, populated_registry):
        """Check for a non-existent agent should return False."""
        assert populated_registry.has("non-existent") is False

    def test_has_after_unregister(self, populated_registry):
        """has() should return False after unregistering."""
        populated_registry.unregister("alpha")
        assert populated_registry.has("alpha") is False


# ---------------------------------------------------------------------------
# List All Tests
# ---------------------------------------------------------------------------


class TestListAll:
    """Tests for AgentRegistry.list_all()."""

    def test_list_all(self, populated_registry):
        """List all registered agents."""
        agents = populated_registry.list_all()
        assert len(agents) == 4
        names = {agent.name for agent in agents}
        assert names == {"alpha", "beta", "gamma", "delta"}

    def test_list_all_empty(self, empty_registry):
        """list_all() should return an empty list when no agents are registered."""
        agents = empty_registry.list_all()
        assert agents == []
        assert isinstance(agents, list)

    def test_list_all_returns_copy(self, populated_registry):
        """list_all() should return a copy, not the internal list."""
        agents = populated_registry.list_all()
        agents.clear()
        assert populated_registry.count() == 4


# ---------------------------------------------------------------------------
# List By Mode Tests
# ---------------------------------------------------------------------------


class TestListByMode:
    """Tests for AgentRegistry.list_by_mode()."""

    def test_list_by_mode_primary(self, populated_registry):
        """Filter agents by mode='primary'."""
        agents = populated_registry.list_by_mode("primary")
        assert len(agents) == 1
        assert agents[0].name == "alpha"

    def test_list_by_mode_subagent(self, populated_registry):
        """Filter agents by mode='subagent'."""
        agents = populated_registry.list_by_mode("subagent")
        assert len(agents) == 2
        names = {agent.name for agent in agents}
        assert names == {"beta", "gamma"}

    def test_list_by_mode_hidden(self, populated_registry):
        """Filter agents by mode='hidden'."""
        agents = populated_registry.list_by_mode("hidden")
        assert len(agents) == 1
        assert agents[0].name == "delta"

    def test_list_by_mode_no_match(self, populated_registry):
        """Filter by a mode with no agents should return empty list."""
        agents = populated_registry.list_by_mode("nonexistent-mode")
        assert agents == []

    def test_list_by_mode_empty_registry(self, empty_registry):
        """list_by_mode() on empty registry should return empty list."""
        agents = empty_registry.list_by_mode("primary")
        assert agents == []


# ---------------------------------------------------------------------------
# Convenience List Methods Tests
# ---------------------------------------------------------------------------


class TestConvenienceListMethods:
    """Tests for list_primary() and list_subagents()."""

    def test_list_primary(self, populated_registry):
        """list_primary() should return agents with mode='primary'."""
        agents = populated_registry.list_primary()
        assert len(agents) == 1
        assert agents[0].name == "alpha"
        assert agents[0].mode == "primary"

    def test_list_subagents(self, populated_registry):
        """list_subagents() should return agents with mode='subagent'."""
        agents = populated_registry.list_subagents()
        assert len(agents) == 2
        names = {agent.name for agent in agents}
        assert names == {"beta", "gamma"}

    def test_list_primary_empty(self, empty_registry):
        """list_primary() on empty registry should return empty list."""
        agents = empty_registry.list_primary()
        assert agents == []

    def test_list_subagents_empty(self, empty_registry):
        """list_subagents() on empty registry should return empty list."""
        agents = empty_registry.list_subagents()
        assert agents == []


# ---------------------------------------------------------------------------
# Clear Tests
# ---------------------------------------------------------------------------


class TestClear:
    """Tests for AgentRegistry.clear()."""

    def test_clear(self, populated_registry):
        """Clear all agents from the registry."""
        assert populated_registry.count() == 4
        populated_registry.clear()
        assert populated_registry.count() == 0
        assert populated_registry.list_all() == []

    def test_clear_empty_registry(self, empty_registry):
        """Clear on an empty registry should not raise."""
        empty_registry.clear()
        assert empty_registry.count() == 0

    def test_clear_then_register(self, populated_registry):
        """After clear, should be able to register new agents."""
        populated_registry.clear()
        populated_registry.register(make_agent("fresh-agent"))
        assert populated_registry.count() == 1
        assert populated_registry.has("fresh-agent") is True


# ---------------------------------------------------------------------------
# Count Tests
# ---------------------------------------------------------------------------


class TestCount:
    """Tests for AgentRegistry.count()."""

    def test_count(self, populated_registry):
        """Verify agent count matches registered agents."""
        assert populated_registry.count() == 4

    def test_count_empty(self, empty_registry):
        """Count should return 0 when registry is empty."""
        assert empty_registry.count() == 0

    def test_count_after_operations(self, empty_registry):
        """Count should reflect register/unregister operations."""
        assert empty_registry.count() == 0
        empty_registry.register(make_agent("a"))
        assert empty_registry.count() == 1
        empty_registry.register(make_agent("b"))
        assert empty_registry.count() == 2
        empty_registry.unregister("a")
        assert empty_registry.count() == 1
        empty_registry.clear()
        assert empty_registry.count() == 0


# ---------------------------------------------------------------------------
# Thread Safety Tests
# ---------------------------------------------------------------------------


class TestThreadSafety:
    """Tests for concurrent access to AgentRegistry."""

    def test_concurrent_register(self, empty_registry):
        """Multiple threads registering different agents should not cause errors."""
        num_threads = 20
        errors = []  # type: list

        def register_agent(index):
            # type: (int) -> None
            try:
                agent = make_agent("thread-agent-{}".format(index))
                empty_registry.register(agent)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=register_agent, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, "Errors during concurrent register: {}".format(errors)
        assert empty_registry.count() == num_threads

    def test_concurrent_get(self, populated_registry):
        """Multiple threads reading the same agent should not cause errors."""
        num_threads = 50
        results = []  # type: list
        errors = []  # type: list

        def get_agent():
            # type: () -> None
            try:
                agent = populated_registry.get("alpha")
                results.append(agent)
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=get_agent)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, "Errors during concurrent get: {}".format(errors)
        assert len(results) == num_threads
        # All results should be the same agent
        assert all(r.name == "alpha" for r in results)

    def test_concurrent_register_and_get(self, empty_registry):
        """Mixed read/write concurrency should not cause errors or data corruption."""
        num_writers = 10
        num_readers = 20
        write_errors = []  # type: list
        read_errors = []  # type: list
        read_results = []  # type: list

        def writer(index):
            # type: (int) -> None
            try:
                agent = make_agent("mixed-agent-{}".format(index))
                empty_registry.register(agent)
            except AgentRegistrationError:
                pass  # Duplicate is acceptable in concurrent scenario
            except Exception as e:
                write_errors.append(e)

        def reader():
            # type: () -> None
            try:
                agents = empty_registry.list_all()
                read_results.append(len(agents))
            except Exception as e:
                read_errors.append(e)

        threads = []
        for i in range(num_writers):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
            t.start()

        for _ in range(num_readers):
            t = threading.Thread(target=reader)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(write_errors) == 0, "Write errors: {}".format(write_errors)
        assert len(read_errors) == 0, "Read errors: {}".format(read_errors)
        # All reads should return valid counts (0 to num_writers)
        assert all(0 <= count <= num_writers for count in read_results)

    def test_concurrent_unregister(self, populated_registry):
        """Multiple threads unregistering different agents should be safe."""
        errors = []  # type: list

        def unregister_agent(name):
            # type: (str) -> None
            try:
                populated_registry.unregister(name)
            except AgentNotFoundError:
                pass  # Already unregistered is acceptable
            except Exception as e:
                errors.append(e)

        threads = []
        for name in ["alpha", "beta", "gamma", "delta"]:
            t = threading.Thread(target=unregister_agent, args=(name,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, "Errors during concurrent unregister: {}".format(errors)
        assert populated_registry.count() == 0

    def test_concurrent_has_and_register(self, empty_registry):
        """Concurrent has() checks and register() calls should be safe."""
        num_threads = 30
        errors = []  # type: list
        has_results = []  # type: list

        def check_and_register(index):
            # type: (int) -> None
            try:
                name = "concurrent-agent"
                exists = empty_registry.has(name)
                has_results.append(exists)
                if not exists:
                    agent = make_agent(name)
                    empty_registry.register(agent)
            except AgentRegistrationError:
                pass  # Another thread registered first
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=check_and_register, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0, "Errors: {}".format(errors)
        assert empty_registry.count() == 1
