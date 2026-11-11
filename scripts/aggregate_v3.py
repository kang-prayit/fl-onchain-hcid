"""Build v3 aggregate tables. Does not modify aggregate_v2_20261111 or v2 timing/grid CSVs."""
import glob, json, os, sys
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G, S = os.path.join(ROOT, "results", "grid"), os.path.join(ROOT, "results", "synthetic")
TC = os.path.join(ROOT, "results", "timing_clean_v3")
PS = os.path.join(ROOT, "results", "proofsize_v3")
TAM = os.path.join(ROOT, "results", "tamper_v3")
OUT = os.path.join(ROOT, "results", "aggregate_v3")
os.makedirs(OUT, exist_ok=True)

def load(base, name):
    fs = [f for f in glob.glob(os.path.join(base, "*", name)) if os.path.exists(os.path.join(os.path.dirname(f), "DONE")) or base.endswith("tamper_v3") or base.endswith("proofsize_v3")]
    # for tamper_v3, DONE is at the parent; child runs have tamper.csv
    if not fs and "tamper_v3" in base:
        fs = glob.glob(os.path.join(base, "*", "tamper.csv"))
    return pd.concat([pd.read_csv(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()

def lab(r):
    if r["scheme"] == "S3":
        fo, k = r.get("fanout", 0), r.get("k", 0)
        try:
            fo, k = int(fo), int(k)
        except Exception:
            return "S3"
        return f"S3 b={'flat' if fo == k else fo}"
    return r["scheme"]

def iqr(s):
    return float(s.quantile(0.25)), float(s.quantile(0.75))

# ----- gas / bytes from grid + synthetic (same CSVs as v2; contracts for S1/S2/S3 unchanged)
def load_done(base, name):
    fs = [f for f in glob.glob(os.path.join(base, "*", name)) if os.path.exists(os.path.join(os.path.dirname(f), "DONE"))]
    return pd.concat([pd.read_csv(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()

gas = pd.concat([load_done(G, "gas.csv").assign(src="grid"),
                 load_done(S, "gas.csv").assign(src="synthetic")], ignore_index=True)
gas["fanout"] = gas["fanout"].fillna(0); gas["label"] = gas.apply(lab, axis=1)
gs = gas[gas["round"] >= 2]
gtab = gs.groupby(["src", "k", "scheme", "variant", "label"]).agg(
    gas_median=("gas_total", "median"), gas_min=("gas_total", "min"), gas_max=("gas_total", "max"),
    n_tx=("n_tx", "median"), calldata_B=("calldata_bytes", "median"), onchain_B=("onchain_bytes", "median"),
    n=("gas_total", "size")).reset_index()
gtab.to_csv(os.path.join(OUT, "table_gas_bytes.csv"), index=False)
g1 = gas[gas["round"] == 1].groupby(["src", "k", "label", "variant"])["gas_total"].median().reset_index()
g1.to_csv(os.path.join(OUT, "table_gas_round1.csv"), index=False)

# S2-sig gas from timing_clean_v3 (not in the v2 grid)
gas_tc = load_done(TC, "gas.csv")
if not gas_tc.empty:
    gas_tc["fanout"] = gas_tc["fanout"].fillna(0); gas_tc["label"] = gas_tc.apply(lab, axis=1)
    gas_tc["src"] = "timing_clean_v3"
    gs2 = gas_tc[gas_tc["round"] >= 2]
    gtab2 = gs2.groupby(["src", "k", "scheme", "variant", "label"]).agg(
        gas_median=("gas_total", "median"), gas_min=("gas_total", "min"), gas_max=("gas_total", "max"),
        onchain_B=("onchain_bytes", "median"), n=("gas_total", "size")).reset_index()
    gtab2.to_csv(os.path.join(OUT, "table_gas_timing_v3.csv"), index=False)

# ----- latency from timing_clean_v3 only (official)
lat = load_done(TC, "latency.csv")
lat["fanout"] = lat["fanout"].fillna(0); lat["label"] = lat.apply(lab, axis=1); lat["src"] = "timing_clean_v3"
ltab = lat.groupby(["src", "k", "label", "variant"]).agg(
    t_chain_ms=("t_chain_ms", "median"),
    t_chain_q1=("t_chain_ms", lambda s: s.quantile(0.25)),
    t_chain_q3=("t_chain_ms", lambda s: s.quantile(0.75)),
    t_total_ms=("t_total_ms", "median"),
    t_blob_put_ms=("t_blob_put_ms", "median"),
    dag_blocks=("dag_blocks", "median"),
    dag_meta_B=("dag_meta_bytes", "median"),
    root_block_B=("root_block_bytes", "median")).reset_index()
ltab.to_csv(os.path.join(OUT, "table_latency.csv"), index=False)

# ----- verification from timing_clean_v3
ver = load_done(TC, "verify.csv")
ver["fanout"] = ver["fanout"].fillna(0); ver["label"] = ver.apply(lab, axis=1); ver["src"] = "timing_clean_v3"
vtab = ver.groupby(["src", "k", "label"]).agg(
    n=("ok", "size"), ok_rate=("ok", "mean"),
    proof_B_mean=("proof_bytes", "mean"), proof_B_max=("proof_bytes", "max"),
    n_fetched=("n_fetched", "mean"),
    t_verify_ms_median=("t_verify_ms", "median"),
    t_verify_ms_q1=("t_verify_ms", lambda s: s.quantile(0.25)),
    t_verify_ms_q3=("t_verify_ms", lambda s: s.quantile(0.75)),
    t_verify_nofetch_ms_median=("t_verify_nofetch_ms", "median"),
    t_chain_ms_median=("t_chain_ms", "median") if "t_chain_ms" in ver.columns else ("t_verify_ms", "median"),
    t_index_ms_median=("t_index_ms", "median") if "t_index_ms" in ver.columns else ("t_verify_ms", "median"),
    t_fetch_ms_median=("t_fetch_ms", "median") if "t_fetch_ms" in ver.columns else ("t_verify_ms", "median"),
).reset_index()
vtab.to_csv(os.path.join(OUT, "table_verify.csv"), index=False)

# ----- proof size sweep (4 KiB, MemoryStore)
if os.path.exists(os.path.join(PS, "proof_bytes.csv")):
    ps = pd.read_csv(os.path.join(PS, "proof_bytes.csv"))
    ps["label"] = ps.apply(lambda r: r["scheme"] if r["scheme"] != "S3" else f"S3 b={'flat' if int(r['fanout'])==int(r['k']) else int(r['fanout'])}", axis=1)
    ptab = ps.groupby(["k", "label"]).agg(proof_B_mean=("proof_bytes", "mean"),
        proof_B_max=("proof_bytes", "max"), n_fetched=("n_fetched", "mean"), n=("proof_bytes", "size")).reset_index()
    ptab["src"] = "proofsize_v3"
    ptab.to_csv(os.path.join(OUT, "table_proofsize.csv"), index=False)

# ----- tamper v3
tam = load(TAM, "tamper.csv")
if not tam.empty:
    ttab = tam.groupby(["test", "scheme"]).agg(runs=("detected", "size"), detected=("detected", "sum")).reset_index()
    ttab["rate"] = ttab["detected"] / ttab["runs"]
    ttab.to_csv(os.path.join(OUT, "table_tamper.csv"), index=False)

# copy accuracy / na / partition from v2 aggregate (training unchanged)
for name in ["table_accuracy.csv", "table_na_cells.csv", "table_partition.csv", "table_runtime.csv"]:
    src = os.path.join(ROOT, "results", "aggregate_v2_20261111", name)
    if os.path.exists(src):
        pd.read_csv(src).to_csv(os.path.join(OUT, name), index=False)

# env summary
envs = []
for f in glob.glob(os.path.join(TC, "*", "env.json")):
    if os.path.exists(os.path.join(os.path.dirname(f), "DONE")):
        envs.append(json.load(open(f)))
loads = []
for f in glob.glob(os.path.join(TC, "*", "load.csv")):
    if os.path.exists(os.path.join(os.path.dirname(f), "DONE")):
        loads.append(pd.read_csv(f))
load_rng = None
if loads:
    L = pd.concat(loads, ignore_index=True)
    load_rng = (float(L.load1.min()), float(L.load1.max()))
open(os.path.join(OUT, "meta.json"), "w").write(json.dumps(dict(
    n_timing=len(envs), load1_range=load_rng, timing_ks=sorted({e["k"] for e in envs}),
), indent=1))
print("wrote", OUT, "timing envs", len(envs), "load1", load_rng)
