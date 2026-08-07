"""Secret resolution with a clear precedence, so the same code runs in test and
production without change.

For any secret (Elasticsearch API key, Kibana API key) the order is:

    1. explicit value passed on the command line   (e.g. --es-api-key ...)
    2. environment variable                         (e.g. DHM_ES_API_KEY)
    3. AWS Secrets Manager                          (production default)
    4. whatever is already in settings (usually empty)

In the test environment we pass the key on the command line; in production we
pass nothing and it is read from AWS Secrets Manager. boto3 is imported lazily,
so environments that never touch AWS do not need it installed.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from .config import Settings


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


def _get_aws_secret(
    secret_id: str, region: Optional[str], json_key: str, max_attempts: int
) -> str:
    """Fetch a secret from AWS Secrets Manager.

    Accepts either a raw-string secret (the key itself) or a JSON secret; for
    JSON, returns `json_key` (default "api_key"), or the sole value if the JSON
    has exactly one field.
    """
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "AWS Secrets Manager requested but boto3 is not installed. "
            "Install boto3, or pass the key on the command line "
            "(e.g. --es-api-key)."
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
    return _extract_secret_value(raw, json_key, secret_id)


def _extract_secret_value(raw: str, json_key: str, secret_id: str = "<secret>") -> str:
    """Pull the key out of a Secrets Manager payload.

    Accepts a plain-string secret (the key itself) or a JSON secret; for JSON,
    returns `json_key`, or the sole value if the JSON has exactly one field.
    Pure/browser-free so it can be unit tested.
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


def resolve_es_api_key(settings: Settings, cli_value: Optional[str], env_value: Optional[str]) -> str:
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
