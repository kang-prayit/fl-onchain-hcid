"""v4 IPFS test on the *actual* FL-round DAG path (not random raw blocks).

Three private Kubo daemons on THIS box (same host, loopback swarm, empty bootstrap, mDNS off,
no public network):
  A = aggregator / provider : stores update chunks + DAG, pins every round root recursively
  B = auditor               : holds nothing; fetches the path over bitswap from its swarm peers
  C = replica pinner        : (phase 3 only) pins the same root recursively, i.e. a second custodian
A local Anvil chain (id 31337, Prague) holds one CidRootRegistry per fan-out; the auditor reads
rootOf(1) from the chain and walks committed root -> inner nodes -> manifest -> update chunks.

Phase 1 (cold path fetch): for each (K, b) and sampled client j, B fetches the metadata path only, then
          (after deleting what it fetched) the full path incl. chunks; every block re-hashed, signature
          recovered against the registry. Fetched blocks are removed from B after each trial (cold).
Phase 2 (T5, no reachable replica): C pins root (b=16), then C is stopped. A unpins and deletes the
          manifest or one update chunk of W sampled clients (withholding). B tries the path (timeout).
Phase 3 (T5, replica online): C is restarted from its repo and connected to B; B retries the same clients.

Results: results/ipfs_dagpath_v4/{trials.csv, withhold.csv, env.json, DONE}. Mid-run safe: writes only to
its own output dir and /tmp/flc-v4-*; a rerun wipes only those. Keys are generated in memory and never
written or printed. usage: .venv/bin/python scripts/run_ipfs_dagpath_v4.py [--ks 100,1000] [--force]
"""
import argparse, csv, json, os, shutil, statistics, subprocess, sys, time, types
import numpy as np
import requests
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from eth_account import Account
from fl_onchain import chain, services
from fl_onchain.ipld import KuboStore, cid_for, CODEC_RAW, CODEC_CBOR, encode_node, decode_node, cid_digest32
from fl_onchain.commitments import put_blob, sign_update, sha256, s3_build, s3_prove_and_verify, s3_child_index

ap = argparse.ArgumentParser()
ap.add_argument("--ks", default="100,1000"); ap.add_argument("--fanouts", default="16,4")
ap.add_argument("--size", type=int, default=424356); ap.add_argument("--audit-n", type=int, default=100)
ap.add_argument("--withhold-n", type=int, default=20); ap.add_argument("--timeout", type=float, default=5.0)
ap.add_argument("--max-load", type=float, default=4.0); ap.add_argument("--force", action="store_true")
ap.add_argument("--out", default=os.path.join(ROOT, "results", "ipfs_dagpath_v4"))
a = ap.parse_args()
IPFS = os.path.join(ROOT, "tools", "kubo", "ipfs")
PORTS = dict(A=(5491, 4031), B=(5492, 4032), C=(5493, 4033)); ANVIL = 8891
REPO = {p: f"/tmp/flc-v4-peer{p}" for p in "ABC"}

l1 = os.getloadavg()[0]
if l1 >= a.max_load and not a.force:
    sys.exit(f"load {l1:.2f} >= {a.max_load}; not measuring")
if os.path.exists(os.path.join(a.out, "DONE")) and not a.force:
    sys.exit("DONE exists; use --force to re-run (overwrites only this v4 dir)")
for f in ("trials.csv", "withhold.csv", "env.json", "DONE"):
    p = os.path.join(a.out, f)
    if os.path.exists(p): os.remove(p)
os.makedirs(a.out, exist_ok=True)


class Store(KuboStore):
    """KuboStore with a daemon-side timeout and a record of fetched CIDs (so B can be reset to cold)."""
    def __init__(self, api, timeout):
        super().__init__(api, timeout=timeout + 5); self.to = timeout; self.seen = []
    def get(self, cid):
        try:
            r = self.s.post(f"{self.api}/block/get", params={"arg": str(cid), "timeout": f"{self.to}s"},
                            timeout=self.to + 5)
        except requests.RequestException as e:
            raise FileNotFoundError(f"timeout/conn: {type(e).__name__}")
        if r.status_code != 200:
            raise FileNotFoundError(f"block/get {r.status_code}: {r.text[:120]}")
        self.seen.append(cid); return r.content
    def forget(self):
        for c in self.seen:
            try: self._post("block/rm", params={"arg": str(c), "force": "true"})
            except Exception: pass
        self.seen = []
    def unpin(self, cid):
        self._post("pin/rm", params={"arg": str(cid), "recursive": "true"})


def connect(src, dst):
    pid = services.kubo_peer_id(IPFS, REPO[dst])
    addr = f"/ip4/127.0.0.1/tcp/{PORTS[dst][1]}/p2p/{pid}"
    for i in range(20):
        try: services.kubo_swarm_connect(IPFS, REPO[src], addr); return
        except Exception:
            if i == 19: raise
            time.sleep(0.5)


def start(p, fresh=True):
    return services.start_kubo_private(REPO[p], os.path.join(a.out, f"peer{p}.log"), ipfs_bin=IPFS,
                                       api_port=PORTS[p][0], swarm_port=PORTS[p][1], fresh=fresh)


def q(xs, i):
    return round(statistics.quantiles(xs, n=4)[i], 2) if len(xs) >= 4 else None


tcsv = csv.DictWriter(open(os.path.join(a.out, "trials.csv"), "w", newline=""),
                      ["k", "fanout", "depth", "client", "phase", "ok", "t_ms", "n_meta_blocks", "meta_bytes",
                       "n_blocks_total", "bytes_total", "reason", "load1"]); tcsv.writer.writerow(tcsv.fieldnames)
wcsv = csv.DictWriter(open(os.path.join(a.out, "withhold.csv"), "w", newline=""),
                      ["k", "fanout", "client", "withheld", "replica", "ok", "detected", "t_ms", "reason"])
wcsv.writer.writerow(wcsv.fieldnames)
env = dict(tagged="ipfs_dagpath_v4", started=time.strftime("%Y-%m-%d %H:%M:%S %z"), load_at_start=round(l1, 2),
           hosts="ONE box: three Kubo daemons (A provider, B auditor, C replica) on loopback; NOT distinct hosts",
           public_network=False, bootstrap="empty", mdns=False, chain="local Anvil 31337 prague",
           update_bytes=a.size, timeout_s=a.timeout, configs=[], withhold=[])
procs = {}
anvil = None
t_start = time.time()
try:
    anvil = services.start_anvil(os.path.join(a.out, "anvil.log"), port=ANVIL)
    w3 = chain.connect(f"http://127.0.0.1:{ANVIL}")
    procs["A"] = start("A"); procs["B"] = start("B")
    connect("B", "A")
    A = Store(f"http://127.0.0.1:{PORTS['A'][0]}", a.timeout); B = Store(f"http://127.0.0.1:{PORTS['B'][0]}", a.timeout)
    fanouts = [int(x) for x in a.fanouts.split(",")]
    for K in [int(x) for x in a.ks.split(",")]:
        rng = np.random.default_rng(4000 + K)
        wallets = {c: Account.create() for c in range(K)}           # in memory only
        registry = {c: w.address for c, w in wallets.items()}
        gbytes = rng.bytes(a.size); ghash = sha256(gbytes)
        ups, descs = [], {}
        t0 = time.perf_counter()
        for c in range(K):
            data = rng.bytes(a.size); n = int(rng.integers(50, 500)); h = sha256(data)
            descs[c] = put_blob(A, data)
            ups.append(types.SimpleNamespace(client=c, n_samples=n, h=h,
                                             sig=sign_update(wallets[c], 1, c, n, h, ghash)))
        gdesc = put_blob(A, gbytes)
        gb, gcid = encode_node({"type": "fl-model", "round": 1, "blob": gdesc, "sha256": ghash}); A.put(gb, CODEC_CBOR)
        t_blob = time.perf_counter() - t0
        roots = {}
        for b in fanouts:
            t0 = time.perf_counter(); dag = s3_build(A, 1, None, gcid, ups, descs, b, ghash=ghash); tb = time.perf_counter() - t0
            t0 = time.perf_counter(); A.pin(dag.root_cid); tp = time.perf_counter() - t0
            reg = chain.deploy(w3, os.path.join(ROOT, "contracts"), "CidRootRegistry")[0]
            rc, _ = chain.send(w3, reg.functions.commitStorage(1, cid_digest32(dag.root_cid)))
            roots[b] = (dag, reg)
            env["configs"].append(dict(k=K, fanout=b, depth=dag.depth, n_meta_blocks=dag.n_blocks,
                                       dag_meta_bytes=dag.dag_bytes, t_build_ms=round(tb * 1e3, 1),
                                       t_pin_A_ms=round(tp * 1e3, 1), commit_gas=rc["gasUsed"],
                                       t_blob_put_ms=round(t_blob * 1e3, 1)))
        del wallets
        sample = list(range(K)) if K <= a.audit_n else sorted(rng.choice(K, a.audit_n, replace=False).tolist())
        # ---------------- phase 1: cold DAG-path fetch on B
        for b in fanouts:
            dag, reg = roots[b]
            for c in sample:
                for phase, full in (("meta", False), ("full", True)):
                    t0 = time.perf_counter()
                    dig = bytes(reg.functions.rootOf(1).call())
                    r = s3_prove_and_verify(B, dig, 1, c, c, registry, fetch_update=full)
                    dt = (time.perf_counter() - t0) * 1e3
                    nb = len(B.seen); tot = sum(len(A.get(x)) for x in B.seen) if False else None
                    tcsv.writerow(dict(k=K, fanout=b, depth=dag.depth, client=c, phase=phase, ok=int(r["ok"]),
                                       t_ms=round(dt, 3), n_meta_blocks=r["n_fetched"], meta_bytes=r["proof_bytes"],
                                       n_blocks_total=nb, bytes_total=(r["proof_bytes"] + (a.size if full else 0)),
                                       reason=r["reason"], load1=round(os.getloadavg()[0], 2)))
                    B.forget()
            print(f"K={K} b={b} phase1 done load={os.getloadavg()[0]:.2f}", flush=True)
        # ---------------- phase 2/3: withholding with / without a reachable replica (b=16 only)
        b = 16 if 16 in fanouts else fanouts[0]
        dag, reg = roots[b]
        procs["C"] = start("C"); connect("C", "A")
        Cst = Store(f"http://127.0.0.1:{PORTS['C'][0]}", 600)
        t0 = time.perf_counter(); Cst.pin(dag.root_cid); t_rep = time.perf_counter() - t0
        rs = requests.post(f"http://127.0.0.1:{PORTS['C'][0]}/api/v0/repo/stat", timeout=60).json()
        services.stop(procs.pop("C"))                     # replica goes offline (repo kept on disk)
        wh = sorted(rng.choice(K, a.withhold_n, replace=False).tolist())
        for bb in fanouts: A.unpin(roots[bb][0].root_cid)   # A stops pinning, then withholds blocks
        withheld = {}
        for i, c in enumerate(wh):
            root_node = decode_node(A.get(dag.root_cid)); node = root_node
            for lvl in range(dag.depth, 0, -1):
                node = decode_node(A.get(node["children"][s3_child_index(c, b, dag.depth, lvl)]))
            mcid = node["children"][c % b]
            if i % 2 == 0:
                tgt, kind = mcid, "manifest"
            else:
                tgt, kind = decode_node(A.get(mcid))["update"]["chunks"][0], "update-chunk"
            A.rm(tgt); withheld[c] = kind
        A.seen = []
        subprocess.run([IPFS, "repo", "gc"], env=dict(os.environ, IPFS_PATH=REPO["B"]),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for replica in ("offline", "online"):
            if replica == "online":
                procs["C"] = start("C", fresh=False); connect("B", "C")
            for c in wh:
                t0 = time.perf_counter()
                dig = bytes(reg.functions.rootOf(1).call())
                r = s3_prove_and_verify(B, dig, 1, c, c, registry, fetch_update=True)
                dt = (time.perf_counter() - t0) * 1e3
                wcsv.writerow(dict(k=K, fanout=b, client=c, withheld=withheld[c], replica=replica, ok=int(r["ok"]),
                                   detected=int(not r["ok"]), t_ms=round(dt, 1), reason=r["reason"]))
                B.forget()
        env["withhold"].append(dict(k=K, fanout=b, n=len(wh), replica_pin_ms=round(t_rep * 1e3, 1),
                                    replica_repo_bytes=rs.get("RepoSize"), replica_num_objects=rs.get("NumObjects")))
        services.stop(procs.pop("C")); shutil.rmtree(REPO["C"], ignore_errors=True)
        # reset A for the next K (fresh repo keeps disk use bounded)
        services.stop(procs.pop("A")); procs["A"] = start("A"); connect("B", "A")
        subprocess.run([IPFS, "repo", "gc"], env=dict(os.environ, IPFS_PATH=REPO["B"]),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"K={K} withholding done", flush=True)
    env["finished"] = time.strftime("%Y-%m-%d %H:%M:%S %z"); env["wall_s"] = round(time.time() - t_start, 1)
    env["load_at_end"] = round(os.getloadavg()[0], 2)
    json.dump(env, open(os.path.join(a.out, "env.json"), "w"), indent=1)
    open(os.path.join(a.out, "DONE"), "w").write(time.strftime("%F %T %Z"))
    print("DONE")
finally:
    services.stop(*procs.values(), anvil)
    for r in REPO.values(): shutil.rmtree(r, ignore_errors=True)
