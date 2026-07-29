// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Fee-on-transfer token (recipient gets less than amount).
/// Inspired by d-xo/weird-erc20 TransferFee (minimal port).
contract TransferFee {
    string public name = "TransferFee";
    string public symbol = "FEE";
    uint8 public decimals = 18;
    uint256 public totalSupply;
    uint256 public constant FEE_BPS = 100; // 1%
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor() {
        totalSupply = 1_000_000 ether;
        balanceOf[msg.sender] = totalSupply;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "bal");
        uint256 fee = (amount * FEE_BPS) / 10_000;
        uint256 sendAmount = amount - fee;
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += sendAmount;
        // fee burned (not sent to fee recipient) for simplicity
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(balanceOf[from] >= amount, "bal");
        require(allowance[from][msg.sender] >= amount, "allow");
        allowance[from][msg.sender] -= amount;
        uint256 fee = (amount * FEE_BPS) / 10_000;
        uint256 sendAmount = amount - fee;
        balanceOf[from] -= amount;
        balanceOf[to] += sendAmount;
        return true;
    }
}
