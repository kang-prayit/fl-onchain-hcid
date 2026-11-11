"""Paper figures (v5: larger fonts) from v3 aggregate CSVs (matplotlib only; no generative AI)."""
import os, pandas as pd, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
A = os.path.join(os.path.dirname(__file__), "..", "results", "aggregate_v3")
A2 = os.path.join(os.path.dirname(__file__), "..", "results", "aggregate_v2_20261111")
O = os.path.join(os.path.dirname(__file__), "figs")
plt.rcParams.update({"font.size": 8.4, "font.family": "serif", "axes.titlesize": 8.6, "axes.labelsize": 8.4,
                     "xtick.labelsize": 8.0, "ytick.labelsize": 8.0, "legend.fontsize": 7.0,
                     "lines.linewidth": 1.1, "lines.markersize": 4.0, "pdf.fonttype": 42})

# Fig 3: gas — unchanged source (grid + synthetic v2)
gpath = os.path.join(A, "table_gas_bytes.csv")
if not os.path.exists(gpath):
    gpath = os.path.join(A2, "table_gas_bytes.csv")
g = pd.read_csv(gpath)
g = pd.concat([g[(g.src == "grid")], g[(g.src == "synthetic") & (g.k >= 500)]])
sty = {"S1a": ("tab:red", "o"), "S1b": ("tab:orange", "s"), "S2": ("tab:blue", "^"),
       "S3 b=16": ("tab:olive", "v"), "S3 b=4": ("tab:purple", "x"), "S3 b=flat": ("tab:green", "D")}
fig, ax = plt.subplots(1, 2, figsize=(3.45, 1.85), sharey=True)
for i, var in enumerate(["storage", "event"]):
    for lab, (c, m) in sty.items():
        d = g[(g.variant == var) & (g.label == lab)].sort_values("k")
        if d.empty:
            continue
        ax[i].plot(d.k, d.gas_median, color=c, marker=m, label=lab.replace("b=", "$b$="), mfc="none" if "S3" in lab else c)
    ax[i].set_xscale("log"); ax[i].set_yscale("log"); ax[i].grid(alpha=.3, which="both", lw=.4)
    ax[i].set_title(f"{var} variant"); ax[i].set_xlabel("clients per round $K$")
ax[0].set_ylabel("gas per round (median)")
h, l = ax[0].get_legend_handles_labels()
fig.legend(h, l, ncol=6, loc="upper center", handlelength=1.3, columnspacing=0.55, handletextpad=0.25, frameon=False)
fig.tight_layout(pad=0.2, w_pad=0.4, rect=(0, 0, 1, 0.88)); fig.savefig(os.path.join(O, "fig3_gas.pdf")); plt.close(fig)

# Fig 4a: proof size from proofsize_v3 (4 KiB); 4b: verify from timing_clean_v3 with IQR
ps = os.path.join(A, "table_proofsize.csv")
v = pd.read_csv(os.path.join(A, "table_verify.csv")) if os.path.exists(os.path.join(A, "table_verify.csv")) else pd.DataFrame()
fig, ax = plt.subplots(1, 2, figsize=(3.45, 2.15))
if os.path.exists(ps):
    s = pd.read_csv(ps)
    s = s[s.label.str.startswith("S3")].copy()
    for k, c in zip([100, 500, 1000], ["tab:green", "tab:red", "tab:purple"]):
        d = s[s.k == k].copy()
        d["b"] = d.label.str.extract(r"b=(\w+)")[0].replace("flat", str(k)).astype(int)
        d = d.sort_values("b")
        ax[0].plot(d.b, d.proof_B_mean / 1024, marker="o", color=c, label=f"S3, $K$={k}")
        s2 = s[(s.k == k) & (s.label == "S2")].proof_B_mean
        # S2 is in the same file with label S2 — read from full table
    # reload all labels for S2
    allp = pd.read_csv(ps)
    for k, c in zip([100, 500, 1000], ["tab:green", "tab:red", "tab:purple"]):
        s2 = allp[(allp.k == k) & (allp.label == "S2")].proof_B_mean
        if len(s2):
            ax[0].axhline(float(s2.iloc[0]) / 1024, color=c, ls=":", lw=.8)
ax[0].set_xscale("log", base=2); ax[0].set_yscale("log"); ax[0].grid(alpha=.3, which="both", lw=.4)
ax[0].set_xlabel("fan-out $b$ (last point: flat, $b$=$K$)"); ax[0].set_ylabel("metadata path (KiB)")
ax[0].set_title("(a) S3 metadata path size"); ax[0].legend(frameon=False, loc="upper left", fontsize=7.0)

if not v.empty:
    t = v[v.src == "timing_clean_v3"] if "src" in v.columns else v
    series = [("S1", "tab:red", "o"), ("S2", "tab:blue", "^"), ("S2sig", "tab:brown", "s"),
              ("S3 b=flat", "tab:green", "D"), ("S3 b=16", "tab:olive", "v"), ("S3 b=4", "tab:purple", "x")]
    for lab, c, m in series:
        d = t[t.label == lab].sort_values("k")
        if d.empty:
            continue
        ax[1].plot(d.k, d.t_verify_ms_median, color=c, marker=m,
                   label=lab.replace("b=", "$b$=").replace("S2sig", "S2-sig"),
                   mfc="none" if "S3" in lab else c)
        if "t_verify_ms_q1" in d.columns:
            ax[1].fill_between(d.k, d.t_verify_ms_q1, d.t_verify_ms_q3, color=c, alpha=0.12, lw=0)
ax[1].set_xscale("log"); ax[1].set_yscale("log"); ax[1].grid(alpha=.3, which="both", lw=.4)
ax[1].set_xlabel("clients per round $K$")
ax[1].set_ylabel("verify time (ms, median)")
ax[1].set_title("(b) per-client verification")
ax[1].set_ylim(5, 3000); ax[1].legend(frameon=False, loc="upper left", fontsize=6.6, ncol=2, columnspacing=0.7, handlelength=1.4)
fig.tight_layout(pad=0.2, w_pad=0.45); fig.savefig(os.path.join(O, "fig4_proof_verify.pdf")); plt.close(fig)
print("ok")
