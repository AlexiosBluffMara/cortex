"""Browser e2e coverage for funded model/cloud unlock controls."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_browser_funded_unlock_enables_paid_model_and_cloud_submission() -> None:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        pytest.skip("npm is required for the browser funded-access harness")
    chrome = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
    if not chrome.exists():
        pytest.skip("installed Chrome is required for funded-access e2e")

    result = subprocess.run(
        [npm, "--prefix", "webapp", "run", "test:funded"],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ok": true' in result.stdout
    assert '"computeTarget": "cloud_hf"' in result.stdout
    assert '"paidAccess": true' in result.stdout
