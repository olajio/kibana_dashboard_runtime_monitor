"""Dashboard Health & Load-Time Monitor (dhm).

Loads the Federal Overview dashboard family in a headless browser, measures
per-dashboard and per-panel load time, reads each panel's rendered health
state, and writes the result to Elasticsearch for trending and alerting.
"""

__version__ = "0.1.0"
