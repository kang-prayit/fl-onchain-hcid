"""Tamper tests T1-T6 against the last recorded round, including the S2-sig baseline and an n_j-only test."""
from __future__ import annotations
import copy, os, pickle

from eth_account import Account
from multiformats import CID

from . import chain
from .commitments import (ClientUpdate, sign_update, s2_build, s2sig_build, s3_build, s3_prove_and_verify,
                          put_blob, sha256, merkle_verify, merkle_leaf, merkle_leaf_signed, recover_signer)
from .ipld import encode_node, cid_digest32, CODEC_CBOR, CODEC_RAW, MemoryStore, decode_node, cid_from_digest
from .recorder import load_server_store, save_server_store


class TamperStore:
    """Malicious storage provider: serves substitute bytes for chosen CIDs, or withholds them."""

    def __init__(self, base, substitute=None, withhold=()):
        self.base, self.sub, self.withhold = base, {bytes(CID.decode(k) if isinstance(k, str) else k): v for k, v in (substitute or {}).items()}, {bytes(c) for c in withhold}

    def get(self, cid):
        if bytes(cid) in self.withhold:
            raise FileNotFoundError(f"block {cid} withheld")
        return self.sub.get(bytes(cid)) or self.base.get(cid)

    def put(self, data, codec):
        return self.base.put(data, codec)


def _manifest_cid(rec, rnd, victim, b):
    """Walk the genuine S3 path and return the manifest CID (last fetched block)."""
    dig = bytes(rec.c[("S3", "storage", b)].functions.rootOf(rnd).call())
    seen = []

    class Rec:
        def get(self, c):
            seen.append(c)
            return rec.store.get(c)

    s3_prove_and_verify(Rec(), dig, rnd, victim, victim, None, False)
    return seen[-1]


def run_tamper_tests(rec, rnd: int, victim: int = 3):
    out = []
    st = rec.state[rnd]; b = rec.fanouts[-1]
    desc = st["descs"][victim]
    first_chunk = desc["chunks"][0]
    orig = rec.store.get(first_chunk)
    flipped = bytes([orig[0] ^ 0x01]) + orig[1:]
    ghash = st["ghash"]
    has_s2sig = bool(rec.record_s2sig) and "s2sig_proofs" in st
    man_cid = _manifest_cid(rec, rnd, victim, b)

    def log(test, scheme, desc_, r):
        det = not r["ok"]
        out.append((test, scheme, det))
        rec.tam.row(run_id=rec.run_id, test=test, scheme=scheme, description=desc_, detected=det, reason=r.get("reason", ""))

    schemes_verify = [("S1", lambda **kw: rec.verify_s1(rnd, victim, **kw)),
                      ("S2", lambda **kw: rec.verify_s2(rnd, victim, **kw))]
    if has_s2sig:
        schemes_verify.append(("S2sig", lambda **kw: rec.verify_s2sig(rnd, victim, **kw)))
    schemes_verify.append(("S3", lambda **kw: rec.verify_s3(rnd, victim, b, **kw)))

    # T1: one byte of the victim's stored update is flipped by the storage provider
    ts = TamperStore(rec.store, substitute={first_chunk: flipped})
    d = "1 byte of client update flipped after commit"
    for name, fn in schemes_verify:
        log("T1", name, d, fn(store=ts) if name != "S3" else fn(store=ts))

    # T2: the victim's record is swapped for a forged one (different update) after commit
    forged = ClientUpdate(victim, 1, b"forged-update" * 100)
    forged.sig = sign_update(Account.create(), rnd, victim, 1, forged.h, ghash)
    srv = load_server_store(st["s2_file"])
    fd = put_blob(rec.store, forged.data)
    srv["descs"][victim] = fd
    f2 = st["s2_file"] + ".forged.json"
    save_server_store(f2, srv["proofs"], srv["descs"])
    d = "client record replaced by a forged update (index/manifest substituted)"
    log("T2", "S1", d, rec.verify_s1(rnd, victim, server_file=f2, indexed=False))
    log("T2", "S2", d, rec.verify_s2(rnd, victim, server_file=f2, indexed=False))
    if has_s2sig:
        srv_s = load_server_store(st["s2sig_file"])
        srv_s["descs"][victim] = fd
        f2s = st["s2sig_file"] + ".forged.json"
        save_server_store(f2s, srv_s["proofs"], srv_s["descs"])
        log("T2", "S2sig", d, rec.verify_s2sig(rnd, victim, server_file=f2s, indexed=False))
    genuine = s3_prove_and_verify(rec.store, bytes(rec.c[("S3", "storage", b)].functions.rootOf(rnd).call()),
                                  rnd, victim, victim, rec.registry, False)["manifest"]
    fake = dict(genuine); fake["update"] = fd
    fb, _ = encode_node(fake)
    log("T2", "S3", d, rec.verify_s3(rnd, victim, b, store=TamperStore(rec.store, substitute={man_cid: fb})))

    # T3a: aggregator silently omits the victim, commits a root without it; victim checks its own receipt
    ups_wo = [u for u in st["updates"] if u.client != victim]
    root_wo, proofs_wo = s2_build(rnd, ups_wo)
    ok2 = victim in proofs_wo
    log("T3a", "S2", "victim omitted from committed round; victim checks its receipt",
        dict(ok=ok2, reason="" if ok2 else "no inclusion path for victim"))
    if has_s2sig:
        _, proofs_wo_s = s2sig_build(rnd, ups_wo, ghash)
        ok2s = victim in proofs_wo_s
        log("T3a", "S2sig", "victim omitted from committed round; victim checks its receipt",
            dict(ok=ok2s, reason="" if ok2s else "no inclusion path for victim"))
    mem = MemoryStore()
    dag_wo = s3_build(mem, rnd, None, st["global_cid"], ups_wo, st["descs"], b, ghash=ghash)
    r = s3_prove_and_verify(TamperStore(mem), cid_digest32(dag_wo.root_cid), rnd, victim, victim, rec.registry, False)
    log("T3a", "S3", "victim omitted from committed round; victim checks its receipt", r)
    s1_wo = {u.client: u.h for u in ups_wo}
    ok1 = s1_wo.get(victim) == next(u.h for u in st["updates"] if u.client == victim)
    log("T3a", "S1", "victim omitted from committed round; victim checks its receipt",
        dict(ok=ok1, reason="" if ok1 else "no per-client hash committed for victim"))

    # T3b: aggregator injects a fake client (unregistered key) in place of the victim.
    # The forged update keeps the original n_j so this test isolates the unregistered key
    # (n_j-only alteration is T6).
    orig_u = next(u for u in st["updates"] if u.client == victim)
    fake_u = ClientUpdate(victim, orig_u.n_samples, b"sybil" * 200)
    fake_u.sig = sign_update(Account.create(), rnd, victim, fake_u.n_samples, fake_u.h, ghash)
    ups_inj = [fake_u if u.client == victim else u for u in st["updates"]]
    mem = MemoryStore()
    descs_inj = dict(st["descs"]); descs_inj[victim] = put_blob(mem, fake_u.data)
    dag_inj = s3_build(mem, rnd, None, st["global_cid"], ups_inj, descs_inj, b, ghash=ghash)
    r = s3_prove_and_verify(mem, cid_digest32(dag_inj.root_cid), rnd, victim, victim, rec.registry, True)
    log("T3b", "S3", "fake/unregistered client injected (signature in manifest checked)", r)
    if has_s2sig:
        root_inj_s, proofs_inj_s = s2sig_build(rnd, ups_inj, ghash)
        leaf = merkle_leaf_signed(rnd, victim, fake_u.n_samples, fake_u.h, ghash, fake_u.sig)
        okm = merkle_verify(root_inj_s, leaf, proofs_inj_s[victim]["proof"])
        reason = ""
        if okm:
            try:
                signer = recover_signer(rnd, victim, fake_u.n_samples, fake_u.h, ghash, fake_u.sig)
                if signer != rec.registry.get(victim):
                    okm, reason = False, "bad client signature"
            except Exception as e:
                okm, reason = False, f"sig recover failed: {type(e).__name__}"
        log("T3b", "S2sig", "fake/unregistered client injected (signed leaf checked against registry)",
            dict(ok=okm, reason=reason))
    root_inj, proofs_inj = s2_build(rnd, ups_inj)
    okm = merkle_verify(root_inj, merkle_leaf(rnd, victim, fake_u.h), proofs_inj[victim]["proof"])
    log("T3b", "S2", "fake client injected; unsigned leaf has no signature, so inclusion still verifies",
        dict(ok=okm, reason="not detected without a signature registry" if okm else ""))
    # S1 commits only the hash: a substituted hash of the same length is a well-formed S1 record,
    # so T3b is not applicable to S1 (no attribution check). Mark n/t by skipping, or record n/t.
    log("T3b", "S1", "S1 has no client signature; attribution of a substituted hash is not in scope",
        dict(ok=True, reason="n/t: S1 has no signature/registry check"))

    # T4: replay / rollback of an older round root. Compared only among contracts that enforce
    # r = lastRound + 1 (S2, S2-sig, S3, and a guarded S1 variant). The unguarded S1 used for
    # the gas table is not part of this comparison.
    for key, scheme in ((("S3", "storage", b), "S3"), (("S2", "storage"), "S2")):
        c = rec.c[key]
        try:
            chain.send(rec.w3, c.functions.commitStorage(1, b"\x11" * 32)); r = dict(ok=True, reason="replay accepted")
        except Exception as e:
            r = dict(ok=False, reason=f"reverted: {type(e).__name__}"[:120])
        log("T4", scheme, "re-commit round 1 (replay/rollback) on a guarded registry", r)
    if has_s2sig:
        c = rec.c[("S2sig", "storage")]
        try:
            chain.send(rec.w3, c.functions.commitStorage(1, b"\x11" * 32)); r = dict(ok=True, reason="replay accepted")
        except Exception as e:
            r = dict(ok=False, reason=f"reverted: {type(e).__name__}"[:120])
        log("T4", "S2sig", "re-commit round 1 (replay/rollback) on a guarded registry", r)
    # Guarded S1: first write a genuine round on the guarded contract, then try to replay round 1.
    cg = rec.c.get(("S1guard", "storage"))
    if cg is not None:
        # Bring the guarded registry up to the current round so a replay of round 1 is out of order.
        if int(cg.functions.lastRound().call()) == 0:
            for rr in range(1, rnd + 1):
                # one dummy write per prior round to bump lastRound
                chain.send(rec.w3, cg.functions.commitStorage(rr, 0, b"\x22" * 32))
        try:
            chain.send(rec.w3, cg.functions.commitStorage(1, 0, b"\x11" * 32))
            r = dict(ok=True, reason="replay accepted")
        except Exception as e:
            r = dict(ok=False, reason=f"reverted: {type(e).__name__}"[:120])
        log("T4", "S1-guard", "re-commit round 1 (replay/rollback) on a guarded S1 registry", r)

    # T5: data withholding (manifest block / proof store / locator unavailable)
    log("T5", "S3", "victim manifest block withheld by storage",
        rec.verify_s3(rnd, victim, b, store=TamperStore(rec.store, withhold=[man_cid])))
    log("T5", "S2", "server proof store withheld",
        rec.verify_s2(rnd, victim, server_file=st["s2_file"] + ".missing", indexed=False))
    if has_s2sig:
        log("T5", "S2sig", "server proof store withheld",
            rec.verify_s2sig(rnd, victim, server_file=st.get("s2sig_file", "") + ".missing", indexed=False))
    log("T5", "S1", "update blob withheld",
        rec.verify_s1(rnd, victim, store=TamperStore(rec.store, withhold=[first_chunk])))

    # T6: aggregator changes only n_j (FedAvg weight), leaving U_j and the original σ_j intact,
    # then rebuilds the commitment. Detected iff the signature binds n_j.
    new_n = orig_u.n_samples + 999
    ups_n = []
    for u in st["updates"]:
        if u.client == victim:
            u2 = ClientUpdate(u.client, new_n, u.data); u2.sig = u.sig  # original sig, new n
            ups_n.append(u2)
        else:
            ups_n.append(u)
    mem = MemoryStore()
    dag_n = s3_build(mem, rnd, None, st["global_cid"], ups_n, st["descs"], b, ghash=ghash)
    r = s3_prove_and_verify(mem, cid_digest32(dag_n.root_cid), rnd, victim, victim, rec.registry, True)
    log("T6", "S3", "aggregator rebuilds the DAG with only n_j changed (original signature)", r)
    if has_s2sig:
        root_n, proofs_n = s2sig_build(rnd, ups_n, ghash)
        # Auditor who is given the tampered n and the original sig: leaf may match the (tampered) root,
        # but the signature check against (r, j, n', h, ghash) must fail.
        ent = proofs_n[victim]
        leaf_ok = merkle_verify(root_n, ent["leaf"], ent["proof"])
        try:
            signer = recover_signer(rnd, victim, new_n, orig_u.h, ghash, orig_u.sig)
            sig_ok = signer == rec.registry.get(victim)
        except Exception:
            sig_ok = False
        ok = leaf_ok and sig_ok
        log("T6", "S2sig", "aggregator rebuilds a signed-leaf tree with only n_j changed (original signature)",
            dict(ok=ok, reason="" if ok else "signature does not cover the altered n_j"))
    # Unsigned S2 and S1 do not bind n_j, so T6 is not applicable.
    log("T6", "S2", "S2 leaf does not bind n_j; weight changes are outside its guarantee",
        dict(ok=True, reason="n/t: unsigned S2 does not record n_j"))
    log("T6", "S1", "S1 commits only sha256(U_j); weight changes are outside its guarantee",
        dict(ok=True, reason="n/t: S1 does not record n_j"))
    return out
