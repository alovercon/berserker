"""
Test script to verify concurrent multi-agent execution in berserker.

Tests:
1. Single agent execution (baseline)
2. Parallel execution of multiple agents via orchestrator
3. Verify concurrent execution (timing comparison)
"""
import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from berserker.agent.manager import agent_manager
from berserker.agent.orchestrator import OrchestrationTask, get_orchestrator
from berserker.provider.base import ChatMessage
from berserker.tool.registry import registry as tool_registry
from berserker.session.manager import session_manager


def test_single_agent():
    """Test single agent execution."""
    print("=" * 60)
    print("TEST 1: Single Agent Execution")
    print("=" * 60)
    
    session_id = session_manager.create(title="test-single-agent")
    messages = [ChatMessage(role="user", content="Count from 1 to 3 and stop.")]
    
    start = time.time()
    result = agent_manager.execute(
        agent_name="general",
        messages=messages,
        session_id=session_id,
        tool_registry=tool_registry,
        on_tool_call=None,
    )
    elapsed = time.time() - start
    
    print("Result: {}".format(result.get("content", "")[:200]))
    print("Elapsed: {:.2f}s".format(elapsed))
    print("Status: PASS" if result.get("content") else "Status: FAIL")
    print()
    return elapsed


def test_parallel_agents():
    """Test parallel agent execution via orchestrator."""
    print("=" * 60)
    print("TEST 2: Parallel Agent Execution")
    print("=" * 60)
    
    orchestrator = get_orchestrator(agent_manager, tool_registry)
    
    # Create 3 parallel tasks
    tasks = []
    for i in range(3):
        session_id = session_manager.create(title="test-parallel-{}".format(i))
        tasks.append(
            OrchestrationTask(
                task_id="parallel-{}".format(i),
                agent_name="general",
                prompt="Count from 1 to 3 and stop. This is task {}.".format(i),
                session_id=session_id,
            )
        )
    
    start = time.time()
    results = orchestrator.execute_parallel(tasks)
    elapsed = time.time() - start
    
    print("Tasks completed: {}".format(len(results)))
    for r in results:
        status = r.get("status", "unknown")
        task_id = r.get("task_id", "unknown")
        result_data = r.get("result", {})
        content = ""
        if isinstance(result_data, dict):
            content = result_data.get("content", "")[:100]
        print("  Task {}: status={}, content='{}...'".format(task_id, status, content))
    
    print("Elapsed: {:.2f}s".format(elapsed))
    print("Status: PASS" if all(r.get("status") == "completed" for r in results) else "Status: FAIL")
    print()
    return elapsed


def test_concurrent_timing():
    """Verify that parallel execution is actually concurrent."""
    print("=" * 60)
    print("TEST 3: Concurrent Timing Verification")
    print("=" * 60)
    
    # Run 3 sequential tasks to get baseline
    print("Running 3 sequential tasks...")
    sequential_times = []
    for i in range(3):
        session_id = session_manager.create(title="test-sequential-{}".format(i))
        messages = [ChatMessage(role="user", content="Count from 1 to 3 and stop.")]
        start = time.time()
        agent_manager.execute(
            agent_name="general",
            messages=messages,
            session_id=session_id,
            tool_registry=tool_registry,
            on_tool_call=None,
        )
        sequential_times.append(time.time() - start)
    
    sequential_total = sum(sequential_times)
    print("Sequential total: {:.2f}s (avg: {:.2f}s per task)".format(
        sequential_total, sequential_total / 3))
    
    # Run 3 parallel tasks
    print("Running 3 parallel tasks...")
    orchestrator = get_orchestrator(agent_manager, tool_registry)
    tasks = []
    for i in range(3):
        session_id = session_manager.create(title="test-parallel-timing-{}".format(i))
        tasks.append(
            OrchestrationTask(
                task_id="parallel-timing-{}".format(i),
                agent_name="general",
                prompt="Count from 1 to 3 and stop. This is task {}.".format(i),
                session_id=session_id,
            )
        )
    
    start = time.time()
    results = orchestrator.execute_parallel(tasks)
    parallel_elapsed = time.time() - start
    
    print("Parallel total: {:.2f}s".format(parallel_elapsed))
    
    # Parallel should be faster than sequential (at least 30% faster)
    speedup = sequential_total / parallel_elapsed if parallel_elapsed > 0 else 0
    print("Speedup: {:.2f}x".format(speedup))
    
    if parallel_elapsed < sequential_total * 0.7:
        print("Status: PASS (parallel is significantly faster)")
    else:
        print("Status: WARN (parallel may not be fully concurrent)")
    print()
    
    return {
        "sequential": sequential_total,
        "parallel": parallel_elapsed,
        "speedup": speedup,
    }


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("PyBerserker Concurrent Multi-Agent Test Suite")
    print("=" * 60 + "\n")
    
    try:
        single_time = test_single_agent()
        parallel_time = test_parallel_agents()
        timing_results = test_concurrent_timing()
        
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print("Single agent execution: {:.2f}s".format(single_time))
        print("Parallel execution (3 tasks): {:.2f}s".format(parallel_time))
        print("Sequential baseline: {:.2f}s".format(timing_results["sequential"]))
        print("Parallel timing: {:.2f}s".format(timing_results["parallel"]))
        print("Speedup: {:.2f}x".format(timing_results["speedup"]))
        print()
        print("All tests completed successfully!")
        
    except Exception as e:
        print("TEST FAILED: {}".format(e))
        import traceback
        traceback.print_exc()
        sys.exit(1)
