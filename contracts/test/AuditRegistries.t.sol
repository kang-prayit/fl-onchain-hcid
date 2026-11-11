// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {PerClientRegistry, MerkleRootRegistry, CidRootRegistry, RoundRootRegistry, Owned} from "../src/AuditRegistries.sol";

interface Vm { function prank(address) external; function expectRevert(bytes calldata) external; function expectRevert() external; }

contract AuditRegistriesTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    PerClientRegistry s1; MerkleRootRegistry s2; CidRootRegistry s3;

    function setUp() public { s1 = new PerClientRegistry(); s2 = new MerkleRootRegistry(); s3 = new CidRootRegistry(); }

    function _eq(bytes32 a, bytes32 b) internal pure { require(a == b, "neq"); }

    function test_S1_storage_and_batch() public {
        s1.commitStorage(1, 7, bytes32(uint256(42)));
        _eq(s1.updateHash(1, 7), bytes32(uint256(42)));
        bytes32[] memory hs = new bytes32[](3);
        hs[0] = bytes32(uint256(1)); hs[1] = bytes32(uint256(2)); hs[2] = bytes32(uint256(3));
        s1.commitBatchStorage(2, hs);
        _eq(s1.updateHash(2, 2), bytes32(uint256(3)));
        s1.commitBatchEvent(3, hs);
        _eq(s1.updateHash(3, 0), bytes32(0)); // event-only leaves state untouched
    }

    function test_onlyOwner() public {
        vm.prank(address(0xBEEF));
        vm.expectRevert(abi.encodeWithSelector(Owned.NotOwner.selector));
        s3.commitStorage(1, bytes32(uint256(1)));
    }

    function test_monotonic_rounds_block_replay() public {
        s3.commitStorage(1, bytes32(uint256(11)));
        s3.commitEvent(2, bytes32(uint256(12)));
        vm.expectRevert(abi.encodeWithSelector(RoundRootRegistry.BadRound.selector, uint64(3), uint64(1)));
        s3.commitStorage(1, bytes32(uint256(99))); // replay / rollback of round 1
        vm.expectRevert(abi.encodeWithSelector(RoundRootRegistry.BadRound.selector, uint64(3), uint64(5)));
        s3.commitStorage(5, bytes32(uint256(99))); // skipping rounds
        _eq(s3.rootOf(1), bytes32(uint256(11)));
    }

    function test_zero_root_rejected() public {
        vm.expectRevert(abi.encodeWithSelector(RoundRootRegistry.ZeroRoot.selector));
        s2.commitStorage(1, bytes32(0));
    }

    function test_cid_prefix() public {
        bytes32 d = sha256("node");
        s3.commitStorage(1, d);
        bytes memory c = s3.cidOf(1);
        require(c.length == 36 && c[0] == 0x01 && c[1] == 0x71 && c[2] == 0x12 && c[3] == 0x20, "prefix");
    }

    function test_merkle_inclusion_three_leaves() public view {
        // leaves l0,l1,l2 ; root = H(H(l0,l1), l2) with odd l2 promoted
        bytes32 l0 = s2.leafOf(1, 0, sha256("a"));
        bytes32 l1 = s2.leafOf(1, 1, sha256("b"));
        bytes32 l2 = s2.leafOf(1, 2, sha256("c"));
        bytes32 n01 = sha256(abi.encodePacked(bytes1(0x01), l0, l1));
        bytes32 root = sha256(abi.encodePacked(bytes1(0x01), n01, l2));
        bytes32[] memory sib = new bytes32[](2); bool[] memory left = new bool[](2);
        sib[0] = l0; left[0] = true; sib[1] = l2; left[1] = false;
        require(s2.verifyInclusion(root, l1, sib, left), "l1 must verify");
        bytes32[] memory sib2 = new bytes32[](1); bool[] memory left2 = new bool[](1);
        sib2[0] = n01; left2[0] = true;
        require(s2.verifyInclusion(root, l2, sib2, left2), "l2 must verify");
        require(!s2.verifyInclusion(root, s2.leafOf(1, 1, sha256("tampered")), sib, left), "tamper must fail");
    }
}
