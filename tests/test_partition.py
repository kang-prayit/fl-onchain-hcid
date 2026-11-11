"""Partition invariance vs the original Dirichlet sampler, plus ladder metadata."""
import hashlib, json, os, sys
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fl_onchain.data import (ensure_dataset, load_npz, make_partitions, partition_dirichlet,
                             DEFAULT_MIN_SIZE, DEFAULT_ATTEMPTS_FIRST)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEFORE = os.path.join(HERE, "work", "partition_fingerprints_before.json")


def _old_partition_dirichlet(labels, k, alpha, seed, min_size=10):
    """Frozen copy of the original sampler (1000 attempts, min_size=10, no ladder)."""
    rng = np.random.default_rng(seed)
    classes = np.unique(labels)
    for _ in range(1000):
        parts = [[] for _ in range(k)]
        for c in classes:
            idc = rng.permutation(np.where(labels == c)[0])
            p = rng.dirichlet(np.full(k, alpha))
            cuts = (np.cumsum(p) * len(idc)).astype(int)[:-1]
            for j, chunk in enumerate(np.split(idc, cuts)):
                parts[j].extend(chunk.tolist())
        if min(len(p) for p in parts) >= min_size:
            return [np.sort(np.array(p, dtype=np.int64)) for p in parts]
    raise RuntimeError("Dirichlet partition failed to satisfy min_size")


def _fp(parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(np.asarray(p, dtype=np.int64).tobytes())
    return h.hexdigest()


def test_ladder_never_includes_zero():
    with pytest.raises(ValueError):
        partition_dirichlet(np.array([0, 1, 0, 1]), k=2, alpha=0.5, seed=0, min_size=0)
    with pytest.raises(ValueError):
        partition_dirichlet(np.array([0, 1, 0, 1]), k=2, alpha=0.5, seed=0,
                            min_size=10, min_size_ladder=(10, 0))


def test_new_sampler_matches_old_when_min_size_10_succeeds():
    """Same seed must yield the identical partition for configs the old sampler accepted."""
    rng = np.random.default_rng(2026)
    # 5 classes, plenty of samples: old sampler succeeds at min_size=10
    labels = np.repeat(np.arange(5), 200)
    rng.shuffle(labels)
    for k, alpha, seed in [(10, 0.1, 0), (10, 0.5, 1), (20, 0.5, 2), (10, 1.0, 7)]:
        old = _old_partition_dirichlet(labels, k, alpha, seed)
        new = partition_dirichlet(labels, k, alpha, seed)
        assert [_fp([p]) for p in old] == [_fp([p]) for p in new]
        assert all(np.array_equal(a, b) for a, b in zip(old, new))
        meta = partition_dirichlet.last_meta
        assert meta["feasible"] is True
        assert meta["effective_min_size"] == DEFAULT_MIN_SIZE
        assert meta["attempts"] <= DEFAULT_ATTEMPTS_FIRST
        assert meta["requested_min_size"] == 10
        assert 0 not in meta["min_size_ladder"]


def test_pneumoniamnist_successful_configs_bit_identical():
    """Every already-DONE Dirichlet/IID config must match the pre-fix fingerprint."""
    if not os.path.exists(BEFORE):
        pytest.skip("no pre-fix fingerprint file")
    before = json.load(open(BEFORE))
    cache = {}
    checked = 0
    for tag, rec in before.items():
        if not rec.get("ok"):
            continue
        ds = rec["dataset"]
        if ds not in cache:
            cache[ds] = load_npz(ensure_dataset(ds, os.path.join(HERE, "data")))["y_train"]
        parts = make_partitions(cache[ds], rec["k"], rec["scheme"], rec["alpha"], rec["seed"])
        assert _fp(parts) == rec["fingerprint"], tag
        assert make_partitions.last_meta["feasible"] is True
        checked += 1
    assert checked == 45


def test_metadata_logs_client_stats():
    labels = np.repeat(np.arange(3), 80)
    parts = make_partitions(labels, 8, "dirichlet", 0.5, 0)
    meta = make_partitions.last_meta
    assert len(meta["clients"]) == 8
    assert len(meta["client_sizes"]) == 8
    assert meta["client_min"] == min(len(p) for p in parts)
    assert sum(meta["client_sizes"]) == len(labels)
    for c in meta["clients"]:
        assert c["n"] == sum(c["label_counts"].values())
