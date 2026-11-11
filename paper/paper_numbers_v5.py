"""v5 macros: all v4 macros (same official CSVs and v4 additions) plus Eq. (1) constants,
custody sizes, S2-sig gas range, DAG-build split, two-round gas at K>=500, and the
PneumoniaMNIST floor count. Never invents a number. Does not write paper_numbers_v4.txt
or edit official CSVs."""
import os, json
from pathlib import Path
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(HERE, "..", "results")
src = open(os.path.join(HERE, "paper_numbers_v4.py")).read()
# Reuse v4 (which itself execs v3) up to, but not including, the write of numbers_macros.tex
head, tail = src.split("tex = [", 1)
ns = {"__file__": os.path.join(HERE, "paper_numbers_v4.py"), "__name__": "v4"}
exec(compile(head, "paper_numbers_v4.py", "exec"), ns)
add, fmt_int, fmt1 = ns["add"], ns["fmt_int"], ns["fmt1"]
macros, log = ns["macros"], ns["log"]
log.append("# ---- v5 additions ----")

# --- Eq. (1) bound measurement (MemoryStore, results/eq1_bound_v5) ---
EQ = os.path.join(R, "eq1_bound_v5", "summary.json")
assert os.path.exists(EQ), f"missing {EQ}; run scripts/measure_eq1_bound_v5.py first"
eq = json.load(open(EQ))
assert eq["tight_bound_failures"] == 0 and eq["cons_bound_failures"] == 0, eq
add("EqCidLinkB", str(eq["c_ell_B"]), "DAG-CBOR size of one CID link")
add("EqHeaderInnerB", str(eq["h_I_max_B"]), "max non-children inner-node header")
add("EqHeaderRootB", str(eq["h_R_prev_max_B"]), "max later-round root header (prev is a CID)")
add("EqHeaderRootNullB", str(eq["h_R_null_max_B"]), "max round-1 root header (prev null)")
# h_I at the fan-outs used in the paper (b=4 and b=16 are both <24, 1-B array header)
hib = {int(k): v for k, v in eq["h_I_by_fanout"].items()}
if 4 in hib:
    add("EqHeaderInnerBfour", str(hib[4]["h_I_max"]))
if 16 in hib:
    add("EqHeaderInnerBsixteen", str(hib[16]["h_I_max"]))
man = eq["manifest_by_update_B"]
for usize, key in [("4096", "FourKiB"), ("424356", "Pathsize")]:
    m = man[usize]
    add(f"EqMan{key}Min", fmt_int(m["min"]))
    add(f"EqMan{key}Max", fmt_int(m["max"]))
    add(f"EqMan{key}Mean", fmt_int(round(m["mean"])))
add("EqNpaths", fmt_int(eq["n_path_rows"]))
add("EqNconfigs", fmt_int(eq["n_configs"]))
add("EqOldFail", fmt_int(eq["old_bound_failures"]))
add("EqArgminB", str(eq["argmin_b_K1000_4KiB_round2"]), "fan-out with smallest mean path at K=1000, 4 KiB, later round")

def _ex(name_substr):
    for k, v in eq["reviewer_examples"].items():
        if name_substr in k:
            return v
    raise KeyError(name_substr)

# K=1000, b=4, 4 KiB later-round: the configuration that violated v4 Eq. (1)
ex = _ex("K=1000 b=4 4KiB later-round")
add("EqExFourMaxPath", fmt_int(ex["path_max_B"]))
add("EqExFourOldBound", fmt_int(ex["old_bound_at_maxpath"]))
add("EqExFourNewBound", fmt_int(ex["tight_bound_at_maxpath"]))
add("EqExFourManMax", fmt_int(ex["manifest_max_B"]))
# Table 7 / clean-run path means from this encoder (for the prev-null explanation)
ex7 = _ex("K=1000 b=16 424356-B round 1")
exC = _ex("K=1000 b=16 424356-B later-round")
add("EqPathRoneBsixteen", fmt_int(round(ex7["path_mean_B"])))
add("EqPathRtwoBsixteen", fmt_int(round(exC["path_mean_B"])))
# later-round minus round-1 should equal prev-CID minus null
hr = eq["h_R_prev_range"]; hn = eq["h_R_null_range"]
# report measured prev-link sizes from the same examples (path max difference is fine too)
add("EqPrevGapBsixteen", str(int(round(exC["path_mean_B"]) - round(ex7["path_mean_B"]))),
    "later-round minus round-1 mean path at K=1000 b=16, 424356-B")

# --- official DAG-path whole-metadata sizes and replica pin (env.json, already verified) ---
env = json.load(open(os.path.join(R, "ipfs_dagpath_v4", "env.json")))
for c in env["configs"]:
    if c["k"] == 1000 and c["fanout"] == 16:
        add("DagMetaBKonethousandBsixteen", fmt_int(c["dag_meta_bytes"]))
        add("DagMetaBlocksKonethousandBsixteen", fmt_int(c["n_meta_blocks"]))
    if c["k"] == 1000 and c["fanout"] == 4:
        add("DagMetaBKonethousandBfour", fmt_int(c["dag_meta_bytes"]))
        add("DagMetaBlocksKonethousandBfour", fmt_int(c["n_meta_blocks"]))
# 410 MiB already WhRepMiBKonethousand; also exact bytes
rw = [x for x in env["withhold"] if x["k"] == 1000][0]
add("WhRepBytesKonethousand", fmt_int(rw["replica_repo_bytes"]))

# --- official S2 path: mean vs max at K=1000 (odd-node promotion) ---
ps = pd.read_csv(os.path.join(R, "aggregate_v3", "table_proofsize.csv"))
s2 = ps[(ps.k == 1000) & (ps.label == "S2")].iloc[0]
add("PszStwoK1000Mean", fmt_int(round(s2.proof_B_mean)), "official mean; paper already has PszStwoK1000=323 from the same cell")
add("PszStwoK1000Max", fmt_int(s2.proof_B_max))
# clean-run S2 mean (should match)
ver = pd.read_csv(os.path.join(R, "aggregate_v3", "table_verify.csv"))
s2c = ver[(ver.k == 1000) & (ver.label == "S2")].iloc[0]
add("ProofStwoK1000Mean", fmt_int(round(s2c.proof_B_mean)))
add("ProofStwoK1000Max", fmt_int(s2c.proof_B_max))

# --- S2-sig vs S2 gas on official timing_clean_v3 and tamper_v3 campaigns ---
def _load_gas(root):
    rows = [pd.read_csv(p).assign(_src=str(p)) for p in Path(root).glob("*/gas.csv")]
    return pd.concat(rows, ignore_index=True)

def _sig_gaps(df):
    d = df[(df["round"] >= 2) & (df.variant == "storage")]
    a = d[d.scheme == "S2"].set_index(["k", "round", "_src"])["gas_total"].rename("s2")
    b = d[d.scheme == "S2sig"].set_index(["k", "round", "_src"])["gas_total"].rename("sig")
    m = pd.concat([a, b], axis=1).dropna()
    m["gap"] = m["sig"] - m["s2"]
    return m

g_tc = _sig_gaps(_load_gas(os.path.join(R, "timing_clean_v3")))
g_tm = _sig_gaps(_load_gas(os.path.join(R, "tamper_v3")))
both = pd.concat([g_tc, g_tm])
add("GasSigPairsClean", fmt_int(len(g_tc)))
add("GasSigPairsTamper", fmt_int(len(g_tm)))
add("GasSigPairs", fmt_int(len(both)))
add("GasSigGapMin", f"$-${-int(both.gap.min())}" if both.gap.min() < 0 else str(int(both.gap.min())))
add("GasSigGapMax", str(int(both.gap.max())))
add("GasSigGapMed", str(int(both.gap.median())))

# --- two storage-gas values at K>=500 (official gasgap_v4, 2 rounds each) ---
gap = pd.read_csv(os.path.join(R, "gasgap_v4", "gasgap_rounds.csv"))
for k, key in [(500, "K500"), (1000, "K1000")]:
    d = gap[gap.k == k].sort_values("round")
    assert len(d) == 2, (k, len(d))
    add(f"GasGap{key}Rtwo", str(int(d.iloc[0].gap)))
    add(f"GasGap{key}Rthree", str(int(d.iloc[1].gap)))
    add(f"GasSthree{key}Rtwo", fmt_int(d.iloc[0].gas_s3))
    add(f"GasSthree{key}Rthree", fmt_int(d.iloc[1].gas_s3))
    add(f"GasStwo{key}Rtwo", fmt_int(d.iloc[0].gas_s2))
    add(f"GasStwo{key}Rthree", fmt_int(d.iloc[1].gas_s2))

# --- DAG build vs chain in the official clean timing run (all 10 rounds, storage, same rule as table_latency) ---
lat_rows = [pd.read_csv(p) for p in Path(os.path.join(R, "timing_clean_v3")).glob("*/latency.csv")]
lat = pd.concat(lat_rows, ignore_index=True)
s3b = lat[(lat.k == 1000) & (lat.scheme == "S3") & (lat.fanout == 16) & (lat.variant == "storage")]
s2b = lat[(lat.k == 1000) & (lat.scheme == "S2") & (lat.variant == "storage")]
assert len(s3b) == 10 and len(s2b) == 10
add("TbuildSthreeK1000", fmt1(s3b.t_build_ms.median()), "median over 10 rounds, storage, official clean run")
add("TbuildStwoK1000", fmt1(s2b.t_build_ms.median()), "S2 tree construction, same run")
# sanity: official Ttotal macros come from the same median-of-10-rounds rule
assert abs(s3b.t_total_ms.median() - 1602.3335) < 0.01, s3b.t_total_ms.median()

# --- PneumoniaMNIST majority-class floor (official accuracy table) ---
acc = pd.read_csv(os.path.join(R, "aggregate_v3", "table_accuracy.csv"))
pneu = acc[(acc.dataset == "pneumoniamnist") & acc.seeds.notna() & (acc.seeds > 0)]
floor = pneu[(pneu.acc_mean - 0.625).abs() < 1e-9]
add("AccPneuCells", str(int(len(pneu))))
add("AccPneuFloorCells", str(int(len(floor))))
add("AccCompletedRuns", str(int(acc.loc[acc.seeds > 0, "seeds"].sum())))
add("AccPneuFloorRuns", str(int(floor.seeds.sum())))

tex = ["% auto-generated by paper_numbers_v5.py (v4 macros + v5 additions) — do not edit by hand\n",
       "% names may contain digits, so they are stored via \\csname and used as \\Nm{Name}\n",
       "\\providecommand{\\Nm}[1]{\\ifcsname N#1\\endcsname\\csname N#1\\endcsname\\else\\textbf{??#1}\\fi}\n"]
for k, v in macros.items():
    tex.append(f"\\expandafter\\def\\csname N{k}\\endcsname{{{v}}}\n")
open(os.path.join(HERE, "numbers_macros.tex"), "w").write("".join(tex))
open(os.path.join(HERE, "paper_numbers_v5.txt"), "w").write("\n".join(log) + "\n")
print(f"wrote {len(macros)} macros")
print("\n".join(log[log.index("# ---- v5 additions ----"):]))
