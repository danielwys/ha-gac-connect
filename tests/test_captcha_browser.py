"""Execute the actual embedded page logic with controlled HTTP ordering."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_captcha_page_logic():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the embedded-page tests (installed by CI)")
    result = subprocess.run([node, "--test", "tests/captcha_page.test.cjs"],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
