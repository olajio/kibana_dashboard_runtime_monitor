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
    time_from: str = "now-30d"
    time_to: str = "now"
    verify_tls: bool = True


@dataclass
class ESConfig:
    base_url: str = ""
    api_key: str = ""
    # No leading dot: dot-prefixed streams are hidden from Kibana's data-view
    # picker and some ES setups refuse to let non-system users create them.
    index: str = "dashboard-health-monitor"
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
    # How we choose which dashboards to monitor:
    #   linked (default) - the hub (`hub_title`) plus every dashboard reachable from
    #                      its navigation: the Federal Overview dashboard and all the
    #                      dashboards linked to it.
    #   titles           - exactly the dashboards named in `include_titles`.
    #   all              - every dashboard in the space.
    selection: str = "linked"
    hub_title: str = "Federal Overview"
    # Used only when selection == "titles".
    include_titles: List[str] = field(default_factory=list)
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
    # Per-dashboard hard timeout. Sized for prod with time_from=now-30d and
    # heavy dashboards (e.g. Priority Risks, 35 panels); tune per environment.
    dashboard_timeout_ms: int = 180000
    poll_interval_ms: int = 250
    concurrency: int = 1
    # Load-time classification, in ms.
    degraded_over_ms: int = 30000
    failed_over_ms: int = 120000
    # A dashboard is 'failed' when the share of not-ok panels reaches this
    # percentage (0-100). Below it, any not-ok panel makes the dashboard
    # 'degraded'. Set 0 to make ANY not-ok panel a failure; 101 to disable.
    failed_not_ok_pct: float = 50.0
    # Politeness: pause between dashboard loads so we do not hammer Kibana.
    inter_request_delay_ms: int = 500
    # Retry a dashboard once if the initial navigation fails.
    load_retries: int = 1


@dataclass
class AWSSecretKeys:
    """Field names to read out of the AWS connection-bundle secret's JSON.

    Defaults match the field names in our production secret; override them to fit
    a secret that exists with different names.
    """
    kibana_url: str = "kibana_url"
    es_url: str = "elastic_url"
    api_key: str = "ans_dashboard_health_monitor"
    kibana_api_key: str = "kibana_api_key"


@dataclass
class Settings:
    app: str = "federal_overview"
    cluster: str = "fed2"
    # The Kibana space ID (the /s/<id> URL slug), not the display name. Our
    # dashboards live in fed2; "default" is the one space with no /s/ prefix.
    kibana_space: str = "fed2"
    # Path to a PEM CA bundle to verify Kibana/Elasticsearch TLS against. Needed
    # where a TLS-inspecting proxy re-signs traffic with an internal CA: the cert
    # chain is then "self signed" as far as certifi's default bundle is concerned.
    # Empty = use certifi's defaults. This is the correct fix for that case;
    # verify_tls: false is the blunt alternative and disables verification.
    # NOTE: this governs the REST calls (requests). The browser uses the operating
    # system trust store instead, so the CA also belongs there for the page loads.
    ca_bundle: str = ""
    # AWS region for Secrets Manager. us-east-1 is our default; override per
    # environment with DHM_AWS_REGION (or the standard AWS_REGION).
    aws_region: str = "us-east-1"
    # One AWS Secrets Manager secret holding the connection settings as JSON
    # (kibana_url, es_url, api_key). This is how production is configured: set
    # this and nothing else. Empty -> AWS is never contacted for the bundle.
    aws_secret_id: str = ""
    aws_secret_keys: AWSSecretKeys = field(default_factory=AWSSecretKeys)
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
    ask = raw.get("aws_secret_keys", {}) or {}

    # include_titles (used only when selection == "titles"):
    # DHM_INCLUDE_TITLES (comma-separated) > yaml > empty.
    env_titles = os.environ.get("DHM_INCLUDE_TITLES")
    if env_titles is not None:
        include_titles = [t.strip() for t in env_titles.split(",") if t.strip()]
    else:
        include_titles = col.get("include_titles", []) or []

    s = Settings(
        app=raw.get("app", "federal_overview"),
        cluster=_env("DHM_CLUSTER", raw.get("cluster", "fed2")),
        kibana_space=_env("DHM_SPACE", raw.get("kibana_space", "") or "fed2"),
        ca_bundle=_env("DHM_CA_BUNDLE", raw.get("ca_bundle", "")),
        aws_region=_env(
            "DHM_AWS_REGION", _env("AWS_REGION", raw.get("aws_region", "") or "us-east-1")
        ),
        aws_secret_id=_env("DHM_AWS_SECRET_ID", raw.get("aws_secret_id", "")),
        aws_secret_keys=AWSSecretKeys(
            kibana_url=ask.get("kibana_url", "kibana_url"),
            es_url=ask.get("es_url", "elastic_url"),
            api_key=ask.get("api_key", "ans_dashboard_health_monitor"),
            kibana_api_key=ask.get("kibana_api_key", "kibana_api_key"),
        ),
        kibana=KibanaConfig(
            # Not env-overridable by design: the URL comes from the AWS secret
            # (or this file, or an explicit --kibana-url flag).
            base_url=(k.get("base_url", "") or "").rstrip("/"),
            auth=KibanaAuth(
                method=ka.get("method", "api_key"),
                api_key=_env("DHM_KIBANA_API_KEY", ka.get("api_key", "")),
                cookie_name=ka.get("cookie_name", "sid"),
                cookie_value=_env("DHM_KIBANA_COOKIE", ka.get("cookie_value", "")),
                aws_secret_id=_env("DHM_KIBANA_AWS_SECRET_ID", ka.get("aws_secret_id", "")),
                aws_secret_json_key=ka.get("aws_secret_json_key", "api_key"),
            ),
            time_from=k.get("time_from", "now-30d"),
            time_to=k.get("time_to", "now"),
            verify_tls=str(_env("DHM_KIBANA_VERIFY_TLS", k.get("verify_tls", True))).lower()
            not in ("false", "0", "no"),
        ),
        elasticsearch=ESConfig(
            # Not env-overridable by design — see kibana.base_url above.
            base_url=(es.get("base_url", "") or "").rstrip("/"),
            api_key=_env("DHM_ES_API_KEY", es.get("api_key", "")),
            index=es.get("index", "dashboard-health-monitor"),
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
            selection=_env("DHM_SELECTION", col.get("selection", "linked")),
            hub_title=_env("DHM_HUB_TITLE", col.get("hub_title", "Federal Overview")),
            include_titles=include_titles,
            headless=bool(col.get("headless", True)),
            backend=_env("DHM_BACKEND", col.get("backend", "playwright")),
            browser_channel=_env("DHM_BROWSER_CHANNEL", col.get("browser_channel", "msedge")),
            webdriver_path=_env("DHM_WEBDRIVER_PATH", col.get("webdriver_path", "")),
            dashboard_timeout_ms=int(col.get("dashboard_timeout_ms", 180000)),
            poll_interval_ms=int(col.get("poll_interval_ms", 250)),
            concurrency=int(col.get("concurrency", 1)),
            degraded_over_ms=int(col.get("degraded_over_ms", 30000)),
            failed_over_ms=int(col.get("failed_over_ms", 120000)),
            failed_not_ok_pct=float(col.get("failed_not_ok_pct", 50.0)),
            inter_request_delay_ms=int(col.get("inter_request_delay_ms", 500)),
            load_retries=int(col.get("load_retries", 1)),
        ),
    )
    return s



def tls_verify(settings: Settings, verify_tls: bool):
    """The value to hand `requests` as `verify=`.

    False skips verification entirely; a string is a CA bundle path; True uses
    certifi's defaults. Kept in one place so the REST call sites cannot drift.
    """
    if not verify_tls:
        return False
    return settings.ca_bundle or True
