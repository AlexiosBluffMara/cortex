"""Browser e2e coverage for local camera + voice capture controls.

Requires a running Cortex webapp at 127.0.0.1:8765. The Node harness launches
installed Chrome with fake camera/mic devices, intercepts /api/scan in-page, and
asserts that camera + voice submit non-empty media files with the expected
Cortex form fields.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_browser_camera_and_voice_capture_submit_media() -> None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        pytest.skip("npm is required for the browser capture harness")
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome.exists():
        pytest.skip("installed Chrome is required for fake camera/mic e2e")

    result = subprocess.run(
        [npm, "--prefix", "webapp", "run", "test:capture"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ok": true' in result.stdout
    assert '"capture.jpg"' in result.stdout
    assert '"recording.webm"' in result.stdout or '"recording.m4a"' in result.stdout
