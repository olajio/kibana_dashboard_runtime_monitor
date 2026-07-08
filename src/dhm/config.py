"""Settings loading for the collector.

Reads config/settings.yaml (a copy of settings.example.yaml) and lets any
value be overridden by an environment variable, so secrets never have to live
on disk in CI or cron.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml


def _env(name: str, current):
    """Return the environment override for `name` if set, else `current`."""
    val = os.environ.get(name)
    return val if val is not None and val != "" else current


@dataclass
class KibanaAuth:
    method: str = "api_key"
    api_key: str = ""
    cookie_name: str = "sid"
    cookie_value: str = ""


@dataclass
class KibanaConfig:
    base_url: str = ""
    auth: KibanaAuth = field(default_factory=KibanaAuth)
    time_from: str = "now-24h"
    time_to: str = "now"
    verify_tls: bool = True


@dataclass
class ESConfig:
    base_url: str = ""
    api_key: str = ""
    index: str = ".dashboard-health-monitor"
    verify_tls: bool = True


@dataclass
class CollectorConfig:
    registry_path: str = "config/dashboards.generated.json"
    headless: bool = True
    # Which browser to drive. "msedge" / "chrome" use an already-installed
    # branded browser via Playwright channels (no download). "chromium" (or
    # "bundled"/empty) uses Playwright's own downloaded Chromium.
    browser_channel: str = "msedge"
    dashboard_timeout_ms: int = 90000
    poll_interval_ms: int = 250
    concurrency: int = 1
    degraded_over_ms: int = 15000
    failed_over_ms: int = 45000


@dataclass
class Settings:
    app: str = "federal_overview"
    cluster: str = "fed2"
    kibana_space: str = "default"
    kibana: KibanaConfig = field(default_factory=KibanaConfig)
    elasticsearch: ESConfig = field(default_factory=ESConfig)
    collector: CollectorConfig = field(default_factory=CollectorConfig)


def load_settings(path: str = "config/settings.yaml") -> Settings:
    """Load settings from YAML, then apply environment-variable overrides."""
    raw = {}
    if os.path.exists(path):
        with open(path) as f:
            raw = yaml.safe_load(f) or {}

    k = raw.get("kibana", {}) or {}
    ka = k.get("auth", {}) or {}
    es = raw.get("elasticsearch", {}) or {}
    col = raw.get("collector", {}) or {}

    s = Settings(
        app=raw.get("app", "federal_overview"),
        cluster=_env("DHM_CLUSTER", raw.get("cluster", "fed2")),
        kibana_space=_env("DHM_SPACE", raw.get("kibana_space", "default")),
        kibana=KibanaConfig(
            base_url=_env("DHM_KIBANA_URL", k.get("base_url", "")).rstrip("/"),
            auth=KibanaAuth(
                method=ka.get("method", "api_key"),
                api_key=_env("DHM_KIBANA_API_KEY", ka.get("api_key", "")),
                cookie_name=ka.get("cookie_name", "sid"),
                cookie_value=_env("DHM_KIBANA_COOKIE", ka.get("cookie_value", "")),
            ),
            time_from=k.get("time_from", "now-24h"),
            time_to=k.get("time_to", "now"),
            verify_tls=str(_env("DHM_KIBANA_VERIFY_TLS", k.get("verify_tls", True))).lower()
            not in ("false", "0", "no"),
        ),
        elasticsearch=ESConfig(
            base_url=_env("DHM_ES_URL", es.get("base_url", "")).rstrip("/"),
            api_key=_env("DHM_ES_API_KEY", es.get("api_key", "")),
            index=es.get("index", ".dashboard-health-monitor"),
            verify_tls=str(_env("DHM_ES_VERIFY_TLS", es.get("verify_tls", True))).lower()
            not in ("false", "0", "no"),
        ),
        collector=CollectorConfig(
            registry_path=col.get("registry_path", "config/dashboards.generated.json"),
            headless=bool(col.get("headless", True)),
            browser_channel=_env("DHM_BROWSER_CHANNEL", col.get("browser_channel", "msedge")),
            dashboard_timeout_ms=int(col.get("dashboard_timeout_ms", 90000)),
            poll_interval_ms=int(col.get("poll_interval_ms", 250)),
            concurrency=int(col.get("concurrency", 1)),
            degraded_over_ms=int(col.get("degraded_over_ms", 15000)),
            failed_over_ms=int(col.get("failed_over_ms", 45000)),
        ),
    )
    return s
