"""Centralized, version-sensitive Kibana DOM selectors.

Everything that depends on Kibana's HTML structure lives here so a Kibana
upgrade is a one-file change (see the project plan, "render-state detection
fragility"). The collector and the browser-side extraction script import from
this module only.

Verified against Kibana 8.x. If panels stop being detected after an upgrade,
re-run the render-detection spike and adjust the constants below.
"""

# Container element for every dashboard panel (data or navigation).
PANEL = '[data-test-subj="embeddablePanel"]'

# Control group (the filter bar at the top of many dashboards). We wait for it
# so its "loading" does not count against panel timing.
CONTROL_GROUP = '[data-test-subj="controlGroup"]'

# The browser-side function that reads the current state of every panel.
# It returns raw signals only; classification happens in Python
# (render_detection.classify_panel) so it can be unit tested without a browser.
PANEL_STATE_JS = r"""
() => {
  const panelEls = Array.from(document.querySelectorAll('[data-test-subj="embeddablePanel"]'));
  return panelEls.map((el, idx) => {
    // render-complete: Kibana stamps data-render-complete="true" on the panel
    // (or an inner element) when the embeddable has finished rendering.
    const rcInner = el.querySelector('[data-render-complete="true"]');
    const renderComplete =
      el.getAttribute('data-render-complete') === 'true' || !!rcInner;

    // still loading?
    const loadingAttr = el.getAttribute('data-loading');
    const loadingInner = el.querySelector('[data-loading="true"]');
    const loading = loadingAttr === 'true' || !!loadingInner;

    // error embeddable
    const errEl = el.querySelector(
      '[data-test-subj="embeddableStackTrace"],' +
      '[data-test-subj="embeddableError"],' +
      '.euiErrorBoundary'
    );
    const hasError = !!errEl;

    // "no results" / empty state (Lens, agg-based vis, and generic prompts)
    const emptyEl = el.querySelector(
      '[data-test-subj="emptyPlaceholder"],' +
      '[data-test-subj="lnsEmptyLayout"],' +
      '.lnsEmptyLayout,' +
      '[data-test-subj="visNoResult"],' +
      '[data-test-subj="euiEmptyPrompt"]'
    );
    const emptyText = emptyEl ? (emptyEl.innerText || '').trim().slice(0, 200) : null;

    // identity: data-test-embeddable-id matches the panelIndex in the export
    const id =
      el.getAttribute('data-test-embeddable-id') ||
      el.getAttribute('data-embeddable-id') ||
      null;
    const titleEl = el.querySelector('[data-test-subj="dashboardPanelTitle"]');
    const title = (el.getAttribute('data-title') ||
      (titleEl ? titleEl.innerText : '') || '').trim();

    return { id, title, index: idx, renderComplete, loading, hasError, emptyText };
  });
}
"""
