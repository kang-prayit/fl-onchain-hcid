"""v4 analysis (read-only on official grid/synthetic gas CSVs): per-round S3(b=16) - S2 storage gas gap
versus the difference in non-zero calldata bytes. Writes results/gasgap_v4/{gasgap_rounds.csv, summary.json}."""
import glob, json, os
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rows = []
for f in sorted(glob.glob(os.path.join(ROOT, "results/grid/*/gas.csv")) + glob.glob(os.path.join(ROOT, "results/synthetic/*/gas.csv"))):
    g = pd.read_csv(f); g = g[(g.variant == "storage") & (g["round"] >= 2)]
    s2 = g[g.scheme == "S2"].set_index("round")
    s3 = g[(g.scheme == "S3") & (g.fanout.astype(str).isin(["16", "16.0"]))].set_index("round")
    src = "grid" if "/grid/" in f else "synthetic"
    for r in s2.index.intersection(s3.index):
        rows.append(dict(src=src, run=os.path.basename(os.path.dirname(f)), k=int(s2.loc[r, "k"]), round=int(r),
                         gas_s2=int(s2.loc[r, "gas_total"]), gas_s3=int(s3.loc[r, "gas_total"]),
                         nz_s2=int(s2.loc[r, "calldata_nonzero"]), nz_s3=int(s3.loc[r, "calldata_nonzero"])))
d = pd.DataFrame(rows)
d["gap"] = d.gas_s3 - d.gas_s2; d["dnz"] = d.nz_s3 - d.nz_s2; d["resid"] = d.gap - 12 * d.dnz
out = os.path.join(ROOT, "results", "gasgap_v4"); os.makedirs(out, exist_ok=True)
d.to_csv(os.path.join(out, "gasgap_rounds.csv"), index=False)
s = dict(n_round_pairs=len(d), gap_min=int(d.gap.min()), gap_max=int(d.gap.max()),
         resid_values=sorted(d.resid.unique().tolist()), per_zero_byte_gas=12,
         note="gap = 22 + 12*(nonzero calldata bytes S3 - S2) in every round pair; EIP-2028: 16 gas per non-zero, 4 per zero calldata byte")
json.dump(s, open(os.path.join(out, "summary.json"), "w"), indent=1); print(s)
