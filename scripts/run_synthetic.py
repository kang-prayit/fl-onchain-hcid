"""Synthetic cost test (no training): K in {10,50,100,500,1000}, random 4 KiB client updates, 3 rounds,
all schemes/variants, S3 fan-out ablation flat/32/16/8/4/2. Local Anvil (31337) + offline Kubo only."""
import json, os, shutil, sys, time
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from fl_onchain import chain, services
from fl_onchain.ipld import KuboStore
from fl_onchain.recorder import RoundRecorder
from fl_onchain.tamper import run_tamper_tests

OUT = os.path.join(ROOT, "results", "synthetic"); os.makedirs(OUT, exist_ok=True)
KS = [int(x) for x in os.environ.get("SYN_KS", "10,50,100,500,1000").split(",")]
ROUNDS, SIZE, FAN = 3, 4096, [None, 32, 16, 8, 4, 2]
AP, IP = 8850, 5350
for k in KS:
    rid = f"synthetic_k{k}_r{ROUNDS}"; out = os.path.join(OUT, rid)
    if os.path.exists(os.path.join(out, "DONE")):
        print("skip", rid); continue
    shutil.rmtree(out, ignore_errors=True); os.makedirs(os.path.join(out, "logs"))
    repo = "/tmp/flc-ipfs-syn"
    procs = [services.start_anvil(os.path.join(out, "logs", "anvil.log"), port=AP),
             services.start_kubo(repo, os.path.join(out, "logs", "kubo.log"), api_port=IP)]
    try:
        w3 = chain.connect(f"http://127.0.0.1:{AP}"); store = KuboStore(f"http://127.0.0.1:{IP}")
        rec = RoundRecorder(store, w3, os.path.join(ROOT, "contracts"), out, rid, k, fanouts=FAN, seed=k)
        rng = np.random.default_rng(k); t0 = time.time()
        for r in range(1, ROUNDS + 1):
            ups = [(c, int(rng.integers(50, 500)), rng.bytes(SIZE)) for c in range(k)]
            rec.record_round(r, ups, rng.bytes(SIZE))
        t_rec = time.time() - t0; t0 = time.time()
        for r in range(1, ROUNDS + 1):
            rec.audit_round(r)
        t_aud = time.time() - t0
        run_tamper_tests(rec, ROUNDS)
        json.dump(dict(run_id=rid, k=k, rounds=ROUNDS, update_bytes=SIZE, fanouts=[str(f) for f in FAN],
                       chain_id=w3.eth.chain_id, t_record_s=round(t_rec, 1), t_audit_s=round(t_aud, 1)),
                  open(os.path.join(out, "env.json"), "w"), indent=1)
        open(os.path.join(out, "DONE"), "w").write(time.strftime("%F %T"))
        print(rid, "record", round(t_rec, 1), "s audit", round(t_aud, 1), "s", flush=True)
    finally:
        services.stop(*procs); shutil.rmtree(repo, ignore_errors=True)
