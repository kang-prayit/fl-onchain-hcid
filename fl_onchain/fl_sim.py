"""FedAvg simulation with Flower (Message API, flwr>=1.2x) and a plain-loop fallback.
The commitment hook runs inside the server after every aggregation, on the exact arrays the clients returned."""
from __future__ import annotations
import os, time
import numpy as np
import torch

from .data import load_npz, make_partitions
from .model import SmallCNN, get_state, set_state, train_local, evaluate, state_to_bytes

_CACHE = {}


def _cfg():
    return dict(path=os.environ["FLC_DATA"], k=int(os.environ["FLC_K"]), scheme=os.environ["FLC_PART"],
                alpha=float(os.environ.get("FLC_ALPHA", "0") or 0), seed=int(os.environ.get("FLC_SEED", "0")),
                epochs=int(os.environ.get("FLC_EPOCHS", "1")), width=int(os.environ.get("FLC_WIDTH", "16")))


def _client_data(pid):
    c = _cfg()
    key = (c["path"], c["k"], c["scheme"], c["alpha"], c["seed"])
    if key not in _CACHE:
        d = load_npz(c["path"])
        parts = make_partitions(d["y_train"], c["k"], c["scheme"], c["alpha"], c["seed"])
        _CACHE.clear(); _CACHE[key] = (d, parts)
    d, parts = _CACHE[key]
    idx = parts[pid]
    return d, d["x_train"][idx], d["y_train"][idx]


def local_update(pid: int, global_state: dict, rnd: int):
    c = _cfg()
    d, x, y = _client_data(pid)
    torch.manual_seed(c["seed"] * 100003 + rnd * 1009 + pid)
    m = SmallCNN(d["in_ch"], d["n_classes"], c["width"]); set_state(m, global_state)
    train_local(m, x, y, epochs=c["epochs"], seed=c["seed"] * 7919 + rnd * 31 + pid)
    return get_state(m), len(x)


def fedavg(states_and_n):
    tot = sum(n for _, n in states_and_n)
    keys = states_and_n[0][0].keys()
    return {k: (sum(s[k].astype(np.float64) * n for s, n in states_and_n) / tot).astype(np.float32) for k in keys}


# ------------------------------------------------------------------ plain loop backend (fallback / debugging)
def run_loop(rounds: int, hook, eval_fn, init_state):
    g = init_state
    c = _cfg()
    for r in range(1, rounds + 1):
        t0 = time.perf_counter()
        res = [(pid,) + local_update(pid, g, r) for pid in range(c["k"])]
        g = fedavg([(s, n) for _, s, n in res])
        t_train = time.perf_counter() - t0
        hook(r, [(pid, n, state_to_bytes(s)) for pid, s, n in res], state_to_bytes(g))
        eval_fn(r, g, t_train)
    return g


# ------------------------------------------------------------------ Flower backend
def run_flower(rounds: int, hook, eval_fn, init_state, num_cpus_per_client=1.0):
    from flwr.app import ArrayRecord, MetricRecord, RecordDict, Message, Context, ConfigRecord
    from flwr.clientapp import ClientApp
    from flwr.serverapp import ServerApp, Grid
    from flwr.serverapp.strategy import FedAvg
    from flwr.simulation import run_simulation

    c = _cfg()
    client_app = ClientApp()

    @client_app.train()
    def train(msg: Message, context: Context):
        pid = int(context.node_config["partition-id"])
        rnd = int(msg.content["config"]["server-round"])
        g = {k: v.numpy() for k, v in msg.content["arrays"].items()}
        s, n = local_update(pid, g, rnd)
        content = RecordDict({"arrays": ArrayRecord(torch_state_dict={k: torch.from_numpy(np.ascontiguousarray(v)) for k, v in s.items()}),
                              "metrics": MetricRecord({"num-examples": n, "partition-id": pid})})
        return Message(content=content, reply_to=msg)

    class CommitFedAvg(FedAvg):
        t_round0 = None

        def configure_train(self, server_round, arrays, config, grid):
            self.t_round0 = time.perf_counter()
            return super().configure_train(server_round, arrays, config, grid)

        def aggregate_train(self, server_round, replies):
            replies = list(replies)
            arrays, metrics = super().aggregate_train(server_round, replies)
            t_train = time.perf_counter() - (self.t_round0 or time.perf_counter())
            ups = []
            for m in replies:
                if m.has_error():
                    continue
                s = {k: v.numpy() for k, v in m.content["arrays"].items()}
                mm = m.content["metrics"]
                ups.append((int(mm["partition-id"]), int(mm["num-examples"]), state_to_bytes(s)))
            g = {k: v.numpy() for k, v in arrays.items()}
            hook(server_round, ups, state_to_bytes(g))
            eval_fn(server_round, g, t_train)
            return arrays, metrics

    server_app = ServerApp()

    @server_app.main()
    def main(grid: Grid, context: Context):
        strat = CommitFedAvg(fraction_train=1.0, fraction_evaluate=0.0, min_train_nodes=c["k"],
                             min_available_nodes=c["k"])
        strat.start(grid=grid, initial_arrays=ArrayRecord(torch_state_dict={k: torch.from_numpy(np.ascontiguousarray(v)) for k, v in init_state.items()}),
                    num_rounds=rounds, train_config=ConfigRecord({}))

    run_simulation(server_app=server_app, client_app=client_app, num_supernodes=c["k"],
                   backend_config={"client_resources": {"num_cpus": num_cpus_per_client, "num_gpus": 0.0},
                                   "init_args": {"log_to_driver": False}})
