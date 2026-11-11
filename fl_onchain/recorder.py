"""Per-round recording of the same client updates under every scheme/variant, plus audit and tamper tests."""
from __future__ import annotations
import csv, json, os, pickle, time
from dataclasses import dataclass

from eth_account import Account

from . import chain
from .commitments import (ClientUpdate, put_blob, fetch_blob, sign_update, recover_signer, s1_hashes,
                          s2_build, s2sig_build, merkle_leaf, merkle_leaf_signed, merkle_verify,
                          s3_build, s3_prove_and_verify, sha256, s2_proof_bytes, s2sig_proof_bytes)
from .ipld import encode_node, cid_digest32, CODEC_CBOR, cid_for
from . import proof_index

ms = lambda s: round(1000.0 * s, 3)

# Back-compat aliases used by tamper.py and older tests
save_server_store = proof_index.save_server_store
load_server_store = proof_index.load_server_store


class CsvLog:
    def __init__(self, path, fields):
        self.path, self.fields = path, fields
        new = not os.path.exists(path)
        self.f = open(path, "a", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=fields)
        if new:
            self.w.writeheader()

    def row(self, **kw):
        self.w.writerow({k: kw.get(k, "") for k in self.fields}); self.f.flush()


GAS_F = ["run_id", "round", "k", "scheme", "variant", "fanout", "n_tx", "gas_total", "calldata_bytes",
         "calldata_nonzero", "new_slots", "log_bytes", "onchain_bytes"]
LAT_F = ["run_id", "round", "k", "scheme", "variant", "fanout", "t_build_ms", "t_chain_ms", "t_total_ms",
         "t_blob_put_ms", "dag_blocks", "dag_meta_bytes", "root_block_bytes", "t_index_write_ms"]
VER_F = ["run_id", "round", "k", "scheme", "fanout", "client", "ok", "proof_bytes", "n_fetched",
         "t_verify_ms", "t_verify_nofetch_ms", "t_chain_ms", "t_index_ms", "t_fetch_ms", "t_check_ms",
         "reason"]
TAM_F = ["run_id", "test", "scheme", "description", "detected", "reason"]


class RoundRecorder:
    """Holds one Kubo store + one Anvil connection and a dedicated contract instance per scheme/variant,
    so that each variant pays first-write (cold, zero->non-zero) storage cost exactly once per slot."""

    def __init__(self, store, w3, contracts_dir, out_dir, run_id, k, fanouts=(None, 4), seed=0,
                 record_s2sig=True, deploy_s1_guard=True):
        self.store, self.w3, self.out, self.run_id, self.k = store, w3, out_dir, run_id, k
        self.fanouts = [k if f is None else int(f) for f in fanouts]
        self.record_s2sig = record_s2sig
        os.makedirs(out_dir, exist_ok=True)
        self.gas = CsvLog(os.path.join(out_dir, "gas.csv"), GAS_F)
        self.lat = CsvLog(os.path.join(out_dir, "latency.csv"), LAT_F)
        self.ver = CsvLog(os.path.join(out_dir, "verify.csv"), VER_F)
        self.tam = CsvLog(os.path.join(out_dir, "tamper.csv"), TAM_F)
        # simulated client wallets (in memory only; never printed or written to disk)
        self._wallets = {c: Account.create() for c in range(k)}
        self.registry = {c: a.address for c, a in self._wallets.items()}
        dep = lambda n: chain.deploy(w3, contracts_dir, n)[0]
        self.c = {("S1a", "storage"): dep("PerClientRegistry"), ("S1a", "event"): dep("PerClientRegistry"),
                  ("S1b", "storage"): dep("PerClientRegistry"), ("S1b", "event"): dep("PerClientRegistry"),
                  ("S2", "storage"): dep("MerkleRootRegistry"), ("S2", "event"): dep("MerkleRootRegistry")}
        if record_s2sig:
            self.c[("S2sig", "storage")] = dep("MerkleRootRegistry")
            self.c[("S2sig", "event")] = dep("MerkleRootRegistry")
        # Guarded S1 is used only for the T4 comparison; it is not on the official gas path.
        self.c[("S1guard", "storage")] = dep("PerClientRegistryGuarded") if deploy_s1_guard else None
        for b in self.fanouts:
            self.c[("S3", "storage", b)] = dep("CidRootRegistry")
            self.c[("S3", "event", b)] = dep("CidRootRegistry")
        self.prev_root = {b: None for b in self.fanouts}
        self.state = {}  # round -> dict(updates, blob_descs, s2_proofs, s3 roots, ghash)

    # ------------------------------------------------------------ recording
    def _gas_row(self, rnd, scheme, variant, fanout, rcs, new_slots):
        cd = [chain.calldata_stats(self.w3, rc["transactionHash"]) for rc in rcs]
        lb = sum(chain.log_bytes(rc) for rc in rcs)
        cdb = sum(x[0] for x in cd)
        self.gas.row(run_id=self.run_id, round=rnd, k=self.k, scheme=scheme, variant=variant, fanout=fanout,
                     n_tx=len(rcs), gas_total=sum(rc["gasUsed"] for rc in rcs), calldata_bytes=cdb,
                     calldata_nonzero=sum(x[1] for x in cd), new_slots=new_slots, log_bytes=lb,
                     onchain_bytes=cdb + 32 * new_slots + lb)

    def record_round(self, rnd: int, raw_updates: list[tuple[int, int, bytes]], global_bytes: bytes):
        """raw_updates: list of (client_id, n_samples, update_bytes)."""
        ghash = sha256(global_bytes)
        ups = []
        for cid_, n, data in raw_updates:
            u = ClientUpdate(cid_, n, data)
            u.sig = sign_update(self._wallets[cid_], rnd, cid_, n, u.h, ghash)
            ups.append(u)
        ups.sort(key=lambda u: u.client)
        # common to all schemes: update blobs and the global model go to IPFS as raw chunks
        t0 = time.perf_counter()
        descs = {u.client: put_blob(self.store, u.data) for u in ups}
        gdesc = put_blob(self.store, global_bytes)
        gb, gcid = encode_node({"type": "fl-model", "round": rnd, "blob": gdesc, "sha256": ghash})
        self.store.put(gb, CODEC_CBOR)
        t_blob = time.perf_counter() - t0
        st = {"updates": ups, "descs": descs, "global_cid": gcid, "ghash": ghash, "gdesc": gdesc}

        # S1a: one tx per client; S1b: one batched tx
        for variant in ("storage", "event"):
            t0 = time.perf_counter(); hs = s1_hashes(ups); tb = time.perf_counter() - t0
            c = self.c[("S1a", variant)]; rcs, tc = [], 0.0
            for u in ups:
                f = c.functions.commitStorage if variant == "storage" else c.functions.commitEvent
                rc, dt = chain.send(self.w3, f(rnd, u.client, u.h)); rcs.append(rc); tc += dt
            self._gas_row(rnd, "S1a", variant, "", rcs, len(ups) if variant == "storage" else 0)
            self.lat.row(run_id=self.run_id, round=rnd, k=self.k, scheme="S1a", variant=variant, t_build_ms=ms(tb),
                         t_chain_ms=ms(tc), t_total_ms=ms(tb + tc), t_blob_put_ms=ms(t_blob))
            c = self.c[("S1b", variant)]
            f = c.functions.commitBatchStorage if variant == "storage" else c.functions.commitBatchEvent
            rc, tc = chain.send(self.w3, f(rnd, hs))
            self._gas_row(rnd, "S1b", variant, "", [rc], len(ups) if variant == "storage" else 0)
            self.lat.row(run_id=self.run_id, round=rnd, k=self.k, scheme="S1b", variant=variant, t_build_ms=ms(tb),
                         t_chain_ms=ms(tc), t_total_ms=ms(tb + tc), t_blob_put_ms=ms(t_blob))

        # S2: unsigned Merkle root (server keeps the proof store)
        t0 = time.perf_counter(); root2, proofs = s2_build(rnd, ups); tb = time.perf_counter() - t0
        st["s2_root"], st["s2_proofs"] = root2, proofs
        for variant in ("storage", "event"):
            c = self.c[("S2", variant)]
            f = c.functions.commitStorage if variant == "storage" else c.functions.commitEvent
            rc, tc = chain.send(self.w3, f(rnd, root2))
            self._gas_row(rnd, "S2", variant, "", [rc], (1 if variant == "storage" else 0))
            self.lat.row(run_id=self.run_id, round=rnd, k=self.k, scheme="S2", variant=variant, t_build_ms=ms(tb),
                         t_chain_ms=ms(tc), t_total_ms=ms(tb + tc), t_blob_put_ms=ms(t_blob))

        # S2-sig: signed-leaf Merkle root (same on-chain shape as S2)
        s2sig_proofs = None
        if self.record_s2sig:
            t0 = time.perf_counter(); root2s, s2sig_proofs = s2sig_build(rnd, ups, ghash); tb = time.perf_counter() - t0
            st["s2sig_root"], st["s2sig_proofs"] = root2s, s2sig_proofs
            for variant in ("storage", "event"):
                c = self.c[("S2sig", variant)]
                f = c.functions.commitStorage if variant == "storage" else c.functions.commitEvent
                rc, tc = chain.send(self.w3, f(rnd, root2s))
                self._gas_row(rnd, "S2sig", variant, "", [rc], (1 if variant == "storage" else 0))
                self.lat.row(run_id=self.run_id, round=rnd, k=self.k, scheme="S2sig", variant=variant, t_build_ms=ms(tb),
                             t_chain_ms=ms(tc), t_total_ms=ms(tb + tc), t_blob_put_ms=ms(t_blob))

        # Indexed per-client proof/locator files (official verification path)
        t0 = time.perf_counter()
        proof_index.save_round_index(self.out, rnd, descs, proofs, s2sig_proofs)
        t_idx = time.perf_counter() - t0
        st["index_dir"] = os.path.join(self.out, "proof_index", f"round{rnd}")
        # also keep a bundled JSON for debugging / some tamper helpers (not used in official verify timing)
        pfile = os.path.join(self.out, "server_store", f"s2_round{rnd}.json")
        save_server_store(pfile, proofs, descs)
        st["s2_file"] = pfile
        if s2sig_proofs is not None:
            pfile_s = os.path.join(self.out, "server_store", f"s2sig_round{rnd}.json")
            save_server_store(pfile_s, s2sig_proofs, descs)
            st["s2sig_file"] = pfile_s

        # S3: hierarchical CID DAG, one per fan-out
        st["s3"] = {}
        for b in self.fanouts:
            t0 = time.perf_counter()
            dag = s3_build(self.store, rnd, self.prev_root[b], gcid, ups, descs, b, ghash=ghash)
            tb = time.perf_counter() - t0
            dig = cid_digest32(dag.root_cid)
            for variant in ("storage", "event"):
                c = self.c[("S3", variant, b)]
                f = c.functions.commitStorage if variant == "storage" else c.functions.commitEvent
                rc, tc = chain.send(self.w3, f(rnd, dig))
                self._gas_row(rnd, "S3", variant, b, [rc], (1 if variant == "storage" else 0))
                self.lat.row(run_id=self.run_id, round=rnd, k=self.k, scheme="S3", variant=variant, fanout=b,
                             t_build_ms=ms(tb), t_chain_ms=ms(tc), t_total_ms=ms(tb + tc), t_blob_put_ms=ms(t_blob),
                             dag_blocks=dag.n_blocks, dag_meta_bytes=dag.dag_bytes, root_block_bytes=dag.root_bytes,
                             t_index_write_ms=ms(t_idx))
            self.prev_root[b] = dag.root_cid
            st["s3"][b] = dag
        # note: lastRound is also bumped (non-zero->non-zero) inside the root registries; new_slots counts rootOf only
        self.state[rnd] = st
        for r_old in self.state:            # keep raw update bytes only for the latest round (memory)
            if r_old != rnd:
                self.state[r_old].pop("updates", None)
        return st

    # ------------------------------------------------------------ auditing
    def _onchain_s1(self, rnd, client):
        return bytes(self.c[("S1a", "storage")].functions.updateHash(rnd, client).call())

    def _onchain_root(self, key, rnd):
        return bytes(self.c[key].functions.rootOf(rnd).call())

    def verify_s1(self, rnd, client, store=None, server_file=None, indexed=True):
        """Official path (indexed=True) does a per-client locator lookup, not an O(K) JSON load.
        server_file is retained for tamper tests that substitute a forged bundled index."""
        store = store or self.store
        t0 = time.perf_counter()
        h = self._onchain_s1(rnd, client)
        t_chain = time.perf_counter()
        try:
            if server_file is not None:
                descs = load_server_store(server_file)["descs"]
                desc = descs[client]
            elif indexed:
                desc = proof_index.load_s1(self.out, rnd, client)
            else:
                desc = load_server_store(self.state[rnd]["s2_file"])["descs"][client]
            t_index = time.perf_counter()
            data = fetch_blob(store, desc)
            t_fetch = time.perf_counter()
            ok = sha256(data) == h
            reason = "" if ok else "hash mismatch"
        except (FileNotFoundError, ValueError, KeyError) as e:
            t_index = t_fetch = time.perf_counter()
            ok, reason = False, f"{type(e).__name__}: {e}"[:160]
        t2 = time.perf_counter()
        return dict(ok=ok, proof_bytes=32, n_fetched=0, t=t2 - t0, t_nofetch=t_index - t0, reason=reason,
                    t_chain=t_chain - t0, t_index=t_index - t_chain, t_fetch=t_fetch - t_index,
                    t_check=t2 - t_fetch)

    def _verify_merkle(self, rnd, client, signed: bool, store=None, server_file=None, indexed=True,
                       registry=True, client_hash=None):
        store = store or self.store
        scheme = "s2sig" if signed else "s2"
        key = ("S2sig", "storage") if signed else ("S2", "storage")
        t0 = time.perf_counter()
        root = self._onchain_root(key, rnd)
        t_chain = time.perf_counter()
        try:
            if server_file is not None:
                srv = load_server_store(server_file)
                ent, desc = srv["proofs"][client], srv["descs"][client]
            elif indexed:
                packed = proof_index.load_s2(self.out, rnd, client, scheme)
                ent, desc = packed["proof"], packed["desc"]
            else:
                srv = load_server_store(self.state[rnd]["s2sig_file" if signed else "s2_file"])
                ent, desc = srv["proofs"][client], srv["descs"][client]
            t_index = time.perf_counter()
            h = client_hash
            if h is None:
                h = sha256(fetch_blob(store, desc))
            t_fetch = time.perf_counter()
            if signed:
                ghash = ent.get("ghash") or self.state[rnd]["ghash"]
                n = ent.get("n")
                sig = ent.get("sig")
                leaf = merkle_leaf_signed(rnd, client, n, h, ghash, sig)
                ok = merkle_verify(root, leaf, ent["proof"])
                reason = "" if ok else "Merkle path does not reach on-chain root"
                if ok and registry:
                    signer = recover_signer(rnd, client, n, h, ghash, sig)
                    if signer != self.registry.get(client):
                        ok, reason = False, "bad client signature"
                pb = s2sig_proof_bytes(ent["proof"], sig)
            else:
                ok = merkle_verify(root, merkle_leaf(rnd, client, h), ent["proof"])
                reason = "" if ok else "Merkle path does not reach on-chain root"
                pb = s2_proof_bytes(ent["proof"])
        except (FileNotFoundError, ValueError, KeyError) as e:
            t_index = t_fetch = time.perf_counter()
            ok, reason, pb = False, f"{type(e).__name__}: {e}"[:160], 0
        t2 = time.perf_counter()
        return dict(ok=ok, proof_bytes=pb, n_fetched=0, t=t2 - t0,
                    t_nofetch=(t_index - t0) if client_hash is None else (t2 - t0), reason=reason,
                    t_chain=t_chain - t0, t_index=t_index - t_chain, t_fetch=t_fetch - t_index,
                    t_check=t2 - t_fetch)

    def verify_s2(self, rnd, client, client_hash=None, store=None, server_file=None, indexed=True):
        return self._verify_merkle(rnd, client, signed=False, store=store, server_file=server_file,
                                   indexed=indexed, client_hash=client_hash)

    def verify_s2sig(self, rnd, client, client_hash=None, store=None, server_file=None, indexed=True,
                     registry=True):
        return self._verify_merkle(rnd, client, signed=True, store=store, server_file=server_file,
                                   indexed=indexed, registry=registry, client_hash=client_hash)

    def verify_s3(self, rnd, client, fanout, store=None, registry=True, fetch_update=True):
        store = store or self.store
        t0 = time.perf_counter()
        dig = self._onchain_root(("S3", "storage", fanout), rnd)
        t_chain = time.perf_counter()
        r = s3_prove_and_verify(store, dig, rnd, client, client, self.registry if registry else None, fetch_update)
        t1 = time.perf_counter()
        r0 = s3_prove_and_verify(store, dig, rnd, client, client, self.registry if registry else None, False)
        t2 = time.perf_counter()
        return dict(ok=r["ok"], proof_bytes=r["proof_bytes"], n_fetched=r["n_fetched"], t=t1 - t0,
                    t_nofetch=t2 - t1, reason=r["reason"],
                    t_chain=t_chain - t0, t_index=0.0, t_fetch=t1 - t_chain, t_check=0.0)

    def _ver_row(self, rnd, scheme, fo, client, r):
        self.ver.row(run_id=self.run_id, round=rnd, k=self.k, scheme=scheme, fanout=fo, client=client, ok=r["ok"],
                     proof_bytes=r["proof_bytes"], n_fetched=r["n_fetched"], t_verify_ms=ms(r["t"]),
                     t_verify_nofetch_ms=ms(r["t_nofetch"]), t_chain_ms=ms(r.get("t_chain", 0)),
                     t_index_ms=ms(r.get("t_index", 0)), t_fetch_ms=ms(r.get("t_fetch", 0)),
                     t_check_ms=ms(r.get("t_check", 0)), reason=r["reason"])

    def audit_round(self, rnd, clients=None):
        clients = range(self.k) if clients is None else clients
        for c in clients:
            self._ver_row(rnd, "S1", "", c, self.verify_s1(rnd, c))
            self._ver_row(rnd, "S2", "", c, self.verify_s2(rnd, c))
            if self.record_s2sig:
                self._ver_row(rnd, "S2sig", "", c, self.verify_s2sig(rnd, c))
            for b in self.fanouts:
                self._ver_row(rnd, "S3", b, c, self.verify_s3(rnd, c, b))

    def export_car(self, path, mode="meta"):
        from .ipld import write_meta_car
        if mode == "full":
            return self.store.export_car(self.prev_root[self.fanouts[0]], path)
        roots = [self.prev_root[b] for b in self.fanouts]
        return write_meta_car(self.store, roots, path)[1]
