"""web3 helpers for a *local Anvil* chain only. Uses Anvil's unlocked dev accounts via eth_sendTransaction,
so no private key is ever read, printed or stored by this code."""
from __future__ import annotations
import json, os, time
from web3 import Web3

LOCAL_HOSTS = ("127.0.0.1", "localhost")
# K=1000 S1a submits 1000 sequential txs; a temporarily slow local Anvil must not
# false-timeout. 600s covers a long receipt wait without changing the local-only guard.
HTTP_TIMEOUT_S = 600
RECEIPT_TIMEOUT_S = 600


def connect(rpc: str = "http://127.0.0.1:8545") -> Web3:
    if not any(h in rpc for h in LOCAL_HOSTS):
        raise RuntimeError("refusing non-local RPC: this project uses a local Anvil chain only")
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": HTTP_TIMEOUT_S}))
    if not w3.is_connected():
        raise RuntimeError(f"cannot reach {rpc}")
    cid = w3.eth.chain_id
    if cid != 31337:
        raise RuntimeError(f"unexpected chain id {cid}; expected Anvil 31337")
    return w3


def load_artifact(contracts_dir: str, name: str):
    p = os.path.join(contracts_dir, "out", "AuditRegistries.sol", f"{name}.json")
    with open(p) as f:
        a = json.load(f)
    return a["abi"], a["bytecode"]["object"]


def deploy(w3: Web3, contracts_dir: str, name: str, sender=None):
    abi, bc = load_artifact(contracts_dir, name)
    sender = sender or w3.eth.accounts[0]
    C = w3.eth.contract(abi=abi, bytecode=bc)
    tx = C.constructor().transact({"from": sender})
    rc = w3.eth.wait_for_transaction_receipt(tx, timeout=RECEIPT_TIMEOUT_S)
    return w3.eth.contract(address=rc["contractAddress"], abi=abi), rc


def calldata_stats(w3: Web3, txhash) -> tuple[int, int]:
    data = bytes(w3.eth.get_transaction(txhash)["input"])
    return len(data), sum(1 for b in data if b)


def send(w3: Web3, fn, sender=None):
    """Send one transaction and return (receipt, seconds from send to receipt)."""
    sender = sender or w3.eth.accounts[0]
    t0 = time.perf_counter()
    tx = fn.transact({"from": sender})
    rc = w3.eth.wait_for_transaction_receipt(tx, timeout=RECEIPT_TIMEOUT_S)
    dt = time.perf_counter() - t0
    if rc["status"] != 1:
        raise RuntimeError("transaction reverted")
    return rc, dt


def log_bytes(rc) -> int:
    return sum(len(bytes(l["data"])) + 32 * len(l["topics"]) for l in rc["logs"])
