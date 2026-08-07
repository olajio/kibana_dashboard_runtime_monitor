"""Tests for include_titles resolution precedence (env > yaml > default)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.config import load_settings


def _write(tmp_path, body):
    p = tmp_path / "settings.yaml"
    p.write_text(body)
    return str(p)


def test_default_is_federal_overview(monkeypatch):
    monkeypatch.delenv("DHM_INCLUDE_TITLES", raising=False)
    s = load_settings("does_not_exist.yaml")
    assert s.collector.include_titles == ["Federal Overview"]


def test_yaml_empty_means_all(monkeypatch, tmp_path):
    monkeypatch.delenv("DHM_INCLUDE_TITLES", raising=False)
    path = _write(tmp_path, "collector:\n  include_titles: []\n")
    s = load_settings(path)
    assert s.collector.include_titles == []


def test_yaml_specific_titles(monkeypatch, tmp_path):
    monkeypatch.delenv("DHM_INCLUDE_TITLES", raising=False)
    path = _write(tmp_path, 'collector:\n  include_titles: ["A", "B"]\n')
    s = load_settings(path)
    assert s.collector.include_titles == ["A", "B"]


def test_env_overrides_yaml(monkeypatch, tmp_path):
    monkeypatch.setenv("DHM_INCLUDE_TITLES", "X, Y")
    path = _write(tmp_path, 'collector:\n  include_titles: ["A"]\n')
    s = load_settings(path)
    assert s.collector.include_titles == ["X", "Y"]


def test_env_empty_means_all(monkeypatch):
    monkeypatch.setenv("DHM_INCLUDE_TITLES", "")
    s = load_settings("does_not_exist.yaml")
    assert s.collector.include_titles == []
