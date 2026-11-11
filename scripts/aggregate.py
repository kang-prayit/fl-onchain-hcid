"""Aggregate grid + synthetic results into tables (CSV/Markdown) and draft figures (PNG/PDF)."""
import glob, itertools, json, os, sys
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G, S = os.path.join(ROOT, "results", "grid"), os.path.join(ROOT, "results", "synthetic")
TC = os.path.join(ROOT, "results", "timing_clean")
OUT = os.path.join(ROOT, "results", "aggregate"); FIG = os.path.join(OUT, "figures")
os.makedirs(FIG, exist_ok=True)
plt.rcParams.update({"font.size": 8, "figure.dpi": 150, "savefig.bbox": "tight"})

def load(base, name):
    fs = [f for f in glob.glob(os.path.join(base, "*", name)) if os.path.exists(os.path.join(os.path.dirname(f), "DONE"))]
    return pd.concat([pd.read_csv(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()

def meta(base):
    rows = []
    for f in glob.glob(os.path.join(base, "*", "env.json")):
        if os.path.exists(os.path.join(os.path.dirname(f), "DONE")):
            j = json.load(open(f)); a = j.get("args", {})
            rows.append(dict(run_id=j["run_id"], dataset=a.get("dataset", "synthetic"), partition=a.get("partition", ""),
                             alpha=a.get("alpha", 0), seed=a.get("seed", 0), k=a.get("k", j.get("k")),
                             t_fl_total_s=j.get("t_fl_total_s"), t_audit_s=j.get("t_audit_s"), chain_id=j.get("chain_id")))
    return pd.DataFrame(rows)

def lab(r):
    return r["scheme"] + ("" if r["scheme"] != "S3" else f" b={'flat' if int(r['fanout']) == int(r['k']) else int(r['fanout'])}")

md = ["# Aggregate results (draft, local Anvil chain 31337)\n"]
def table(df, title, fn):
    df.to_csv(os.path.join(OUT, fn + ".csv"), index=False)
    md.extend([f"\n## {title}\n", df.to_markdown(index=False), ""])

M = meta(G); assert M.empty or set(M.chain_id) == {31337}

EXPECTED = []
for ds, part, k, seed in itertools.product(
        ["pathmnist", "pneumoniamnist"],
        [("dirichlet", 0.1), ("dirichlet", 0.5), ("iid", 0.0)],
        [100, 50, 10], [0, 1, 2]):
    scheme, alpha = part
    tag = f"{ds}_k{k}_{scheme}{'' if scheme == 'iid' else alpha}_s{seed}_r20"
    EXPECTED.append(dict(run_id=tag, dataset=ds, partition=scheme, alpha=alpha, k=k, seed=seed))

def run_status(tag):
    d = os.path.join(G, tag)
    if os.path.exists(os.path.join(d, "DONE")):
        return "DONE", ""
    if os.path.exists(os.path.join(d, "INFEASIBLE")):
        reason = open(os.path.join(d, "INFEASIBLE")).read().strip()
        return "INFEASIBLE", reason
    return "MISSING", "run not completed"

status_rows = []
for e in EXPECTED:
    st, reason = run_status(e["run_id"])
    status_rows.append({**e, "status": st, "reason": reason})
ST = pd.DataFrame(status_rows)
n_done = int((ST["status"] == "DONE").sum())
n_inf = int((ST["status"] == "INFEASIBLE").sum())
n_miss = int((ST["status"] == "MISSING").sum())
n_tc = len(meta(TC))
md.append(f"Grid runs DONE: {n_done} / 54. Infeasible (N/A): {n_inf}. Missing: {n_miss}. Synthetic runs: {len(meta(S))}. Clean timing runs: {n_tc}.\n")
md.append("Official recording latency and per-client verification times use `results/timing_clean` (quiet-box re-measurement) when those runs have a DONE marker. Gas, on-chain bytes, accuracy, and tamper stay on the grid + synthetic runs.\n")
if n_inf or n_miss:
    na = ST[ST["status"] != "DONE"][["run_id", "dataset", "partition", "alpha", "k", "seed", "status", "reason"]]
    table(na, "Grid cells marked N/A (infeasible or missing; not fabricated)", "table_na_cells")
    for _, r in na.iterrows():
        md.append(f"- N/A `{r['run_id']}`: {r['status']}. {r['reason']}")
    md.append("PathMNIST (9 classes) covers the non-IID setting at large K for cells where PneumoniaMNIST (2 classes) cannot yield a Dirichlet partition with min_size >= 2.")
    md.append("")

# ---------- gas / bytes per round vs K (rounds >= 2, i.e. steady state) ----------
gas = pd.concat([load(G, "gas.csv").assign(src="grid"), load(S, "gas.csv").assign(src="synthetic")], ignore_index=True)
gas["fanout"] = gas["fanout"].fillna(0); gas["label"] = gas.apply(lab, axis=1)
gs = gas[gas["round"] >= 2]
gtab = gs.groupby(["src", "k", "scheme", "variant", "label"]).agg(
    gas_median=("gas_total", "median"), gas_min=("gas_total", "min"), gas_max=("gas_total", "max"),
    n_tx=("n_tx", "median"), calldata_B=("calldata_bytes", "median"), onchain_B=("onchain_bytes", "median"),
    n=("gas_total", "size")).reset_index()
table(gtab, "Gas and on-chain bytes per round (rounds >= 2)", "table_gas_bytes")
g1 = gas[gas["round"] == 1].groupby(["src", "k", "label", "variant"])["gas_total"].median().reset_index()
table(g1, "Gas in round 1 (cold lastRound slot)", "table_gas_round1")

# ---------- latency ----------
lat = pd.concat([load(G, "latency.csv").assign(src="grid"), load(S, "latency.csv").assign(src="synthetic"), load(TC, "latency.csv").assign(src="timing_clean")], ignore_index=True)
lat["fanout"] = lat["fanout"].fillna(0); lat["label"] = lat.apply(lab, axis=1)
ltab = lat.groupby(["src", "k", "label", "variant"]).agg(
    t_chain_ms=("t_chain_ms", "median"), t_total_ms=("t_total_ms", "median"),
    t_blob_put_ms=("t_blob_put_ms", "median"), dag_blocks=("dag_blocks", "median"),
    dag_meta_B=("dag_meta_bytes", "median"), root_block_B=("root_block_bytes", "median")).reset_index()
table(ltab, "Recording latency per round (median)", "table_latency")

# ---------- verification ----------
ver = pd.concat([load(G, "verify.csv").assign(src="grid"), load(S, "verify.csv").assign(src="synthetic"), load(TC, "verify.csv").assign(src="timing_clean")], ignore_index=True)
ver["fanout"] = ver["fanout"].fillna(0); ver["label"] = ver.apply(lab, axis=1)
vtab = ver.groupby(["src", "k", "label"]).agg(n=("ok", "size"), ok_rate=("ok", "mean"),
    proof_B_mean=("proof_bytes", "mean"), proof_B_max=("proof_bytes", "max"), n_fetched=("n_fetched", "mean"),
    t_verify_ms_median=("t_verify_ms", "median"), t_verify_nofetch_ms_median=("t_verify_nofetch_ms", "median")).reset_index()
table(vtab, "Per-client verification (proof size, blocks fetched, time)", "table_verify")

# ---------- tamper ----------
tam = pd.concat([load(G, "tamper.csv").assign(src="grid"), load(S, "tamper.csv").assign(src="synthetic")], ignore_index=True)
ttab = tam.groupby(["test", "scheme"]).agg(runs=("detected", "size"), detected=("detected", "sum")).reset_index()
ttab["rate"] = ttab["detected"] / ttab["runs"]
table(ttab, "Tamper detection (all runs)", "table_tamper")

# ---------- accuracy ----------
acc = load(G, "acc.csv")
if not acc.empty:
    acc = acc.merge(M, on="run_id")
    acc["setting"] = acc.apply(lambda r: "IID" if r["partition"] == "iid" else f"Dir({r['alpha']})", axis=1)
    fin = acc[acc["round"] == acc["round"].max()]
    atab = fin.groupby(["dataset", "setting", "k"]).agg(acc_mean=("test_accuracy", "mean"),
            acc_sd=("test_accuracy", "std"), seeds=("seed", "nunique")).reset_index().round(4)
    present = set(zip(atab["dataset"], atab["setting"], atab["k"]))
    na_acc = []
    for (ds, part, alpha, k), g in ST.groupby(["dataset", "partition", "alpha", "k"], dropna=False):
        setting = "IID" if part == "iid" else f"Dir({alpha})"
        if (ds, setting, k) in present:
            continue
        reasons = "; ".join(sorted({
            f"seed {int(r.seed)}: {r.status}" + (f" ({r.reason})" if r.reason else "")
            for r in g.itertuples()
        }))
        na_acc.append(dict(dataset=ds, setting=setting, k=int(k), acc_mean="N/A", acc_sd="N/A",
                           seeds=0, reason=reasons))
    if na_acc:
        atab = pd.concat([atab, pd.DataFrame(na_acc)], ignore_index=True)
    table(atab, "Final test accuracy (round 20), mean ± sd over seeds (N/A = infeasible or missing)", "table_accuracy")
    tt = M.groupby(["dataset", "k"]).agg(t_fl_total_s=("t_fl_total_s", "median"), t_audit_s=("t_audit_s", "median")).reset_index()
    table(tt, "Wall-clock per run (median)", "table_runtime")

# ---------- partition metadata (from each run's partition_meta.json; never invented) ----------
prows = []
for e in EXPECTED:
    fp = os.path.join(G, e["run_id"], "partition_meta.json")
    if not os.path.exists(fp):
        continue
    j = json.load(open(fp))
    prows.append(dict(
        run_id=e["run_id"], dataset=e["dataset"], partition=j.get("scheme", e["partition"]),
        alpha=j.get("alpha", e["alpha"]), k=j.get("k", e["k"]), seed=j.get("seed", e["seed"]),
        feasible=j.get("feasible"), requested_min_size=j.get("requested_min_size"),
        effective_min_size=j.get("effective_min_size"), attempts=j.get("attempts"),
        attempts_per_level=json.dumps(j.get("attempts_per_level")) if j.get("attempts_per_level") is not None else "",
        client_min=j.get("client_min"), client_max=j.get("client_max"),
        client_mean=None if j.get("client_mean") is None else round(float(j["client_mean"]), 2),
        n_empty=j.get("n_empty"), n_train=j.get("n_train"), n_classes=j.get("n_classes"),
        reason=j.get("reason", ""),
    ))
if prows:
    table(pd.DataFrame(prows), "Partition metadata (effective min_size, attempts, per-client size range)", "table_partition")

open(os.path.join(OUT, "summary.md"), "w").write("\n".join(md))

# ================= figures =================
def save(fig, name):
    for ext in ("png", "pdf"):
        fig.savefig(os.path.join(FIG, f"{name}.{ext}"))
    plt.close(fig)

src = "synthetic" if (gtab["src"] == "synthetic").any() else "grid"
gg = gtab[gtab["src"] == src]
sty = {"S1a": ("tab:red", "o"), "S1b": ("tab:orange", "s"), "S2": ("tab:blue", "^"), "S3 b=flat": ("tab:green", "D"),
       "S3 b=16": ("tab:olive", "v"), "S3 b=4": ("tab:purple", "x")}
fig, axs = plt.subplots(1, 2, figsize=(7, 2.6), sharey=True)
for ax, var in zip(axs, ["storage", "event"]):
    for lb, (c, m) in sty.items():
        d = gg[(gg["variant"] == var) & (gg["label"] == lb)].sort_values("k")
        if len(d): ax.plot(d["k"], d["gas_median"], marker=m, color=c, label=lb, ms=4, lw=1)
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("clients per round K"); ax.set_title(f"{var} variant")
    ax.grid(alpha=.3, which="both")
axs[0].set_ylabel("gas per round"); axs[1].legend(fontsize=6, ncol=2)
save(fig, "fig_gas_vs_k")

fig, axs = plt.subplots(1, 2, figsize=(7, 2.6), sharey=True)
for ax, var in zip(axs, ["storage", "event"]):
    for lb, (c, m) in sty.items():
        d = gg[(gg["variant"] == var) & (gg["label"] == lb)].sort_values("k")
        if len(d): ax.plot(d["k"], d["onchain_B"], marker=m, color=c, label=lb, ms=4, lw=1)
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("clients per round K")
    ax.set_title(f"{var} variant (storage slots / log data + calldata)", fontsize=7); ax.grid(alpha=.3, which="both")
axs[0].set_ylabel("on-chain bytes per round"); axs[1].legend(fontsize=6, ncol=2)
save(fig, "fig_onchain_bytes_vs_k")

lat_src = "timing_clean" if (ltab["src"] == "timing_clean").any() else src
ll = ltab[(ltab["src"] == lat_src) & (ltab["variant"] == "storage")]
fig, axs = plt.subplots(1, 2, figsize=(7, 2.6))
for lb, (c, m) in sty.items():
    d = ll[ll["label"] == lb].sort_values("k")
    if len(d):
        axs[0].plot(d["k"], d["t_chain_ms"], marker=m, color=c, label=lb, ms=4, lw=1)
        axs[1].plot(d["k"], d["t_total_ms"], marker=m, color=c, label=lb, ms=4, lw=1)
for ax, t in zip(axs, ["on-chain submit (ms)", "commit build + submit (ms)"]):
    ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xlabel("clients per round K"); ax.set_ylabel(t); ax.grid(alpha=.3, which="both")
axs[0].legend(fontsize=6)
save(fig, "fig_latency_vs_k")

# verification time vs K (clean timing when available)
ver_src = "timing_clean" if (vtab["src"] == "timing_clean").any() else src
vv = vtab[vtab["src"] == ver_src]
fig, ax = plt.subplots(figsize=(3.5, 2.6))
for lb, (c, m) in {"S1": ("tab:red", "o"), "S2": ("tab:blue", "^"), **{k: v for k, v in sty.items() if k.startswith("S3")}}.items():
    d = vv[vv["label"] == lb].sort_values("k")
    if len(d): ax.plot(d["k"], d["t_verify_ms_median"], marker=m, color=c, label=lb, ms=4, lw=1)
ax.set_xscale("log"); ax.set_xlabel("clients per round K"); ax.set_ylabel("per-client verify (ms, median)")
ax.grid(alpha=.3, which="both"); ax.legend(fontsize=6); save(fig, "fig_verify_vs_k")

# proof size vs fan-out (synthetic ablation)
vs = ver[ver["src"] == "synthetic"]
if len(vs):
    fig, ax = plt.subplots(figsize=(3.5, 2.6))
    for k in sorted(vs["k"].unique()):
        d = vs[(vs["k"] == k) & (vs["scheme"] == "S3")].groupby("fanout")["proof_bytes"].mean().reset_index()
        d["b"] = d["fanout"].astype(int); d = d.sort_values("b")
        ax.plot(d["b"], d["proof_bytes"] / 1024, marker="o", ms=3, lw=1, label=f"S3, K={k}")
        s2 = vs[(vs["k"] == k) & (vs["scheme"] == "S2")]["proof_bytes"].mean() / 1024
        ax.axhline(s2, ls=":", lw=.7, color=ax.lines[-1].get_color())
    ax.set_xscale("log", base=2); ax.set_yscale("log"); ax.set_xlabel("fan-out b (rightmost point = flat, b=K)")
    ax.set_ylabel("inclusion proof (KiB)"); ax.grid(alpha=.3, which="both"); ax.legend(fontsize=6)
    ax.set_title("dotted: S2 Merkle path", fontsize=7); save(fig, "fig_proof_vs_fanout")

# accuracy
if not acc.empty:
    fig, axs = plt.subplots(1, 2, figsize=(7, 2.6), sharey=False)
    for ax, ds in zip(axs, sorted(acc["dataset"].unique())):
        for (st, k), d in acc[acc["dataset"] == ds].groupby(["setting", "k"]):
            m = d.groupby("round")["test_accuracy"].agg(["mean", "std"]).reset_index()
            ls = {"IID": "-", "Dir(0.5)": "--", "Dir(0.1)": ":"}.get(st, "-")
            c = {10: "tab:blue", 50: "tab:orange", 100: "tab:green"}.get(k, "k")
            ax.plot(m["round"], m["mean"], ls=ls, color=c, lw=1, label=f"{st}, K={k}")
            ax.fill_between(m["round"], m["mean"] - m["std"].fillna(0), m["mean"] + m["std"].fillna(0), color=c, alpha=.08)
        ax.set_title(ds); ax.set_xlabel("round"); ax.set_ylabel("test accuracy"); ax.grid(alpha=.3)
    axs[-1].legend(fontsize=5, ncol=3); save(fig, "fig_accuracy")
print("wrote", OUT)
