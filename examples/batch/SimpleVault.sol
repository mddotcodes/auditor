// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Medium complexity: deposits + owner withdraw + public emergency drain (intentional flaw).
contract SimpleVault {
    mapping(address => uint256) public balances;
    address public owner;
    uint256 public totalDeposited;

    constructor() {
        owner = msg.sender;
    }

    function deposit() external payable {
        balances[msg.sender] += msg.value;
        totalDeposited += msg.value;
    }

    function withdraw(uint256 amount) external {
        require(balances[msg.sender] >= amount, "bal");
        balances[msg.sender] -= amount;
        totalDeposited -= amount;
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "send");
    }

    function ownerWithdraw(uint256 amount) external {
        require(msg.sender == owner, "not owner");
        (bool ok, ) = owner.call{value: amount}("");
        require(ok, "send");
    }

    /// @dev Intentional: anyone can call — drains entire balance to caller.
    function emergencyWithdraw() external {
        uint256 amount = address(this).balance;
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "send");
    }
}
