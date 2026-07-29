// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @notice Slightly richer multi-owner pattern with a submit/execute flow (educational flaws).
contract MultiSigLite {
    address[] public owners;
    mapping(address => bool) public isOwner;
    uint256 public required;
    uint256 public nonce;

    struct Tx {
        address to;
        uint256 value;
        bool executed;
        uint256 confirmations;
    }

    mapping(uint256 => Tx) public transactions;
    mapping(uint256 => mapping(address => bool)) public confirmed;

    constructor(address[] memory _owners, uint256 _required) {
        require(_owners.length > 0, "owners");
        require(_required > 0 && _required <= _owners.length, "required");
        for (uint256 i = 0; i < _owners.length; i++) {
            address o = _owners[i];
            require(o != address(0), "zero");
            require(!isOwner[o], "dup");
            isOwner[o] = true;
            owners.push(o);
        }
        required = _required;
    }

    receive() external payable {}

    function submit(address to, uint256 value) external returns (uint256 id) {
        require(isOwner[msg.sender], "not owner");
        id = nonce++;
        transactions[id] = Tx({to: to, value: value, executed: false, confirmations: 0});
    }

    function confirm(uint256 id) external {
        require(isOwner[msg.sender], "not owner");
        require(!transactions[id].executed, "done");
        require(!confirmed[id][msg.sender], "already");
        confirmed[id][msg.sender] = true;
        transactions[id].confirmations += 1;
    }

    /// @dev Flaw: does not re-check confirmations count against `required` before send
    /// if state was manipulated; also no zero-address check on execute path.
    function execute(uint256 id) external {
        Tx storage t = transactions[id];
        require(!t.executed, "done");
        // Intentional weak check for calibration (should use >= required)
        require(t.confirmations > 0, "no confirms");
        t.executed = true;
        (bool ok, ) = t.to.call{value: t.value}("");
        require(ok, "call failed");
    }

    /// @dev Flaw: any owner can change required without multi-sig.
    function setRequired(uint256 r) external {
        require(isOwner[msg.sender], "not owner");
        required = r;
    }
}
