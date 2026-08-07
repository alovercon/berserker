"""
Thread safety tests for berserker.agent.messaging module.

Covers:
- Concurrent send operations from multiple threads.
- Concurrent receive operations from multiple threads.
- Mixed concurrent send/receive operations.
- No race conditions or data loss under heavy concurrency.
"""

import threading
import pytest

from berserker.agent.messaging import AgentMessage, MessageRouter


# ---------------------------------------------------------------------------
# Thread Safety Tests
# ---------------------------------------------------------------------------


class TestMessagingThreadSafety:
    """Tests for thread-safe message routing."""

    def setup_method(self):
        """Create a fresh router for each test."""
        self.router = MessageRouter()

    def test_concurrent_send_same_agent(self):
        """Test concurrent sends to the same agent from multiple threads."""
        self.router.register_agent("plan")
        num_threads = 10
        messages_per_thread = 50
        errors = []

        def send_messages(thread_id):
            try:
                for i in range(messages_per_thread):
                    msg = AgentMessage(
                        from_agent="thread-{}".format(thread_id),
                        to_agent="plan",
                        session_id="sess-1",
                        content="msg-{}-{}".format(thread_id, i),
                    )
                    self.router.send(msg)
            except Exception as e:
                errors.append(e)

        threads = []
        for t in range(num_threads):
            thread = threading.Thread(target=send_messages, args=(t,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, "Errors occurred: {}".format(errors)
        expected = num_threads * messages_per_thread
        assert self.router.get_pending_count("plan") == expected

    def test_concurrent_send_different_agents(self):
        """Test concurrent sends to different agents."""
        agents = ["agent-{}".format(i) for i in range(5)]
        for agent in agents:
            self.router.register_agent(agent)

        errors = []

        def send_to_agent(agent_name):
            try:
                for i in range(20):
                    msg = AgentMessage(
                        from_agent="sender",
                        to_agent=agent_name,
                        session_id="sess-1",
                        content="msg-{}".format(i),
                    )
                    self.router.send(msg)
            except Exception as e:
                errors.append(e)

        threads = []
        for agent in agents:
            thread = threading.Thread(target=send_to_agent, args=(agent,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0
        for agent in agents:
            assert self.router.get_pending_count(agent) == 20

    def test_concurrent_receive(self):
        """Test concurrent receives from different agents."""
        agents = ["agent-{}".format(i) for i in range(5)]
        for agent in agents:
            self.router.register_agent(agent)
            for i in range(10):
                self.router.send(
                    AgentMessage(
                        from_agent="sender",
                        to_agent=agent,
                        session_id="sess-1",
                        content="msg-{}".format(i),
                    )
                )

        received_counts = {}
        errors = []

        def receive_from_agent(agent_name):
            try:
                messages = self.router.receive(agent_name)
                received_counts[agent_name] = len(messages)
            except Exception as e:
                errors.append(e)

        threads = []
        for agent in agents:
            thread = threading.Thread(target=receive_from_agent, args=(agent,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0
        for agent in agents:
            assert received_counts[agent] == 10
            assert self.router.get_pending_count(agent) == 0

    def test_concurrent_send_and_receive(self):
        """Test concurrent send and receive on the same agent."""
        self.router.register_agent("plan")
        send_count = [0]
        receive_count = [0]
        errors = []
        lock = threading.Lock()

        def sender():
            try:
                for i in range(50):
                    msg = AgentMessage(
                        from_agent="sender",
                        to_agent="plan",
                        session_id="sess-1",
                        content="msg-{}".format(i),
                    )
                    self.router.send(msg)
                    with lock:
                        send_count[0] += 1
            except Exception as e:
                errors.append(e)

        def receiver():
            try:
                total = 0
                for _ in range(10):
                    messages = self.router.receive("plan")
                    total += len(messages)
                    threading.Event().wait(0.001)  # Small delay
                with lock:
                    receive_count[0] = total
            except Exception as e:
                errors.append(e)

        send_thread = threading.Thread(target=sender)
        recv_thread = threading.Thread(target=receiver)

        send_thread.start()
        recv_thread.start()

        send_thread.join()
        recv_thread.join()

        assert len(errors) == 0
        # All sent messages should be accounted for (either received or still pending)
        total_accounted = receive_count[0] + self.router.get_pending_count("plan")
        assert total_accounted == send_count[0]

    def test_concurrent_broadcast(self):
        """Test concurrent broadcasts from multiple threads."""
        agents = ["agent-{}".format(i) for i in range(5)]
        for agent in agents:
            self.router.register_agent(agent)

        errors = []
        broadcast_count = [0]
        lock = threading.Lock()

        def do_broadcast(thread_id):
            try:
                msg = AgentMessage(
                    from_agent="broadcaster-{}".format(thread_id),
                    to_agent="dummy",
                    session_id="sess-1",
                    content="broadcast-{}".format(thread_id),
                )
                count = self.router.broadcast(msg)
                with lock:
                    broadcast_count[0] += count
            except Exception as e:
                errors.append(e)

        threads = []
        for t in range(5):
            thread = threading.Thread(target=do_broadcast, args=(t,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0
        # Each broadcast should reach 4 agents (5 total - sender)
        # But senders are different, so each reaches all 5 registered agents
        # Actually, broadcast excludes from_agent, so each reaches 4 others
        # Plus the 5 registered agents minus the from_agent
        # Since from_agent is "broadcaster-X" which is not registered,
        # each broadcast reaches all 5 registered agents
        assert broadcast_count[0] == 25  # 5 broadcasts * 5 agents each

    def test_concurrent_register_and_send(self):
        """Test concurrent registration and sending."""
        errors = []

        def register_and_send(agent_name):
            try:
                self.router.register_agent(agent_name)
                for i in range(10):
                    msg = AgentMessage(
                        from_agent="sender",
                        to_agent=agent_name,
                        session_id="sess-1",
                        content="msg-{}".format(i),
                    )
                    self.router.send(msg)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(10):
            thread = threading.Thread(
                target=register_and_send, args=("agent-{}".format(i),)
            )
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0
        for i in range(10):
            assert self.router.get_pending_count("agent-{}".format(i)) == 10

    def test_concurrent_peek_and_receive(self):
        """Test concurrent peek and receive operations."""
        self.router.register_agent("plan")
        for i in range(100):
            self.router.send(
                AgentMessage(
                    from_agent="sender",
                    to_agent="plan",
                    session_id="sess-1",
                    content="msg-{}".format(i),
                )
            )

        peek_results = []
        receive_results = []
        errors = []
        lock = threading.Lock()
        peek_done = threading.Event()

        def do_peek():
            try:
                for _ in range(10):
                    messages = self.router.peek("plan")
                    with lock:
                        peek_results.append(len(messages))
                    threading.Event().wait(0.001)
                peek_done.set()
            except Exception as e:
                errors.append(e)

        def do_receive():
            try:
                peek_done.wait()  # Wait for peek to finish
                messages = self.router.receive("plan")
                with lock:
                    receive_results.append(len(messages))
            except Exception as e:
                errors.append(e)

        peek_thread = threading.Thread(target=do_peek)
        recv_thread = threading.Thread(target=do_receive)

        peek_thread.start()
        recv_thread.start()

        peek_thread.join()
        recv_thread.join()

        assert len(errors) == 0
        # Peek should have seen 100 messages (before receive cleared)
        assert all(count == 100 for count in peek_results)
        # Receive should have gotten all 100
        assert receive_results == [100]
        assert self.router.get_pending_count("plan") == 0

    def test_no_message_duplication(self):
        """Test that messages are not duplicated under concurrent access."""
        self.router.register_agent("plan")
        num_messages = 200
        message_ids = set()

        def send_unique(thread_id, count):
            for i in range(count):
                msg = AgentMessage(
                    from_agent="sender",
                    to_agent="plan",
                    session_id="sess-1",
                    content="msg-{}-{}".format(thread_id, i),
                )
                message_ids.add(msg.message_id)
                self.router.send(msg)

        threads = []
        per_thread = num_messages // 4
        for t in range(4):
            thread = threading.Thread(target=send_unique, args=(t, per_thread))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert self.router.get_pending_count("plan") == num_messages
        received = self.router.receive("plan")
        received_ids = {m.message_id for m in received}
        assert len(received_ids) == num_messages
        assert received_ids == message_ids

    def test_concurrent_clear(self):
        """Test concurrent clear operations."""
        self.router.register_agent("plan")
        for i in range(100):
            self.router.send(
                AgentMessage(
                    from_agent="sender",
                    to_agent="plan",
                    session_id="sess-1",
                    content="msg-{}".format(i),
                )
            )

        errors = []

        def do_clear():
            try:
                self.router.clear("plan")
            except Exception as e:
                errors.append(e)

        threads = []
        for _ in range(5):
            thread = threading.Thread(target=do_clear)
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0
        assert self.router.get_pending_count("plan") == 0
