"""Start/stop a local Anvil node and an offline Kubo daemon as subprocesses (box or Colab)."""
from __future__ import annotations
import os, shutil, subprocess, time
import requests


def _wait(url, payload=None, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.post(url, json=payload, timeout=2) if payload else requests.post(url, timeout=2)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"service at {url} did not come up")


def start_anvil(log_path, port=8545, hardfork="prague", anvil_bin="anvil"):
    # --silent keeps Anvil from printing its well-known dev keys into the log
    f = open(log_path, "w")
    p = subprocess.Popen([anvil_bin, "--port", str(port), "--hardfork", hardfork, "--silent",
                          "--host", "127.0.0.1"], stdout=f, stderr=subprocess.STDOUT)
    _wait(f"http://127.0.0.1:{port}", {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []})
    return p


def start_kubo(repo, log_path, ipfs_bin="ipfs", api_port=5001, fresh=True):
    env = dict(os.environ, IPFS_PATH=repo)
    if fresh and os.path.exists(repo):
        shutil.rmtree(repo)
    if not os.path.exists(os.path.join(repo, "config")):
        subprocess.run([ipfs_bin, "init", "--empty-repo", "--profile", "test"], env=env, check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run([ipfs_bin, "config", "Addresses.API", f"/ip4/127.0.0.1/tcp/{api_port}"], env=env, check=True)
        subprocess.run([ipfs_bin, "config", "Addresses.Gateway", "/ip4/127.0.0.1/tcp/0"], env=env, check=True)
    f = open(log_path, "w")
    p = subprocess.Popen([ipfs_bin, "daemon", "--offline"], env=env, stdout=f, stderr=subprocess.STDOUT)
    _wait(f"http://127.0.0.1:{api_port}/api/v0/version")
    return p


def stop(*procs):
    for p in procs:
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


def start_kubo_private(repo, log_path, ipfs_bin="ipfs", api_port=5001, swarm_port=4001, fresh=True):
    """Local-only Kubo *without* public bootstrap or mDNS, so two peers on this box can
    fetch from each other without publishing to the public IPFS network."""
    env = dict(os.environ, IPFS_PATH=repo)
    if fresh and os.path.exists(repo):
        shutil.rmtree(repo)
    if not os.path.exists(os.path.join(repo, "config")):
        subprocess.run([ipfs_bin, "init", "--empty-repo", "--profile", "test"], env=env, check=True,
                       stdout=subprocess.DEVNULL)
    # Drop any bootstrap list the test profile may have left, bind swarm to loopback, disable mDNS.
    subprocess.run([ipfs_bin, "config", "Bootstrap", "--json", "[]"], env=env, check=True)
    subprocess.run([ipfs_bin, "config", "Addresses.API", f"/ip4/127.0.0.1/tcp/{api_port}"], env=env, check=True)
    subprocess.run([ipfs_bin, "config", "Addresses.Gateway", "/ip4/127.0.0.1/tcp/0"], env=env, check=True)
    swarm = [f"/ip4/127.0.0.1/tcp/{swarm_port}"]
    subprocess.run([ipfs_bin, "config", "Addresses.Swarm", "--json", __import__("json").dumps(swarm)],
                   env=env, check=True)
    subprocess.run([ipfs_bin, "config", "Discovery.MDNS.Enabled", "--json", "false"], env=env, check=True)
    subprocess.run([ipfs_bin, "config", "Swarm.DisableNatPortMap", "--json", "true"], env=env, check=True)
    f = open(log_path, "w")
    # Not --offline: peers must be able to fetch over the local swarm. Bootstrap is empty.
    p = subprocess.Popen([ipfs_bin, "daemon"], env=env, stdout=f, stderr=subprocess.STDOUT)
    _wait(f"http://127.0.0.1:{api_port}/api/v0/version")
    return p


def kubo_peer_id(ipfs_bin, repo) -> str:
    env = dict(os.environ, IPFS_PATH=repo)
    out = subprocess.check_output([ipfs_bin, "id", "-f", "<id>"], env=env, text=True).strip()
    return out


def kubo_swarm_connect(ipfs_bin, repo, multiaddr: str):
    env = dict(os.environ, IPFS_PATH=repo)
    subprocess.run([ipfs_bin, "swarm", "connect", multiaddr], env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
