"""Per-client indexed proof/locator service for S1/S2/S2-sig.

Each lookup reads one small JSON file (or one sqlite row). Verification must not
load an O(K) bundle. Bytes are hex-encoded; CIDs are stored as strings.
"""
from __future__ import annotations
import json, os, sqlite3
from multiformats import CID


def _dir(out, rnd, scheme) -> str:
    d = os.path.join(out, "proof_index", f"round{rnd}", scheme)
    os.makedirs(d, exist_ok=True)
    return d


def _desc_to_j(d):
    return {"chunks": [str(x) for x in d["chunks"]], "size": d["size"], "sha256": d["sha256"].hex(), "enc": d["enc"]}


def _desc_from_j(d):
    return {"chunks": [CID.decode(x) for x in d["chunks"]], "size": d["size"],
            "sha256": bytes.fromhex(d["sha256"]), "enc": d["enc"]}


def _proof_to_j(ent):
    j = {"index": ent["index"], "leaf": ent["leaf"].hex(),
         "proof": [[s.hex(), bool(l)] for s, l in ent["proof"]]}
    if "sig" in ent:
        j["sig"] = ent["sig"].hex()
        j["n"] = int(ent["n"])
        j["ghash"] = ent["ghash"].hex()
    return j


def _proof_from_j(e):
    out = {"index": e["index"], "leaf": bytes.fromhex(e["leaf"]),
           "proof": [(bytes.fromhex(s), l) for s, l in e["proof"]]}
    if "sig" in e:
        out["sig"] = bytes.fromhex(e["sig"])
        out["n"] = int(e["n"])
        out["ghash"] = bytes.fromhex(e["ghash"])
    return out


def save_round_index(out, rnd, descs, s2_proofs=None, s2sig_proofs=None):
    """Write one file per client for S1 locators and (if given) S2 / S2-sig proofs."""
    d1 = _dir(out, rnd, "s1")
    for c, desc in descs.items():
        with open(os.path.join(d1, f"{int(c)}.json"), "w") as f:
            json.dump(_desc_to_j(desc), f, separators=(",", ":"))
    if s2_proofs is not None:
        d2 = _dir(out, rnd, "s2")
        for c, ent in s2_proofs.items():
            with open(os.path.join(d2, f"{int(c)}.json"), "w") as f:
                json.dump({"proof": _proof_to_j(ent), "desc": _desc_to_j(descs[c])}, f, separators=(",", ":"))
    if s2sig_proofs is not None:
        d3 = _dir(out, rnd, "s2sig")
        for c, ent in s2sig_proofs.items():
            with open(os.path.join(d3, f"{int(c)}.json"), "w") as f:
                json.dump({"proof": _proof_to_j(ent), "desc": _desc_to_j(descs[c])}, f, separators=(",", ":"))


def load_s1(out, rnd, client) -> dict:
    p = os.path.join(out, "proof_index", f"round{rnd}", "s1", f"{int(client)}.json")
    with open(p) as f:
        return _desc_from_j(json.load(f))


def load_s2(out, rnd, client, scheme="s2") -> dict:
    p = os.path.join(out, "proof_index", f"round{rnd}", scheme, f"{int(client)}.json")
    with open(p) as f:
        j = json.load(f)
    return {"proof": _proof_from_j(j["proof"]), "desc": _desc_from_j(j["desc"])}


# Keep a thin wrapper used by older callers / tamper helpers that still want a whole-store path.
# These are not used on the official verification timing path.
def save_server_store(path, proofs, descs):
    j = {"proofs": {str(c): _proof_to_j(e) for c, e in proofs.items()},
         "descs": {str(c): _desc_to_j(d) for c, d in descs.items()}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(j, f)


def load_server_store(path):
    with open(path) as f:
        j = json.load(f)
    proofs = {int(c): _proof_from_j(e) for c, e in j["proofs"].items()}
    descs = {int(c): _desc_from_j(d) for c, d in j["descs"].items()}
    return {"proofs": proofs, "descs": descs}
