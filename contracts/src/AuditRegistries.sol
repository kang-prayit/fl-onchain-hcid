// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

/// @notice Minimal owner gate shared by all registries (the aggregator is the only writer).
abstract contract Owned {
    address public immutable owner;
    error NotOwner();
    constructor() { owner = msg.sender; }
    modifier onlyOwner() { if (msg.sender != owner) revert NotOwner(); _; }
}

/// @title S1: per-client hash registry (O(K) on-chain per round)
contract PerClientRegistry is Owned {
    mapping(uint64 => mapping(uint32 => bytes32)) public updateHash;
    event ClientCommitted(uint64 indexed round, uint32 indexed client, bytes32 h);
    event BatchCommitted(uint64 indexed round, bytes32[] hs);

    // S1a-storage: one transaction per client
    function commitStorage(uint64 round, uint32 client, bytes32 h) external onlyOwner {
        updateHash[round][client] = h;
    }
    // S1b-storage: one transaction per round, client id = array index
    function commitBatchStorage(uint64 round, bytes32[] calldata hs) external onlyOwner {
        for (uint256 i = 0; i < hs.length; ++i) updateHash[round][uint32(i)] = hs[i];
    }
    // event-only variants (data lives in receipts/logs, not in state)
    function commitEvent(uint64 round, uint32 client, bytes32 h) external onlyOwner {
        emit ClientCommitted(round, client, h);
    }
    function commitBatchEvent(uint64 round, bytes32[] calldata hs) external onlyOwner {
        emit BatchCommitted(round, hs);
    }
}

/// @title Shared one-commitment-per-round registry with monotonic rounds (anti-replay/rollback)
abstract contract RoundRootRegistry is Owned {
    mapping(uint64 => bytes32) public rootOf;
    uint64 public lastRound;
    event RootCommitted(uint64 indexed round, bytes32 root);
    error BadRound(uint64 expected, uint64 got);
    error ZeroRoot();

    function _next(uint64 round, bytes32 root) internal {
        if (round != lastRound + 1) revert BadRound(lastRound + 1, round);
        if (root == bytes32(0)) revert ZeroRoot();
        lastRound = round;
    }
    function commitStorage(uint64 round, bytes32 root) external onlyOwner {
        _next(round, root);
        rootOf[round] = root;
    }
    function commitEvent(uint64 round, bytes32 root) external onlyOwner {
        _next(round, root);
        emit RootCommitted(round, root);
    }
}

/// @title S2: binary Merkle root registry (+ optional on-chain inclusion check)
contract MerkleRootRegistry is RoundRootRegistry {
    /// leaf = sha256(0x00 || round || client || h); node = sha256(0x01 || L || R); odd node promoted
    function leafOf(uint64 round, uint32 client, bytes32 h) public pure returns (bytes32) {
        return sha256(abi.encodePacked(bytes1(0x00), round, client, h));
    }
    function verifyInclusion(bytes32 root, bytes32 leaf, bytes32[] calldata sib, bool[] calldata sibIsLeft)
        public pure returns (bool)
    {
        bytes32 x = leaf;
        for (uint256 i = 0; i < sib.length; ++i) {
            x = sibIsLeft[i] ? sha256(abi.encodePacked(bytes1(0x01), sib[i], x))
                             : sha256(abi.encodePacked(bytes1(0x01), x, sib[i]));
        }
        return x == root;
    }
}

/// @title S3: hierarchical CID root registry. Stores the 32-byte sha2-256 digest of a CIDv1 dag-cbor root;
/// the fixed 4-byte multiformats prefix (0x01 0x71 0x12 0x20) is a constant, so cidOf() returns the full CID.
contract CidRootRegistry is RoundRootRegistry {
    bytes4 public constant CID_PREFIX = 0x01711220;
    function cidOf(uint64 round) external view returns (bytes memory) {
        return abi.encodePacked(CID_PREFIX, rootOf[round]);
    }
}

/// @title S1 with the same monotonic round guard as the root registries (used only for T4 fairness).
/// A new round may start when round == lastRound + 1; further per-client writes of the same round are allowed.
contract PerClientRegistryGuarded is Owned {
    mapping(uint64 => mapping(uint32 => bytes32)) public updateHash;
    uint64 public lastRound;
    event ClientCommitted(uint64 indexed round, uint32 indexed client, bytes32 h);
    event BatchCommitted(uint64 indexed round, bytes32[] hs);
    error BadRound(uint64 expected, uint64 got);

    function _checkRound(uint64 round) internal {
        if (round == lastRound) return;
        if (round == lastRound + 1) { lastRound = round; return; }
        revert BadRound(lastRound + 1, round);
    }

    function commitStorage(uint64 round, uint32 client, bytes32 h) external onlyOwner {
        _checkRound(round);
        updateHash[round][client] = h;
    }
    function commitBatchStorage(uint64 round, bytes32[] calldata hs) external onlyOwner {
        _checkRound(round);
        for (uint256 i = 0; i < hs.length; ++i) updateHash[round][uint32(i)] = hs[i];
    }
    function commitEvent(uint64 round, uint32 client, bytes32 h) external onlyOwner {
        _checkRound(round);
        emit ClientCommitted(round, client, h);
    }
    function commitBatchEvent(uint64 round, bytes32[] calldata hs) external onlyOwner {
        _checkRound(round);
        emit BatchCommitted(round, hs);
    }
}
