// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Trivial counter — clean-ish baseline for offline demos.
contract SafeCounter {
    uint256 public count;
    address public owner;

    event Incremented(address indexed by, uint256 newCount);

    constructor() {
        owner = msg.sender;
    }

    function increment() external {
        unchecked {
            count += 1;
        }
        emit Incremented(msg.sender, count);
    }

    function reset() external {
        require(msg.sender == owner, "not owner");
        count = 0;
    }
}
