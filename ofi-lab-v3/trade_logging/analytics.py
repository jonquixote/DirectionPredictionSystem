from __future__ import annotations
"""
Analytics queries for candidate trade log.
Spec v2.6, Section 13.
"""

ANALYTICS_QUERIES = {
    "gate_suppression_rate": """
        SELECT suppression_reason, COUNT(*) AS n, AVG(ne_t_computed) AS avg_ne
        FROM candidate_trades
        WHERE executed = 0
        GROUP BY suppression_reason
        ORDER BY n DESC
    """,

    "counterfactual_ne_on_suppressed": """
        SELECT suppression_reason,
               AVG(ne_t_computed)     AS avg_counterfactual_ne,
               AVG(model_correct)     AS win_rate_if_executed
        FROM candidate_trades
        WHERE executed = 0 AND ne_t_computed > 0
        GROUP BY suppression_reason
    """,

    "adverse_selection_calibration": """
        SELECT
            ROUND(gate_adverse_composite_value, 1) AS composite_bucket,
            COUNT(*)          AS n,
            AVG(model_correct) AS win_rate,
            AVG(CASE WHEN gate_adverse_pass = 0 THEN 1.0 ELSE 0.0 END) AS suppression_rate
        FROM candidate_trades
        WHERE resolved_direction IS NOT NULL
        GROUP BY composite_bucket
        ORDER BY composite_bucket
    """,

    "gate_cross_tab": """
        SELECT
            suppression_reason,
            CASE WHEN ne_t_computed > 0 THEN 1 ELSE 0 END AS ne_positive,
            COUNT(*)           AS n,
            AVG(model_correct) AS win_rate_on_suppressed,
            AVG(ne_t_computed) AS avg_ne
        FROM candidate_trades
        WHERE executed = 0
          AND resolved_direction IS NOT NULL
        GROUP BY suppression_reason,
                 CASE WHEN ne_t_computed > 0 THEN 1 ELSE 0 END
        ORDER BY suppression_reason, ne_positive DESC
    """,
}


def run_query(conn, query_name: str) -> list:
    """Execute a named analytics query and return results as list of dicts."""
    if query_name not in ANALYTICS_QUERIES:
        raise ValueError(f"Unknown query: {query_name}. Available: {list(ANALYTICS_QUERIES.keys())}")
    cursor = conn.execute(ANALYTICS_QUERIES[query_name])
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
