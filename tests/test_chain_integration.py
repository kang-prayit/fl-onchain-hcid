"""Needs a local Anvil on 127.0.0.1:8545 (skipped otherwise). Checks Python Merkle == Solidity verifyInclusion,
and the CID registry prefix."""
import os, sys, shutil
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fl_onchain import chain
from fl_onchain.commitments import ClientUpdate, s2_build, merkle_leaf
from fl_onchain.ipld import encode_node, cid_digest32

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def w3():
    try:
        return chain.connect()
    except Exception:
        pytest.skip("no local anvil")


def test_merkle_python_matches_solidity(w3):
    c, _ = chain.deploy(w3, os.path.join(HERE, "contracts"), "MerkleRootRegistry")
    for k in (1, 2, 5, 10, 33):
        ups = [ClientUpdate(i, 1, os.urandom(64)) for i in range(k)]
        root, ps = s2_build(7, ups)
        for u in ups:
            assert bytes(c.functions.leafOf(7, u.client, u.h).call()) == merkle_leaf(7, u.client, u.h)
            sib = [s for s, _ in ps[u.client]["proof"]]; left = [l for _, l in ps[u.client]["proof"]]
            assert c.functions.verifyInclusion(root, ps[u.client]["leaf"], sib, left).call()


def test_cid_registry_returns_full_cid(w3):
    c, _ = chain.deploy(w3, os.path.join(HERE, "contracts"), "CidRootRegistry")
    _, cid = encode_node({"type": "fl-round", "round": 1})
    chain.send(w3, c.functions.commitStorage(1, cid_digest32(cid)))
    assert bytes(c.functions.cidOf(1).call()) == bytes(cid)
