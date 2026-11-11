"""Summarise one run directory (gas, on-chain bytes, latency, verification, tamper) into summary.md + summary.csv."""
import os, sys, json
import pandas as pd


def summarize(run_dir: str) -> str:
    g = pd.read_csv(os.path.join(run_dir, "gas.csv"))
    l = pd.read_csv(os.path.join(run_dir, "latency.csv"))
    v = pd.read_csv(os.path.join(run_dir, "verify.csv"))
    t = pd.read_csv(os.path.join(run_dir, "tamper.csv"))
    a = pd.read_csv(os.path.join(run_dir, "acc.csv"))
    env = json.load(open(os.path.join(run_dir, "env.json")))
    for df in (g, l, v):
        df["fanout"] = df["fanout"].fillna("").astype(str).str.replace(".0", "", regex=False)
    steady = g[g["round"] >= 2] if g["round"].max() >= 2 else g
    gs = steady.groupby(["scheme", "variant", "fanout"]).agg(gas_per_round=("gas_total", "median"),
                                                            n_tx=("n_tx", "median"),
                                                            onchain_bytes=("onchain_bytes", "median")).reset_index()
    g1 = g[g["round"] == 1].groupby(["scheme", "variant", "fanout"])["gas_total"].median().rename("gas_round1")
    gs = gs.merge(g1, on=["scheme", "variant", "fanout"])
    ls = l.groupby(["scheme", "variant", "fanout"]).agg(t_build_ms=("t_build_ms", "median"),
                                                       t_chain_ms=("t_chain_ms", "median"),
                                                       t_total_ms=("t_total_ms", "median")).reset_index()
    s = gs.merge(ls, on=["scheme", "variant", "fanout"])
    vs = v.groupby(["scheme", "fanout"]).agg(ok_rate=("ok", "mean"), proof_bytes=("proof_bytes", "median"),
                                            blocks_fetched=("n_fetched", "median"),
                                            t_verify_ms=("t_verify_ms", "median"),
                                            t_verify_nofetch_ms=("t_verify_nofetch_ms", "median")).reset_index()
    s.to_csv(os.path.join(run_dir, "summary_recording.csv"), index=False)
    vs.to_csv(os.path.join(run_dir, "summary_verify.csv"), index=False)
    md = [f"# Summary {env['run_id']}", "", f"K={env['args']['k']}, rounds={env['rounds_recorded']}, "
          f"params={env['n_params']} (update {env['update_bytes']} B), Kubo {env.get('kubo')}, flwr {env.get('flwr')}, "
          f"{env.get('forge')}, anvil chain {env.get('chain_id')}", "",
          "## Recording (gas = median over rounds >= 2; gas_round1 includes first-write of lastRound)", "",
          s.round(3).to_markdown(index=False), "", "## Per-client verification (all clients, all rounds)", "",
          vs.round(3).to_markdown(index=False), "", "## Tamper tests (last round)", "",
          t[["test", "scheme", "description", "detected", "reason"]].to_markdown(index=False), "",
          "## Accuracy (sanity check)", "", a[["round", "test_accuracy", "test_loss"]].to_markdown(index=False)]
    out = "\n".join(md)
    open(os.path.join(run_dir, "summary.md"), "w").write(out)
    return out


if __name__ == "__main__":
    print(summarize(sys.argv[1]))
