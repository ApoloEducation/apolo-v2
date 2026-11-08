import subprocess
import sys
from pathlib import Path


def test_mock_benchmark_has_no_leaks():
    result = subprocess.run([sys.executable, str(Path(__file__).parent / "run_benchmark.py"), "--mock-llm"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "leak_count | 0" in result.stdout
