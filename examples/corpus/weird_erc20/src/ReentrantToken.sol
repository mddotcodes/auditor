// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Token that calls back into the recipient on transfer (ERC777-like hook).
/// Inspired by d-xo/weird-erc20 Reentrant pattern (minimal port).
interface ITokenReceiver {
    function onTokenTransfer(address from, uint256 amount) external;
}

contract ReentrantToken {
    string public name = "ReentrantToken";
    string public symbol = "RNT";
    uint8 public decimals = 18;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor() {
        totalSupply = 1_000_000 ether;
        balanceOf[msg.sender] = totalSupply;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "bal");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        if (to.code.length > 0) {
            try ITokenReceiver(to).onTokenTransfer(msg.sender, amount) {} catch {}
        }
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
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        if (to.code.length > 0) {
            try ITokenReceiver(to).onTokenTransfer(from, amount) {} catch {}
        }
        return true;
    }
}
