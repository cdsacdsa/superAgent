"""Logging + Azure Monitor (Application Insights) via OpenTelemetry."""

from __future__ import annotations

import logging
import os

from .config import Settings

_configured = False


def setup_observability(settings: Settings, service_name: str) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("httpx", "azure.core.pipeline.policies.http_logging_policy", "azure.identity"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if settings.applicationinsights_connection_string:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor

            os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
            configure_azure_monitor(connection_string=settings.applicationinsights_connection_string,
                                    logger_name="superagent")  # fmt: skip
            logging.getLogger("superagent").info("Azure Monitor telemetry enabled")
        except Exception as e:  # noqa: BLE001
            logging.getLogger("superagent").warning("Azure Monitor not configured: %s", e)
