// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Classic reentrancy: external call before zeroing balance.
/// Inspired by crytic/not-so-smart-contracts patterns (modernized pragma).
contract ReentrancyBank {
    mapping(address => uint256) public balances;

    function deposit() external payable {
        balances[msg.sender] += msg.value;
    }

    function withdraw() external {
        uint256 amount = balances[msg.sender];
        require(amount > 0, "empty");
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "send failed");
        balances[msg.sender] = 0;
    }
}
