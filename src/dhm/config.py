"""Settings loading for the collector.

Reads config/settings.yaml (a copy of settings.example.yaml) and lets any
value be overridden by an environment variable, so secrets never have to live
on disk in CI or cron.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

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
    # Optional AWS Secrets Manager source for the Kibana API key.
    aws_secret_id: str = ""
    aws_secret_json_key: str = "api_key"


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
    # Optional AWS Secrets Manager source for the Elasticsearch API key
    # (production default; test passes the key on the command line instead).
    aws_secret_id: str = ""
    aws_secret_json_key: str = "api_key"
    # Operational limits for ES REST calls.
    request_timeout_s: int = 30
    max_retries: int = 4
    retry_backoff_s: float = 2.0
    bulk_chunk_size: int = 500


@dataclass
class CollectorConfig:
    registry_path: str = "config/dashboards.generated.json"
    # Where the list of dashboards to monitor comes from:
    #   export - read config/dashboards.generated.json (built from a .ndjson);
    #            good for test / offline, but a static snapshot.
    #   api    - query Kibana's Saved Objects API live each run; always reflects
    #            production, needs no export file on the server.
    registry_source: str = "export"
    # Which dashboards to monitor, by title. Defaults to just "Federal Overview".
    # Set to specific titles to monitor those; set to an empty list ([]) to monitor
    # every dashboard in the space. Applies to both registry sources.
    include_titles: List[str] = field(default_factory=lambda: ["Federal Overview"])
    headless: bool = True
    # Browser automation backend. "playwright" (default) or "selenium" — the
    # Selenium fallback is for environments where the playwright pip package
    # cannot be installed. Both drive the same system browser.
    backend: str = "playwright"
    # Which browser to drive. "msedge" / "chrome" use an already-installed
    # branded browser (Playwright channel, or Edge/Chrome WebDriver under
    # Selenium). "chromium" (or "bundled"/empty) uses Playwright's own Chromium.
    browser_channel: str = "msedge"
    # Selenium only: path to msedgedriver/chromedriver. Empty -> PATH / Selenium Manager.
    webdriver_path: str = ""
    dashboard_timeout_ms: int = 90000
    poll_interval_ms: int = 250
    concurrency: int = 1
    degraded_over_ms: int = 15000
    failed_over_ms: int = 45000
    # Politeness: pause between dashboard loads so we do not hammer Kibana.
    inter_request_delay_ms: int = 500
    # Retry a dashboard once if the initial navigation fails.
    load_retries: int = 1


@dataclass
class Settings:
    app: str = "federal_overview"
    cluster: str = "fed2"
    kibana_space: str = "default"
    aws_region: str = ""
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

    # include_titles precedence: DHM_INCLUDE_TITLES (comma-separated) > yaml > default.
    # An explicit empty value ([] in yaml, or "" in the env var) means "all
    # dashboards in the space"; an absent value falls back to just Federal Overview.
    env_titles = os.environ.get("DHM_INCLUDE_TITLES")
    if env_titles is not None:
        include_titles = [t.strip() for t in env_titles.split(",") if t.strip()]
    elif "include_titles" in col:
        include_titles = col["include_titles"] or []
    else:
        include_titles = ["Federal Overview"]

    s = Settings(
        app=raw.get("app", "federal_overview"),
        cluster=_env("DHM_CLUSTER", raw.get("cluster", "fed2")),
        kibana_space=_env("DHM_SPACE", raw.get("kibana_space", "default")),
        aws_region=_env("DHM_AWS_REGION", _env("AWS_REGION", raw.get("aws_region", ""))),
        kibana=KibanaConfig(
            base_url=_env("DHM_KIBANA_URL", k.get("base_url", "")).rstrip("/"),
            auth=KibanaAuth(
                method=ka.get("method", "api_key"),
                api_key=_env("DHM_KIBANA_API_KEY", ka.get("api_key", "")),
                cookie_name=ka.get("cookie_name", "sid"),
                cookie_value=_env("DHM_KIBANA_COOKIE", ka.get("cookie_value", "")),
                aws_secret_id=_env("DHM_KIBANA_AWS_SECRET_ID", ka.get("aws_secret_id", "")),
                aws_secret_json_key=ka.get("aws_secret_json_key", "api_key"),
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
            aws_secret_id=_env("DHM_ES_AWS_SECRET_ID", es.get("aws_secret_id", "")),
            aws_secret_json_key=es.get("aws_secret_json_key", "api_key"),
            request_timeout_s=int(es.get("request_timeout_s", 30)),
            max_retries=int(es.get("max_retries", 4)),
            retry_backoff_s=float(es.get("retry_backoff_s", 2.0)),
            bulk_chunk_size=int(es.get("bulk_chunk_size", 500)),
        ),
        collector=CollectorConfig(
            registry_path=col.get("registry_path", "config/dashboards.generated.json"),
            registry_source=_env("DHM_REGISTRY_SOURCE", col.get("registry_source", "export")),
            include_titles=include_titles,
            headless=bool(col.get("headless", True)),
            backend=_env("DHM_BACKEND", col.get("backend", "playwright")),
            browser_channel=_env("DHM_BROWSER_CHANNEL", col.get("browser_channel", "msedge")),
            webdriver_path=_env("DHM_WEBDRIVER_PATH", col.get("webdriver_path", "")),
            dashboard_timeout_ms=int(col.get("dashboard_timeout_ms", 90000)),
            poll_interval_ms=int(col.get("poll_interval_ms", 250)),
            concurrency=int(col.get("concurrency", 1)),
            degraded_over_ms=int(col.get("degraded_over_ms", 15000)),
            failed_over_ms=int(col.get("failed_over_ms", 45000)),
            inter_request_delay_ms=int(col.get("inter_request_delay_ms", 500)),
            load_retries=int(col.get("load_retries", 1)),
        ),
    )
    return s
