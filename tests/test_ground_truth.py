"""Ground-truth dump + evaluation tool (evaluation-only path)."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from simulator import cli
from simulator.faults import FaultConfig
from simulator.fleet import Fleet

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import evaluate_source_hypothesis as ev  # noqa: E402


def test_dump_with_ground_truth_writes_matching_second_file(tmp_path):
    out = tmp_path / "run.jsonl"
    cli.main(["dump", "--start", "2026-12-10T03:00:00Z", "--hours", "1", "--out", str(out),
              "--with-ground-truth", "--offline-routes"])
    gt = tmp_path / "run.ground_truth.jsonl"
    assert gt.exists()
    wires = [json.loads(l) for l in out.read_text().splitlines()]
    truth = [json.loads(l) for l in gt.read_text().splitlines()]
    keys = {(w["device_id"], w["sequence"], w["timestamp_utc"]) for w in wires
            if {"device_id", "sequence", "timestamp_utc"} <= w.keys()}
    assert {(t["device_id"], t["sequence"], t["timestamp_utc"]) for t in truth} <= keys
    assert {"zone_id", "dominant_source", "dominant_strength", "components_pm25_ugm3"} <= truth[0].keys()


def test_ground_truth_never_in_wire_payloads_or_default_dump(tmp_path):
    fleet = Fleet(1, datetime(2026, 12, 10, 3, tzinfo=timezone.utc), FaultConfig(), allow_route_download=False)
    for em in list(fleet.run_until(datetime(2026, 12, 10, 3, 30, tzinfo=timezone.utc))):
        assert em.truth is not None
        assert not ({"truth", "dominant_source", "components_pm25_ugm3"} & set(em.wire))
    out = tmp_path / "plain.jsonl"
    cli.main(["dump", "--start", "2026-12-10T03:00:00Z", "--hours", "0.2", "--out", str(out), "--offline-routes"])
    assert not (tmp_path / "plain.ground_truth.jsonl").exists()


def test_live_path_sends_only_the_wire_dict():
    import inspect
    assert "truth" not in inspect.getsource(cli.cmd_live)


def test_worker_package_never_references_ground_truth():
    root = Path(__file__).resolve().parent.parent / "worker"
    for f in root.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert "ground_truth" not in src and "dominant_source" not in src, f.name


def test_confusion_matrix_math(capsys):
    pairs = [("traffic", "traffic", 0.8, "A")] * 6 + [("traffic", "insufficient", 0.3, "A")] * 2 \
        + [("dust", "dust", 0.7, "A")] * 3 + [("insufficient", "traffic", 0.6, "A")]
    ev.report(pairs)
    out = capsys.readouterr().out
    assert "Overall accuracy: 75.0%" in out                 # 9/12
    assert "traffic       recall  75.0% (6/8)" in out
    assert "precision  85.7% (6/7)" in out


def test_window_matching_uses_majority_truth_in_evidence_window():
    t0 = datetime(2026, 12, 10, 3, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    truth = {"Z": [(t0 + timedelta(minutes=i), "traffic" if i < 12 else "dust", "D", i) for i in range(15)]}
    outputs = {"Z": [{"window_end_utc": (t0 + timedelta(minutes=14)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "source_label": ev.TRAFFIC, "source_confidence": 0.7}]}
    (pair,) = ev.pairs_window(truth, outputs, window_min=15, min_records=5)
    assert pair[:2] == ("traffic", "traffic")
