"""Locate the installed browser, and explain clearly when it is missing.

We drive an already-installed Chrome or Edge rather than downloading Chromium, so a
missing browser is a host-provisioning problem. Playwright's own message for this
("Run 'playwright install msedge'") is poor advice in a restricted boundary, and it
arrives only after a full discovery cycle has already run.

This module is deliberately advisory, not authoritative: Playwright has its own
resolution logic and we do not want a path list of ours to reject a browser that
would in fact have launched. So `probe` reports what it can see, and the caller uses
it to add context — before the run for a warning, or after a launch failure for a
diagnosis.
"""
from __future__ import annotations

import os
import shutil
from typing import Dict, List, Optional

# Where the branded browsers live, per Playwright channel. Ordered most-canonical
# first; `shutil.which` covers anything on PATH that these miss.
_CHANNEL_PATHS: Dict[str, List[str]] = {
    "msedge": [
        "/opt/microsoft/msedge/msedge",                                       # Linux
        "/usr/bin/microsoft-edge",
        "/usr/bin/microsoft-edge-stable",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",     # macOS
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",      # Windows
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "chrome": [
        "/opt/google/chrome/chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "chromium": [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/lib64/chromium-browser/chromium-browser",
    ],
}

_PATH_COMMANDS: Dict[str, List[str]] = {
    "msedge": ["microsoft-edge", "microsoft-edge-stable", "msedge"],
    "chrome": ["google-chrome", "google-chrome-stable", "chrome"],
    "chromium": ["chromium", "chromium-browser"],
}


def find_channel(channel: str) -> Optional[str]:
    """Return a path for this channel's browser, or None if we cannot see one."""
    channel = (channel or "").strip().lower()
    for path in _CHANNEL_PATHS.get(channel, []):
        if os.path.exists(path):
            return path
    for cmd in _PATH_COMMANDS.get(channel, []):
        found = shutil.which(cmd)
        if found:
            return found
    return None


def probe() -> Dict[str, Optional[str]]:
    """Map every known channel to a path, or None. Used to say what IS available."""
    return {ch: find_channel(ch) for ch in _CHANNEL_PATHS}


def describe_availability() -> str:
    """One line per channel, for an error message or a log."""
    lines = []
    for ch, path in probe().items():
        lines.append(f"    {ch:9} {path if path else '(not found)'}")
    return "\n".join(lines)


def launch_help(channel: str) -> str:
    """The message to show when the requested browser cannot be launched."""
    found = {ch: p for ch, p in probe().items() if p}
    msg = [
        f"Could not launch the '{channel}' browser. The collector drives an "
        f"already-installed browser and downloads nothing, so this is a host "
        f"provisioning problem.",
        "",
        "  Browsers visible on this host:",
        describe_availability(),
        "",
    ]
    alternatives = [ch for ch in found if ch != channel]
    if alternatives:
        alt = alternatives[0]
        msg += [
            f"  A different browser IS installed. To use it:",
            f"      export DHM_BROWSER_CHANNEL={alt}",
            f"  (or set collector.browser_channel: {alt} in settings.yaml)",
            "",
        ]
    else:
        msg += [
            "  No supported browser was found. Install one with the system package",
            "  manager — on RHEL-family hosts, Edge is:",
            "      sudo dnf install -y microsoft-edge-stable",
            "  (after adding Microsoft's repo), or Chrome via google-chrome-stable.",
            "",
        ]
    msg += [
        "  Note: 'playwright install msedge' also works, but it downloads a browser,",
        "  which is what we set out to avoid and may be blocked in this boundary.",
    ]
    return "\n".join(msg)
