"""Centralized, version-sensitive Kibana DOM selectors.

Everything that depends on Kibana's HTML structure lives here so a Kibana
upgrade is a one-file change (see the project plan, "render-state detection
fragility"). The collector and the browser-side extraction script import from
this module only.

Verified against Kibana 8.x (through 8.19). If panels stop being detected after
an upgrade, re-run the render-detection spike (`scripts/debug_spike.py`) and
adjust the constants below.
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

    // identity: older Kibana exposed data-test-embeddable-id; newer 8.x
    // (verified on 8.19) does not — the panel is identified by aria-labelledby
    // pointing to a DOM heading whose text is the panel title. We keep every
    // signal we can and let the Python reconciler use whichever exists.
    const id =
      el.getAttribute('data-test-embeddable-id') ||
      el.getAttribute('data-embeddable-id') ||
      null;

    let title = (el.getAttribute('data-title') || '').trim();
    if (!title) {
      const t1 = el.querySelector('[data-test-subj="dashboardPanelTitle"]');
      if (t1 && t1.innerText) title = t1.innerText.trim();
    }
    if (!title) {
      // Follow aria-labelledby to the heading element that carries the title.
      // Kibana composes that element with a screen-reader label AND the visible
      // title, so innerText can look like: "Panel: My Chart\nMy Chart". Pick
      // the last non-empty line and strip the "Panel: " a11y prefix.
      const labelId = el.getAttribute('aria-labelledby');
      if (labelId) {
        const labelEl = document.getElementById(labelId);
        if (labelEl && labelEl.innerText) {
          const lines = labelEl.innerText.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
          if (lines.length) {
            title = lines[lines.length - 1];
            if (title.startsWith('Panel: ')) title = title.slice('Panel: '.length).trim();
          }
        }
      }
    }
    if (!title) {
      const t2 = el.querySelector('[data-shared-item-title]');
      if (t2) title = (t2.getAttribute('data-shared-item-title') || '').trim();
    }

    return { id, title, index: idx, renderComplete, loading, hasError, emptyText };
  });
}
"""
