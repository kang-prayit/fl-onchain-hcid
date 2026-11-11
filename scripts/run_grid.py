"""Full Week-2 grid on the box: 2 datasets x {iid, dir0.1, dir0.5} x K{10,50,100} x 3 seeds, 20 rounds.
Resumable (runs with a DONE marker are skipped). N workers in parallel, each with its own Anvil/Kubo ports.
Local Anvil (chain 31337) only."""
import itertools, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "grid")
W = int(os.environ.get("GRID_WORKERS", "3"))
PY = os.path.join(ROOT, ".venv", "bin", "python")

jobs = []
for ds, part, k, seed in itertools.product(["pathmnist", "pneumoniamnist"],
                                           [("dirichlet", 0.1), ("dirichlet", 0.5), ("iid", 0.0)],
                                           [100, 50, 10], [0, 1, 2]):
    jobs.append((ds, part[0], part[1], k, seed))

slots = Queue()
for i in range(W):
    slots.put(i)

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)

def run(job):
    ds, part, alpha, k, seed = job
    i = slots.get()
    try:
        tag = f"{ds}_k{k}_{part}{'' if part == 'iid' else alpha}_s{seed}_r20"
        if os.path.exists(os.path.join(OUT, tag, "DONE")):
            log(f"skip {tag}"); return tag, 0, 0.0
        if os.path.exists(os.path.join(OUT, tag, "INFEASIBLE")):
            log(f"skip infeasible {tag}"); return tag, 2, 0.0
        os.makedirs(os.path.join(OUT, "_logs"), exist_ok=True)
        cmd = [PY, "-m", "fl_onchain.run_experiment", "--dataset", ds, "--data-dir", "data", "--k", str(k),
               "--rounds", "20", "--partition", part, "--alpha", str(alpha), "--seed", str(seed),
               "--fanouts", "flat,16,4", "--backend", "loop", "--out", OUT, "--start-services",
               "--ipfs-repo", f"/tmp/flc-ipfs-w{i}", "--anvil-port", str(8700 + i), "--ipfs-port", str(5200 + i),
               "--threads", "2", "--audit-sample", "10", "--car", "meta", "--fixed-run-id"]
        t0 = time.time(); log(f"start {tag} (worker {i})")
        with open(os.path.join(OUT, "_logs", tag + ".log"), "w") as f:
            rc = subprocess.call(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
        dt = time.time() - t0; log(f"end {tag} rc={rc} {dt:.0f}s")
        return tag, rc, dt
    finally:
        slots.put(i)

if __name__ == "__main__":
    log(f"grid: {len(jobs)} runs, {W} workers")
    with ThreadPoolExecutor(W) as ex:
        res = list(ex.map(run, jobs))
    infeas = [r for r in res if r[1] == 2]
    bad = [r for r in res if r[1] not in (0, 2)]
    log(f"grid finished; failed={len(bad)} {bad}; infeasible={len(infeas)} {infeas}")
    sys.exit(1 if bad else 0)
