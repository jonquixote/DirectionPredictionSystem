# ofi-lab-v3/tests/test_pending_queue.py
import json

from storage.pending_queue import PendingResolutionQueue, PendingEntry


def test_enqueue_dequeue_roundtrip(tmp_path):
    q = PendingResolutionQueue(tmp_path / "pending.json")
    e = PendingEntry(
        prediction_id="pid_300e",
        boundary_ms=1_000_000,
        model_name="900s_btc_v3_20260315",
        symbol="BTCUSDT",
        market_window_seconds=300,
        registry_load_generation=0,
        ts_resolve_at_ms=1_300_000,
        resolution_type="evaluation",
        price_at_open=60_000.0,
    )
    q.enqueue(e)
    q.persist()

    q2 = PendingResolutionQueue(tmp_path / "pending.json")
    q2.load()
    found = list(q2.iter_ripe(now_ms=2_000_000))
    assert len(found) == 1
    assert found[0].prediction_id == "pid_300e"


def test_iter_ripe_excludes_future(tmp_path):
    q = PendingResolutionQueue(tmp_path / "pending.json")
    q.enqueue(PendingEntry(
        prediction_id="pid_a", boundary_ms=1_000_000,
        model_name="m", symbol="s", market_window_seconds=900,
        registry_load_generation=0, ts_resolve_at_ms=2_000_000,
        resolution_type="evaluation", price_at_open=1.0,
    ))
    q.enqueue(PendingEntry(
        prediction_id="pid_b", boundary_ms=1_000_000,
        model_name="m", symbol="s", market_window_seconds=300,
        registry_load_generation=0, ts_resolve_at_ms=1_300_000,
        resolution_type="evaluation", price_at_open=1.0,
    ))
    ripe = list(q.iter_ripe(now_ms=1_400_000))
    assert [r.prediction_id for r in ripe] == ["pid_b"]


def test_remove_after_resolution(tmp_path):
    q = PendingResolutionQueue(tmp_path / "pending.json")
    q.enqueue(PendingEntry(
        prediction_id="pid_a", boundary_ms=1_000_000,
        model_name="m", symbol="s", market_window_seconds=900,
        registry_load_generation=0, ts_resolve_at_ms=1_900_000,
        resolution_type="evaluation", price_at_open=1.0,
    ))
    q.remove("pid_a")
    assert list(q.iter_ripe(now_ms=2_000_000)) == []


def test_prune_for_missing_models(tmp_path):
    q = PendingResolutionQueue(tmp_path / "pending.json")
    for name in ("alpha", "beta"):
        q.enqueue(PendingEntry(
            prediction_id=f"{name}_p", boundary_ms=1_000_000,
            model_name=name, symbol="s", market_window_seconds=900,
            registry_load_generation=0, ts_resolve_at_ms=1_900_000,
            resolution_type="evaluation", price_at_open=1.0,
        ))
    pruned = q.prune_for_models(active_models={"alpha"})
    remaining = [e.prediction_id for e in q.iter_all()]
    assert remaining == ["alpha_p"]
    assert pruned == 1


def test_persist_atomic_via_temp_file(tmp_path):
    p = tmp_path / "pending.json"
    q = PendingResolutionQueue(p)
    q.enqueue(PendingEntry(
        prediction_id="pid_a", boundary_ms=1, model_name="m", symbol="s",
        market_window_seconds=900, registry_load_generation=0,
        ts_resolve_at_ms=2, resolution_type="evaluation", price_at_open=1.0,
    ))
    q.persist()
    leftovers = [x for x in p.parent.iterdir() if x.suffix == ".tmp"]
    assert leftovers == []
    raw = json.loads(p.read_text())
    assert raw[0]["prediction_id"] == "pid_a"
