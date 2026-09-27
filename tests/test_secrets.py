"""Tests for connection-setting resolution and payload parsing (no AWS/boto3)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from dhm import secrets as sec
from dhm.config import Settings


@pytest.fixture(autouse=True)
def _clear_cache():
    sec.clear_secret_cache()
    yield
    sec.clear_secret_cache()


# --------------------------------------------------------------------------- #
# Single-value helpers (kept for split-secret setups)
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Bundle parsing
# --------------------------------------------------------------------------- #

def test_parse_bundle_reads_every_field():
    raw = '{"kibana_url": " https://kb ", "es_url": "https://es", "api_key": "k"}'
    b = sec._parse_bundle(raw)
    assert b == {"kibana_url": "https://kb", "es_url": "https://es", "api_key": "k"}


def test_parse_bundle_coerces_non_strings():
    assert sec._parse_bundle('{"port": 9243, "nothing": null}') == {"port": "9243", "nothing": ""}


def test_parse_bundle_rejects_plain_string():
    # A bare API key is not a bundle; say so rather than silently doing nothing.
    with pytest.raises(RuntimeError, match="not a JSON object"):
        sec._parse_bundle("just-an-api-key", "elastic/dhm/connection")


def test_parse_bundle_rejects_json_list():
    with pytest.raises(RuntimeError, match="not a JSON object"):
        sec._parse_bundle('["a", "b"]')


def test_bundle_get_configured_key_wins():
    bundle = {"kibana_url": "https://wrong", "kbn": "https://right"}
    assert sec.bundle_get(bundle, "kibana_url", "kbn") == "https://right"


def test_bundle_get_falls_back_to_aliases():
    assert sec.bundle_get({"kibana_base_url": "https://kb"}, "kibana_url") == "https://kb"
    assert sec.bundle_get({"elasticsearch_url": "https://es"}, "es_url") == "https://es"
    assert sec.bundle_get({"es_api_key": "k"}, "api_key") == "k"


def test_bundle_get_is_case_insensitive():
    assert sec.bundle_get({"KIBANA_URL": "https://kb"}, "kibana_url") == "https://kb"


def test_bundle_get_missing_is_empty():
    assert sec.bundle_get({"api_key": "k"}, "kibana_url") == ""
    assert sec.bundle_get({}, "kibana_url") == ""


def test_load_aws_bundle_skips_aws_when_unconfigured(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("AWS must not be contacted when aws_secret_id is empty")
    monkeypatch.setattr(sec, "_get_aws_secret_raw", _boom)
    assert sec.load_aws_bundle(Settings()) == {}


def test_load_aws_bundle_is_cached(monkeypatch):
    calls = []

    def _fake(secret_id, region, attempts):
        calls.append(secret_id)
        return '{"api_key": "k"}'

    monkeypatch.setattr(sec, "_get_aws_secret_raw", _fake)
    s = Settings()
    s.aws_secret_id = "dhm/connection"
    assert sec.load_aws_bundle(s) == {"api_key": "k"}
    assert sec.load_aws_bundle(s) == {"api_key": "k"}
    assert calls == ["dhm/connection"]  # one AWS call, not two


# --------------------------------------------------------------------------- #
# resolve_connection
# --------------------------------------------------------------------------- #

def _bundled(monkeypatch, payload, region_seen=None):
    """Point Settings at a fake bundle secret and return the Settings."""
    def _fake(secret_id, region, attempts):
        if region_seen is not None:
            region_seen.append(region)
        return payload

    monkeypatch.setattr(sec, "_get_aws_secret_raw", _fake)
    s = Settings()
    s.aws_secret_id = "dhm/connection"
    return s


_FULL = ('{"kibana_url": "https://kb.aws:9243", "es_url": "https://es.aws:9243", '
         '"api_key": "aws-key"}')


def test_bundle_supplies_urls_and_key(monkeypatch):
    s = _bundled(monkeypatch, _FULL)
    src = sec.resolve_connection(s, env={})
    assert s.kibana.base_url == "https://kb.aws:9243"
    assert s.elasticsearch.base_url == "https://es.aws:9243"
    assert s.elasticsearch.api_key == "aws-key"
    # one key in the bundle authorizes both
    assert s.kibana.auth.api_key == "aws-key"
    assert src["kibana.base_url"] == "aws-bundle"
    assert src["elasticsearch.base_url"] == "aws-bundle"
    assert src["elasticsearch.api_key"] == "aws-bundle"
    assert src["kibana.auth.api_key"] == "elasticsearch.api_key"


def test_bundle_kibana_api_key_is_used_when_present(monkeypatch):
    s = _bundled(monkeypatch, '{"api_key": "es-key", "kibana_api_key": "kb-key"}')
    src = sec.resolve_connection(s, env={})
    assert s.elasticsearch.api_key == "es-key"
    assert s.kibana.auth.api_key == "kb-key"
    assert src["kibana.auth.api_key"] == "aws-bundle"


def test_bundle_uses_configured_region(monkeypatch):
    seen = []
    s = _bundled(monkeypatch, _FULL, region_seen=seen)
    sec.resolve_connection(s, env={})
    assert seen == ["us-east-1"]  # the default


def test_bundle_beats_settings_yaml(monkeypatch):
    # A settings.yaml shipped by Ansible carries defaults; the secret is authoritative.
    s = _bundled(monkeypatch, _FULL)
    s.kibana.base_url = "https://from-yaml"
    s.elasticsearch.base_url = "https://es-from-yaml"
    sec.resolve_connection(s, env={})
    assert s.kibana.base_url == "https://kb.aws:9243"
    assert s.elasticsearch.base_url == "https://es.aws:9243"


def test_env_beats_bundle(monkeypatch):
    s = _bundled(monkeypatch, _FULL)
    src = sec.resolve_connection(s, env={"DHM_KIBANA_URL": "https://kb.env",
                                         "DHM_ES_API_KEY": "env-key"})
    assert s.kibana.base_url == "https://kb.env"
    assert s.elasticsearch.api_key == "env-key"
    assert src["kibana.base_url"] == "env"
    # unaffected fields still come from the bundle
    assert s.elasticsearch.base_url == "https://es.aws:9243"


def test_cli_beats_everything(monkeypatch):
    s = _bundled(monkeypatch, _FULL)
    src = sec.resolve_connection(
        s,
        cli_kibana_url="https://kb.cli",
        cli_es_url="https://es.cli",
        cli_es_api_key="cli-key",
        env={"DHM_KIBANA_URL": "https://kb.env", "DHM_ES_API_KEY": "env-key"},
    )
    assert s.kibana.base_url == "https://kb.cli"
    assert s.elasticsearch.base_url == "https://es.cli"
    assert s.elasticsearch.api_key == "cli-key"
    assert src["elasticsearch.api_key"] == "cli"


def test_trailing_slash_is_stripped(monkeypatch):
    s = _bundled(monkeypatch, '{"kibana_url": "https://kb.aws:9243/", "es_url": "https://es/"}')
    sec.resolve_connection(s, env={})
    assert s.kibana.base_url == "https://kb.aws:9243"
    assert s.elasticsearch.base_url == "https://es"


def test_no_aws_falls_through_to_settings(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("AWS must not be contacted")
    monkeypatch.setattr(sec, "_get_aws_secret_raw", _boom)
    s = Settings()
    s.kibana.base_url = "https://kb.local"
    s.elasticsearch.base_url = "https://es.local"
    s.elasticsearch.api_key = "yaml-key"
    src = sec.resolve_connection(s, env={})
    assert s.kibana.base_url == "https://kb.local"
    assert s.elasticsearch.api_key == "yaml-key"
    assert src["elasticsearch.base_url"] == "settings"


def test_value_specific_secret_still_works(monkeypatch):
    # Split-secret setup: no bundle, a secret holding only the ES key.
    monkeypatch.setattr(sec, "_get_aws_secret", lambda sid, region, jk, n: "split-key")
    s = Settings()
    s.elasticsearch.aws_secret_id = "elastic/dhm/es-key"
    s.kibana.base_url = "https://kb.local"
    src = sec.resolve_connection(s, env={})
    assert s.elasticsearch.api_key == "split-key"
    assert src["elasticsearch.api_key"] == "aws-secret"


def test_value_specific_secret_not_fetched_when_bundle_has_key(monkeypatch):
    # Lazy evaluation: the bundle satisfied the key, so no second AWS call.
    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("value-specific secret must not be fetched")
    monkeypatch.setattr(sec, "_get_aws_secret", _boom)
    s = _bundled(monkeypatch, _FULL)
    s.elasticsearch.aws_secret_id = "elastic/dhm/es-key"
    sec.resolve_connection(s, env={})
    assert s.elasticsearch.api_key == "aws-key"


def test_cookie_auth_leaves_api_key_alone(monkeypatch):
    s = _bundled(monkeypatch, _FULL)
    s.kibana.auth.method = "cookie"
    src = sec.resolve_connection(s, env={})
    assert s.kibana.auth.api_key == ""
    assert "kibana.auth.api_key" not in src


def test_unset_when_nothing_provides_a_value(monkeypatch):
    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("AWS must not be contacted")
    monkeypatch.setattr(sec, "_get_aws_secret_raw", _boom)
    src = sec.resolve_connection(Settings(), env={})
    assert src["kibana.base_url"] == "unset"
    assert src["elasticsearch.api_key"] == "unset"


def test_describe_sources_is_secret_free():
    line = sec.describe_sources({"elasticsearch.api_key": "aws-bundle",
                                 "kibana.base_url": "env"})
    assert line == "elasticsearch.api_key<-aws-bundle, kibana.base_url<-env"


# --------------------------------------------------------------------------- #
# Sanity warnings
# --------------------------------------------------------------------------- #

def test_warns_when_both_urls_are_the_same():
    # The real bug we hit: both URLs set to the Kibana host -> _bulk 404s.
    s = Settings()
    s.kibana.base_url = "https://host.kb.example:9243"
    s.elasticsearch.base_url = "https://host.kb.example:9243/"
    warnings = sec.connection_warnings(s)
    assert len(warnings) == 1
    assert "both" in warnings[0]


def test_no_warning_for_distinct_urls():
    s = Settings()
    s.kibana.base_url = "https://host.kb.example:9243"
    s.elasticsearch.base_url = "https://host.es.example:9243"
    assert sec.connection_warnings(s) == []


def test_warns_on_url_without_scheme():
    s = Settings()
    s.kibana.base_url = "host.kb.example:9243"
    s.elasticsearch.base_url = "https://host.es.example:9243"
    assert any("https://" in w for w in sec.connection_warnings(s))


def test_no_warnings_when_urls_unset():
    assert sec.connection_warnings(Settings()) == []
