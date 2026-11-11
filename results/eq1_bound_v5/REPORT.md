# Equation (1) bound measurement (Draft v5)

MemoryStore + canonical DAG-CBOR encoder; elapsed 1895.05 s.
CID link size c_ell = 41 B.
Inner-node non-children header h_I (max observed) = 26 B; by fan-out: b=2: 25–25, b=3: 25–25, b=4: 25–25, b=8: 25–25, b=16: 25–25, b=32: 26–26.
Root non-children header h_R: 167–173 B when prev is null (round 1), 207–213 B when prev is a CID (later rounds). Conservative h_R used in the printed bound = 213 B (max later-round header).

Manifest |M_j| (bytes):
  |U|=4096 B: min 236, max 239, mean 238.27, 4 distinct sizes.
  |U|=424356 B: min 279, max 282, mean 281.27, 4 distinct sizes.

Repaired bound: P_S3(K,b) <= |M_j| + h_R + d h_I + c_ell b (d+1), with d=max(0, ceil(log_b K)-1), c_ell the DAG-CBOR size of one CID link, h_R the non-children bytes of the root (including prev, global, ghash and CBOR headers), and h_I the non-children bytes of an inner node.
Paths checked: 53940 across 192 (K,b,|U|,round) configs.
Old Eq. (1) failures: 41162 / 53940.
Tight new-bound failures (headers of the same DAG): 0.
Conservative new-bound failures (max h_R with prev, max h_I): 0.

## Reviewer / official examples
- K=1000 b=4 4KiB later-round (Fig. 3a / proofsize_v3): path mean 1365.3 B, max 1368 B, |M_j| 236–239 B, d=4, root_full=1. Old bound at max path 1059 (holds_all=False); tight 1368 (holds_all=True); conservative 1376 (holds_all=True).
- K=1000 b=4 424356-B round 1 (Table 7): path mean 1368.3 B, max 1371 B, |M_j| 279–282 B, d=4, root_full=1. Old bound at max path 1102 (holds_all=False); tight 1371 (holds_all=True); conservative 1419 (holds_all=True).
- K=1000 b=4 424356-B later-round (clean timing): path mean 1408.3 B, max 1411 B, |M_j| 279–282 B, d=4, root_full=1. Old bound at max path 1102 (holds_all=False); tight 1411 (holds_all=True); conservative 1419 (holds_all=True).
- K=1000 b=16 4KiB later-round: path mean 1961.4 B, max 1974 B, |M_j| 236–239 B, d=2, root_full=0. Old bound at max path 2207 (holds_all=True); tight 2466 (holds_all=True); conservative 2472 (holds_all=True).
- K=1000 b=16 424356-B round 1 (Table 7): path mean 1964.4 B, max 1977 B, |M_j| 279–282 B, d=2, root_full=0. Old bound at max path 2250 (holds_all=True); tight 2469 (holds_all=True); conservative 2515 (holds_all=True).
- K=1000 b=16 424356-B later-round (clean timing): path mean 2004.4 B, max 2017 B, |M_j| 279–282 B, d=2, root_full=0. Old bound at max path 2250 (holds_all=True); tight 2509 (holds_all=True); conservative 2515 (holds_all=True).
- K=1000 flat 4KiB: path mean 41451.6 B, max 41452 B, |M_j| 236–239 B, d=0, root_full=1. Old bound at max path 41239 (holds_all=False); tight 41452 (holds_all=True); conservative 41452 (holds_all=True).
- K=100 flat 4KiB: path mean 4547.6 B, max 4548 B, |M_j| 236–238 B, d=0, root_full=1. Old bound at max path 4338 (holds_all=False); tight 4548 (holds_all=True); conservative 4551 (holds_all=True).
- K=256 b=16 4KiB: path mean 1783.7 B, max 1784 B, |M_j| 236–238 B, d=1, root_full=1. Old bound at max path 1550 (holds_all=False); tight 1784 (holds_all=True); conservative 1789 (holds_all=True).
- K=10 b=4 Fig. 2: path mean 738.6 B, max 755 B, |M_j| 236–236 B, d=1, root_full=0. Old bound at max path 564 (holds_all=False); tight 796 (holds_all=True); conservative 803 (holds_all=True).

At K=1000, 4 KiB updates, later round, mean path is smallest at b=4 (1365.3 B). Values: b=2: 1491.9, b=3: 1402.8, b=4: 1365.3, b=8: 1583.6, b=16: 1961.4, b=32: 3091.7, b=1000: 41451.6.

Cross-check vs official campaign means (informational; n_j/signatures may differ):
{
  "proofsize_v3_K1000_b16_4KiB_mean_official": 1921.195,
  "this_K1000_b16_4KiB_r2_mean": 1961.43,
  "proofsize_v3_K1000_b4_4KiB_mean_official": 1325.24,
  "this_K1000_b4_4KiB_r2_mean": 1365.27,
  "proofsize_v3_K1000_flat_4KiB_max_official": 41413,
  "this_K1000_flat_4KiB_r2_max": 41452,
  "clean_K1000_b16_424356_mean_official": 2000.802,
  "this_K1000_b16_424356_r2_mean": 2004.43,
  "table7_K1000_b16_424356_mean_official": null,
  "this_K1000_b16_424356_r1_mean": 1964.43,
  "this_K1000_b4_424356_r1_mean": 1368.27,
  "this_K1000_b4_424356_r2_mean": 1408.27,
  "clean_run_dag_meta_K1000_b16_official": 327888,
  "this_dag_meta_K1000_b16_424356_r2": 327197,
  "this_dag_meta_K1000_b4_424356_r2": 344753,
  "this_dag_meta_K1000_b16_424356_r1": 327157,
  "this_dag_meta_K1000_b4_424356_r1": 344713,
  "note": "Small differences vs official CSVs are expected if n_j, signatures or global-node encoding differ; the bound check does not use official CSVs."
}
