// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title GnosisSafeMultiSigCoordinator
 * @dev 2-of-3 Threshold Governance Contract for Consortium FL Coordinator Operations.
 *      Enforces multi-signature approval for critical operations (Incentive Settlement,
 *      Participant Quarantine, Pool Deposit) eliminating Single Point of Failure (SPOF).
 */
contract GnosisSafeMultiSigCoordinator {
    // --- State Variables ---
    uint256 public constant THRESHOLD = 2;
    uint256 public constant OWNER_COUNT = 3;

    address[3] public owners;
    mapping(address => bool) public isOwner;

    uint256 public transactionCount;

    enum ActionType {
        DISTRIBUTE_INCENTIVES,
        QUARANTINE_PARTICIPANT,
        DEPOSIT_POOL,
        TRANSFER_OWNERSHIP
    }

    struct Transaction {
        uint256 txId;
        ActionType action;
        uint256 epochId;
        bytes32 payloadHash;
        uint256 confirmationCount;
        bool executed;
        uint256 createdAt;
    }

    // txId => Transaction
    mapping(uint256 => Transaction) public transactions;

    // txId => owner => bool
    mapping(uint256 => mapping(address => bool)) public isConfirmedBy;

    // --- Events ---
    event ProposalCreated(
        uint256 indexed txId,
        ActionType indexed action,
        uint256 indexed epochId,
        address proposer,
        bytes32 payloadHash
    );
    event ProposalConfirmed(uint256 indexed txId, address indexed owner, uint256 currentConfirmations);
    event ProposalExecuted(uint256 indexed txId, ActionType indexed action, uint256 indexed epochId);
    event ConfirmationRevoked(uint256 indexed txId, address indexed owner);

    // --- Modifiers ---
    modifier onlyOwner() {
        require(isOwner[msg.sender], "GnosisSafeMultiSig: Caller is not an authorized trustee owner");
        _;
    }

    modifier txExists(uint256 _txId) {
        require(_txId < transactionCount, "GnosisSafeMultiSig: Transaction does not exist");
        _;
    }

    modifier notExecuted(uint256 _txId) {
        require(!transactions[_txId].executed, "GnosisSafeMultiSig: Transaction already executed");
        _;
    }

    modifier notConfirmed(uint256 _txId) {
        require(!isConfirmedBy[_txId][msg.sender], "GnosisSafeMultiSig: Transaction already confirmed by caller");
        _;
    }

    // --- Constructor ---
    constructor(address _owner1, address _owner2, address _owner3) {
        require(_owner1 != address(0) && _owner2 != address(0) && _owner3 != address(0), "GnosisSafeMultiSig: Invalid owner address");
        require(_owner1 != _owner2 && _owner2 != _owner3 && _owner1 != _owner3, "GnosisSafeMultiSig: Owners must be unique");

        owners[0] = _owner1;
        owners[1] = _owner2;
        owners[2] = _owner3;

        isOwner[_owner1] = true;
        isOwner[_owner2] = true;
        isOwner[_owner3] = true;
    }

    /**
     * @notice Propose a new coordinator governance action requiring 2-of-3 multi-sig approval.
     */
    function submitProposal(
        ActionType _action,
        uint256 _epochId,
        bytes32 _payloadHash
    ) external onlyOwner returns (uint256 txId) {
        txId = transactionCount;

        transactions[txId] = Transaction({
            txId: txId,
            action: _action,
            epochId: _epochId,
            payloadHash: _payloadHash,
            confirmationCount: 1,
            executed: false,
            createdAt: block.timestamp
        });

        isConfirmedBy[txId][msg.sender] = true;
        transactionCount += 1;

        emit ProposalCreated(txId, _action, _epochId, msg.sender, _payloadHash);
        emit ProposalConfirmed(txId, msg.sender, 1);

        return txId;
    }

    /**
     * @notice Confirm a pending proposal with an additional trustee signature.
     */
    function confirmProposal(uint256 _txId) external onlyOwner txExists(_txId) notExecuted(_txId) notConfirmed(_txId) {
        Transaction storage txn = transactions[_txId];
        txn.confirmationCount += 1;
        isConfirmedBy[_txId][msg.sender] = true;

        emit ProposalConfirmed(_txId, msg.sender, txn.confirmationCount);

        if (txn.confirmationCount >= THRESHOLD && !txn.executed) {
            txn.executed = true;
            emit ProposalExecuted(_txId, txn.action, txn.epochId);
        }
    }

    /**
     * @notice Get confirmation status for all 3 trustee owners.
     */
    function getConfirmations(uint256 _txId) external view txExists(_txId) returns (bool[3] memory confirmations) {
        confirmations[0] = isConfirmedBy[_txId][owners[0]];
        confirmations[1] = isConfirmedBy[_txId][owners[1]];
        confirmations[2] = isConfirmedBy[_txId][owners[2]];
    }

    /**
     * @notice Retrieve details for a given proposal transaction ID.
     */
    function getTransaction(uint256 _txId) external view txExists(_txId) returns (
        uint256 txId,
        ActionType action,
        uint256 epochId,
        bytes32 payloadHash,
        uint256 confirmationCount,
        bool executed,
        uint256 createdAt
    ) {
        Transaction storage txn = transactions[_txId];
        return (
            txn.txId,
            txn.action,
            txn.epochId,
            txn.payloadHash,
            txn.confirmationCount,
            txn.executed,
            txn.createdAt
        );
    }
}
