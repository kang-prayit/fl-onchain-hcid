"""Minimal IPLD helpers: CIDv1 (sha2-256) for raw and dag-cbor blocks, plus block stores.

Blocks are encoded locally with `dag-cbor` (canonical DAG-CBOR) so every CID can be recomputed by an
auditor without trusting Kubo. KuboStore pushes exactly these bytes with `block/put`, and
`check_kubo_roundtrip` confirms that Kubo's own `dag/put` produces the same CID.
"""
from __future__ import annotations
import hashlib, json, os, time
import dag_cbor
import requests
from multiformats import CID, multihash

CODEC_RAW = "raw"
CODEC_CBOR = "dag-cbor"
# CIDv1 prefix bytes: version 0x01, codec (0x71 dag-cbor | 0x55 raw), multihash sha2-256 0x12, len 0x20
PREFIX = {CODEC_CBOR: bytes([0x01, 0x71, 0x12, 0x20]), CODEC_RAW: bytes([0x01, 0x55, 0x12, 0x20])}


def cid_for(data: bytes, codec: str) -> CID:
    return CID("base32", 1, codec, multihash.digest(data, "sha2-256"))


def cid_digest32(cid: CID) -> bytes:
    """32-byte sha2-256 digest of a CIDv1 (what goes on-chain; the 4-byte prefix is a contract constant)."""
    raw = bytes(cid)
    assert raw[:2] in (PREFIX[CODEC_CBOR][:2], PREFIX[CODEC_RAW][:2]) and raw[2:4] == b"\x12\x20", raw[:4].hex()
    return raw[4:]


def cid_from_digest(digest: bytes, codec: str = CODEC_CBOR) -> CID:
    return CID.decode(PREFIX[codec] + digest).set(base="base32")


def encode_node(obj) -> tuple[bytes, CID]:
    b = dag_cbor.encode(obj)
    return b, cid_for(b, CODEC_CBOR)


def decode_node(b: bytes):
    return dag_cbor.decode(b)


class MemoryStore:
    """In-process block store (unit tests; also used as the S2 'server-held' proof store analogue)."""

    def __init__(self):
        self.blocks: dict[str, bytes] = {}

    def put(self, data: bytes, codec: str) -> CID:
        c = cid_for(data, codec)
        self.blocks[bytes(c)] = data
        return c

    def get(self, cid) -> bytes:
        try:
            return self.blocks[bytes(cid)]
        except KeyError:
            raise FileNotFoundError(f"block {cid} not available")

    def rm(self, cid):
        self.blocks.pop(bytes(cid), None)


class KuboStore:
    """Kubo HTTP RPC (daemon started with --offline). Only local blocks are served."""

    def __init__(self, api: str = "http://127.0.0.1:5001", timeout: float = 30.0):
        self.api = api.rstrip("/") + "/api/v0"
        self.s = requests.Session()
        self.timeout = timeout

    def _post(self, ep, params=None, files=None, stream=False):
        r = self.s.post(f"{self.api}/{ep}", params=params, files=files, timeout=self.timeout, stream=stream)
        if r.status_code != 200:
            raise FileNotFoundError(f"kubo {ep} {r.status_code}: {r.text[:200]}")
        return r

    def version(self):
        return self._post("version").json()["Version"]

    def put(self, data: bytes, codec: str) -> CID:
        r = self._post("block/put", params={"cid-codec": codec, "mhtype": "sha2-256", "pin": "false"},
                       files={"file": ("b", data)})
        c = CID.decode(r.json()["Key"])
        exp = cid_for(data, codec)
        if c != exp:
            raise RuntimeError(f"Kubo CID {c} != local {exp}")
        return c

    def get(self, cid) -> bytes:
        return self._post("block/get", params={"arg": str(cid)}).content

    def rm(self, cid):
        self._post("block/rm", params={"arg": str(cid), "force": "true"})

    def pin(self, cid):
        self._post("pin/add", params={"arg": str(cid), "recursive": "true"}, )

    def export_car(self, cid, path: str) -> int:
        r = self._post("dag/export", params={"arg": str(cid)}, stream=True)
        n = 0
        with open(path, "wb") as f:
            for ch in r.iter_content(1 << 16):
                f.write(ch); n += len(ch)
        return n

    def dag_put_cbor(self, data: bytes) -> CID:
        """Let Kubo decode+re-encode the node; used to cross-check canonical encoding."""
        r = self._post("dag/put", params={"store-codec": "dag-cbor", "input-codec": "dag-cbor", "hash": "sha2-256"},
                       files={"file": ("n", data)})
        return CID.decode(r.json()["Cid"]["/"])


def check_kubo_roundtrip(store: KuboStore, obj) -> bool:
    b, c = encode_node(obj)
    return store.dag_put_cbor(b) == c


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _links(obj):
    if isinstance(obj, CID):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _links(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _links(v)


def write_meta_car(store, roots, path: str) -> tuple[int, int]:
    """CARv1 with every dag-cbor block reachable from `roots` (raw update chunks are NOT included).
    Enough to re-verify every root/inner/manifest CID and every round link offline."""
    seen, order, stack = set(), [], list(roots)
    while stack:
        c = stack.pop()
        if bytes(c) in seen or c.codec.name != CODEC_CBOR:
            continue
        seen.add(bytes(c)); b = store.get(c); order.append((c, b))
        stack.extend(_links(dag_cbor.decode(b)))
    hdr = dag_cbor.encode({"version": 1, "roots": list(roots)})
    n = 0
    with open(path, "wb") as f:
        f.write(_varint(len(hdr)) + hdr); n += len(hdr)
        for c, b in order:
            cb = bytes(c); f.write(_varint(len(cb) + len(b)) + cb + b); n += len(b)
    return len(order), os.path.getsize(path)
