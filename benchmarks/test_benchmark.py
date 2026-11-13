import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def test_mock_benchmark_passes_every_case_without_leaks():
    result = subprocess.run([sys.executable, str(HERE / "run_benchmark.py"), "--mock-llm", "--quiet"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "leak_count 0" in result.stdout and "pass_rate 100.0%" in result.stdout


def test_report_builds_from_a_history_file(tmp_path, monkeypatch):
    sys.path.insert(0, str(HERE))
    import report
    setup = {"date": "2026-11-13", "mode": "full", "chat_model": "a/chat", "classifier": "b/decider", "threshold": 0.3,
             "agent_prompt_version": "4", "classifier_prompt_version": "2", "case_set": "2-abc123", "note": ""}
    summary = {"cases": 2, "passed": 1, "pass_rate": 0.5, "answer_pass": 0.5, "escalate_pass": None, "decline_pass": None, "leak_count": 0,
               "needless_escalations": 1, "wrong_answers": 0, "errors": 0, "theme_accuracy": 1.0, "latency_p50_s": 1.0, "latency_p95_s": 2.0, "cost_usd": 0.01}
    line = {"id": "x", "setup": setup, "summary": summary, "result_file": "missing.json", "failures": {"case-1": "needless escalation: <guard>"}}
    monkeypatch.setattr(report, "HISTORY", tmp_path / "history.jsonl")
    monkeypatch.setattr(report, "OUTPUT", tmp_path / "report.html")
    report.HISTORY.write_text(json.dumps(line) + "\n" + json.dumps({"legacy": {"id": "r1-old", "accuracy": "1.00"}}) + "\n")
    page = report.build().read_text()
    assert "a/chat" in page and "case-1" in page and "&lt;guard&gt;" in page and "r1-old" in page
