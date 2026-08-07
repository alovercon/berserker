"""
Result aggregation for berserker orchestration system.

Provides:
- AggregationStrategy enum for different aggregation approaches.
- ResultAggregator class for collecting and combining subagent results.

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class AggregationStrategy(Enum):
    """Strategies for aggregating subagent results.

    Attributes:
        CONCAT: Concatenate all result contents into a single string.
        MERGE: Merge all result dicts (later wins on key conflict).
        SUMMARY: Create summary with task count, success count, etc.
        CUSTOM: Use a provided custom aggregation function.
    """
    CONCAT = "concat"
    MERGE = "merge"
    SUMMARY = "summary"
    CUSTOM = "custom"


class ResultAggregator(object):
    """Collects and aggregates results from multiple subagent executions.

    Thread-safe result collection with multiple aggregation strategies.

    Usage:
        aggregator = ResultAggregator()
        aggregator.add_result("task-1", {"content": "result 1", "status": "success"})
        aggregator.add_result("task-2", {"content": "result 2", "status": "success"})
        combined = aggregator.aggregate(AggregationStrategy.CONCAT)
    """

    def __init__(self):
        # type: () -> None
        """Initialize the result aggregator."""
        self._results = {}  # type: Dict[str, Dict[str, Any]]
        self._order = []  # type: List[str]

    def add_result(self, task_id, result):
        # type: (str, Dict[str, Any]) -> None
        """Add a result from a completed task.

        Args:
            task_id: Unique identifier for the task.
            result: Dict containing the task result (content, status, etc.).
        """
        self._results[task_id] = result
        if task_id not in self._order:
            self._order.append(task_id)
        logger.debug("Added result for task '%s'", task_id)

    def get_result(self, task_id):
        # type: (str) -> Optional[Dict[str, Any]]
        """Get a specific task result.

        Args:
            task_id: Unique identifier for the task.

        Returns:
            The result dict, or None if not found.
        """
        return self._results.get(task_id)

    def get_all_results(self):
        # type: () -> Dict[str, Dict[str, Any]]
        """Get all collected results.

        Returns:
            Dict mapping task_id to result dict.
        """
        return dict(self._results)

    def aggregate(self, strategy, custom_fn=None):
        # type: (AggregationStrategy, Optional[Callable[[Dict[str, Dict[str, Any]]], Dict[str, Any]]]) -> Dict[str, Any]
        """Aggregate all collected results using the specified strategy.

        Args:
            strategy: The aggregation strategy to use.
            custom_fn: Custom aggregation function (required for CUSTOM strategy).
                       Receives dict of {task_id: result} and returns aggregated dict.

        Returns:
            Aggregated result dict.

        Raises:
            ValueError: If CUSTOM strategy is used without custom_fn.
        """
        if strategy == AggregationStrategy.CONCAT:
            return self._aggregate_concat()
        elif strategy == AggregationStrategy.MERGE:
            return self._aggregate_merge()
        elif strategy == AggregationStrategy.SUMMARY:
            return self._aggregate_summary()
        elif strategy == AggregationStrategy.CUSTOM:
            if custom_fn is None:
                raise ValueError(
                    "CUSTOM strategy requires a custom_fn callable"
                )
            return custom_fn(self._results)
        else:
            raise ValueError("Unknown aggregation strategy: {}".format(strategy))

    def clear(self):
        # type: () -> None
        """Clear all collected results."""
        self._results.clear()
        self._order.clear()
        logger.debug("Cleared all results")

    def count(self):
        # type: () -> int
        """Get the number of collected results.

        Returns:
            Number of results currently stored.
        """
        return len(self._results)

    def _aggregate_concat(self):
        # type: () -> Dict[str, Any]
        """Concatenate all result contents into a single string.

        Returns:
            Dict with 'content' key containing concatenated results.
        """
        parts = []  # type: List[str]
        success_count = 0
        failure_count = 0

        for task_id in self._order:
            result = self._results[task_id]
            content = result.get("content", "")
            status = result.get("status", "unknown")

            if status == "success" or status == "completed":
                success_count += 1
            else:
                failure_count += 1

            parts.append(
                "--- Task: {} (status: {}) ---\n{}".format(
                    task_id, status, content
                )
            )

        return {
            "content": "\n\n".join(parts),
            "task_count": len(self._order),
            "success_count": success_count,
            "failure_count": failure_count,
        }

    def _aggregate_merge(self):
        # type: () -> Dict[str, Any]
        """Merge all result dicts into one (later wins on conflict).

        Returns:
            Merged dict with all result keys.
        """
        merged = {}  # type: Dict[str, Any]
        merged["_task_ids"] = list(self._order)

        for task_id in self._order:
            result = self._results[task_id]
            for key, value in result.items():
                merged[key] = value

        merged["_task_count"] = len(self._order)
        return merged

    def _aggregate_summary(self):
        # type: () -> Dict[str, Any]
        """Create a summary of all results.

        Returns:
            Summary dict with statistics and per-task status.
        """
        success_count = 0
        failure_count = 0
        total_content_length = 0
        task_statuses = {}  # type: Dict[str, str]

        for task_id in self._order:
            result = self._results[task_id]
            status = result.get("status", "unknown")
            content = result.get("content", "")

            task_statuses[task_id] = status

            if status == "success" or status == "completed":
                success_count += 1
            else:
                failure_count += 1

            total_content_length += len(content)

        return {
            "task_count": len(self._order),
            "success_count": success_count,
            "failure_count": failure_count,
            "total_content_length": total_content_length,
            "task_statuses": task_statuses,
            "all_successful": failure_count == 0,
        }
