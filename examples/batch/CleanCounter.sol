// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Minimal clean counter — baseline “no bug intended”.
contract CleanCounter {
    uint256 public value;

    function increment() external {
        value += 1;
    }

    function set(uint256 v) external {
        value = v;
    }
}
