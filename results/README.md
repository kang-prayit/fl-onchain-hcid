# Official result files

These CSVs and JSON files are the measurements behind Tables 4–7, Figure 3 and Equation (1) of the manuscript. They are released under CC BY 4.0 (see `../LICENSE-DATA.md`).

- `aggregate_v3/` — official gas, timing, proof-size, tamper and accuracy tables
- `gasgap_v4/` — S3−S2 gas decomposition (922 paired rounds)
- `ipfs_dagpath_v4/` — same-machine DAG-path retrieval and T5 withholding (no Kubo repos, no peer logs)
- `eq1_bound_v5/` — path-size bound constants and checks (MemoryStore; no chain)
- `proofsize_v3/` — fan-out sweep used by Figure 3a
- `timing_clean_v3/` and `tamper_v3/` — per-run `gas.csv` and `latency.csv` only (no proof stores, no update blobs)

MedMNIST+ `.npz` files are not included; download them from https://doi.org/10.5281/zenodo.10519652 (CC BY 4.0).
