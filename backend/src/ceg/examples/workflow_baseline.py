"""Workflow Baseline — same sales pipeline coded in pure LangGraph (no CEG compiler).

This script builds the exact same 4-node sequential pipeline as
``sales_pipeline.py`` but using LangGraph directly, without the CEG
abstraction layer.  It serves as the performance / correctness baseline
for the CEG-vs-baseline benchmark planned in S7.

Run with:  python -m ceg.examples.workflow_baseline
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

# ── helpers ──────────────────────────────────────────────────────────


def _merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    merged = a.copy()
    merged.update(b)
    return merged


# ── state schema ─────────────────────────────────────────────────────


class BaselineState(TypedDict):
    """Minimal state for the baseline pipeline."""

    data: Annotated[dict[str, Any], _merge_dicts]
    messages: Annotated[list[str], operator.add]
    total_cost: Annotated[float, operator.add]
    total_latency_ms: Annotated[float, operator.add]


# ── node functions (hard-coded, no CEG abstraction) ──────────────────


def fetch_data(state: BaselineState) -> dict[str, Any]:
    """Simulate fetching raw sales data."""
    return {
        "data": {"raw_sales": [120, 250, 180, 95, 310]},
        "messages": ["fetch_data: retrieved 5 records"],
        "total_cost": 0.047,
        "total_latency_ms": 150.0,
    }


def aggregate(state: BaselineState) -> dict[str, Any]:
    """Aggregate sales figures."""
    raw = state["data"].get("raw_sales", [])
    total = sum(raw)
    avg = total / len(raw) if raw else 0
    return {
        "data": {
            "total_sales": total,
            "avg_sales": round(avg, 2),
            "record_count": len(raw),
        },
        "messages": [f"aggregate: total={total}, avg={avg:.2f}"],
        "total_cost": 0.053,
        "total_latency_ms": 130.0,
    }


def detect_anomaly(state: BaselineState) -> dict[str, Any]:
    """Detect anomalies in the aggregated data."""
    total = state["data"].get("total_sales", 0)
    anomaly_detected = total > 800
    return {
        "data": {"anomaly_detected": anomaly_detected, "threshold": 800},
        "messages": [f"detect_anomaly: anomaly={'YES' if anomaly_detected else 'NO'}"],
        "total_cost": 0.062,
        "total_latency_ms": 200.0,
    }


def generate_alert(state: BaselineState) -> dict[str, Any]:
    """Generate an alert report."""
    anomaly = state["data"].get("anomaly_detected", False)
    alert_text = (
        "ALERT: Sales anomaly detected — total exceeds threshold."
        if anomaly
        else "OK: Sales within normal range."
    )
    return {
        "data": {"alert": alert_text},
        "messages": [f"generate_alert: {alert_text}"],
        "total_cost": 0.045,
        "total_latency_ms": 160.0,
    }


# ── graph construction ───────────────────────────────────────────────


def build_baseline_graph() -> Any:
    """Build and compile the baseline LangGraph workflow."""
    graph: StateGraph[BaselineState] = StateGraph(BaselineState)

    graph.add_node("fetch_data", fetch_data)
    graph.add_node("aggregate", aggregate)
    graph.add_node("detect_anomaly", detect_anomaly)
    graph.add_node("generate_alert", generate_alert)

    graph.add_edge(START, "fetch_data")
    graph.add_edge("fetch_data", "aggregate")
    graph.add_edge("aggregate", "detect_anomaly")
    graph.add_edge("detect_anomaly", "generate_alert")
    graph.add_edge("generate_alert", END)

    return graph.compile()


# ── main ─────────────────────────────────────────────────────────────


def main() -> None:
    """Execute the baseline pipeline and print results."""
    print("=" * 60)
    print(" LangGraph Baseline Pipeline — Pure LangGraph (no CEG)")
    print("=" * 60)

    compiled = build_baseline_graph()

    initial_state: dict[str, Any] = {
        "data": {},
        "messages": [],
        "total_cost": 0.0,
        "total_latency_ms": 0.0,
    }

    result = compiled.invoke(initial_state)

    print("\n— Messages —\n")
    for msg in result["messages"]:
        print(f"  {msg}")

    print(f"\n  Total cost:    {result['total_cost']:.4f}")
    print(f"  Total latency: {result['total_latency_ms']:.1f} ms")
    print(f"\n  Final data keys: {sorted(result['data'].keys())}")
    print("=" * 60)


if __name__ == "__main__":
    main()
