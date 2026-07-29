// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Low-level call without checking success.
contract UncheckedSend {
    function payout(address payable to) external payable {
        // intentionally ignore return value
        to.call{value: msg.value}("");
    }
}
