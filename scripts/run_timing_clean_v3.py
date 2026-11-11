"""v3 clean re-measurement (indexed S1/S2/S2-sig proofs, n_j-bound signatures). No FL training.
Run when the shared box is quiet (refuses to start if 1-min load average >= MAX_LOAD unless --force).
Synthetic updates of the real model size (pathmnist SmallCNN, 424,356 B) by default.
K in {10,50,100,500,1000}; schemes S1a/S1b/S2/S3 (flat,16,4), storage + event; R rounds per K;
per round all clients audited (K<=100) or a random sample of AUDIT_N clients (K>100).
Load average is written next to every round and every audit batch. Local Anvil (chain 31337) + offline Kubo only.

usage: .venv/bin/python scripts/run_timing_clean.py [--ks 10,50,100,500,1000] [--rounds 10] [--size 424356]
                                                   [--audit-n 200] [--max-load 4] [--force]"""
import argparse, json, os, shutil, sys, time
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from fl_onchain import chain, services
from fl_onchain.ipld import KuboStore
from fl_onchain.recorder import RoundRecorder, CsvLog

ap = argparse.ArgumentParser()
ap.add_argument("--ks", default="10,50,100,500,1000"); ap.add_argument("--rounds", type=int, default=10)
ap.add_argument("--size", type=int, default=424356); ap.add_argument("--audit-n", type=int, default=200)
ap.add_argument("--max-load", type=float, default=4.0); ap.add_argument("--force", action="store_true")
ap.add_argument("--anvil-port", type=int, default=8870); ap.add_argument("--ipfs-port", type=int, default=5370)
ap.add_argument("--out", default=os.path.join(ROOT, "results", "timing_clean_v3"))
a = ap.parse_args()

l1 = os.getloadavg()[0]
if l1 >= a.max_load and not a.force:
    sys.exit(f"load average {l1:.1f} >= {a.max_load}: box is busy, not measuring (use --force to override)")
os.makedirs(a.out, exist_ok=True)
for k in [int(x) for x in a.ks.split(",")]:
    rid = f"timing_clean_k{k}_r{a.rounds}"; out = os.path.join(a.out, rid)
    if os.path.exists(os.path.join(out, "DONE")):
        print("skip", rid); continue
    shutil.rmtree(out, ignore_errors=True); os.makedirs(os.path.join(out, "logs"))
    load = CsvLog(os.path.join(out, "load.csv"), ["run_id", "phase", "round", "t_s", "load1", "load5", "load15", "ncpu"])
    lg = lambda ph, r, t: load.row(run_id=rid, phase=ph, round=r, t_s=round(t, 3), **dict(zip(
        ["load1", "load5", "load15"], [round(x, 2) for x in os.getloadavg()])), ncpu=os.cpu_count())
    repo = f"/tmp/flc-ipfs-timing-clean-v3"
    procs = [services.start_anvil(os.path.join(out, "logs", "anvil.log"), port=a.anvil_port),
             services.start_kubo(repo, os.path.join(out, "logs", "kubo.log"), api_port=a.ipfs_port)]
    try:
        w3 = chain.connect(f"http://127.0.0.1:{a.anvil_port}"); assert w3.eth.chain_id == 31337
        store = KuboStore(f"http://127.0.0.1:{a.ipfs_port}")
        rec = RoundRecorder(store, w3, os.path.join(ROOT, "contracts"), out, rid, k, fanouts=[None, 16, 4], seed=k)
        rng = np.random.default_rng(1000 + k)
        for r in range(1, a.rounds + 1):
            ups = [(c, int(rng.integers(50, 500)), rng.bytes(a.size)) for c in range(k)]
            t0 = time.perf_counter(); rec.record_round(r, ups, rng.bytes(a.size)); lg("record", r, time.perf_counter() - t0)
            del ups
            cl = None if k <= a.audit_n else sorted(rng.choice(k, a.audit_n, replace=False).tolist())
            t0 = time.perf_counter(); rec.audit_round(r, cl); lg("audit", r, time.perf_counter() - t0)
            for rr in list(rec.state):      # keep memory flat for K=1000 x 424 KB
                rec.state[rr].pop("updates", None)
            print(rid, "round", r, "load1", os.getloadavg()[0], flush=True)
        json.dump(dict(run_id=rid, k=k, rounds=a.rounds, update_bytes=a.size, audit_n=a.audit_n,
                       chain_id=w3.eth.chain_id, cpu_count=os.cpu_count(), load_at_start=l1,
                       tagged="timing_clean_v3", hardfork="prague"), open(os.path.join(out, "env.json"), "w"), indent=1)
        open(os.path.join(out, "DONE"), "w").write(time.strftime("%F %T"))
    finally:
        services.stop(*procs); shutil.rmtree(repo, ignore_errors=True)
