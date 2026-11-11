"""Emit LaTeX macros and a text dump from official CSVs. Never invent a measured number."""
import os, json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, "..", "results", "aggregate_v3")
A2 = os.path.join(HERE, "..", "results", "aggregate_v2_20261111")

def fmt_int(x):
    return f"{int(round(float(x))):,}"

def fmt1(x):
    return f"{float(x):,.1f}"

macros = {}
log = []

def add(name, val, comment=""):
    macros[name] = val
    log.append(f"{name} = {val}  {comment}".rstrip())

# --- gas / bytes from v2 grid+synthetic (unchanged) ---
gpath = os.path.join(A, "table_gas_bytes.csv")
if not os.path.exists(gpath):
    gpath = os.path.join(A2, "table_gas_bytes.csv")
g = pd.read_csv(gpath)
st = g[g.variant == "storage"]
for lab, key in [("S2", "Stwo"), ("S3 b=16", "Sthree")]:
    d = st[st.label == lab]
    add(f"Gas{key}Min", fmt_int(d.gas_median.min()), lab)
    add(f"Gas{key}Max", fmt_int(d.gas_median.max()), lab)
ev = g[g.variant == "event"]
for lab, key in [("S2", "Stwo"), ("S3 b=16", "Sthree")]:
    d = ev[ev.label == lab]
    add(f"GasEvent{key}Min", fmt_int(d.gas_median.min()))
    add(f"GasEvent{key}Max", fmt_int(d.gas_median.max()))
    add(f"BytesEvent{key}", fmt_int(d.onchain_B.iloc[0]))

G = pd.concat([g[g.src == "grid"], g[(g.src == "synthetic") & (g.k >= 500)]])
for k in [10, 50, 100, 500, 1000]:
    r = G[(G.k == k) & (G.variant == "storage")].set_index("label")
    for lab, key in [("S1a", "Sonea"), ("S1b", "Soneb"), ("S2", "Stwo"), ("S3 b=16", "Sthree")]:
        add(f"Gas{key}K{k}", fmt_int(r.loc[lab, "gas_median"]))
        add(f"Bytes{key}K{k}", fmt_int(r.loc[lab, "onchain_B"]))

for lab, key in [("S1a", "Sonea"), ("S1b", "Soneb")]:
    for var, vk in [("storage", ""), ("event", "Event")]:
        d = G[(G.label == lab) & (G.variant == var)]
        sl, ic = np.polyfit(d.k, d.gas_median, 1)
        add(f"Fit{key}{vk}Slope", fmt_int(round(sl)))
        add(f"Fit{key}{vk}Intercept", f"{round(ic,1):,}")

r1path = os.path.join(A, "table_gas_round1.csv")
if not os.path.exists(r1path):
    r1path = os.path.join(A2, "table_gas_round1.csv")
r1 = pd.read_csv(r1path)
row = r1[(r1.src == "grid") & (r1.k == 10) & (r1.variant == "storage")]
add("GasStwoRoundOne", fmt_int(row[row.label == "S2"].gas_total.iloc[0]))
add("GasSthreeRoundOne", fmt_int(row[row.label == "S3 b=16"].gas_total.iloc[0]))

# EIP-7825 arithmetic from the fitted S1b storage slope (not a new measurement)
cap = 16_777_216
sl, ic = np.polyfit(G[(G.label == "S1b") & (G.variant == "storage")].k,
                    G[(G.label == "S1b") & (G.variant == "storage")].gas_median, 1)
kcap = (cap - ic) / sl
add("EipSonebKcap", str(int(round(kcap))))

# --- timing / verify from v3 if present ---
vpath = os.path.join(A, "table_verify.csv")
lpath = os.path.join(A, "table_latency.csv")
if os.path.exists(vpath) and os.path.exists(lpath):
    T = pd.read_csv(lpath)
    V = pd.read_csv(vpath)
    T = T[T.src == "timing_clean_v3"] if "src" in T.columns else T
    V = V[V.src == "timing_clean_v3"] if "src" in V.columns else V
    stor = T[T.variant == "storage"]
    ch = stor[stor.label.isin(["S2", "S3 b=16", "S3 b=4", "S3 b=flat"])]
    if len(ch):
        add("TchainStwoSthreeMin", fmt1(ch.t_chain_ms.min()))
        add("TchainStwoSthreeMax", fmt1(ch.t_chain_ms.max()))
    for k in [10, 50, 100, 500, 1000]:
        r = stor[stor.k == k]
        if r.empty:
            continue
        r = r.set_index("label")
        for lab, key in [("S1a", "Sonea"), ("S1b", "Soneb"), ("S2", "Stwo"),
                         ("S3 b=16", "Sthree"), ("S3 b=4", "SthreeFour"), ("S3 b=flat", "SthreeFlat")]:
            if lab in r.index:
                add(f"Tchain{key}K{k}", fmt1(r.loc[lab, "t_chain_ms"]))
                add(f"Ttotal{key}K{k}", fmt1(r.loc[lab, "t_total_ms"]))
        if "S2" in r.index:
            add(f"TblobK{k}", fmt1(r.loc["S2", "t_blob_put_ms"]))
        vr = V[V.k == k]
        if vr.empty:
            continue
        vr = vr.set_index("label")
        for lab, key in [("S1", "Sone"), ("S2", "Stwo"), ("S2sig", "Stwosig"),
                         ("S3 b=16", "Sthree"), ("S3 b=4", "SthreeFour"), ("S3 b=flat", "SthreeFlat")]:
            if lab not in vr.index:
                continue
            add(f"Tver{key}K{k}", fmt1(vr.loc[lab, "t_verify_ms_median"]))
            if "t_verify_ms_q1" in vr.columns:
                add(f"Tver{key}K{k}Qone", fmt1(vr.loc[lab, "t_verify_ms_q1"]))
                add(f"Tver{key}K{k}Qthree", fmt1(vr.loc[lab, "t_verify_ms_q3"]))
            add(f"Proof{key}K{k}", fmt_int(vr.loc[lab, "proof_B_mean"]))
            add(f"Nver{key}K{k}", fmt_int(vr.loc[lab, "n"]))
            if "t_index_ms_median" in vr.columns:
                add(f"Tidx{key}K{k}", fmt1(vr.loc[lab, "t_index_ms_median"]))
            if "n_fetched" in vr.columns and not pd.isna(vr.loc[lab, "n_fetched"]):
                add(f"Nfetch{key}K{k}", f"{vr.loc[lab, 'n_fetched']:.0f}")

# proof-size sweep (4 KiB)
pp = os.path.join(A, "table_proofsize.csv")
if os.path.exists(pp):
    P = pd.read_csv(pp)
    for k in [100, 500, 1000]:
        r = P[P.k == k].set_index("label")
        for lab, key in [("S2", "Stwo"), ("S2sig", "Stwosig"), ("S3 b=16", "Sthree"),
                         ("S3 b=4", "SthreeFour"), ("S3 b=flat", "SthreeFlat")]:
            if lab in r.index:
                add(f"Psz{key}K{k}", fmt_int(r.loc[lab, "proof_B_mean"]))

# grid K=100 proof sizes stay from v2 grid only if we did not re-run; prefer v3 clean if K=100 present
# (v3 clean uses model-size updates, which is what the grid used too)

# tamper
tp = os.path.join(A, "table_tamper.csv")
if os.path.exists(tp):
    Tm = pd.read_csv(tp)
    add("TamperRuns", fmt_int(Tm.runs.max()))
    for _, row in Tm.iterrows():
        key = f"Tamper{row['test']}{str(row['scheme']).replace('-', '')}"
        add(key, f"{int(row['detected'])}/{int(row['runs'])}")

# write
tex = ["% auto-generated by paper_numbers_v3.py — do not edit by hand\n",
       "% names may contain digits, so they are stored via \\csname and used as \\Nm{Name}\n",
       "\\providecommand{\\Nm}[1]{\\ifcsname N#1\\endcsname\\csname N#1\\endcsname\\else\\textbf{??#1}\\fi}\n"]
for k, v in macros.items():
    # sanitize for LaTeX macro names already camel
    tex.append(f"\\expandafter\\def\\csname N{k}\\endcsname{{{v}}}\n")
open(os.path.join(HERE, "numbers_macros.tex"), "w").write("".join(tex))
open(os.path.join(HERE, "paper_numbers_v3.txt"), "w").write("\n".join(log) + "\n")
print(f"wrote {len(macros)} macros")
print("\n".join(log[:40]), "\n...")
