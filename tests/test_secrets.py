"""Tests for secret resolution precedence and payload parsing (no AWS/boto3)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from dhm import secrets as sec
from dhm.config import Settings


def test_cli_wins():
    assert sec.resolve_secret("cli", "env", "aws-id") == "cli"


def test_env_when_no_cli():
    assert sec.resolve_secret(None, "env", "aws-id") == "env"


def test_cli_is_stripped():
    assert sec.resolve_secret("  key  ", None, None) == "key"


def test_aws_used_when_no_cli_or_env(monkeypatch):
    monkeypatch.setattr(sec, "_get_aws_secret", lambda *a, **k: "from-aws")
    assert sec.resolve_secret(None, None, "aws-id") == "from-aws"


def test_empty_when_nothing_configured():
    assert sec.resolve_secret(None, None, None) == ""


def test_extract_plain_string():
    assert sec._extract_secret_value("raw-key", "api_key") == "raw-key"


def test_extract_json_with_key():
    assert sec._extract_secret_value('{"api_key": "abc", "x": 1}', "api_key") == "abc"


def test_extract_json_single_value():
    assert sec._extract_secret_value('{"whatever": "only"}', "api_key") == "only"


def test_extract_json_missing_key_raises():
    with pytest.raises(RuntimeError):
        sec._extract_secret_value('{"a": 1, "b": 2}', "api_key")


def test_resolve_es_api_key_uses_cli(monkeypatch):
    s = Settings()
    s.elasticsearch.aws_secret_id = "es-secret"
    assert sec.resolve_es_api_key(s, cli_value="cli-key", env_value=None) == "cli-key"


def test_resolve_kibana_falls_back_to_es_key():
    s = Settings()  # no kibana secret configured
    key = sec.resolve_kibana_api_key(s, cli_value=None, env_value=None, fallback="es-key")
    assert key == "es-key"


def test_resolve_kibana_prefers_its_own_cli():
    s = Settings()
    key = sec.resolve_kibana_api_key(s, cli_value="kib-key", env_value=None, fallback="es-key")
    assert key == "kib-key"
