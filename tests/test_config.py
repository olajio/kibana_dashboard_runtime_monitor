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
    for v in ("DHM_SELECTION", "DHM_HUB_TITLE", "DHM_INCLUDE_TITLES"):
        monkeypatch.delenv(v, raising=False)


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
