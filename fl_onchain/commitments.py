"""Three recording schemes for one FL round and their per-client inclusion proofs.

S1  per-client hash  : sha256(update) for every client goes on-chain (O(K) on-chain).
S2  Merkle root      : one 32-byte binary-Merkle root on-chain; the inclusion path is held by the server.
S2-sig               : same as S2, but each leaf binds (r, j, n_j, h_j, ghash, σ_j) and σ_j is checked
                       against the client registry (fair signed-leaf baseline).
S3  hierarchical CID : one CIDv1 (dag-cbor, sha2-256) root on-chain; the inclusion path *is* the chain of
                       IPLD blocks root -> inner nodes (fan-out b) -> client manifest -> raw update chunks,
                       so any auditor holding the root can fetch and re-hash it from IPFS.
"""
from __future__ import annotations
import hashlib, math, struct, time
from dataclasses import dataclass, field

from eth_account import Account
from eth_account.messages import encode_defunct

from .ipld import CODEC_CBOR, CODEC_RAW, cid_for, encode_node, decode_node, cid_digest32, cid_from_digest

CHUNK = 256 * 1024  # raw block size for update blobs

# Signature domain (FLC2): binds n_j and the global-model hash in addition to (r, j, SHA256(U_j)).
# Encoding: Ethereum personal_sign (EIP-191, encode_defunct) over
#   b"FLC2" || uint64_be(r) || uint32_be(j) || uint64_be(n_j) || sha256(U_j) || ghash
# Scheme: ECDSA over secp256k1; recover yields the 20-byte Ethereum address.
SIG_DOMAIN = b"FLC2"


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


# ---------------------------------------------------------------- blobs
def put_blob(store, data: bytes) -> dict:
    chunks = [store.put(data[i:i + CHUNK], CODEC_RAW) for i in range(0, len(data), CHUNK)] or [store.put(b"", CODEC_RAW)]
    return {"chunks": chunks, "size": len(data), "sha256": sha256(data), "enc": "f32le-sorted-keys"}


def fetch_blob(store, desc: dict) -> bytes:
    out = bytearray()
    for c in desc["chunks"]:
        b = store.get(c)
        if cid_for(b, CODEC_RAW) != c:
            raise ValueError(f"chunk {c} does not hash to its CID")
        out += b
    data = bytes(out)
    if len(data) != desc["size"] or sha256(data) != desc["sha256"]:
        raise ValueError("blob size/sha256 mismatch")
    return data


# ---------------------------------------------------------------- client signatures (receipts)
def _msg(round_: int, client: int, n_samples: int, h: bytes, ghash: bytes):
    if len(h) != 32 or len(ghash) != 32:
        raise ValueError("h and ghash must be 32-byte SHA-256 digests")
    return encode_defunct(primitive=SIG_DOMAIN + struct.pack(">QIQ", round_, client, n_samples) + h + ghash)


def sign_update(acct, round_: int, client: int, n_samples: int, h: bytes, ghash: bytes) -> bytes:
    return bytes(acct.sign_message(_msg(round_, client, n_samples, h, ghash)).signature)


def recover_signer(round_: int, client: int, n_samples: int, h: bytes, ghash: bytes, sig: bytes) -> str:
    return Account.recover_message(_msg(round_, client, n_samples, h, ghash), signature=sig)


@dataclass
class ClientUpdate:
    client: int
    n_samples: int
    data: bytes
    sig: bytes = b""
    h: bytes = field(init=False)

    def __post_init__(self):
        self.h = sha256(self.data)


# ---------------------------------------------------------------- S1
def s1_hashes(updates: list[ClientUpdate]) -> list[bytes]:
    return [u.h for u in sorted(updates, key=lambda u: u.client)]


# ---------------------------------------------------------------- S2 (binary Merkle, RFC6962-style domain separation)
def merkle_leaf(round_: int, client: int, h: bytes) -> bytes:
    return sha256(b"\x00" + struct.pack(">QI", round_, client) + h)


def merkle_leaf_signed(round_: int, client: int, n_samples: int, h: bytes, ghash: bytes, sig: bytes) -> bytes:
    """S2-sig leaf: binds the signed payload (r, j, n_j, h, ghash) and the signature itself."""
    return sha256(b"\x00" + struct.pack(">QIQ", round_, client, n_samples) + h + ghash + sig)


def _node(a: bytes, b: bytes) -> bytes:
    return sha256(b"\x01" + a + b)


class MerkleTree:
    def __init__(self, leaves: list[bytes]):
        self.levels = [list(leaves)]
        while len(self.levels[-1]) > 1:
            cur, nxt = self.levels[-1], []
            for i in range(0, len(cur), 2):
                nxt.append(_node(cur[i], cur[i + 1]) if i + 1 < len(cur) else cur[i])  # odd node promoted
            self.levels.append(nxt)

    @property
    def root(self) -> bytes:
        return self.levels[-1][0]

    def proof(self, idx: int) -> list[tuple[bytes, bool]]:
        """List of (sibling, sibling_is_left)."""
        out = []
        for lvl in self.levels[:-1]:
            sib = idx ^ 1
            if sib < len(lvl):
                out.append((lvl[sib], sib < idx))
            idx //= 2
        return out


def merkle_verify(root: bytes, leaf: bytes, proof: list[tuple[bytes, bool]]) -> bool:
    x = leaf
    for sib, left in proof:
        x = _node(sib, x) if left else _node(x, sib)
    return x == root


def _ordered(updates: list[ClientUpdate]) -> list[ClientUpdate]:
    """Canonical client order: strictly increasing registered client ID. Position equals ID only
    when IDs are 0..K-1 with full participation (the experimental case)."""
    ups = sorted(updates, key=lambda u: u.client)
    ids = [u.client for u in ups]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate client IDs")
    return ups


def s2_build(round_: int, updates: list[ClientUpdate]):
    ups = _ordered(updates)
    leaves = [merkle_leaf(round_, u.client, u.h) for u in ups]
    t = MerkleTree(leaves)
    proof_store = {u.client: {"index": i, "leaf": leaves[i], "proof": t.proof(i)} for i, u in enumerate(ups)}
    return t.root, proof_store


def s2sig_build(round_: int, updates: list[ClientUpdate], ghash: bytes):
    ups = _ordered(updates)
    leaves = [merkle_leaf_signed(round_, u.client, u.n_samples, u.h, ghash, u.sig) for u in ups]
    t = MerkleTree(leaves)
    proof_store = {u.client: {"index": i, "leaf": leaves[i], "proof": t.proof(i),
                              "sig": u.sig, "n": u.n_samples, "ghash": ghash} for i, u in enumerate(ups)}
    return t.root, proof_store


# S2 proof-size accounting: 32 bytes per sibling plus a 4-byte leaf index.
S2_INDEX_BYTES = 4


def s2_proof_bytes(proof) -> int:
    return 32 * len(proof) + S2_INDEX_BYTES


def s2sig_proof_bytes(proof, sig: bytes) -> int:
    """Path + 4-B index + signature + uint64 n_j + 32-B ghash."""
    return s2_proof_bytes(proof) + len(sig) + 8 + 32


# ---------------------------------------------------------------- S3 (hierarchical CID DAG)
@dataclass
class RoundDag:
    root_cid: object
    root_bytes: int
    n_blocks: int
    dag_bytes: int          # metadata blocks only (root + inner + manifests)
    depth: int
    fanout: int


def s3_build(store, round_: int, prev_root, global_desc_cid, updates: list[ClientUpdate], blob_descs: dict,
             fanout: int, ghash: bytes | None = None) -> RoundDag:
    ups = _ordered(updates)
    n_blocks, dag_bytes = 0, 0
    level = []
    for u in ups:
        man = {"type": "fl-client", "v": 2, "round": round_, "client": u.client, "n": u.n_samples,
               "update": blob_descs[u.client], "sig": u.sig}
        b, c = encode_node(man)
        store.put(b, CODEC_CBOR); level.append(c); n_blocks += 1; dag_bytes += len(b)
    depth = 0
    while len(level) > fanout:
        nxt = []
        for i in range(0, len(level), fanout):
            b, c = encode_node({"type": "fl-inner", "children": level[i:i + fanout]})
            store.put(b, CODEC_CBOR); nxt.append(c); n_blocks += 1; dag_bytes += len(b)
        level = nxt; depth += 1
    root = {"type": "fl-round", "v": 2, "round": round_, "prev": prev_root, "global": global_desc_cid,
            "k": len(ups), "fanout": fanout, "depth": depth, "children": level}
    if ghash is not None:
        root["ghash"] = ghash
    # Canonical client order is increasing registered ID; the i-th child slot is the i-th ID in that order.
    root["order"] = "client-id-asc"
    b, c = encode_node(root)
    store.put(b, CODEC_CBOR); n_blocks += 1; dag_bytes += len(b)
    return RoundDag(c, len(b), n_blocks, dag_bytes, depth, fanout)


def s3_child_index(client_pos: int, fanout: int, depth: int, level: int) -> int:
    """Index into children of the node at `level` (depth..1) on the path to client_pos.
    At the top (level == depth) the root may have fewer than `fanout` children, so no mod.
    Below that, take floor(j / b^level) mod b."""
    if level == depth:
        return client_pos // (fanout ** level)
    return (client_pos // (fanout ** level)) % fanout


def s3_prove_and_verify(store, onchain_digest: bytes, round_: int, client_pos: int, client_id: int,
                        registry: dict | None = None, fetch_update: bool = True) -> dict:
    """Auditor side: start from the 32-byte on-chain digest, fetch every block from the store and re-hash it.
    Returns dict(ok, proof_bytes, n_fetched, reason).

    Under SHA-256 collision resistance and EUF-CMA of the ECDSA signatures, an honest run accepts M at
    position j only if every fetched block hashes to its CID, M.client equals the expected ID at that
    position, M.round equals r, Recover(σ, (r, j, n, SHA256(U), ghash)) equals R[j], and the update
    bytes hash to the committed SHA-256. Omission of a client is detectable by that client (or by a
    third party that knows the expected participant set). Aggregation correctness of FedAvg is not
    re-computed; n_j and ghash are bound in σ_j so an altered weight or swapped global model fails
    the signature check. Availability is not guaranteed: a withheld block makes the check fail.
    """
    nbytes, nfetch = 0, 0
    root_cid = cid_from_digest(onchain_digest, CODEC_CBOR)

    def fetch(c):
        nonlocal nbytes, nfetch
        b = store.get(c)
        if cid_for(b, CODEC_CBOR) != c:
            raise ValueError(f"block {c} does not hash to its CID")
        nbytes += len(b); nfetch += 1
        return decode_node(b)

    try:
        root = fetch(root_cid)
        if root.get("type") != "fl-round" or root["round"] != round_:
            return dict(ok=False, proof_bytes=nbytes, n_fetched=nfetch, reason="root round mismatch")
        b, d = root["fanout"], root["depth"]
        if client_pos >= root["k"]:
            return dict(ok=False, proof_bytes=nbytes, n_fetched=nfetch, reason="client not in round")
        node = root
        for lvl in range(d, 0, -1):
            node = fetch(node["children"][s3_child_index(client_pos, b, d, lvl)])
        man = fetch(node["children"][client_pos % b])
        if man.get("type") != "fl-client" or man["client"] != client_id or man["round"] != round_:
            return dict(ok=False, proof_bytes=nbytes, n_fetched=nfetch, reason="manifest id/round mismatch")
        if registry is not None:
            ghash = root.get("ghash")
            if ghash is None:
                return dict(ok=False, proof_bytes=nbytes, n_fetched=nfetch, reason="root missing ghash")
            signer = recover_signer(round_, client_id, man["n"], man["update"]["sha256"], ghash, man["sig"])
            if signer != registry.get(client_id):
                return dict(ok=False, proof_bytes=nbytes, n_fetched=nfetch, reason="bad client signature")
        if fetch_update:
            fetch_blob(store, man["update"])
        return dict(ok=True, proof_bytes=nbytes, n_fetched=nfetch, reason="", manifest=man)
    except (FileNotFoundError, ValueError, KeyError, IndexError) as e:
        return dict(ok=False, proof_bytes=nbytes, n_fetched=nfetch, reason=f"{type(e).__name__}: {e}"[:160])
