"""Connection-setting resolution, so the same code runs in test and production
without change.

Production deployment (Ansible onto an existing server) keeps nothing sensitive
and nothing environment-specific on disk: the Kibana URL, the Elasticsearch URL
and the API key all come from one AWS Secrets Manager secret — the *connection
bundle*. One secret means one IAM grant, one rotation, and one Ansible variable
(`aws_secret_id`).

The bundle is a JSON object; by default we read the field names our production
secret uses:

    {
      "kibana_url":                   "https://kibana.example.gov:9243",
      "elastic_url":                  "https://es.example.gov:9243",
      "ans_dashboard_health_monitor": "<base64 id:key>",
      "kibana_api_key":               "<base64 id:key>"   # optional; defaults to the above
    }

The two **URLs** resolve as:

    1. command-line flag        (--kibana-url / --es-url) — for debugging
    2. AWS connection bundle    (top-level `aws_secret_id`)
    3. config/settings.yaml

There is deliberately **no environment-variable step for the URLs**: the endpoints
belong to the AWS secret, and an env var that silently outranked it would be a
foot-gun (a stale export in a shell or crontab would quietly redirect the whole
run).

The **API keys** keep an env step, because passing the key by hand is how the test
environment works:

    1. command-line flag        (--es-api-key / --kibana-api-key)
    2. environment variable     (DHM_ES_API_KEY / DHM_KIBANA_API_KEY)
    3. AWS connection bundle    (top-level `aws_secret_id`)
    4. value-specific AWS secret (elasticsearch.aws_secret_id,
                                  kibana.auth.aws_secret_id) — for split-secret setups
    5. config/settings.yaml

So in test we pass the key on the command line and keep the URLs in
settings.yaml; in production we set only `aws_secret_id` and pass nothing. Note
that the bundle deliberately outranks settings.yaml: a settings.yaml shipped by
Ansible carries defaults, while the secret is the source of truth. Configure
either the bundle or the value-specific secrets, not both.

boto3 is imported lazily, so environments that never touch AWS do not need it.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Dict, List, Mapping, Optional, Tuple

from .config import Settings

# One run should make at most one Secrets Manager call per secret, even though
# several values are read out of the same bundle.
_bundle_cache: Dict[str, Dict[str, str]] = {}

# Field names we accept in the bundle in addition to the configured ones, so a
# secret written by hand with a reasonable-looking key still works. The first entry
# in each tuple is our production field name; the rest are accepted fallbacks.
# Matched case-insensitively.
_BUNDLE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "kibana_url": ("kibana_url", "kibana_base_url", "kibanaurl", "kbn_url"),
    "es_url": ("elastic_url", "es_url", "elasticsearch_url", "elasticsearch_base_url", "esurl"),
    "api_key": ("ans_dashboard_health_monitor", "api_key", "apikey", "es_api_key",
                "elasticsearch_api_key"),
    "kibana_api_key": ("kibana_api_key", "kibanaapikey", "kbn_api_key"),
}


def clear_secret_cache() -> None:
    """Drop the cached bundle(s). Only needed by tests and long-lived processes."""
    _bundle_cache.clear()


# --------------------------------------------------------------------------- #
# Low-level AWS access
# --------------------------------------------------------------------------- #

def _get_aws_secret_raw(secret_id: str, region: Optional[str], max_attempts: int) -> str:
    """Return a secret's payload from AWS Secrets Manager as a string."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "AWS Secrets Manager requested but boto3 is not installed. "
            "Install boto3 (it is in requirements.txt), or pass the values on "
            "the command line (e.g. --es-api-key / --kibana-url)."
        ) from exc

    # Bounded, retrying client so a slow/blipping Secrets Manager cannot hang us.
    cfg = Config(
        retries={"max_attempts": max_attempts, "mode": "standard"},
        connect_timeout=5,
        read_timeout=10,
    )
    session = boto3.session.Session()
    client = session.client("secretsmanager", region_name=region or None, config=cfg)

    resp = client.get_secret_value(SecretId=secret_id)
    raw = resp.get("SecretString")
    if raw is None:  # binary secret
        raw = base64.b64decode(resp["SecretBinary"]).decode("utf-8")
    return raw


def _get_aws_secret(
    secret_id: str, region: Optional[str], json_key: str, max_attempts: int
) -> str:
    """Fetch a single value from AWS Secrets Manager.

    Accepts either a raw-string secret (the value itself) or a JSON secret; for
    JSON, returns `json_key` (default "api_key"), or the sole value if the JSON
    has exactly one field.
    """
    raw = _get_aws_secret_raw(secret_id, region, max_attempts)
    return _extract_secret_value(raw, json_key, secret_id)


def _extract_secret_value(raw: str, json_key: str, secret_id: str = "<secret>") -> str:
    """Pull one value out of a Secrets Manager payload.

    Accepts a plain-string secret (the value itself) or a JSON secret; for JSON,
    returns `json_key`, or the sole value if the JSON has exactly one field.
    Pure/network-free so it can be unit tested.
    """
    raw = (raw or "").strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw  # plain-string secret

    if isinstance(data, dict):
        if json_key and json_key in data:
            return str(data[json_key]).strip()
        if len(data) == 1:
            return str(next(iter(data.values()))).strip()
        raise RuntimeError(
            f"Secret {secret_id!r} is JSON but has no {json_key!r} field; "
            f"set aws_secret_json_key to the correct field name."
        )
    return raw


# --------------------------------------------------------------------------- #
# The connection bundle
# --------------------------------------------------------------------------- #

def _parse_bundle(raw: str, secret_id: str = "<secret>") -> Dict[str, str]:
    """Parse a bundle payload into {field: value}. A bundle must be a JSON object."""
    raw = (raw or "").strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        data = None
    if not isinstance(data, dict):
        raise RuntimeError(
            f"Secret {secret_id!r} is configured as the connection bundle "
            f"(aws_secret_id) but its payload is not a JSON object. A bundle looks "
            f'like: {{"kibana_url": "...", "es_url": "...", "api_key": "..."}}. '
            f"For a secret that holds only an API key, use "
            f"elasticsearch.aws_secret_id instead."
        )
    return {str(k): ("" if v is None else str(v).strip()) for k, v in data.items()}


def load_aws_bundle(settings: Settings, max_attempts: int = 4) -> Dict[str, str]:
    """Fetch and cache the connection bundle. Returns {} when none is configured,
    so nothing reaches out to AWS unless the operator asked for it."""
    secret_id = (getattr(settings, "aws_secret_id", "") or "").strip()
    if not secret_id:
        return {}
    if secret_id not in _bundle_cache:
        raw = _get_aws_secret_raw(secret_id, settings.aws_region, max_attempts)
        _bundle_cache[secret_id] = _parse_bundle(raw, secret_id)
    return _bundle_cache[secret_id]


def bundle_get(bundle: Mapping[str, str], field: str, configured_key: str = "") -> str:
    """Read a logical field out of a bundle.

    The name configured in `aws_secret_keys` wins; otherwise we try a short list
    of common aliases. Lookups are case-insensitive so `KIBANA_URL` also works.
    """
    if not bundle:
        return ""
    lower = {str(k).lower(): v for k, v in bundle.items()}
    names: List[str] = []
    if configured_key:
        names.append(configured_key)
    names.extend(_BUNDLE_ALIASES.get(field, (field,)))
    for name in names:
        value = lower.get(name.lower())
        if value and str(value).strip():
            return str(value).strip()
    return ""


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #

def _resolve(*sources) -> Tuple[str, str]:
    """First non-empty source wins. Each source is (name, value-or-callable);
    callables are invoked only when reached, so AWS is not called needlessly.
    Returns (value, source_name) — the name is safe to log, the value is not.
    """
    for name, src in sources:
        value = src() if callable(src) else src
        if value and str(value).strip():
            return str(value).strip(), name
    return "", "unset"


def _aws_single(settings: Settings, secret_id: str, json_key: str) -> str:
    """Value-specific secret lookup; "" when no secret is configured."""
    if not secret_id:
        return ""
    return _get_aws_secret(secret_id, settings.aws_region, json_key, 4)


def resolve_connection(
    settings: Settings,
    cli_es_api_key: Optional[str] = None,
    cli_kibana_api_key: Optional[str] = None,
    cli_kibana_url: Optional[str] = None,
    cli_es_url: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Fill in the Kibana/Elasticsearch URLs and API keys on `settings` in place.

    See the module docstring for the precedence. Returns a {field: source} map so
    callers can log *where* each value came from without ever logging the value.
    """
    env = os.environ if env is None else env
    bundle = load_aws_bundle(settings)
    keys = settings.aws_secret_keys
    es = settings.elasticsearch
    auth = settings.kibana.auth
    src: Dict[str, str] = {}

    # Note: load_settings() has already folded DHM_*_API_KEY into settings, so the
    # "settings" source for a key is effectively the YAML value — the explicit env
    # lookups below are what give env its place in the order. The two URLs have no
    # env step at all, by design: they belong to the AWS secret.

    # --- Kibana URL (needed by the browser and by Saved Objects discovery) ---
    value, src["kibana.base_url"] = _resolve(
        ("cli", cli_kibana_url),
        ("aws-bundle", lambda: bundle_get(bundle, "kibana_url", keys.kibana_url)),
        ("settings", settings.kibana.base_url),
    )
    settings.kibana.base_url = value.rstrip("/")

    # --- Elasticsearch URL (where results are written) ---
    value, src["elasticsearch.base_url"] = _resolve(
        ("cli", cli_es_url),
        ("aws-bundle", lambda: bundle_get(bundle, "es_url", keys.es_url)),
        ("settings", es.base_url),
    )
    es.base_url = value.rstrip("/")

    # --- Elasticsearch API key ---
    es_key, src["elasticsearch.api_key"] = _resolve(
        ("cli", cli_es_api_key),
        ("env", env.get("DHM_ES_API_KEY")),
        ("aws-bundle", lambda: bundle_get(bundle, "api_key", keys.api_key)),
        ("aws-secret", lambda: _aws_single(settings, es.aws_secret_id, es.aws_secret_json_key)),
        ("settings", es.api_key),
    )
    es.api_key = es_key

    # --- Kibana API key: its own value if configured, else the ES key (one
    #     Elastic API key commonly authorizes both). ---
    if auth.method == "api_key":
        kb_key, src["kibana.auth.api_key"] = _resolve(
            ("cli", cli_kibana_api_key),
            ("env", env.get("DHM_KIBANA_API_KEY")),
            ("aws-bundle", lambda: bundle_get(bundle, "kibana_api_key", keys.kibana_api_key)),
            ("aws-secret", lambda: _aws_single(settings, auth.aws_secret_id, auth.aws_secret_json_key)),
            ("settings", auth.api_key),
        )
        if not kb_key and es_key:
            kb_key, src["kibana.auth.api_key"] = es_key, "elasticsearch.api_key"
        auth.api_key = kb_key

    return src


def describe_sources(src: Mapping[str, str]) -> str:
    """One-line, secret-free summary of where each connection value came from."""
    return ", ".join(f"{field}<-{where}" for field, where in sorted(src.items()))


def connection_warnings(settings: Settings) -> List[str]:
    """Sanity checks on the resolved connection settings.

    These catch the mistakes that have actually bitten us — most notably pointing
    both URLs at the Kibana host, which makes every `_bulk` write 404.
    """
    out: List[str] = []
    kb = (settings.kibana.base_url or "").strip()
    es = (settings.elasticsearch.base_url or "").strip()

    if kb and es and kb.rstrip("/").lower() == es.rstrip("/").lower():
        out.append(
            f"kibana.base_url and elasticsearch.base_url are both {kb!r}. These are "
            f"normally different hosts (e.g. ...kb... vs ...es...); writing to "
            f"Kibana's host will fail with 404 on _bulk."
        )
    for label, url in (("kibana.base_url", kb), ("elasticsearch.base_url", es)):
        if url and not url.lower().startswith(("http://", "https://")):
            out.append(f"{label} is {url!r} — it should start with https://")
    return out


# --------------------------------------------------------------------------- #
# Backwards-compatible single-value helpers
# --------------------------------------------------------------------------- #

def resolve_secret(
    cli_value: Optional[str],
    env_value: Optional[str],
    aws_secret_id: Optional[str],
    aws_region: Optional[str] = None,
    aws_secret_json_key: str = "api_key",
    max_attempts: int = 4,
) -> str:
    """Return the first secret found by precedence (cli > env > AWS)."""
    if cli_value:
        return cli_value.strip()
    if env_value:
        return env_value.strip()
    if aws_secret_id:
        return _get_aws_secret(aws_secret_id, aws_region, aws_secret_json_key, max_attempts)
    return ""


def resolve_es_api_key(
    settings: Settings, cli_value: Optional[str], env_value: Optional[str]
) -> str:
    es = settings.elasticsearch
    return resolve_secret(
        cli_value=cli_value,
        env_value=env_value,
        aws_secret_id=es.aws_secret_id,
        aws_region=settings.aws_region,
        aws_secret_json_key=es.aws_secret_json_key,
    )


def resolve_kibana_api_key(
    settings: Settings, cli_value: Optional[str], env_value: Optional[str], fallback: str = ""
) -> str:
    """Resolve the Kibana API key. If none is configured for Kibana, fall back to
    the Elasticsearch key (common when one Elastic API key authorizes both)."""
    auth = settings.kibana.auth
    key = resolve_secret(
        cli_value=cli_value,
        env_value=env_value,
        aws_secret_id=auth.aws_secret_id,
        aws_region=settings.aws_region,
        aws_secret_json_key=auth.aws_secret_json_key,
    )
    return key or fallback
