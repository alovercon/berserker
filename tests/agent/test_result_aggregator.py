"""
Tests for berserker.agent.result_aggregator module.

Covers:
- AggregationStrategy enum values.
- ResultAggregator add/get/clear operations.
- CONCAT, MERGE, SUMMARY, CUSTOM aggregation strategies.
- Insertion order preservation.
- Edge cases (empty results, single result, duplicate task_ids).
"""

import pytest

from berserker.agent.result_aggregator import AggregationStrategy, ResultAggregator


# ---------------------------------------------------------------------------
# AggregationStrategy Tests
# ---------------------------------------------------------------------------


class TestAggregationStrategy:
    """Tests for AggregationStrategy enum."""

    def test_enum_values(self):
        """Test all enum values are defined correctly."""
        assert AggregationStrategy.CONCAT.value == "concat"
        assert AggregationStrategy.MERGE.value == "merge"
        assert AggregationStrategy.SUMMARY.value == "summary"
        assert AggregationStrategy.CUSTOM.value == "custom"

    def test_enum_count(self):
        """Test exactly 4 strategies exist."""
        assert len(AggregationStrategy) == 4


# ---------------------------------------------------------------------------
# ResultAggregator Basic Operations
# ---------------------------------------------------------------------------


class TestResultAggregatorBasic:
    """Tests for basic ResultAggregator operations."""

    def setup_method(self):
        """Create a fresh aggregator for each test."""
        self.aggregator = ResultAggregator()

    def test_initial_state(self):
        """Test aggregator starts empty."""
        assert self.aggregator.count() == 0
        assert self.aggregator.get_all_results() == {}
        assert self.aggregator.get_result("nonexistent") is None

    def test_add_single_result(self):
        """Test adding a single result."""
        result = {"content": "hello", "status": "success"}
        self.aggregator.add_result("task-1", result)

        assert self.aggregator.count() == 1
        assert self.aggregator.get_result("task-1") == result

    def test_add_multiple_results(self):
        """Test adding multiple results."""
        self.aggregator.add_result("task-1", {"content": "result 1"})
        self.aggregator.add_result("task-2", {"content": "result 2"})
        self.aggregator.add_result("task-3", {"content": "result 3"})

        assert self.aggregator.count() == 3
        all_results = self.aggregator.get_all_results()
        assert all_results["task-1"]["content"] == "result 1"
        assert all_results["task-2"]["content"] == "result 2"
        assert all_results["task-3"]["content"] == "result 3"

    def test_overwrite_existing_result(self):
        """Test that adding a result with existing task_id overwrites."""
        self.aggregator.add_result("task-1", {"content": "original"})
        self.aggregator.add_result("task-1", {"content": "updated"})

        assert self.aggregator.count() == 1
        assert self.aggregator.get_result("task-1")["content"] == "updated"

    def test_order_preservation(self):
        """Test that insertion order is preserved."""
        self.aggregator.add_result("task-c", {"content": "c"})
        self.aggregator.add_result("task-a", {"content": "a"})
        self.aggregator.add_result("task-b", {"content": "b"})

        # CONCAT strategy preserves order in output
        result = self.aggregator.aggregate(AggregationStrategy.CONCAT)
        content = result["content"]

        # task-c should appear before task-a, task-a before task-b
        pos_c = content.find("task-c")
        pos_a = content.find("task-a")
        pos_b = content.find("task-b")
        assert pos_c < pos_a < pos_b

    def test_clear(self):
        """Test clearing all results."""
        self.aggregator.add_result("task-1", {"content": "result 1"})
        self.aggregator.add_result("task-2", {"content": "result 2"})
        assert self.aggregator.count() == 2

        self.aggregator.clear()

        assert self.aggregator.count() == 0
        assert self.aggregator.get_all_results() == {}
        assert self.aggregator.get_result("task-1") is None

    def test_clear_and_reuse(self):
        """Test that aggregator can be reused after clear."""
        self.aggregator.add_result("task-1", {"content": "old"})
        self.aggregator.clear()
        self.aggregator.add_result("task-2", {"content": "new"})

        assert self.aggregator.count() == 1
        assert self.aggregator.get_result("task-2")["content"] == "new"
        assert self.aggregator.get_result("task-1") is None


# ---------------------------------------------------------------------------
# CONCAT Strategy
# ---------------------------------------------------------------------------


class TestAggregationConcat:
    """Tests for CONCAT aggregation strategy."""

    def setup_method(self):
        self.aggregator = ResultAggregator()

    def test_concat_empty(self):
        """Test CONCAT with no results."""
        result = self.aggregator.aggregate(AggregationStrategy.CONCAT)
        assert result["content"] == ""
        assert result["task_count"] == 0
        assert result["success_count"] == 0
        assert result["failure_count"] == 0

    def test_concat_single_success(self):
        """Test CONCAT with a single successful result."""
        self.aggregator.add_result("task-1", {
            "content": "Hello world",
            "status": "success",
        })
        result = self.aggregator.aggregate(AggregationStrategy.CONCAT)

        assert "task-1" in result["content"]
        assert "Hello world" in result["content"]
        assert result["task_count"] == 1
        assert result["success_count"] == 1
        assert result["failure_count"] == 0

    def test_concat_multiple_results(self):
        """Test CONCAT with multiple results."""
        self.aggregator.add_result("task-1", {
            "content": "Result A",
            "status": "success",
        })
        self.aggregator.add_result("task-2", {
            "content": "Result B",
            "status": "completed",
        })
        self.aggregator.add_result("task-3", {
            "content": "Result C",
            "status": "failed",
        })

        result = self.aggregator.aggregate(AggregationStrategy.CONCAT)

        assert "Result A" in result["content"]
        assert "Result B" in result["content"]
        assert "Result C" in result["content"]
        assert result["task_count"] == 3
        assert result["success_count"] == 2  # success + completed
        assert result["failure_count"] == 1

    def test_concat_includes_task_headers(self):
        """Test that CONCAT includes task headers with status."""
        self.aggregator.add_result("task-1", {
            "content": "data",
            "status": "success",
        })
        result = self.aggregator.aggregate(AggregationStrategy.CONCAT)

        assert "--- Task: task-1 (status: success) ---" in result["content"]

    def test_concat_missing_content_key(self):
        """Test CONCAT handles results without 'content' key."""
        self.aggregator.add_result("task-1", {"status": "success"})
        result = self.aggregator.aggregate(AggregationStrategy.CONCAT)

        assert result["content"] == "--- Task: task-1 (status: success) ---\n"


# ---------------------------------------------------------------------------
# MERGE Strategy
# ---------------------------------------------------------------------------


class TestAggregationMerge:
    """Tests for MERGE aggregation strategy."""

    def setup_method(self):
        self.aggregator = ResultAggregator()

    def test_merge_empty(self):
        """Test MERGE with no results."""
        result = self.aggregator.aggregate(AggregationStrategy.MERGE)
        assert result["_task_ids"] == []
        assert result["_task_count"] == 0

    def test_merge_single_result(self):
        """Test MERGE with a single result."""
        self.aggregator.add_result("task-1", {
            "key_a": "value_a",
            "key_b": 42,
        })
        result = self.aggregator.aggregate(AggregationStrategy.MERGE)

        assert result["key_a"] == "value_a"
        assert result["key_b"] == 42
        assert result["_task_ids"] == ["task-1"]
        assert result["_task_count"] == 1

    def test_merge_multiple_results(self):
        """Test MERGE combines keys from multiple results."""
        self.aggregator.add_result("task-1", {"key_a": "a"})
        self.aggregator.add_result("task-2", {"key_b": "b"})

        result = self.aggregator.aggregate(AggregationStrategy.MERGE)

        assert result["key_a"] == "a"
        assert result["key_b"] == "b"
        assert result["_task_count"] == 2

    def test_merge_key_conflict_later_wins(self):
        """Test that later results overwrite earlier ones on key conflict."""
        self.aggregator.add_result("task-1", {"shared_key": "first"})
        self.aggregator.add_result("task-2", {"shared_key": "second"})

        result = self.aggregator.aggregate(AggregationStrategy.MERGE)

        assert result["shared_key"] == "second"

    def test_merge_preserves_task_ids(self):
        """Test that MERGE tracks task IDs in order."""
        self.aggregator.add_result("task-c", {"c": 1})
        self.aggregator.add_result("task-a", {"a": 1})
        self.aggregator.add_result("task-b", {"b": 1})

        result = self.aggregator.aggregate(AggregationStrategy.MERGE)

        assert result["_task_ids"] == ["task-c", "task-a", "task-b"]


# ---------------------------------------------------------------------------
# SUMMARY Strategy
# ---------------------------------------------------------------------------


class TestAggregationSummary:
    """Tests for SUMMARY aggregation strategy."""

    def setup_method(self):
        self.aggregator = ResultAggregator()

    def test_summary_empty(self):
        """Test SUMMARY with no results."""
        result = self.aggregator.aggregate(AggregationStrategy.SUMMARY)

        assert result["task_count"] == 0
        assert result["success_count"] == 0
        assert result["failure_count"] == 0
        assert result["total_content_length"] == 0
        assert result["task_statuses"] == {}
        assert result["all_successful"] is True

    def test_summary_all_successful(self):
        """Test SUMMARY with all successful results."""
        self.aggregator.add_result("task-1", {
            "content": "abc",
            "status": "success",
        })
        self.aggregator.add_result("task-2", {
            "content": "defgh",
            "status": "completed",
        })

        result = self.aggregator.aggregate(AggregationStrategy.SUMMARY)

        assert result["task_count"] == 2
        assert result["success_count"] == 2
        assert result["failure_count"] == 0
        assert result["total_content_length"] == 8  # "abc" + "defgh"
        assert result["all_successful"] is True
        assert result["task_statuses"]["task-1"] == "success"
        assert result["task_statuses"]["task-2"] == "completed"

    def test_summary_with_failures(self):
        """Test SUMMARY with mixed success/failure."""
        self.aggregator.add_result("task-1", {
            "content": "ok",
            "status": "success",
        })
        self.aggregator.add_result("task-2", {
            "content": "error",
            "status": "failed",
        })

        result = self.aggregator.aggregate(AggregationStrategy.SUMMARY)

        assert result["task_count"] == 2
        assert result["success_count"] == 1
        assert result["failure_count"] == 1
        assert result["all_successful"] is False

    def test_summary_unknown_status_counts_as_failure(self):
        """Test that unknown status is counted as failure."""
        self.aggregator.add_result("task-1", {
            "content": "data",
            "status": "unknown",
        })

        result = self.aggregator.aggregate(AggregationStrategy.SUMMARY)

        assert result["success_count"] == 0
        assert result["failure_count"] == 1
        assert result["all_successful"] is False


# ---------------------------------------------------------------------------
# CUSTOM Strategy
# ---------------------------------------------------------------------------


class TestAggregationCustom:
    """Tests for CUSTOM aggregation strategy."""

    def setup_method(self):
        self.aggregator = ResultAggregator()

    def test_custom_without_fn_raises(self):
        """Test that CUSTOM strategy without custom_fn raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            self.aggregator.aggregate(AggregationStrategy.CUSTOM)

        assert "custom_fn" in str(exc_info.value)

    def test_custom_with_fn(self):
        """Test CUSTOM strategy with a custom aggregation function."""
        self.aggregator.add_result("task-1", {"value": 10})
        self.aggregator.add_result("task-2", {"value": 20})

        def sum_values(results):
            # type: (dict) -> dict
            total = sum(r.get("value", 0) for r in results.values())
            return {"total": total}

        result = self.aggregator.aggregate(
            AggregationStrategy.CUSTOM, custom_fn=sum_values
        )

        assert result["total"] == 30

    def test_custom_receives_all_results(self):
        """Test that custom function receives all results."""
        self.aggregator.add_result("task-1", {"a": 1})
        self.aggregator.add_result("task-2", {"b": 2})
        self.aggregator.add_result("task-3", {"c": 3})

        received_keys = []  # type: list

        def collect_keys(results):
            # type: (dict) -> dict
            received_keys.extend(results.keys())
            return {"keys": received_keys}

        self.aggregator.aggregate(
            AggregationStrategy.CUSTOM, custom_fn=collect_keys
        )

        assert set(received_keys) == {"task-1", "task-2", "task-3"}

    def test_custom_can_return_any_dict(self):
        """Test that custom function can return any dict structure."""
        self.aggregator.add_result("task-1", {"data": "x"})

        def custom_agg(results):
            # type: (dict) -> dict
            return {
                "custom": True,
                "count": len(results),
                "first_key": list(results.keys())[0],
            }

        result = self.aggregator.aggregate(
            AggregationStrategy.CUSTOM, custom_fn=custom_agg
        )

        assert result["custom"] is True
        assert result["count"] == 1
        assert result["first_key"] == "task-1"


# ---------------------------------------------------------------------------
# Unknown Strategy
# ---------------------------------------------------------------------------


class TestUnknownStrategy:
    """Tests for handling unknown aggregation strategies."""

    def setup_method(self):
        self.aggregator = ResultAggregator()

    def test_unknown_strategy_raises(self):
        """Test that unknown strategy raises ValueError."""
        # Create a fake enum-like object
        class FakeStrategy:
            value = "fake"

        with pytest.raises(ValueError) as exc_info:
            self.aggregator.aggregate(FakeStrategy())  # type: ignore

        assert "Unknown aggregation strategy" in str(exc_info.value)
