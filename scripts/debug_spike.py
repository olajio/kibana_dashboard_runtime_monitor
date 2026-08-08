#!/usr/bin/env python3
"""Diagnostic: dump the raw DOM identity of every panel on ONE dashboard.

When the collector reports panels as `missing`, it means the identity attributes
we read from the page do not line up with the panelIndex/title we got from the
export/API. This script bypasses reconciliation entirely and just shows what the
browser actually observes — so we can compare it to what the registry expects
and fix the selectors in `src/dhm/selectors.py`.

Usage:
    export DHM_REGISTRY_SOURCE=api            # or leave for export mode
    python scripts/debug_spike.py --es-api-key "$APIKEY" \
        --dashboard-title "Federal Overview" --out debug.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhm.collect_core import dashboard_url  # noqa: E402
from dhm.config import load_settings  # noqa: E402
from dhm.secrets import resolve_es_api_key, resolve_kibana_api_key  # noqa: E402

# JS we run in the page: walk multiple candidate panel-container selectors and
# report every identity/state attribute Kibana currently exposes. We do NOT
# filter to `data-test-subj="embeddablePanel"` alone — the whole point is to
# discover what the panels look like on this Kibana version.
_DEBUG_JS = r"""
() => {
  const CANDIDATE_SELECTORS = [
    '[data-test-subj="embeddablePanel"]',
    '[data-test-subj="dashboardPanel"]',
    '[data-shared-item]',
    '.embPanel',
    '.dshDashboardGrid__item',
  ];
  const seen = new Set();
  const panels = [];

  const collect = (el, matchedBy) => {
    if (seen.has(el)) return;
    seen.add(el);

    const attrs = {};
    for (const a of el.attributes) attrs[a.name] = a.value;

    // Text-y probes for identity/title regardless of chrome variant
    const titleTestSubj = el.querySelector('[data-test-subj="dashboardPanelTitle"]');
    const sharedTitle = el.querySelector('[data-shared-item-title]');
    const anyHeading = el.querySelector('h1,h2,h3,h4,.embPanel__title');
    const errorEl = el.querySelector(
      '[data-test-subj="embeddableStackTrace"],' +
      '[data-test-subj="embeddableError"],' +
      '.euiErrorBoundary'
    );
    const emptyEl = el.querySelector(
      '[data-test-subj="emptyPlaceholder"],' +
      '[data-test-subj="lnsEmptyLayout"],' +
      '.lnsEmptyLayout,' +
      '[data-test-subj="visNoResult"],' +
      '[data-test-subj="euiEmptyPrompt"]'
    );
    // The render-complete signal can sit on the panel or a descendant.
    const rcInner = el.querySelector('[data-render-complete="true"]');
    const rcAny = el.querySelector('[data-render-complete]');

    panels.push({
      matchedBy,
      attrs,
      selfRenderComplete: el.getAttribute('data-render-complete'),
      selfLoading: el.getAttribute('data-loading'),
      descRenderCompleteTrueValue: rcInner ? true : false,
      descRenderCompleteAnyValue: rcAny ? rcAny.getAttribute('data-render-complete') : null,
      hasError: !!errorEl,
      emptyText: emptyEl ? (emptyEl.innerText || '').trim().slice(0, 200) : null,
      titleTestSubjText: titleTestSubj ? (titleTestSubj.innerText || '').trim() : null,
      sharedTitleAttr: sharedTitle ? sharedTitle.getAttribute('data-shared-item-title') : null,
      anyHeadingText: anyHeading ? (anyHeading.innerText || '').trim().slice(0, 120) : null,
    });
  };

  for (const sel of CANDIDATE_SELECTORS) {
    for (const el of Array.from(document.querySelectorAll(sel))) {
      collect(el, sel);
    }
  }

  // Global counts to give context on Kibana's chrome
  const counts = {};
  for (const sel of CANDIDATE_SELECTORS) counts[sel] = document.querySelectorAll(sel).length;

  return {
    url: location.href,
    kibanaVersionMeta:
      (document.querySelector('meta[name="kbn-injected-metadata"]') || {}).content || null,
    counts,
    panels,
  };
}
"""


def _resolve_dashboard_id(settings, title):
    """Look up the dashboard id for a title, either via the live API or the file."""
    from dhm.registry import select_registry
    if settings.collector.registry_source == "api":
        from dhm.discovery import build_registry_from_api
        from dhm.registry import registry_to_dict
        reg = registry_to_dict(build_registry_from_api(settings))
    else:
        with open(settings.collector.registry_path) as f:
            reg = json.load(f)
    match = next((d for d in reg.get("dashboards", [])
                  if (d.get("title") or "").strip().lower() == title.strip().lower()),
                 None)
    if not match:
        titles = sorted({d.get("title") for d in reg.get("dashboards", [])})
        raise SystemExit(f"Dashboard title not found: {title!r}. Available: {titles}")
    # Also print the registry's expected panels for comparison
    print(f"\n--- Registry expects for '{title}' ({match['dashboard_id']}) ---")
    for p in match.get("panels", []):
        if p.get("is_data_panel"):
            print(f"  panel_id={p['panel_id']!r}  title={p.get('title')!r}  type={p.get('panel_type')}")
    return match["dashboard_id"], match.get("panels", [])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--settings", default="config/settings.yaml")
    ap.add_argument("--dashboard-title", default="Federal Overview")
    ap.add_argument("--wait-ms", type=int, default=15000,
                    help="How long to let the dashboard render before dumping (ms)")
    ap.add_argument("--out", default="debug.json")
    ap.add_argument("--es-api-key", default=None)
    ap.add_argument("--kibana-api-key", default=None)
    args = ap.parse_args()

    settings = load_settings(args.settings)
    settings.elasticsearch.api_key = resolve_es_api_key(
        settings, cli_value=args.es_api_key, env_value=os.environ.get("DHM_ES_API_KEY")
    )
    if settings.kibana.auth.method == "api_key":
        settings.kibana.auth.api_key = resolve_kibana_api_key(
            settings,
            cli_value=args.kibana_api_key,
            env_value=os.environ.get("DHM_KIBANA_API_KEY"),
            fallback=settings.elasticsearch.api_key,
        )

    dash_id, expected_panels = _resolve_dashboard_id(settings, args.dashboard_title)
    url = dashboard_url(settings, dash_id)
    print(f"\nLoading {url} for {args.wait_ms}ms...")

    from dhm.collector import _new_context
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        launch_kwargs = {"headless": settings.collector.headless}
        channel = (settings.collector.browser_channel or "").strip().lower()
        if channel and channel not in ("chromium", "bundled"):
            launch_kwargs["channel"] = channel
        browser = pw.chromium.launch(**launch_kwargs)
        context = _new_context(browser, settings)
        page = context.new_page()
        page.set_default_timeout(settings.collector.dashboard_timeout_ms)
        try:
            page.goto(url, wait_until="domcontentloaded",
                      timeout=settings.collector.dashboard_timeout_ms)
            # Give it real time to render.
            time.sleep(args.wait_ms / 1000.0)
            snapshot = page.evaluate(_DEBUG_JS)
        finally:
            context.close()
            browser.close()

    snapshot["_expected"] = [
        {"panel_id": p["panel_id"], "title": p.get("title"), "type": p.get("panel_type")}
        for p in expected_panels if p.get("is_data_panel")
    ]

    with open(args.out, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"\nWrote {args.out}")
    print(f"Container-selector counts on the page: {snapshot['counts']}")
    print(f"Total unique panel elements collected: {len(snapshot['panels'])}")
    print(f"Expected data panels from registry:   {len(snapshot['_expected'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
