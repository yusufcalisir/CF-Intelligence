// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title ConsortiumIncentiveSettlement
 * @dev Automated Smart Contract for Federated Learning Consortium Incentive Settlement
 *      using Central Bank Digital Currencies (CBDC) or Fiat-Backed Stablecoins (USDC / e-TRY).
 *      Calculates and disburses payouts based on Leave-One-Out (LOO) Federated Shapley Values,
 *      linking on-chain distribution directly to cryptographic audit chain proof hashes.
 */
contract ConsortiumIncentiveSettlement {
    // --- State Variables ---
    address public immutable coordinator;
    string public settlementCurrency;
    uint256 public totalPoolBalanceWei;

    struct EpochSettlement {
        uint256 epochId;
        bytes32 auditProofHash;
        uint256 totalPayoutWei;
        uint256 blockTimestamp;
        bool isSettled;
    }

    struct ParticipantPayout {
        address bankWallet;
        string bankName;
        int256 shapleyScoreBasisPoints; // Shapley score scaled by 10,000
        uint256 payoutAmountWei;
        bool isClaimed;
        bool isQuarantined;
    }

    // Mapping: epochId => EpochSettlement
    mapping(uint256 => EpochSettlement) public epochSettlements;

    // Mapping: epochId => (bankAddress => ParticipantPayout)
    mapping(uint256 => mapping(address => ParticipantPayout)) public payouts;

    // Mapping: bankAddress => isQuarantined
    mapping(address => bool) public blacklistedParticipants;

    // List of recorded epoch IDs
    uint256[] public recordedEpochs;

    // --- Events ---
    event PoolDeposited(uint256 indexed epochId, uint256 amountWei, string currency);
    event IncentivesDistributed(
        uint256 indexed epochId,
        bytes32 indexed auditProofHash,
        uint256 totalRecipients,
        uint256 totalPayoutWei
    );
    event PayoutClaimed(uint256 indexed epochId, address indexed participant, uint256 amountWei);
    event ParticipantQuarantined(address indexed participant, string reason);
    event ParticipantCleared(address indexed participant);

    // --- Modifiers ---
    modifier onlyCoordinator() {
        require(msg.sender == coordinator, "ConsortiumIncentiveSettlement: Caller is not the authorized FL coordinator");
        _;
    }

    // --- Constructor ---
    constructor(string memory _currency) {
        coordinator = msg.sender;
        settlementCurrency = _currency;
    }

    /**
     * @notice Fund the settlement pool for a given simulation epoch.
     * @param epochId Unique simulation run epoch identifier.
     * @param amountWei Total pool budget in token wei units (18 decimals).
     */
    function depositPool(uint256 epochId, uint256 amountWei) external onlyCoordinator {
        require(amountWei > 0, "ConsortiumIncentiveSettlement: Deposit amount must be greater than zero");
        totalPoolBalanceWei += amountWei;
        emit PoolDeposited(epochId, amountWei, settlementCurrency);
    }

    /**
     * @notice Distribute incentive payouts to consortium banks based on verified Shapley scores.
     * @param epochId Unique simulation run epoch identifier.
     * @param recipients Array of consortium bank wallet addresses.
     * @param bankNames Array of bank identifiers.
     * @param shapleyScoresBasisPoints Array of Shapley contribution scores in basis points.
     * @param amountsWei Array of token payout amounts in wei.
     * @param auditProofHash SHA-256 cryptographic audit chain proof hash.
     */
    function distributeIncentives(
        uint256 epochId,
        address[] calldata recipients,
        string[] calldata bankNames,
        int256[] calldata shapleyScoresBasisPoints,
        uint256[] calldata amountsWei,
        bytes32 auditProofHash
    ) external onlyCoordinator {
        require(!epochSettlements[epochId].isSettled, "ConsortiumIncentiveSettlement: Epoch already settled");
        require(
            recipients.length == amountsWei.length &&
            recipients.length == bankNames.length &&
            recipients.length == shapleyScoresBasisPoints.length,
            "ConsortiumIncentiveSettlement: Parameter array length mismatch"
        );

        uint256 totalPayout = 0;
        for (uint256 i = 0; i < recipients.length; i++) {
            address recipient = recipients[i];
            bool isBlocked = blacklistedParticipants[recipient];
            uint256 payout = isBlocked ? 0 : amountsWei[i];

            payouts[epochId][recipient] = ParticipantPayout({
                bankWallet: recipient,
                bankName: bankNames[i],
                shapleyScoreBasisPoints: shapleyScoresBasisPoints[i],
                payoutAmountWei: payout,
                isClaimed: false,
                isQuarantined: isBlocked
            });

            totalPayout += payout;
        }

        require(totalPoolBalanceWei >= totalPayout, "ConsortiumIncentiveSettlement: Insufficient pool balance");
        totalPoolBalanceWei -= totalPayout;

        epochSettlements[epochId] = EpochSettlement({
            epochId: epochId,
            auditProofHash: auditProofHash,
            totalPayoutWei: totalPayout,
            blockTimestamp: block.timestamp,
            isSettled: true
        });

        recordedEpochs.push(epochId);

        emit IncentivesDistributed(epochId, auditProofHash, recipients.length, totalPayout);
    }

    /**
     * @notice Claim allocated CBDC / Stablecoin token payout for a specific epoch.
     * @param epochId Simulation epoch identifier to claim.
     */
    function claimPayout(uint256 epochId) external {
        ParticipantPayout storage payout = payouts[epochId][msg.sender];
        require(epochSettlements[epochId].isSettled, "ConsortiumIncentiveSettlement: Epoch not settled");
        require(!payout.isClaimed, "ConsortiumIncentiveSettlement: Payout already claimed");
        require(!payout.isQuarantined && !blacklistedParticipants[msg.sender], "ConsortiumIncentiveSettlement: Participant is quarantined");
        require(payout.payoutAmountWei > 0, "ConsortiumIncentiveSettlement: No payout allocated");

        payout.isClaimed = true;
        emit PayoutClaimed(epochId, msg.sender, payout.payoutAmountWei);
    }

    /**
     * @notice Quarantine a malicious or free-riding participant node on-chain.
     * @param participant Address of the node to quarantine.
     * @param reason Description of adversarial behavior (e.g. gradient poisoning / LOO Shapley <= -0.05).
     */
    function quarantineParticipant(address participant, string calldata reason) external onlyCoordinator {
        blacklistedParticipants[participant] = true;
        emit ParticipantQuarantined(participant, reason);
    }

    /**
     * @notice Remove quarantine status for a participant node.
     * @param participant Address of the node to clear.
     */
    function clearQuarantine(address participant) external onlyCoordinator {
        blacklistedParticipants[participant] = false;
        emit ParticipantCleared(participant);
    }

    // --- View Functions ---

    function getPayoutDetails(uint256 epochId, address participant)
        external
        view
        returns (
            string memory bankName,
            int256 shapleyScoreBasisPoints,
            uint256 payoutAmountWei,
            bool isClaimed,
            bool isQuarantined
        )
    {
        ParticipantPayout memory p = payouts[epochId][participant];
        return (p.bankName, p.shapleyScoreBasisPoints, p.payoutAmountWei, p.isClaimed, p.isQuarantined);
    }

    function getRecordedEpochsCount() external view returns (uint256) {
        return recordedEpochs.length;
    }
}
