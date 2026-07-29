// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Intentionally insecure bank for local scanner demos.
/// Classic external-call-before-state-update reentrancy pattern.
contract VulnerableBank {
    mapping(address => uint256) public balances;

    function deposit() external payable {
        balances[msg.sender] += msg.value;
    }

    /// @dev Vulnerable: sends ETH before zeroing the balance.
    function withdraw() external {
        uint256 amount = balances[msg.sender];
        require(amount > 0, "no balance");
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
        balances[msg.sender] = 0;
    }

    function balanceOf(address account) external view returns (uint256) {
        return balances[account];
    }
}
