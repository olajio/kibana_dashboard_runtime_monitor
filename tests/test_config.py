"""Tests for monitoring-selection config (selection / hub_title / include_titles)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings


def _write(tmp_path, body):
    p = tmp_path / "settings.yaml"
    p.write_text(body)
    return str(p)


def _clean(monkeypatch):
    for v in ("DHM_SELECTION", "DHM_HUB_TITLE", "DHM_INCLUDE_TITLES", "DHM_SPACE"):
        monkeypatch.delenv(v, raising=False)


# --- Kibana space ------------------------------------------------------------

def test_kibana_space_defaults_to_fed2(monkeypatch):
    # Our dashboards live in the fed2 space, so that is the built-in default.
    _clean(monkeypatch)
    assert load_settings("does_not_exist.yaml").kibana_space == "fed2"


def test_kibana_space_default_survives_empty_yaml_value(monkeypatch, tmp_path):
    _clean(monkeypatch)
    assert load_settings(_write(tmp_path, 'kibana_space: ""\n')).kibana_space == "fed2"


def test_kibana_space_from_yaml_and_env(monkeypatch, tmp_path):
    _clean(monkeypatch)
    path = _write(tmp_path, "kibana_space: other\n")
    assert load_settings(path).kibana_space == "other"
    monkeypatch.setenv("DHM_SPACE", "fed3")
    assert load_settings(path).kibana_space == "fed3"


def test_space_default_can_still_be_selected(monkeypatch):
    # "default" is the one space that carries no /s/<id> URL prefix; it must stay
    # reachable now that it is no longer the built-in default.
    _clean(monkeypatch)
    monkeypatch.setenv("DHM_SPACE", "default")
    assert load_settings("does_not_exist.yaml").kibana_space == "default"


def test_defaults_are_linked_federal_overview(monkeypatch):
    _clean(monkeypatch)
    s = load_settings("does_not_exist.yaml")
    assert s.collector.selection == "linked"
    assert s.collector.hub_title == "Federal Overview"
    assert s.collector.include_titles == []


def test_yaml_selection_all(monkeypatch, tmp_path):
    _clean(monkeypatch)
    s = load_settings(_write(tmp_path, "collector:\n  selection: all\n"))
    assert s.collector.selection == "all"


def test_yaml_titles(monkeypatch, tmp_path):
    _clean(monkeypatch)
    s = load_settings(_write(tmp_path, 'collector:\n  selection: titles\n  include_titles: ["A", "B"]\n'))
    assert s.collector.selection == "titles"
    assert s.collector.include_titles == ["A", "B"]


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("DHM_SELECTION", "titles")
    monkeypatch.setenv("DHM_HUB_TITLE", "Other Hub")
    monkeypatch.setenv("DHM_INCLUDE_TITLES", "X, Y")
    s = load_settings("does_not_exist.yaml")
    assert s.collector.selection == "titles"
    assert s.collector.hub_title == "Other Hub"
    assert s.collector.include_titles == ["X", "Y"]


def test_env_include_titles_empty_is_all(monkeypatch):
    _clean(monkeypatch)
    monkeypatch.setenv("DHM_INCLUDE_TITLES", "")
    s = load_settings("does_not_exist.yaml")
    assert s.collector.include_titles == []


# --- AWS Secrets Manager settings -------------------------------------------

def _clean_aws(monkeypatch):
    for v in ("DHM_AWS_REGION", "AWS_REGION", "DHM_AWS_SECRET_ID"):
        monkeypatch.delenv(v, raising=False)


def test_aws_region_defaults_to_us_east_1(monkeypatch):
    _clean_aws(monkeypatch)
    assert load_settings("does_not_exist.yaml").aws_region == "us-east-1"


def test_aws_region_default_survives_empty_yaml_value(monkeypatch, tmp_path):
    # An explicitly blank aws_region in the file must not wipe the default.
    _clean_aws(monkeypatch)
    assert load_settings(_write(tmp_path, 'aws_region: ""\n')).aws_region == "us-east-1"


def test_aws_region_from_yaml(monkeypatch, tmp_path):
    _clean_aws(monkeypatch)
    assert load_settings(_write(tmp_path, "aws_region: us-gov-west-1\n")).aws_region == "us-gov-west-1"


def test_aws_region_env_overrides(monkeypatch, tmp_path):
    _clean_aws(monkeypatch)
    path = _write(tmp_path, "aws_region: us-gov-west-1\n")
    monkeypatch.setenv("AWS_REGION", "eu-west-2")
    assert load_settings(path).aws_region == "eu-west-2"
    # DHM_AWS_REGION is the more specific override and wins over AWS_REGION
    monkeypatch.setenv("DHM_AWS_REGION", "us-west-2")
    assert load_settings(path).aws_region == "us-west-2"


def test_aws_secret_id_defaults_empty(monkeypatch):
    _clean_aws(monkeypatch)
    assert load_settings("does_not_exist.yaml").aws_secret_id == ""


def test_aws_secret_id_from_yaml_and_env(monkeypatch, tmp_path):
    _clean_aws(monkeypatch)
    path = _write(tmp_path, "aws_secret_id: elastic/dhm/connection\n")
    assert load_settings(path).aws_secret_id == "elastic/dhm/connection"
    monkeypatch.setenv("DHM_AWS_SECRET_ID", "elastic/dhm/prod")
    assert load_settings(path).aws_secret_id == "elastic/dhm/prod"


def test_aws_secret_keys_default_and_override(monkeypatch, tmp_path):
    _clean_aws(monkeypatch)
    s = load_settings("does_not_exist.yaml")
    assert (s.aws_secret_keys.kibana_url, s.aws_secret_keys.es_url) == ("kibana_url", "es_url")
    assert s.aws_secret_keys.api_key == "api_key"

    s = load_settings(_write(tmp_path, "aws_secret_keys:\n  kibana_url: kbn\n  api_key: key\n"))
    assert s.aws_secret_keys.kibana_url == "kbn"
    assert s.aws_secret_keys.api_key == "key"
    assert s.aws_secret_keys.es_url == "es_url"  # unspecified fields keep defaults
