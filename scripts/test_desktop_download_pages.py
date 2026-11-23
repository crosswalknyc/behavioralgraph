#!/usr/bin/env python3
"""Two desktop apps in the user menu; Windows no longer asks to bypass SmartScreen."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "templates" / "index.html"
DOWNLOAD = ROOT / "templates" / "download.html"
APP = ROOT / "app.py"


def test_menu_has_both_apps():
    src = INDEX.read_text(encoding="utf-8")
    assert 'href="/download" target="_blank" rel="noopener">Download dashboard app</a>' in src
    assert 'href="/download/prometheus" target="_blank" rel="noopener">Download Prometheus app</a>' in src
    assert "Download Desktop App" not in src


def test_download_page_no_windows_bypass():
    src = DOWNLOAD.read_text(encoding="utf-8")
    assert "Run anyway" not in src
    assert "More info" not in src
    assert "SmartScreen" not in src
    assert "troubleWin" not in src
    assert "{{ heading }}" in src
    assert "mac_intel_url" in src
    assert "show_mac_trouble" in src


def test_app_has_prometheus_download_route():
    src = APP.read_text(encoding="utf-8")
    assert "@app.route('/download/prometheus')" in src
    assert "PROMETHEUS_DESKTOP_DOWNLOAD" in src
    assert '"windows":   "/api/prometheus/v1/desktop/download/win"' in src
    assert 'heading="Download the dashboard app"' in src
    assert 'heading="Download the Prometheus app"' in src


if __name__ == "__main__":
    test_menu_has_both_apps()
    test_download_page_no_windows_bypass()
    test_app_has_prometheus_download_route()
    print("ok")
