"""Same-box two-peer Kubo fetch test (private swarm, no public bootstrap / no public pin).

Starts two local Kubo daemons bound to 127.0.0.1, connects them over a loopback swarm,
pins a block on the provider, and measures fetch latency and failures on the auditor peer.
Hosts are NOT geographically distinct. Nothing is published to the public IPFS network.

usage: .venv/bin/python scripts/run_ipfs_twopeer.py
"""
import json, os, shutil, statistics, sys, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from fl_onchain import services
from fl_onchain.ipld import KuboStore, cid_for, CODEC_RAW, encode_node, CODEC_CBOR

OUT = os.path.join(ROOT, "results", "ipfs_twopeer_v3")
IPFS = os.path.join(ROOT, "tools", "kubo", "ipfs")
N = 30
SIZES = [1024, 4096, 256 * 1024, 424356]  # 1 KiB, 4 KiB, one raw block, model-sized update


def main():
    os.makedirs(OUT, exist_ok=True)
    repo_a, repo_b = "/tmp/flc-ipfs-peerA", "/tmp/flc-ipfs-peerB"
    procs = []
    started = time.strftime("%Y-%m-%d %H:%M:%S %z")
    load0 = os.getloadavg()
    try:
        procs.append(services.start_kubo_private(repo_a, os.path.join(OUT, "peerA.log"),
                                                 ipfs_bin=IPFS, api_port=5381, swarm_port=4011))
        procs.append(services.start_kubo_private(repo_b, os.path.join(OUT, "peerB.log"),
                                                 ipfs_bin=IPFS, api_port=5382, swarm_port=4012))
        id_a = services.kubo_peer_id(IPFS, repo_a)
        addr = f"/ip4/127.0.0.1/tcp/4011/p2p/{id_a}"
        # Retry connect a few times; daemons may need a moment after /version is up.
        for i in range(10):
            try:
                services.kubo_swarm_connect(IPFS, repo_b, addr)
                break
            except Exception as e:
                if i == 9:
                    raise
                time.sleep(0.5)
        provider = KuboStore("http://127.0.0.1:5381", timeout=30)
        auditor = KuboStore("http://127.0.0.1:5382", timeout=30)
        rows = []
        rng = os.urandom
        for size in SIZES:
            ok = 0
            lats = []
            reasons = []
            for i in range(N):
                data = rng(size)
                t0 = time.perf_counter()
                try:
                    cid = provider.put(data, CODEC_RAW)
                    provider.pin(cid)
                    got = auditor.get(cid)
                    dt = time.perf_counter() - t0
                    if got != data or cid_for(got, CODEC_RAW) != cid:
                        reasons.append("content/CID mismatch")
                    else:
                        ok += 1
                        lats.append(dt * 1000)
                except Exception as e:
                    dt = time.perf_counter() - t0
                    reasons.append(f"{type(e).__name__}: {e}"[:160])
            rec = dict(size=size, n=N, ok=ok, fail=N - ok,
                       t_ms_median=round(statistics.median(lats), 2) if lats else None,
                       t_ms_mean=round(statistics.fmean(lats), 2) if lats else None,
                       t_ms_p25=round(statistics.quantiles(lats, n=4)[0], 2) if len(lats) >= 4 else (round(min(lats), 2) if lats else None),
                       t_ms_p75=round(statistics.quantiles(lats, n=4)[2], 2) if len(lats) >= 4 else (round(max(lats), 2) if lats else None),
                       fail_reasons=reasons[:5])
            rows.append(rec)
            print(rec, flush=True)
        env = dict(tagged="ipfs_twopeer_v3", hosts="same box, two Kubo daemons, loopback swarm",
                   geographically_distinct=False, public_network=False, pinning="provider pins each block",
                   n_per_size=N, started=started, finished=time.strftime("%Y-%m-%d %H:%M:%S %z"),
                   load_at_start=[round(x, 2) for x in load0], rows=rows)
        json.dump(env, open(os.path.join(OUT, "env.json"), "w"), indent=1)
        open(os.path.join(OUT, "DONE"), "w").write(time.strftime("%F %T %Z"))
        print("DONE")
    finally:
        services.stop(*procs)
        shutil.rmtree(repo_a, ignore_errors=True)
        shutil.rmtree(repo_b, ignore_errors=True)


if __name__ == "__main__":
    main()
