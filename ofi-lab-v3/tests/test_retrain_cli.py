import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "validation" / "retrain.py"


def test_cli_rejects_missing_required_args():
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--horizon", "900"],
        capture_output=True, text=True,
    )
    assert res.returncode != 0
    assert "symbol" in res.stderr.lower() or "required" in res.stderr.lower()


def test_cli_dry_run_prints_resolved_window(tmp_path):
    res = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--horizon", "900",
         "--symbol", "BTCUSDT",
         "--feature-version", "v3",
         "--train-end", "2026-05-08",
         "--train-days", "330",
         "--val-days", "30",
         "--test-days", "14",
         "--feature-dir", str(tmp_path),
         "--output-dir", str(tmp_path / "models"),
         "--dry-run"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "train: 2025-06-13 → 2026-05-08" in res.stdout or \
           "train_window_start=2025-06-13" in res.stdout
    assert "auto_name=900s_btcusdt_v3_20260508" in res.stdout.lower() or \
           "900s_btcusdt_v3_20260508" in res.stdout.lower()


def test_cli_calls_run_training_when_not_dry(tmp_path, monkeypatch):
    """When --dry-run is absent, retrain.py must invoke run_training's
    main() with the resolved date range. We verify by patching
    run_training.main to a sentinel and confirming it was called.
    """
    sentinel_path = tmp_path / "sentinel.txt"
    fake_runner = tmp_path / "fake_run_training.py"
    fake_runner.write_text(
        "import sys; from pathlib import Path\n"
        f"Path({str(sentinel_path)!r}).write_text(' '.join(sys.argv[1:]))\n"
        "sys.exit(0)\n"
    )
    monkeypatch.setenv("V3_RUN_TRAINING_OVERRIDE", str(fake_runner))
    res = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--horizon", "900",
         "--symbol", "BTCUSDT",
         "--feature-version", "v3",
         "--train-end", "2026-05-08",
         "--train-days", "330",
         "--val-days", "30",
         "--test-days", "14",
         "--feature-dir", str(tmp_path),
         "--output-dir", str(tmp_path / "models")],
        capture_output=True, text=True,
        env={**__import__("os").environ, "V3_RUN_TRAINING_OVERRIDE": str(fake_runner)},
    )
    assert res.returncode == 0, res.stderr
    assert sentinel_path.exists()
    args = sentinel_path.read_text()
    assert "BTCUSDT" in args
    assert "900" in args
