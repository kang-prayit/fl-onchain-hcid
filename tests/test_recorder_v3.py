"""Needs a local Anvil on 127.0.0.1:8545 (skipped otherwise). Indexed verify + tamper T3b/T6/T4."""
import os, sys, shutil
import pytest
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fl_onchain import chain, services
from fl_onchain.ipld import MemoryStore
from fl_onchain.recorder import RoundRecorder
from fl_onchain.tamper import run_tamper_tests
from fl_onchain import proof_index

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def w3():
    try:
        return chain.connect()
    except Exception:
        pytest.skip("no local anvil")


def test_indexed_verify_and_tamper(w3, tmp_path):
    store = MemoryStore()
    rec = RoundRecorder(store, w3, os.path.join(HERE, "contracts"), str(tmp_path), "t", 8,
                        fanouts=[8, 4], seed=0)
    rng = np.random.default_rng(0)
    for r in range(1, 3):
        ups = [(c, 10 + c, rng.bytes(800)) for c in range(8)]
        rec.record_round(r, ups, rng.bytes(800))
    # honest indexed verify
    for c in range(8):
        assert rec.verify_s1(2, c)["ok"]
        assert rec.verify_s2(2, c)["ok"]
        assert rec.verify_s2sig(2, c)["ok"]
        assert rec.verify_s3(2, c, 4)["ok"]
        # one-file lookup exists
        proof_index.load_s1(str(tmp_path), 2, c)
        proof_index.load_s2(str(tmp_path), 2, c, "s2")
        proof_index.load_s2(str(tmp_path), 2, c, "s2sig")
    rows = run_tamper_tests(rec, 2, victim=3)
    got = {(t, s): d for t, s, d in rows}

    def expect(test, scheme, detected):
        assert (test, scheme) in got, (test, scheme, got.keys())
        assert got[(test, scheme)] is detected, (test, scheme, got[(test, scheme)], "expected", detected)

    for sch in ("S1", "S2", "S2sig", "S3"):
        expect("T1", sch, True)
        expect("T2", sch, True)
        expect("T3a", sch, True)
        expect("T5", sch, True)
    expect("T3b", "S3", True)
    expect("T3b", "S2sig", True)
    expect("T3b", "S2", False)          # unsigned leaf
    # S1 T3b is n/t (logged as ok=True => detected False)
    expect("T3b", "S1", False)
    expect("T4", "S3", True)
    expect("T4", "S2", True)
    expect("T4", "S2sig", True)
    expect("T4", "S1-guard", True)
    expect("T6", "S3", True)
    expect("T6", "S2sig", True)
    expect("T6", "S2", False)           # n/t
    expect("T6", "S1", False)           # n/t
