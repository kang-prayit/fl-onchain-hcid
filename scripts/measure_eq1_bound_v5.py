#!/usr/bin/env python3
"""Measure S3 metadata-path sizes and per-block header constants for Draft v5 Eq. (1).

Uses the in-process MemoryStore and the same DAG-CBOR encoder as the paper
(fl_onchain.commitments.s3_build). No chain, no Kubo, no private keys written to disk.
Client keys are generated in memory only (eth_account.Account.create).

The v4 bound P <= |M_j| + c_ell * b * (d+1) counts only child CID links and can
fail when the root is full. This script measures |M_j|, the non-link headers of
the root and of inner nodes, the CID-link size, and checks a repaired bound
    P <= |M_j| + h_R + d * h_I + c_ell * b * (d+1)
against every (position, K, b, update size, prev-null vs prev-CID) case below.
"""
from __future__ import annotations
import json, math, os, sys, time
from collections import defaultdict
from pathlib import Path

import dag_cbor
import pandas as pd
from eth_account import Account
from multiformats import CID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fl_onchain.commitments import (  # noqa: E402
    CHUNK, ClientUpdate, encode_node, s3_build, s3_child_index,
    s3_prove_and_verify, sha256, sign_update,
)
from fl_onchain.ipld import CODEC_CBOR, CODEC_RAW, MemoryStore, cid_digest32, decode_node  # noqa: E402

OUT = ROOT / "results" / "eq1_bound_v5"
UPDATE_SIZES = (4096, 424356)  # 4 KiB (proof-size campaign) and PathMNIST-model size (clean / DAG-path)
KS = (1, 2, 10, 16, 50, 100, 256, 500, 1000)
FANOUTS = (2, 3, 4, 8, 16, 32)  # plus flat (b=K) added per K
# Official table / figure / Table-7 configurations, always included.
OFFICIAL = {
    (10, 4), (10, 16), (10, 10),
    (100, 2), (100, 4), (100, 8), (100, 16), (100, 32), (100, 100),
    (256, 16),
    (500, 2), (500, 4), (500, 8), (500, 16), (500, 32), (500, 500),
    (1000, 2), (1000, 4), (1000, 8), (1000, 16), (1000, 32), (1000, 1000),
}


def depth_formula(K: int, b: int) -> int:
    """d = max(0, ceil(log_b K) - 1) for K>=1, b>=2. Matches s3_build for the cases we check."""
    if K <= 1:
        return 0
    return max(0, math.ceil(math.log(K, b)) - 1)


def cid_link_nbytes(cid: CID) -> int:
    """DAG-CBOR size of a single CID value (tag 42 + byte string), as used in children/prev/global."""
    return len(dag_cbor.encode(cid))


def header_minus_children(block: bytes, n_children: int, c_ell: int) -> int:
    return len(block) - n_children * c_ell


def walk_meta_path(store: MemoryStore, root_cid, client_pos: int) -> dict:
    """Re-fetch the metadata path (root, inners, manifest) and record per-block sizes."""
    blocks = []
    b = store.get(root_cid)
    assert cid_digest32.__module__  # keep import used
    from fl_onchain.ipld import cid_for
    assert cid_for(b, CODEC_CBOR) == root_cid
    root = decode_node(b)
    blocks.append(("root", len(b), len(root["children"])))
    node, raw = root, b
    d, fanout = root["depth"], root["fanout"]
    for lvl in range(d, 0, -1):
        c = node["children"][s3_child_index(client_pos, fanout, d, lvl)]
        raw = store.get(c)
        node = decode_node(raw)
        blocks.append(("inner", len(raw), len(node["children"])))
    man_c = node["children"][client_pos % fanout]
    raw = store.get(man_c)
    man = decode_node(raw)
    blocks.append(("manifest", len(raw), 0))
    path = sum(sz for _, sz, _ in blocks)
    return {
        "path_B": path,
        "root_B": blocks[0][1],
        "root_nchild": blocks[0][2],
        "inners": [(t, sz, n) for t, sz, n in blocks if t == "inner"],
        "manifest_B": blocks[-1][1],
        "n_meta_blocks": len(blocks),
        "n_chunks": len(man["update"]["chunks"]),
        "client_id": man["client"],
        "prev_is_null": root.get("prev") is None,
    }


def n_chunks_for(nbytes: int) -> int:
    """Same chunking rule as put_blob: 256 KiB raw blocks, at least one (possibly empty) block."""
    return max(1, (nbytes + CHUNK - 1) // CHUNK) if nbytes else 1


def fake_desc(store: MemoryStore, client: int, usize: int) -> dict:
    """Manifest update descriptor with the real `size` and the right number of raw CIDs.

    Chunk *payloads* are short unique stand-ins: a CID link is always the same encoded
    size, and |M_j| depends on the number of chunk CIDs and on the CBOR integer width
    of `size`, not on the raw chunk bytes (which are not part of the metadata path).
    """
    n = n_chunks_for(usize)
    chunks = [store.put(f"{client}:{i}:{usize}".encode(), CODEC_RAW) for i in range(n)]
    return {"chunks": chunks, "size": usize, "sha256": sha256(f"u:{client}:{usize}".encode()),
            "enc": "f32le-sorted-keys"}


def make_updates(k: int, rnd: int, nbytes: int, ghash: bytes, accts: dict) -> list[ClientUpdate]:
    ups = []
    # The update *bytes* are not stored. ClientUpdate still needs a `data` field to
    # form SHA256(U_j) for the signature; we use a short distinct stand-in. The
    # digest in the receipt is therefore not the digest of a 4 KiB / 424,356-B
    # payload, but both are 32-B SHA-256 values and do not change |M_j|.
    for c in range(k):
        u = ClientUpdate(c, 10 + (c % 97), f"stand-in:{c}:{nbytes}".encode())
        u.sig = sign_update(accts[c], rnd, c, u.n_samples, u.h, ghash)
        ups.append(u)
    return ups


def build_round(store, rnd, prev, g_cid, ups, descs, fanout, ghash):
    return s3_build(store, rnd, prev, g_cid, ups, descs, fanout, ghash=ghash)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ghash = sha256(b"eq1-bound-v5-global")
    # One in-memory key per client index up to max K (never written to disk).
    max_k = max(KS)
    accts = {c: Account.create() for c in range(max_k)}

    # --- CID link size ---
    probe_cid = encode_node({"t": 1})[1]
    c_ell = cid_link_nbytes(probe_cid)
    # Confirm it matches the increment of a children array.
    inner0, _ = encode_node({"type": "fl-inner", "children": [probe_cid]})
    inner1, _ = encode_node({"type": "fl-inner", "children": [probe_cid, probe_cid]})
    inc = len(inner1) - len(inner0)
    assert inc == c_ell, (inc, c_ell)

    # --- cases ---
    cases = set()
    for K in KS:
        for b in FANOUTS:
            if b <= K:
                cases.add((K, b))
        cases.add((K, K))  # flat
    cases |= OFFICIAL

    header_rows, path_rows, man_rows, bound_rows = [], [], [], []
    dag_rows = []
    held_precid = {}  # (K, b, usize) -> prev root cid from round 1

    # Precompute global-model node once per update size? global is a CID of a model node;
    # use one model node per (usize) so encodings are comparable.
    # We'll rebuild per case in the same store.

    # Group fan-outs by K so each (usize, K, round) signs and describes updates once.
    fanouts_by_k = {}
    for K, b in cases:
        fanouts_by_k.setdefault(K, set()).add(b)

    for usize in UPDATE_SIZES:
        for K in sorted(fanouts_by_k):
            print(f"  measuring |U|={usize} K={K} fan-outs={sorted(fanouts_by_k[K])}...", flush=True)
            # One model node and one descriptor set per round; DAGs of every fan-out
            # share them (CIDs are identical; only the tree shape changes).
            base = MemoryStore()
            model_desc = fake_desc(base, -1, min(usize, 4096))
            gbytes, g_cid = encode_node({"type": "fl-model", "round": 1, "blob": model_desc, "sha256": ghash})
            base.put(gbytes, CODEC_CBOR)

            prev_by_b = {}
            for rnd in (1, 2):
                ups = make_updates(K, rnd, usize, ghash, accts)
                descs = {u.client: fake_desc(base, u.client, usize) for u in ups}
                for b in sorted(fanouts_by_k[K]):
                    store = MemoryStore()
                    store.blocks.update(base.blocks)  # model + chunk CID stand-ins
                    prev = prev_by_b.get(b)
                    dag = build_round(store, rnd, prev, g_cid, ups, descs, b, ghash)
                    assert dag.fanout == b and dag.depth == depth_formula(K, b), (
                        K, b, dag.depth, depth_formula(K, b)
                    )
                    root_b = store.get(dag.root_cid)
                    root = decode_node(root_b)
                    nchild = len(root["children"])
                    h_R_this = header_minus_children(root_b, nchild, c_ell)
                    # Inner-node headers (every inner on every path would repeat; enumerate unique inners).
                    inner_headers = []

                    def collect_inners(cid, remaining_depth):
                        if remaining_depth <= 0:
                            return
                        node_b = store.get(cid)
                        node = decode_node(node_b)
                        if node.get("type") != "fl-inner":
                            return
                        inner_headers.append({
                            "size_B": len(node_b),
                            "n_children": len(node["children"]),
                            "h_I": header_minus_children(node_b, len(node["children"]), c_ell),
                        })
                        for ch in node["children"]:
                            collect_inners(ch, remaining_depth - 1)

                    for ch in root["children"]:
                        collect_inners(ch, dag.depth)

                    header_rows.append({
                        "update_B": usize, "k": K, "fanout": b, "round": rnd,
                        "depth": dag.depth, "depth_formula": depth_formula(K, b),
                        "root_full": int(nchild == b),
                        "root_nchild": nchild,
                        "root_B": len(root_b),
                        "h_R": h_R_this,
                        "prev_is_null": int(root.get("prev") is None),
                        "prev_link_B": 1 if root.get("prev") is None else cid_link_nbytes(root["prev"]),
                        "global_link_B": cid_link_nbytes(root["global"]),
                        "n_inners": len(inner_headers),
                        "h_I_min": min((x["h_I"] for x in inner_headers), default=0),
                        "h_I_max": max((x["h_I"] for x in inner_headers), default=0),
                        "inner_size_min": min((x["size_B"] for x in inner_headers), default=0),
                        "inner_size_max": max((x["size_B"] for x in inner_headers), default=0),
                        "dag_meta_B": dag.dag_bytes,
                        "dag_n_blocks": dag.n_blocks,
                        "c_ell": c_ell,
                    })
                    dag_rows.append({
                        "update_B": usize, "k": K, "fanout": b, "round": rnd,
                        "depth": dag.depth, "dag_meta_B": dag.dag_bytes,
                        "dag_n_blocks": dag.n_blocks, "root_B": len(root_b),
                        "prev_is_null": int(root.get("prev") is None),
                    })

                    # Manifest sizes (all clients; cheap — already in store).
                    # Walk first child chain to manifests? Easier: prove path for every client.
                    # For large K, path-walk all clients is fine (K=1000, d<=10).
                    paths = []
                    for pos in range(K):
                        w = walk_meta_path(store, dag.root_cid, pos)
                        assert w["n_meta_blocks"] == dag.depth + 2
                        paths.append(w)
                        man_rows.append({
                            "update_B": usize, "k": K, "fanout": b, "round": rnd,
                            "client_pos": pos, "manifest_B": w["manifest_B"],
                            "n_chunks": w["n_chunks"],
                        })
                        # Bound check uses measured constants defined after the loop;
                        # record raw path now.
                        path_rows.append({
                            "update_B": usize, "k": K, "fanout": b, "round": rnd,
                            "depth": dag.depth, "client_pos": pos,
                            "path_B": w["path_B"], "manifest_B": w["manifest_B"],
                            "root_B": w["root_B"], "n_meta_blocks": w["n_meta_blocks"],
                            "prev_is_null": int(w["prev_is_null"]),
                            "n_chunks": w["n_chunks"],
                        })
                        # Sanity: verifier (metadata only) accepts (position=id in this experiment).
                        digest = cid_digest32(dag.root_cid)
                        # registry not needed if we skip? signatures still optional when registry is None.
                        r = s3_prove_and_verify(store, digest, rnd, pos, pos, registry=None, fetch_update=False)
                        assert r["ok"] and r["proof_bytes"] == w["path_B"], (K, b, pos, r)

                    prev_by_b[b] = dag.root_cid

    headers = pd.DataFrame(header_rows)
    paths = pd.DataFrame(path_rows)
    mans = pd.DataFrame(man_rows)
    dags = pd.DataFrame(dag_rows)

    # Constants used in the repaired bound:
    # c_ell: measured DAG-CBOR size of one CID link (independent of K,b).
    # h_I: non-children header of an inner node. Array-header is 1 B for n<24 and
    #      2 B for 24<=n<256, so h_I grows slightly with a full fan-out >=24.
    #      We take the max observed header over full inner nodes, which is conservative
    #      for every smaller node.
    # h_R: non-children header of the root, including prev, global, ghash and map/array
    #      headers. prev is a CID in later rounds and null (1 B) in round 1; we take
    #      the max observed header over roots that have a prev CID (later-round worst
    #      case). The children-array header likewise depends on the number of child
    #      slots; taking the max over full and non-full roots with a prev CID is
    #      conservative, because a length-b array header is at least as large as a
    #      shorter array when n<=b and b>=2.
    h_I_full = headers.loc[headers.n_inners > 0, "h_I_max"]
    # Prefer headers of inners that actually exist; max over all such.
    h_I = int(headers["h_I_max"].max()) if (headers["n_inners"] > 0).any() else 0
    # Also record header of a *full* inner at each b, and of a 1-child inner.
    h_I_by_b = (
        headers[headers.n_inners > 0]
        .groupby("fanout")[["h_I_min", "h_I_max"]]
        .max()
    )
    h_R_prev = int(headers.loc[headers.prev_is_null == 0, "h_R"].max())
    h_R_null = int(headers.loc[headers.prev_is_null == 1, "h_R"].max())
    # Manifest sizes by update size (chunk list grows with |U|).
    man_by_u = mans.groupby("update_B")["manifest_B"].agg(["min", "max", "mean", "median", "nunique"])

    # Conservative constants for the printed bound (later-round, max headers, max |M_j| per usize).
    # We also report a tight per-config check: h_R and h_I of that same DAG, |M_j| of that client.

    def old_bound(Mj, b, d):
        return Mj + c_ell * b * (d + 1)

    def new_bound(Mj, hR, hI, b, d):
        return Mj + hR + d * hI + c_ell * b * (d + 1)

    # Per-path tight and conservative checks.
    check_rows = []
    for _, row in paths.iterrows():
        usize, K, b, rnd, d = int(row.update_B), int(row.k), int(row.fanout), int(row["round"]), int(row.depth)
        Mj = int(row.manifest_B)
        P = int(row.path_B)
        # tight: headers from the same (K,b,usize,round) DAG
        h = headers[(headers.update_B == usize) & (headers.k == K) & (headers.fanout == b) & (headers["round"] == rnd)].iloc[0]
        hR_t, hI_t = int(h.h_R), int(h.h_I_max) if int(h.n_inners) else 0
        nb_old = old_bound(Mj, b, d)
        nb_tight = new_bound(Mj, hR_t, hI_t, b, d)
        nb_cons = new_bound(Mj, h_R_prev, h_I, b, d)
        check_rows.append({
            "update_B": usize, "k": K, "fanout": b, "round": rnd, "depth": d,
            "client_pos": int(row.client_pos), "path_B": P, "manifest_B": Mj,
            "prev_is_null": int(row.prev_is_null),
            "root_full": int(h.root_full),
            "old_bound": nb_old, "old_holds": int(P <= nb_old),
            "old_slack": nb_old - P,
            "tight_h_R": hR_t, "tight_h_I": hI_t, "tight_bound": nb_tight,
            "tight_holds": int(P <= nb_tight), "tight_slack": nb_tight - P,
            "cons_h_R": h_R_prev, "cons_h_I": h_I, "cons_bound": nb_cons,
            "cons_holds": int(P <= nb_cons), "cons_slack": nb_cons - P,
        })
    checks = pd.DataFrame(check_rows)

    # Summary for official / reviewer cases
    def subset_fail(df, mask, col="old_holds"):
        s = df[mask]
        return int((s[col] == 0).sum()), len(s)

    official_mask = checks.apply(lambda r: (int(r.k), int(r.fanout)) in OFFICIAL, axis=1)

    summary = {
        "started_note": "MemoryStore only; no chain; keys in RAM only",
        "c_ell_B": c_ell,
        "c_ell_source": "len(dag_cbor.encode(CID)); equals children-array increment",
        "h_I_max_B": h_I,
        "h_I_by_fanout": {int(i): {"h_I_min": int(r.h_I_min), "h_I_max": int(r.h_I_max)} for i, r in h_I_by_b.iterrows()},
        "h_R_prev_max_B": h_R_prev,
        "h_R_null_max_B": h_R_null,
        "h_R_prev_range": {
            "min": int(headers.loc[headers.prev_is_null == 0, "h_R"].min()),
            "max": int(headers.loc[headers.prev_is_null == 0, "h_R"].max()),
        },
        "h_R_null_range": {
            "min": int(headers.loc[headers.prev_is_null == 1, "h_R"].min()),
            "max": int(headers.loc[headers.prev_is_null == 1, "h_R"].max()),
        },
        "manifest_by_update_B": {
            str(int(u)): {k: (int(v) if k != "mean" else float(v)) for k, v in r.items()}
            for u, r in man_by_u.iterrows()
        },
        "n_path_rows": int(len(checks)),
        "n_configs": int(headers.shape[0]),
        "old_bound_failures": int((checks.old_holds == 0).sum()),
        "old_bound_n": int(len(checks)),
        "tight_bound_failures": int((checks.tight_holds == 0).sum()),
        "cons_bound_failures": int((checks.cons_holds == 0).sum()),
        "reviewer_examples": {},
        "elapsed_s": None,
    }

    # Explicit reviewer examples (and official table points)
    examples = [
        (1000, 4, 4096, 2, "K=1000 b=4 4KiB later-round (Fig. 3a / proofsize_v3)"),
        (1000, 4, 424356, 1, "K=1000 b=4 424356-B round 1 (Table 7)"),
        (1000, 4, 424356, 2, "K=1000 b=4 424356-B later-round (clean timing)"),
        (1000, 16, 4096, 2, "K=1000 b=16 4KiB later-round"),
        (1000, 16, 424356, 1, "K=1000 b=16 424356-B round 1 (Table 7)"),
        (1000, 16, 424356, 2, "K=1000 b=16 424356-B later-round (clean timing)"),
        (1000, 1000, 4096, 2, "K=1000 flat 4KiB"),
        (100, 100, 4096, 2, "K=100 flat 4KiB"),
        (256, 16, 4096, 2, "K=256 b=16 4KiB"),
        (10, 4, 4096, 2, "K=10 b=4 Fig. 2"),
    ]
    for K, b, usize, rnd, note in examples:
        s = checks[(checks.k == K) & (checks.fanout == b) & (checks.update_B == usize) & (checks["round"] == rnd)]
        if s.empty:
            summary["reviewer_examples"][note] = "NOT MEASURED"
            continue
        worst = s.loc[s.path_B.idxmax()]
        summary["reviewer_examples"][note] = {
            "k": K, "fanout": b, "update_B": usize, "round": rnd,
            "depth": int(worst.depth),
            "path_mean_B": float(s.path_B.mean()),
            "path_max_B": int(s.path_B.max()),
            "manifest_min_B": int(s.manifest_B.min()),
            "manifest_max_B": int(s.manifest_B.max()),
            "old_bound_at_maxpath": int(worst.old_bound),
            "old_holds_all": bool(s.old_holds.all()),
            "tight_bound_at_maxpath": int(worst.tight_bound),
            "tight_holds_all": bool(s.tight_holds.all()),
            "cons_bound_at_maxpath": int(worst.cons_bound),
            "cons_holds_all": bool(s.cons_holds.all()),
            "root_full": int(worst.root_full),
            "n_paths": int(len(s)),
        }

    # b=e discussion: at K=1000, 4 KiB, later round, mean path vs b including b=3
    be = (
        paths[(paths.k == 1000) & (paths.update_B == 4096) & (paths["round"] == 2)]
        .groupby("fanout")["path_B"]
        .mean()
        .sort_index()
    )
    summary["mean_path_K1000_4KiB_round2_by_b"] = {int(i): float(v) for i, v in be.items()}
    summary["argmin_b_K1000_4KiB_round2"] = int(be.idxmin())
    summary["argmin_path_B"] = float(be.min())

    # Compare MemoryStore later-round / round-1 means to official tables (documentation only)
    def mean_path(K, b, usize, rnd):
        s = paths[(paths.k == K) & (paths.fanout == b) & (paths.update_B == usize) & (paths["round"] == rnd)]
        return None if s.empty else float(s.path_B.mean())

    summary["crosscheck_vs_official"] = {
        "proofsize_v3_K1000_b16_4KiB_mean_official": 1921.195,
        "this_K1000_b16_4KiB_r2_mean": mean_path(1000, 16, 4096, 2),
        "proofsize_v3_K1000_b4_4KiB_mean_official": 1325.24,
        "this_K1000_b4_4KiB_r2_mean": mean_path(1000, 4, 4096, 2),
        "proofsize_v3_K1000_flat_4KiB_max_official": 41413,
        "this_K1000_flat_4KiB_r2_max": None if paths[(paths.k == 1000) & (paths.fanout == 1000) & (paths.update_B == 4096) & (paths["round"] == 2)].empty
        else int(paths[(paths.k == 1000) & (paths.fanout == 1000) & (paths.update_B == 4096) & (paths["round"] == 2)].path_B.max()),
        "clean_K1000_b16_424356_mean_official": 2000.802,
        "this_K1000_b16_424356_r2_mean": mean_path(1000, 16, 424356, 2),
        "table7_K1000_b16_424356_mean_official": None,  # filled by caller from env if desired
        "this_K1000_b16_424356_r1_mean": mean_path(1000, 16, 424356, 1),
        "this_K1000_b4_424356_r1_mean": mean_path(1000, 4, 424356, 1),
        "this_K1000_b4_424356_r2_mean": mean_path(1000, 4, 424356, 2),
        "clean_run_dag_meta_K1000_b16_official": 327888,
        "this_dag_meta_K1000_b16_424356_r2": int(dags[(dags.k == 1000) & (dags.fanout == 16) & (dags.update_B == 424356) & (dags["round"] == 2)].dag_meta_B.iloc[0]),
        "this_dag_meta_K1000_b4_424356_r2": int(dags[(dags.k == 1000) & (dags.fanout == 4) & (dags.update_B == 424356) & (dags["round"] == 2)].dag_meta_B.iloc[0]),
        "this_dag_meta_K1000_b16_424356_r1": int(dags[(dags.k == 1000) & (dags.fanout == 16) & (dags.update_B == 424356) & (dags["round"] == 1)].dag_meta_B.iloc[0]),
        "this_dag_meta_K1000_b4_424356_r1": int(dags[(dags.k == 1000) & (dags.fanout == 4) & (dags.update_B == 424356) & (dags["round"] == 1)].dag_meta_B.iloc[0]),
        "note": "Small differences vs official CSVs are expected if n_j, signatures or global-node encoding differ; the bound check does not use official CSVs.",
    }

    summary["elapsed_s"] = round(time.time() - t0, 2)
    summary["formula"] = (
        "P_S3(K,b) <= |M_j| + h_R + d h_I + c_ell b (d+1), "
        "with d=max(0, ceil(log_b K)-1), c_ell the DAG-CBOR size of one CID link, "
        "h_R the non-children bytes of the root (including prev, global, ghash and CBOR headers), "
        "and h_I the non-children bytes of an inner node."
    )

    headers.to_csv(OUT / "headers.csv", index=False)
    paths.to_csv(OUT / "paths.csv", index=False)
    mans.to_csv(OUT / "manifests.csv", index=False)
    dags.to_csv(OUT / "dag_meta.csv", index=False)
    checks.to_csv(OUT / "bounds.csv", index=False)
    # Compact per-config table (mean/max path, bounds at max path)
    cfg = checks.groupby(["update_B", "k", "fanout", "round", "depth", "prev_is_null", "root_full"], as_index=False).agg(
        n=("path_B", "size"),
        path_mean_B=("path_B", "mean"),
        path_max_B=("path_B", "max"),
        manifest_min_B=("manifest_B", "min"),
        manifest_max_B=("manifest_B", "max"),
        old_holds=("old_holds", "min"),
        tight_holds=("tight_holds", "min"),
        cons_holds=("cons_holds", "min"),
        tight_bound_maxpath=("tight_bound", "max"),
        cons_bound_maxpath=("cons_bound", "max"),
        old_bound_maxpath=("old_bound", "max"),
    )
    cfg.to_csv(OUT / "configs.csv", index=False)

    with open(OUT / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    # Human-readable report
    lines = [
        "# Equation (1) bound measurement (Draft v5)",
        "",
        f"MemoryStore + canonical DAG-CBOR encoder; elapsed {summary['elapsed_s']} s.",
        f"CID link size c_ell = {c_ell} B.",
        f"Inner-node non-children header h_I (max observed) = {h_I} B; by fan-out: "
        + ", ".join(f"b={i}: {v['h_I_min']}–{v['h_I_max']}" for i, v in summary['h_I_by_fanout'].items()) + ".",
        f"Root non-children header h_R: {summary['h_R_null_range']['min']}–{summary['h_R_null_range']['max']} B when prev is null (round 1), "
        f"{summary['h_R_prev_range']['min']}–{summary['h_R_prev_range']['max']} B when prev is a CID (later rounds). "
        f"Conservative h_R used in the printed bound = {h_R_prev} B (max later-round header).",
        "",
        "Manifest |M_j| (bytes):",
    ]
    for u, r in man_by_u.iterrows():
        lines.append(f"  |U|={int(u)} B: min {int(r['min'])}, max {int(r['max'])}, mean {r['mean']:.2f}, {int(r['nunique'])} distinct sizes.")
    lines += [
        "",
        f"Repaired bound: {summary['formula']}",
        f"Paths checked: {summary['n_path_rows']} across {summary['n_configs']} (K,b,|U|,round) configs.",
        f"Old Eq. (1) failures: {summary['old_bound_failures']} / {summary['old_bound_n']}.",
        f"Tight new-bound failures (headers of the same DAG): {summary['tight_bound_failures']}.",
        f"Conservative new-bound failures (max h_R with prev, max h_I): {summary['cons_bound_failures']}.",
        "",
        "## Reviewer / official examples",
    ]
    for note, ev in summary["reviewer_examples"].items():
        if ev == "NOT MEASURED":
            lines.append(f"- {note}: NOT MEASURED")
            continue
        lines.append(
            f"- {note}: path mean {ev['path_mean_B']:.1f} B, max {ev['path_max_B']} B, "
            f"|M_j| {ev['manifest_min_B']}–{ev['manifest_max_B']} B, d={ev['depth']}, root_full={ev['root_full']}. "
            f"Old bound at max path {ev['old_bound_at_maxpath']} (holds_all={ev['old_holds_all']}); "
            f"tight {ev['tight_bound_at_maxpath']} (holds_all={ev['tight_holds_all']}); "
            f"conservative {ev['cons_bound_at_maxpath']} (holds_all={ev['cons_holds_all']})."
        )
    lines += [
        "",
        f"At K=1000, 4 KiB updates, later round, mean path is smallest at b={summary['argmin_b_K1000_4KiB_round2']} "
        f"({summary['argmin_path_B']:.1f} B). Values: "
        + ", ".join(f"b={b}: {v:.1f}" for b, v in summary["mean_path_K1000_4KiB_round2_by_b"].items()) + ".",
        "",
        "Cross-check vs official campaign means (informational; n_j/signatures may differ):",
        json.dumps(summary["crosscheck_vs_official"], indent=2),
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    if summary["tight_bound_failures"] or summary["cons_bound_failures"]:
        sys.exit("NEW BOUND FAILED on at least one measured path")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
