"""
Verify concurrent multi-agent execution capability.

Tests the orchestrator's parallel execution using mock agents
to verify threading/concurrency works correctly without requiring API calls.
"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from berserker.agent.orchestrator import OrchestrationTask, Orchestrator


class MockAgentManager:
    """Mock agent manager that simulates agent execution with configurable delay."""
    
    def __init__(self, delay=1.0):
        self.delay = delay
        self.execution_log = []
        self.lock = threading.Lock()
    
    def execute(self, agent_name, messages, session_id, tool_registry, on_tool_call=None, abort_event=None):
        """Simulate agent execution with delay."""
        start = time.time()
        
        # Simulate work
        if abort_event and not abort_event.is_set():
            time.sleep(self.delay)
        
        elapsed = time.time() - start
        
        with self.lock:
            self.execution_log.append({
                'agent_name': agent_name,
                'session_id': session_id,
                'start': start,
                'end': time.time(),
                'elapsed': elapsed,
                'thread': threading.current_thread().name,
            })
        
        return {
            'content': 'Task completed by {} in {:.2f}s'.format(agent_name, elapsed),
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30},
        }


class MockToolRegistry:
    """Mock tool registry."""
    pass


def test_parallel_concurrency():
    """Verify that parallel execution actually runs tasks concurrently."""
    print("=" * 60)
    print("TEST: Parallel Execution Concurrency")
    print("=" * 60)
    
    # Create mock agent manager with 1 second delay per task
    mock_manager = MockAgentManager(delay=1.0)
    mock_registry = MockToolRegistry()
    
    orchestrator = Orchestrator(mock_manager, mock_registry)
    
    # Create 3 parallel tasks
    tasks = []
    for i in range(3):
        tasks.append(
            OrchestrationTask(
                task_id="test-parallel-{}".format(i),
                agent_name="general",
                prompt="Test task {}".format(i),
                session_id="session-{}".format(i),
            )
        )
    
    # Execute in parallel
    start = time.time()
    results = orchestrator.execute_parallel(tasks)
    elapsed = time.time() - start
    
    print("Tasks executed: {}".format(len(results)))
    print("Total elapsed: {:.2f}s".format(elapsed))
    print()
    
    # Check execution log for concurrency
    print("Execution Log:")
    for entry in mock_manager.execution_log:
        print("  Thread: {}, Agent: {}, Elapsed: {:.2f}s".format(
            entry['thread'], entry['agent_name'], entry['elapsed']))
    print()
    
    # Verify concurrency:
    # If tasks ran sequentially, total time would be ~3.0s (3 tasks * 1.0s)
    # If tasks ran in parallel, total time should be ~1.0s
    if elapsed < 2.0:
        print("CONCURRENCY: PASS (tasks ran in parallel, {:.2f}s < 2.0s)".format(elapsed))
    else:
        print("CONCURRENCY: FAIL (tasks may have run sequentially, {:.2f}s >= 2.0s)".format(elapsed))
    
    # Verify all tasks completed
    completed = sum(1 for r in results if r.get('status') == 'completed')
    if completed == 3:
        print("COMPLETION: PASS (all 3 tasks completed)")
    else:
        print("COMPLETION: FAIL (only {} of 3 tasks completed)".format(completed))
    
    # Verify different threads were used
    threads = set(e['thread'] for e in mock_manager.execution_log)
    if len(threads) >= 2:
        print("THREADING: PASS ({} different threads used)".format(len(threads)))
    else:
        print("THREADING: WARN (only {} thread used)".format(len(threads)))
    
    print()
    return elapsed < 2.0 and completed == 3


def test_sequential_order():
    """Verify that sequential execution maintains order."""
    print("=" * 60)
    print("TEST: Sequential Execution Order")
    print("=" * 60)
    
    mock_manager = MockAgentManager(delay=0.2)
    mock_registry = MockToolRegistry()
    
    orchestrator = Orchestrator(mock_manager, mock_registry)
    
    # Create 3 sequential tasks
    tasks = []
    for i in range(3):
        tasks.append(
            OrchestrationTask(
                task_id="test-sequential-{}".format(i),
                agent_name="general",
                prompt="Sequential task {}".format(i),
                session_id="seq-session-{}".format(i),
            )
        )
    
    start = time.time()
    results = orchestrator.execute_sequential(tasks)
    elapsed = time.time() - start
    
    print("Tasks executed: {}".format(len(results)))
    print("Total elapsed: {:.2f}s".format(elapsed))
    print()
    
    # Verify order
    order_correct = True
    for i, result in enumerate(results):
        task_id = result.get('task_id', '')
        expected_id = 'test-sequential-{}'.format(i)
        if task_id != expected_id:
            order_correct = False
            print("ORDER: FAIL (expected {}, got {})".format(expected_id, task_id))
    
    if order_correct:
        print("ORDER: PASS (tasks executed in correct order)")
    
    # Sequential should take ~0.6s (3 * 0.2s)
    if elapsed > 0.5:
        print("TIMING: PASS (sequential took {:.2f}s >= 0.5s)".format(elapsed))
    else:
        print("TIMING: WARN (sequential took {:.2f}s < 0.5s)".format(elapsed))
    
    print()
    return order_correct


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("PyBerserker Concurrent Multi-Agent Verification")
    print("=" * 60 + "\n")
    
    try:
        parallel_pass = test_parallel_concurrency()
        sequential_pass = test_sequential_order()
        
        print("=" * 60)
        print("FINAL RESULTS")
        print("=" * 60)
        print("Parallel Concurrency: {}".format("PASS" if parallel_pass else "FAIL"))
        print("Sequential Order: {}".format("PASS" if sequential_pass else "FAIL"))
        print()
        
        if parallel_pass and sequential_pass:
            print("All tests PASSED! Concurrent multi-agent execution is working correctly.")
        else:
            print("Some tests FAILED. Please review the output above.")
            sys.exit(1)
            
    except Exception as e:
        print("TEST FAILED: {}".format(e))
        import traceback
        traceback.print_exc()
        sys.exit(1)
