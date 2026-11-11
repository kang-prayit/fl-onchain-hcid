"""Off-chain S3/S2/S2-sig proof-size sweep for Fig. 4a (MemoryStore, no chain, no Kubo).

Uses the v3 DAG schema (ghash in the root, n_j bound in signatures). Does not overwrite
v2 synthetic CSVs. 4-KiB updates, 1 round, K in {100,500,1000}, b in {2,4,8,16,32,flat}.
"""
import csv, os, sys
import numpy as np
from eth_account import Account
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from fl_onchain.commitments import (ClientUpdate, sign_update, sha256, s2_build, s2sig_build,
                                    s3_build, s3_prove_and_verify, put_blob, s2_proof_bytes,
                                    s2sig_proof_bytes)
from fl_onchain.ipld import MemoryStore, encode_node, cid_digest32, CODEC_CBOR

OUT = os.path.join(ROOT, "results", "proofsize_v3")
KS = [100, 500, 1000]
BS = [2, 4, 8, 16, 32, "flat"]
SIZE = 4096
N_SAMPLE = 200  # clients measured per (K,b); all if K<=200


def main():
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "proof_bytes.csv")
    f = open(path, "w", newline=""); w = csv.DictWriter(f, fieldnames=[
        "k", "scheme", "fanout", "client", "proof_bytes", "n_fetched"])
    w.writeheader()
    for k in KS:
        rng = np.random.default_rng(3000 + k)
        gbytes = rng.bytes(SIZE); ghash = sha256(gbytes)
        ws = {c: Account.create() for c in range(k)}
        ups = []
        for c in range(k):
            u = ClientUpdate(c, int(rng.integers(50, 500)), rng.bytes(SIZE))
            u.sig = sign_update(ws[c], 1, c, u.n_samples, u.h, ghash)
            ups.append(u)
        root2, ps2 = s2_build(1, ups)
        root2s, ps2s = s2sig_build(1, ups, ghash)
        clients = list(range(k)) if k <= N_SAMPLE else sorted(rng.choice(k, N_SAMPLE, replace=False).tolist())
        for c in clients:
            w.writerow(dict(k=k, scheme="S2", fanout="", client=c,
                            proof_bytes=s2_proof_bytes(ps2[c]["proof"]), n_fetched=0))
            w.writerow(dict(k=k, scheme="S2sig", fanout="", client=c,
                            proof_bytes=s2sig_proof_bytes(ps2s[c]["proof"], ps2s[c]["sig"]), n_fetched=0))
            w.writerow(dict(k=k, scheme="S1", fanout="", client=c, proof_bytes=32, n_fetched=0))
        for b in BS:
            fan = k if b == "flat" else int(b)
            st = MemoryStore()
            descs = {u.client: put_blob(st, u.data) for u in ups}
            gb, gc = encode_node({"type": "fl-model", "round": 1, "sha256": ghash})
            st.put(gb, CODEC_CBOR)
            dag = s3_build(st, 1, None, gc, ups, descs, fan, ghash=ghash)
            dig = cid_digest32(dag.root_cid)
            for c in clients:
                r = s3_prove_and_verify(st, dig, 1, c, c, None, False)
                assert r["ok"], r
                w.writerow(dict(k=k, scheme="S3", fanout=fan, client=c,
                                proof_bytes=r["proof_bytes"], n_fetched=r["n_fetched"]))
            print("K", k, "b", b, "root_B", dag.root_bytes, "dag_B", dag.dag_bytes, "depth", dag.depth, flush=True)
        del ups, ws, st
    f.close()
    open(os.path.join(OUT, "DONE"), "w").write(__import__("time").strftime("%F %T %Z"))
    print("wrote", path)

if __name__ == "__main__":
    main()
