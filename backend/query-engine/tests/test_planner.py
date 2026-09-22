"""The planner decides which access path a statement uses.

These tests pin the rules the assignment states: an equality on an indexed
column uses an IndexScan, a range on a B+ tree uses an IndexRangeScan, and
anything else falls back to a full scan.

They run against a table that spans many pages on purpose. On a single page
table a sequential scan is genuinely the cheapest option and the planner is
right to pick it, so the rules would be invisible.
"""

import pytest

from queryengine.errors import CatalogError


def plan_of(engine, sql):
    return engine.execute(f"EXPLAIN {sql}").plan_text


def test_without_indexes_everything_is_a_sequential_scan(large):
    assert "SeqScan" in plan_of(large, "SELECT * FROM ventas WHERE id = 1500")


def test_equality_on_a_hash_index_uses_an_index_scan(large):
    large.execute("CREATE INDEX idx_id ON ventas(id) USING HASH")
    plan = plan_of(large, "SELECT * FROM ventas WHERE id = 1500")
    assert "IndexScan" in plan
    assert "HASH" in plan


def test_equality_on_a_btree_index_uses_an_index_scan(large):
    large.execute("CREATE INDEX idx_id ON ventas(id) USING BTREE")
    plan = plan_of(large, "SELECT * FROM ventas WHERE id = 1500")
    assert "IndexScan" in plan
    assert "BTREE" in plan


def test_a_narrow_range_on_a_btree_uses_a_range_scan(large):
    large.execute("CREATE INDEX idx_id ON ventas(id) USING BTREE")
    plan = plan_of(large, "SELECT * FROM ventas WHERE id >= 1000 AND id <= 1005")
    assert "IndexRangeScan" in plan


def test_a_wide_range_falls_back_to_a_full_scan(large):
    """The crossover the selectivity experiment is meant to expose.

    Each RID reached through a secondary index costs one block, so once the
    range covers a large share of the table the full scan is cheaper.
    """
    large.execute("CREATE INDEX idx_id ON ventas(id) USING BTREE")
    plan = plan_of(large, "SELECT * FROM ventas WHERE id >= 0 AND id <= 3000")
    assert "SeqScan" in plan


def test_a_hash_index_cannot_serve_a_range(large):
    large.execute("CREATE INDEX idx_id ON ventas(id) USING HASH")
    plan = plan_of(large, "SELECT * FROM ventas WHERE id >= 1000 AND id <= 1005")
    assert "IndexRangeScan" not in plan
    assert "SeqScan" in plan


def test_between_is_planned_as_a_range(large):
    large.execute("CREATE INDEX idx_id ON ventas(id) USING BTREE")
    assert "IndexRangeScan" in plan_of(large, "SELECT * FROM ventas WHERE id BETWEEN 1000 AND 1005")


def test_sequential_tables_can_binary_search_the_primary_key(large_sequential):
    plan = plan_of(large_sequential, "SELECT * FROM ventas_ord WHERE id = 1500")
    assert "SequentialSearch" in plan


def test_sequential_tables_sweep_a_primary_key_range(large_sequential):
    plan = plan_of(large_sequential, "SELECT * FROM ventas_ord WHERE id >= 1000 AND id <= 1005")
    assert "SequentialRangeScan" in plan


def test_a_disjunction_cannot_drive_an_access_path(large):
    large.execute("CREATE INDEX idx_id ON ventas(id) USING BTREE")
    plan = plan_of(large, "SELECT * FROM ventas WHERE id = 1500 OR region = 'region-1'")
    assert "SeqScan" in plan
    assert "Filter" in plan


def test_unindexed_conjuncts_become_a_filter_above_the_index_scan(large):
    large.execute("CREATE INDEX idx_id ON ventas(id) USING BTREE")
    plan = plan_of(large, "SELECT * FROM ventas WHERE id = 1500 AND region = 'region-4'")
    assert "IndexScan" in plan
    assert "Filter" in plan


def test_a_fully_covered_predicate_needs_no_filter(large):
    large.execute("CREATE INDEX idx_id ON ventas(id) USING BTREE")
    assert "Filter" not in plan_of(large, "SELECT * FROM ventas WHERE id = 1500")


def test_the_planner_prefers_the_cheaper_of_two_indexes(large):
    large.execute("CREATE INDEX idx_hash ON ventas(id) USING HASH")
    large.execute("CREATE INDEX idx_btree ON ventas(id) USING BTREE")
    plan = plan_of(large, "SELECT * FROM ventas WHERE id = 1500")
    assert "idx_hash" in plan


def test_projection_and_limit_sit_above_the_access_path(seeded):
    plan = plan_of(seeded, "SELECT nombre FROM empleados ORDER BY salario DESC LIMIT 2")
    assert plan.index("Project") < plan.index("Limit") < plan.index("Sort")


def test_the_plan_carries_a_cost_estimate(seeded):
    plan = seeded.execute("EXPLAIN SELECT * FROM empleados").plan
    assert plan["cost"]["estimated_blocks"] > 0


def test_the_estimate_tracks_the_table_size(large):
    plan = large.execute("EXPLAIN SELECT * FROM ventas").plan
    assert plan["cost"]["estimated_rows"] == 4000
    assert plan["cost"]["estimated_blocks"] > 1


def test_explain_does_not_execute(seeded):
    result = seeded.execute("EXPLAIN DELETE FROM empleados WHERE id = 101")
    assert result.affected_rows == 0
    assert seeded.execute("SELECT * FROM empleados").row_count == 5


def test_unknown_column_is_reported(seeded):
    with pytest.raises(CatalogError):
        seeded.execute("SELECT * FROM empleados WHERE inexistente = 1")


def test_unknown_table_is_reported(seeded):
    with pytest.raises(CatalogError):
        seeded.execute("SELECT * FROM inexistente")
