# Constant on-chain commitment for federated learning rounds via hierarchical content identifiers

[![Code licence: MIT](https://img.shields.io/badge/code-MIT-blue.svg)](LICENSE)

This tree is the artifact archive for the ICT Express manuscript

> Prayitno, Karisma Trinanda Putra, Cahya Damarjati, Sunardi, Wiktasari, Suko Tyas Pernanda and Liliek Triyono. **Constant on-chain commitment for federated learning rounds via hierarchical content identifiers.** Manuscript draft, 2026.

The public archive is at [https://github.com/kang-prayit/fl-onchain-hcid](https://github.com/kang-prayit/fl-onchain-hcid).

The package contains the Python recorder and verifier, Solidity registry contracts, tests, Foundry configuration, reproduction scripts, and the official result CSVs behind Tables 4–7, Figure 3 and the path-size bound (Equation 1) of the manuscript.

**Warning: local chain only.** All chain code talks to a local Anvil instance (chain id 31337) and refuses non-local RPCs. Do not point it at mainnet, a public testnet, or any wallet that holds real funds. The recorder uses Anvil's unlocked development accounts over `eth_sendTransaction`; it never reads, prints or stores a private key.

## Purpose

Scheme S3 records each federated-learning (FL) round as a DAG-CBOR tree on IPFS and writes only the 32-byte digest of the root CID to an Ethereum contract. Client manifests carry signatures over the update digest, the FedAvg weight \(n_j\) and the global-model hash. The repository compares S3 with per-client hashing (S1), a binary Merkle root (S2) and a signed-leaf Merkle root (S2-sig) on a local chain, and it stores the measured gas, timing, proof-size, tamper and same-machine IPFS retrieval results.

## Repository layout

```
fl_onchain/          Python package (commitments, IPLD, recorder, tamper tests, CLI)
contracts/           Foundry project (Solidity 0.8.28, Prague): S1/S2/S3 registries
tests/               pytest (Merkle/DAG paths; optional Anvil integration)
scripts/             aggregation, clean timing, tamper, DAG-path and Eq. (1) measurement
paper/               elsarticle sources that rebuild the manuscript (optional)
results/             official CSVs used by the paper macros and figures
requirements.txt
```

The MedMNIST+ v3.0 image archives (`.npz`) are **not** included. Download them from Zenodo, DOI [10.5281/zenodo.10519652](https://doi.org/10.5281/zenodo.10519652), licence CC BY 4.0, and place `pneumoniamnist.npz` and `pathmnist.npz` in `data/`.

## Hardware and software of the reported runs

All reported measurements ran on one KVM guest. `lscpu` reported the model string “Intel(R) Xeon(R) Processor” (family 6, model 207, stepping 2) and no SKU; we do not infer one. The guest had 8 vCPUs (one thread per core), 16 GiB RAM and no swap; storage was a virtio block device; no CPU-frequency governor was visible. Software: Debian 13.7, Linux 6.12, Python 3.13.5, PyTorch 2.14.1 on CPU, Anvil v1.9.0 (chain id 31337, Prague), Solidity 0.8.28 and Kubo 0.43.1. Anvil, Kubo and the Python recorder ran co-located; processes were not pinned to CPUs.

The same-machine IPFS retrieval test used three private Kubo daemons on this guest (loopback, empty bootstrap, mDNS off, no public network). It is a sanity test of the committed path, not a wide-area measurement.

## Install and run locally

You need Python 3.11+, [Foundry](https://getfoundry.sh/) (Anvil and Forge, v1.9.0 was used) and [Kubo](https://github.com/ipfs/kubo/releases) v0.43.1 on `PATH`.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# Optional, only if you will rerun the FedAvg grid (not required to rebuild the tables):
# pip install torch  # CPU build is enough

(cd contracts && forge build && forge test)
python -m pytest -q tests

# Small end-to-end run (local Anvil + offline Kubo; starts both if --start-services is set)
python -m fl_onchain.run_experiment --dataset pneumoniamnist --k 10 --rounds 3 --partition iid \
    --out results/smoke --start-services
```

`fl_onchain.chain.connect` rejects any RPC host other than `127.0.0.1` or `localhost`, and it rejects a chain id other than 31337.

To regenerate the manuscript numbers and figures from the official CSVs:

```bash
python paper/paper_numbers_v5.py
python paper/make_figs.py
# then compile paper/main.tex with latexmk (elsarticle, [final,3p,times,twocolumn])
```

The official CSVs under `results/aggregate_v3/`, `results/gasgap_v4/`, `results/ipfs_dagpath_v4/`, `results/eq1_bound_v5/` and the compact per-run `gas.csv` / `latency.csv` extracts are enough to rebuild Tables 4–7 and Figure 3. Raw Kubo repositories, update blobs and `.npz` datasets are not shipped.

## Data licence

- **Code and contracts:** MIT, Copyright (c) 2026 the manuscript authors (Prayitno et al.).
- **Result CSVs in `results/`:** CC BY 4.0, same authors (see `LICENSE-DATA.md`).
- **MedMNIST+ v3.0:** third-party, CC BY 4.0, Yang et al., [10.5281/zenodo.10519652](https://doi.org/10.5281/zenodo.10519652). Not redistributed here.

## Citation

Please cite the manuscript (see `CITATION.cff`). No paper or software DOI has been assigned yet.
