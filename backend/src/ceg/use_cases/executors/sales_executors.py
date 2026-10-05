"""Sales pipeline executor functions — pure Python business logic, no LLM.

Each function follows the contract:
    execute_<name>(node_id: str, objective: str, inputs: dict[str, Any])
        -> ExecutionResult

Raises ExecutionError on business-logic failures.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Any

from ceg.compiler.mock_executor import ExecutionError, ExecutionResult

# ── 1. fetch_data ─────────────────────────────────────────────────────────────

_REQUIRED_COLUMNS = {"date", "region", "montant", "produit", "quantite"}


def execute_fetch_data(
    node_id: str,
    objective: str,
    inputs: dict[str, Any],
) -> ExecutionResult:
    """Read a CSV file and validate its structure and content."""
    path: str = inputs.get("csv_path", "")

    if not os.path.exists(path):
        raise ExecutionError(node_id, f"Fichier CSV introuvable: {path}")

    rows: list[dict[str, Any]] = []

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)

        # Validate columns
        if reader.fieldnames is None:
            raise ExecutionError(node_id, "Fichier CSV vide ou sans en-tête")

        actual_columns = {c.strip() for c in reader.fieldnames}
        missing = _REQUIRED_COLUMNS - actual_columns
        if missing:
            raise ExecutionError(node_id, f"Colonnes manquantes: {sorted(missing)}")

        for i, raw_row in enumerate(reader, start=2):  # line 1 = header
            row: dict[str, Any] = {k.strip(): v.strip() for k, v in raw_row.items()}

            # Validate montant
            try:
                montant = float(row["montant"])
            except (ValueError, KeyError) as exc:
                raise ExecutionError(
                    node_id, f"Données invalides ligne {i}: montant non numérique"
                ) from exc

            # Validate quantite
            try:
                quantite = int(row["quantite"])
            except (ValueError, KeyError) as exc:
                raise ExecutionError(
                    node_id, f"Données invalides ligne {i}: quantite non entière"
                ) from exc

            # Validate date format YYYY-MM-DD
            date_str = row["date"]
            try:
                datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError as exc:
                raise ExecutionError(
                    node_id,
                    f"Données invalides ligne {i}: date '{date_str}' "
                    "hors format YYYY-MM-DD",
                ) from exc

            rows.append(
                {
                    "date": date_str,
                    "region": row["region"],
                    "montant": montant,
                    "produit": row["produit"],
                    "quantite": quantite,
                }
            )

    dates = [r["date"] for r in rows]
    regions = sorted({r["region"] for r in rows})

    output: dict[str, Any] = {
        "rows": rows,
        "row_count": len(rows),
        "regions": regions,
        "date_range": {
            "min": min(dates) if dates else "",
            "max": max(dates) if dates else "",
        },
    }

    return ExecutionResult(output=output, cost=0.001, latency_ms=50.0, confidence=0.99)


# ── 2. aggregate_region ───────────────────────────────────────────────────────


def execute_aggregate_region(
    node_id: str,
    objective: str,
    inputs: dict[str, Any],
) -> ExecutionResult:
    """Group transactions by (region, month) and sum montants."""
    fetch_output: dict[str, Any] = inputs.get("fetch_data", {})
    rows: list[dict[str, Any]] = fetch_output.get("rows", [])

    # aggregation[region][YYYY-MM] = total_montant
    aggregation: dict[str, dict[str, float]] = {}

    for row in rows:
        region: str = row["region"]
        month: str = row["date"][:7]  # YYYY-MM
        montant: float = float(row["montant"])

        aggregation.setdefault(region, {})
        aggregation[region][month] = aggregation[region].get(month, 0.0) + montant

    return ExecutionResult(
        output={"aggregation": aggregation},
        cost=0.002,
        latency_ms=80.0,
        confidence=0.98,
    )


# ── 3. compute_trend ──────────────────────────────────────────────────────────


def execute_compute_trend(
    node_id: str,
    objective: str,
    inputs: dict[str, Any],
) -> ExecutionResult:
    """Compute month-over-month variation for each region."""
    agg_output: dict[str, Any] = inputs.get("aggregate_region", {})
    aggregation: dict[str, dict[str, float]] = agg_output.get("aggregation", {})

    trends: dict[str, dict[str, Any]] = {}

    for region, monthly in aggregation.items():
        sorted_months = sorted(monthly.keys())

        if len(sorted_months) < 2:
            # Only one data point — variation defaults to 0.0
            only_month = sorted_months[0] if sorted_months else ""
            only_amount = monthly.get(only_month, 0.0)
            trends[region] = {
                "mois_precedent": only_month,
                "mois_courant": only_month,
                "montant_precedent": only_amount,
                "montant_courant": only_amount,
                "variation_pct": 0.0,
            }
        else:
            prev_month = sorted_months[-2]
            curr_month = sorted_months[-1]
            prev_amount = monthly[prev_month]
            curr_amount = monthly[curr_month]

            if prev_amount != 0.0:
                variation_pct = (curr_amount - prev_amount) / prev_amount * 100.0
            else:
                variation_pct = 0.0

            trends[region] = {
                "mois_precedent": prev_month,
                "mois_courant": curr_month,
                "montant_precedent": prev_amount,
                "montant_courant": curr_amount,
                "variation_pct": round(variation_pct, 4),
            }

    return ExecutionResult(
        output={"trends": trends},
        cost=0.003,
        latency_ms=100.0,
        confidence=0.97,
    )


# ── 4. detect_anomaly ─────────────────────────────────────────────────────────

_ANOMALY_THRESHOLD_PCT = -20.0


def execute_detect_anomaly(
    node_id: str,
    objective: str,
    inputs: dict[str, Any],
) -> ExecutionResult:
    """Flag regions whose variation falls below the -20% threshold."""
    trend_output: dict[str, Any] = inputs.get("compute_trend", {})
    trends: dict[str, dict[str, Any]] = trend_output.get("trends", {})

    anomalies: list[dict[str, Any]] = []
    regions_checked: list[str] = sorted(trends.keys())

    for region in regions_checked:
        data = trends[region]
        variation_pct: float = data["variation_pct"]

        if variation_pct < _ANOMALY_THRESHOLD_PCT:
            anomalies.append(
                {
                    "region": region,
                    "variation_pct": variation_pct,
                    "montant_precedent": data["montant_precedent"],
                    "montant_courant": data["montant_courant"],
                }
            )

    return ExecutionResult(
        output={
            "anomalies": anomalies,
            "anomalies_found": len(anomalies) > 0,
            "threshold_pct": _ANOMALY_THRESHOLD_PCT,
            "regions_checked": regions_checked,
        },
        cost=0.005,
        latency_ms=120.0,
        confidence=0.95,
    )


# ── 5. generate_alert ─────────────────────────────────────────────────────────

_ALERT_TEMPLATE = """\
=== ALERTE VENTES — CHUTE DE VOLUME DÉTECTÉE ===

Date de génération : {datetime}
Seuil d'alerte : {threshold}%

Régions en anomalie ({count} région(s)) :
{anomaly_lines}
⚠️  Action recommandée : Vérifier les causes de la baisse pour les régions listées.
Contacter les équipes commerciales régionales dans les 48h.
"""


def execute_generate_alert(
    node_id: str,
    objective: str,
    inputs: dict[str, Any],
) -> ExecutionResult:
    """Generate a text alert message from detected anomalies."""
    anomaly_output: dict[str, Any] = inputs.get("detect_anomaly", {})
    anomalies: list[dict[str, Any]] = anomaly_output.get("anomalies", [])
    threshold_pct: float = anomaly_output.get("threshold_pct", _ANOMALY_THRESHOLD_PCT)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    anomaly_lines = "\n".join(
        f"  • {a['region']} : {a['variation_pct']:.1f}% "
        f"({a['montant_precedent']:.0f} → {a['montant_courant']:.0f} EUR)"
        for a in anomalies
    )
    # Ensure a trailing newline so the template footer is properly separated
    if anomaly_lines:
        anomaly_lines += "\n"

    alert_message = _ALERT_TEMPLATE.format(
        datetime=now_str,
        threshold=threshold_pct,
        count=len(anomalies),
        anomaly_lines=anomaly_lines,
    )

    regions_alerted = [a["region"] for a in anomalies]

    return ExecutionResult(
        output={
            "alert_message": alert_message,
            "alert_sent": True,
            "regions_alerted": regions_alerted,
            "anomaly_count": len(anomalies),
        },
        cost=0.008,
        latency_ms=150.0,
        confidence=0.92,
    )
