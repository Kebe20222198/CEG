"""Sales Pipeline — CEG compiled and executed end-to-end.

Demonstrates the CEG Compiler on realistic use cases:
  1. Sequential: fetch_data → aggregate → detect_anomaly → generate_alert
  2. Parallel (fan-out/fan-in): {fetch_nord, fetch_sud} → aggregate → detect_anomaly → generate_alert

Run with:  python -m ceg.examples.sales_pipeline
"""

from __future__ import annotations

from ceg.compiler.compiler import CEGCompiler
from ceg.models.graph import CEGEdge, CEGGraph, EdgeType
from ceg.models.node import CEGNode, ModelTierHint
from ceg.models.task import CognitiveTask


def build_sales_pipeline() -> CEGGraph:
    """Build a 4-node sequential sales anomaly detection pipeline."""
    nodes = [
        CEGNode(
            id="fetch_data",
            objective="Fetch raw sales data from regional databases",
            task=CognitiveTask(
                objective="Retrieve daily sales records",
                constraints=["Read-only access", "Max 10 000 records"],
                success_criteria=["Non-empty dataset returned"],
            ),
            required_capabilities=["data_retrieval"],
        ),
        CEGNode(
            id="aggregate",
            objective="Aggregate sales metrics by region and product category",
            dependencies=["fetch_data"],
            task=CognitiveTask(
                objective="Compute totals, averages, and trends",
                constraints=["Handle missing values gracefully"],
                success_criteria=["All regions covered"],
            ),
            required_capabilities=["data_analysis"],
        ),
        CEGNode(
            id="detect_anomaly",
            objective="Detect statistical anomalies in aggregated sales data",
            dependencies=["aggregate"],
            task=CognitiveTask(
                objective="Identify outliers using Z-score method",
                constraints=["Threshold: 2.5 sigma"],
                success_criteria=["Anomaly list produced"],
            ),
            required_capabilities=["anomaly_detection"],
            model_tier_hint=ModelTierHint.QUALITY,
        ),
        CEGNode(
            id="generate_alert",
            objective="Generate alert report for detected anomalies",
            dependencies=["detect_anomaly"],
            task=CognitiveTask(
                objective="Produce human-readable alert summary",
                constraints=["Max 500 words", "Include severity levels"],
                success_criteria=["Report generated"],
            ),
            required_capabilities=["text_generation"],
            model_tier_hint=ModelTierHint.BALANCED,
        ),
    ]

    edges = [
        CEGEdge(source="fetch_data", target="aggregate", edge_type=EdgeType.SEQUENTIAL),
        CEGEdge(
            source="aggregate", target="detect_anomaly", edge_type=EdgeType.SEQUENTIAL
        ),
        CEGEdge(
            source="detect_anomaly",
            target="generate_alert",
            edge_type=EdgeType.SEQUENTIAL,
        ),
    ]

    return CEGGraph(nodes=nodes, edges=edges)


def build_parallel_sales_pipeline() -> CEGGraph:
    """Build a sales pipeline with parallel regional data fetching (fan-out/fan-in).

         ┌──→ fetch_nord ──┐
    init ┤                 ├──→ aggregate → detect_anomaly → generate_alert
         └──→ fetch_sud  ──┘
    """
    nodes = [
        CEGNode(
            id="init",
            objective="Initialize pipeline parameters and regional targets",
            task=CognitiveTask(objective="Set pipeline parameters"),
        ),
        CEGNode(
            id="fetch_nord",
            objective="Fetch sales data for Northern region",
            dependencies=["init"],
            task=CognitiveTask(objective="Retrieve North region daily records"),
            required_capabilities=["data_retrieval"],
        ),
        CEGNode(
            id="fetch_sud",
            objective="Fetch sales data for Southern region",
            dependencies=["init"],
            task=CognitiveTask(objective="Retrieve South region daily records"),
            required_capabilities=["data_retrieval"],
        ),
        CEGNode(
            id="aggregate",
            objective="Aggregate multi-regional sales metrics and cross-compare",
            dependencies=["fetch_nord", "fetch_sud"],
            task=CognitiveTask(objective="Compute cross-region totals and trends"),
            required_capabilities=["data_analysis"],
        ),
        CEGNode(
            id="detect_anomaly",
            objective="Detect statistical anomalies across aggregated regional data",
            dependencies=["aggregate"],
            task=CognitiveTask(objective="Identify outliers using Z-score method"),
            required_capabilities=["anomaly_detection"],
            model_tier_hint=ModelTierHint.QUALITY,
        ),
        CEGNode(
            id="generate_alert",
            objective="Generate consolidated alert report for detected anomalies",
            dependencies=["detect_anomaly"],
            task=CognitiveTask(objective="Produce human-readable alert summary"),
            required_capabilities=["text_generation"],
            model_tier_hint=ModelTierHint.BALANCED,
            interrupt_before=True,  # HITL validation before sending alert
        ),
    ]

    edges = [
        CEGEdge(source="init", target="fetch_nord", edge_type=EdgeType.PARALLEL),
        CEGEdge(source="init", target="fetch_sud", edge_type=EdgeType.PARALLEL),
        CEGEdge(source="fetch_nord", target="aggregate", edge_type=EdgeType.PARALLEL),
        CEGEdge(source="fetch_sud", target="aggregate", edge_type=EdgeType.PARALLEL),
        CEGEdge(
            source="aggregate", target="detect_anomaly", edge_type=EdgeType.SEQUENTIAL
        ),
        CEGEdge(
            source="detect_anomaly",
            target="generate_alert",
            edge_type=EdgeType.SEQUENTIAL,
        ),
    ]

    return CEGGraph(nodes=nodes, edges=edges)


def main() -> None:
    """Build, compile, and execute both sales pipelines end-to-end."""
    print("=" * 60)
    print(" CEG Sales Pipeline — Parallel Fan-Out/Fan-In Execution")
    print("=" * 60)

    # 1. Build the Parallel CEG
    ceg = build_parallel_sales_pipeline()
    print(f"\n✓ Built CEG with {len(ceg.nodes)} nodes and {len(ceg.edges)} edges")

    # 2. Compile
    compiler = CEGCompiler()
    workflow = compiler.compile(ceg)
    print("✓ Compiled to LangGraph workflow")
    print(f"  Execution order: {workflow.metadata['execution_order']}")
    print(f"  Has parallel:    {workflow.metadata['has_parallel']}")
    print(f"  Has HITL:        {workflow.metadata['has_hitl']}")

    # 3. Execute
    print("\n— Executing workflow —\n")
    result = workflow.invoke()

    # 4. Display results
    print("\n— Execution Results —\n")
    for entry in result["execution_log"]:
        print(
            f"  [{entry['node_id']}] status={entry['status']}  "
            f"cost={entry['cost']:.4f}  latency={entry['latency_ms']:.1f}ms  "
            f"confidence={entry['confidence']:.2f}"
        )

    print(f"\n  Total cost:    {result['total_cost']:.4f}")
    print(f"  Total latency: {result['total_latency_ms']:.1f} ms")

    print("\n— Node statuses —\n")
    for node_id, status in result["node_statuses"].items():
        print(f"  {node_id}: {status}")

    all_completed = all(s == "completed" for s in result["node_statuses"].values())
    print(f"\n✓ All nodes completed: {all_completed}")
    print("=" * 60)


if __name__ == "__main__":
    main()
