"""Dedicated tamper campaign for draft v3 (S2-sig, T6 n_j-only, guarded S1 for T4).

Does not overwrite grid/synthetic/timing_clean CSVs. Uses a local Anvil node and an
in-memory IPLD store (content is synthetic; detection is independent of update bytes
beyond T1's single-byte flip). 2 rounds per run so T4 can replay round 1.

Mix of 53 runs chosen to mirror v2 (18 at K=10, 15 at K=50, 15 at K=100, 3 at K=500, 2 at K=1000).

usage: .venv/bin/python scripts/run_tamper_v3.py
"""
import json, os, sys, time
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from fl_onchain import chain, services
from fl_onchain.ipld import MemoryStore
from fl_onchain.recorder import RoundRecorder
from fl_onchain.tamper import run_tamper_tests

OUT = os.path.join(ROOT, "results", "tamper_v3")
MIX = [(10, 18), (50, 15), (100, 15), (500, 3), (1000, 2)]  # 53 runs
SIZE = 4096
ROUNDS = 2
ANVIL_PORT = 8875


def main():
    os.makedirs(OUT, exist_ok=True)
    if os.path.exists(os.path.join(OUT, "DONE")):
        print("already complete", OUT); return
    logp = os.path.join(OUT, "anvil.log")
    proc = services.start_anvil(logp, port=ANVIL_PORT)
    started = time.strftime("%Y-%m-%d %H:%M:%S %z")
    load0 = os.getloadavg()
    try:
        w3 = chain.connect(f"http://127.0.0.1:{ANVIL_PORT}")
        assert w3.eth.chain_id == 31337
        summary = []
        n = 0
        for k, reps in MIX:
            for i in range(reps):
                n += 1
                rid = f"tamper_v3_k{k}_rep{i+1:02d}"
                out = os.path.join(OUT, rid)
                os.makedirs(out, exist_ok=True)
                store = MemoryStore()
                rec = RoundRecorder(store, w3, os.path.join(ROOT, "contracts"), out, rid, k,
                                    fanouts=[None, 16, 4], seed=1000 + k * 100 + i)
                rng = np.random.default_rng(2000 + k * 100 + i)
                for r in range(1, ROUNDS + 1):
                    ups = [(c, int(rng.integers(50, 500)), rng.bytes(SIZE)) for c in range(k)]
                    rec.record_round(r, ups, rng.bytes(SIZE))
                rows = run_tamper_tests(rec, ROUNDS)
                # close logs
                rec.gas.f.close(); rec.lat.f.close(); rec.ver.f.close(); rec.tam.f.close()
                det = {(t, s): d for t, s, d in rows}
                summary.append({"run_id": rid, "k": k, "rep": i + 1, "detected": {f"{t}:{s}": bool(d) for (t, s), d in det.items()}})
                print(rid, "ok", flush=True)
        env = dict(tagged="tamper_v3", n_runs=n, mix=MIX, update_bytes=SIZE, rounds=ROUNDS,
                   chain_id=31337, started=started, finished=time.strftime("%Y-%m-%d %H:%M:%S %z"),
                   load_at_start=[round(x, 2) for x in load0], cpu_count=os.cpu_count())
        json.dump(env, open(os.path.join(OUT, "env.json"), "w"), indent=1)
        json.dump(summary, open(os.path.join(OUT, "summary_rows.json"), "w"), indent=1)
        open(os.path.join(OUT, "DONE"), "w").write(time.strftime("%F %T %Z"))
        print("DONE", n, "runs")
    finally:
        services.stop(proc)


if __name__ == "__main__":
    main()
