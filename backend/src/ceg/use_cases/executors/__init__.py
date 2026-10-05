"""Sales pipeline executor functions."""

from ceg.use_cases.executors.sales_executors import (
    execute_aggregate_region,
    execute_compute_trend,
    execute_detect_anomaly,
    execute_fetch_data,
    execute_generate_alert,
)

__all__ = [
    "execute_aggregate_region",
    "execute_compute_trend",
    "execute_detect_anomaly",
    "execute_fetch_data",
    "execute_generate_alert",
]
