// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Privileged mint with no access control.
contract UnprotectedMint {
    mapping(address => uint256) public balanceOf;
    address public owner;

    constructor() {
        owner = msg.sender;
    }

    /// @dev Anyone can mint — unprotected privileged function.
    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function transfer(address to, uint256 amount) external {
        require(balanceOf[msg.sender] >= amount, "bal");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
    }
}
