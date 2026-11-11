"""End-to-end run: FedAvg (Flower) + recording under S1/S2/S3 on local Anvil + offline Kubo, then audit and tamper tests.

Example (smoke):  python -m fl_onchain.run_experiment --dataset pneumoniamnist --k 10 --rounds 3 --partition iid \
                     --out results/smoke --start-services
"""
from __future__ import annotations
import argparse, csv, json, os, platform, subprocess, sys, time
import numpy as np
import torch

from . import chain, services
from .data import ensure_dataset, load_npz
from .ipld import KuboStore, check_kubo_roundtrip, encode_node
from .model import SmallCNN, get_state, set_state, evaluate, state_to_bytes
from .recorder import RoundRecorder, CsvLog
from .tamper import run_tamper_tests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="pneumoniamnist", choices=["pneumoniamnist", "pathmnist"])
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--partition", default="iid", choices=["iid", "dirichlet"])
    ap.add_argument("--alpha", type=float, default=0.5)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--width", type=int, default=16)
    ap.add_argument("--fanouts", default="flat,4", help="comma list; 'flat' = fan-out K")
    ap.add_argument("--backend", default="flower", choices=["flower", "loop"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--contracts", default=os.path.join(HERE, "contracts"))
    ap.add_argument("--start-services", action="store_true")
    ap.add_argument("--anvil-bin", default="anvil")
    ap.add_argument("--ipfs-bin", default="ipfs")
    ap.add_argument("--ipfs-repo", default="/tmp/flc-ipfs")
    ap.add_argument("--no-tamper", action="store_true")
    ap.add_argument("--anvil-port", type=int, default=8545)
    ap.add_argument("--ipfs-port", type=int, default=5001)
    ap.add_argument("--threads", type=int, default=0, help="torch threads (0 = torch default)")
    ap.add_argument("--audit-sample", type=int, default=0,
                    help="0 = audit every client in every round; n>0 = all clients of the last round + n per earlier round")
    ap.add_argument("--car", default="meta", choices=["meta", "full", "none"])
    ap.add_argument("--fixed-run-id", action="store_true", help="run id without timestamp; skip if already complete")
    a = ap.parse_args(argv)

    run_id = f"{a.dataset}_k{a.k}_{a.partition}{'' if a.partition == 'iid' else a.alpha}_s{a.seed}_r{a.rounds}"
    if not a.fixed_run_id:
        run_id += "_" + time.strftime('%Y%m%d-%H%M%S')
    out = os.path.join(a.out, run_id)
    if a.fixed_run_id and os.path.exists(os.path.join(out, "DONE")):
        print("SKIP (complete)", out); return out
    if a.fixed_run_id and os.path.exists(os.path.join(out, "INFEASIBLE")):
        print("SKIP (infeasible)", out); raise SystemExit(2)
    if os.path.exists(out):
        import shutil; shutil.rmtree(out)          # partial run from an earlier attempt: redo from scratch
    logs = os.path.join(out, "logs"); os.makedirs(logs, exist_ok=True)
    if a.threads:
        torch.set_num_threads(a.threads)
    procs = []
    if a.start_services:
        procs.append(services.start_anvil(os.path.join(logs, "anvil.log"), port=a.anvil_port, anvil_bin=a.anvil_bin))
        procs.append(services.start_kubo(a.ipfs_repo, os.path.join(logs, "kubo.log"), ipfs_bin=a.ipfs_bin, api_port=a.ipfs_port))
    try:
        path = ensure_dataset(a.dataset, a.data_dir)
        os.environ.update(FLC_DATA=path, FLC_K=str(a.k), FLC_PART=a.partition, FLC_ALPHA=str(a.alpha),
                          FLC_SEED=str(a.seed), FLC_EPOCHS=str(a.epochs), FLC_WIDTH=str(a.width))
        w3 = chain.connect(f"http://127.0.0.1:{a.anvil_port}")
        store = KuboStore(f"http://127.0.0.1:{a.ipfs_port}")
        kubo_ok = check_kubo_roundtrip(store, {"probe": 1, "x": [1, 2, 3]})
        fanouts = [None if f == "flat" else int(f) for f in a.fanouts.split(",")]
        rec = RoundRecorder(store, w3, a.contracts, out, run_id, a.k, fanouts=fanouts, seed=a.seed)

        d = load_npz(path)
        from .data import make_partitions
        try:
            make_partitions(d["y_train"], a.k, a.partition, a.alpha, a.seed)
            part_meta = dict(make_partitions.last_meta or {})
        except RuntimeError as e:
            part_meta = dict(make_partitions.last_meta or {"feasible": False, "reason": str(e)})
            json.dump(part_meta, open(os.path.join(out, "partition_meta.json"), "w"), indent=1)
            env = dict(run_id=run_id, args=vars(a), partition=part_meta, status="INFEASIBLE")
            json.dump(env, open(os.path.join(out, "env.json"), "w"), indent=1, default=str)
            open(os.path.join(out, "INFEASIBLE"), "w").write(part_meta.get("reason", str(e)) + "\n")
            print("INFEASIBLE", out, part_meta.get("reason", e), flush=True)
            services.stop(*procs); procs = []
            raise SystemExit(2)
        ems = part_meta.get("effective_min_size")
        print(f"[partition] scheme={part_meta.get('scheme')} alpha={part_meta.get('alpha')} "
              f"k={part_meta.get('k')} seed={part_meta.get('seed')} "
              f"effective_min_size={ems} attempts={part_meta.get('attempts')} "
              f"client_min={part_meta.get('client_min')} client_max={part_meta.get('client_max')}",
              flush=True)
        json.dump(part_meta, open(os.path.join(out, "partition_meta.json"), "w"), indent=1)
        torch.manual_seed(a.seed)
        m0 = SmallCNN(d["in_ch"], d["n_classes"], a.width)
        init = get_state(m0)
        n_params = sum(v.size for v in init.values())
        acc = CsvLog(os.path.join(out, "acc.csv"), ["run_id", "round", "test_accuracy", "test_loss", "t_train_s"])
        em = SmallCNN(d["in_ch"], d["n_classes"], a.width)

        def eval_fn(r, g, t_train):
            set_state(em, g); e = evaluate(em, d["x_test"], d["y_test"])
            acc.row(run_id=run_id, round=r, test_accuracy=round(e["accuracy"], 4), test_loss=round(e["loss"], 4),
                    t_train_s=round(t_train, 2))
            print(f"[round {r}] test acc={e['accuracy']:.4f}", flush=True)

        def hook(r, ups, gbytes):
            rec.record_round(r, ups, gbytes)

        from . import fl_sim
        t0 = time.perf_counter()
        if a.backend == "flower":
            fl_sim.run_flower(a.rounds, hook, eval_fn, init)
        else:
            fl_sim.run_loop(a.rounds, hook, eval_fn, init)
        t_fl = time.perf_counter() - t0
        rounds_done = sorted(rec.state)
        if not rounds_done:
            raise RuntimeError("no round was recorded")
        t0 = time.perf_counter()
        rng = np.random.default_rng(a.seed + 12345)
        for r in rounds_done:
            if a.audit_sample and r != rounds_done[-1]:
                rec.audit_round(r, sorted(rng.choice(a.k, size=min(a.audit_sample, a.k), replace=False).tolist()))
            else:
                rec.audit_round(r)
        t_audit = time.perf_counter() - t0
        tam = [] if a.no_tamper else run_tamper_tests(rec, rounds_done[-1])
        car_bytes = 0
        if a.car != "none":
            car = os.path.join(out, f"{run_id}{'' if a.car == 'full' else '.meta'}.car"); car_bytes = rec.export_car(car, a.car)
        env = dict(run_id=run_id, args=vars(a), python=sys.version, platform=platform.platform(),
                   cpu_count=os.cpu_count(), torch=torch.__version__, kubo=store.version(),
                   kubo_dagput_matches_local_cid=kubo_ok, n_params=int(n_params),
                   update_bytes=int(n_params * 4), rounds_recorded=rounds_done, t_fl_total_s=round(t_fl, 1), t_audit_s=round(t_audit, 1),
                   car_bytes=car_bytes, anvil_client=w3.client_version,
                   chain_id=w3.eth.chain_id, final_root_cid=str(rec.prev_root[rec.fanouts[0]]),
                   tamper=[dict(test=t, scheme=s, detected=bool(dd)) for t, s, dd in tam],
                   partition=part_meta, status="DONE")
        try:
            import flwr; env["flwr"] = flwr.__version__
        except Exception:
            pass
        try:
            env["forge"] = subprocess.run(["forge", "--version"], capture_output=True, text=True).stdout.split("\n")[0]
        except Exception:
            pass
        json.dump(env, open(os.path.join(out, "env.json"), "w"), indent=1, default=str)
        services.stop(*procs); procs = []
        import shutil; shutil.rmtree(a.ipfs_repo, ignore_errors=True)
        open(os.path.join(out, "DONE"), "w").write(time.strftime("%Y-%m-%d %H:%M:%S %Z"))
        print("RUN_DIR", out)
        return out
    finally:
        services.stop(*procs)


if __name__ == "__main__":
    main()
