const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("ConsortiumIncentiveSettlement", function () {
  let contract;
  let coordinator;
  let bankA;
  let bankB;
  let bankC;
  let attacker;

  const CURRENCY = "e-TRY";
  const AUDIT_PROOF_HASH = ethers.keccak256(ethers.toUtf8Bytes("SIMULATION_EPOCH_101_AUDIT_PROOFS"));

  beforeEach(async function () {
    [coordinator, bankA, bankB, bankC, attacker] = await ethers.getSigners();

    const Factory = await ethers.getContractFactory("ConsortiumIncentiveSettlement");
    contract = await Factory.deploy(CURRENCY);
    await contract.waitForDeployment();
  });

  describe("Deployment", function () {
    it("Should set the correct coordinator and settlement currency", async function () {
      expect(await contract.coordinator()).to.equal(coordinator.address);
      expect(await contract.settlementCurrency()).to.equal(CURRENCY);
      expect(await contract.totalPoolBalanceWei()).to.equal(0);
      expect(await contract.getRecordedEpochsCount()).to.equal(0);
    });
  });

  describe("Pool Deposit", function () {
    it("Should allow coordinator to deposit pool funds", async function () {
      const depositAmount = ethers.parseEther("100");
      await expect(contract.connect(coordinator).depositPool(1, depositAmount))
        .to.emit(contract, "PoolDeposited")
        .withArgs(1, depositAmount, CURRENCY);

      expect(await contract.totalPoolBalanceWei()).to.equal(depositAmount);
    });

    it("Should revert if deposit amount is zero", async function () {
      await expect(contract.connect(coordinator).depositPool(1, 0))
        .to.be.revertedWith("ConsortiumIncentiveSettlement: Deposit amount must be greater than zero");
    });

    it("Should revert if non-coordinator attempts deposit", async function () {
      const depositAmount = ethers.parseEther("50");
      await expect(contract.connect(attacker).depositPool(1, depositAmount))
        .to.be.revertedWith("ConsortiumIncentiveSettlement: Caller is not the authorized FL coordinator");
    });
  });

  describe("Incentive Distribution", function () {
    beforeEach(async function () {
      // Deposit 1,000 ETH equivalent tokens for testing
      await contract.connect(coordinator).depositPool(100, ethers.parseEther("1000"));
    });

    it("Should correctly distribute incentives to participants based on Shapley values", async function () {
      const epochId = 100;
      const recipients = [bankA.address, bankB.address];
      const bankNames = ["JPMorgan", "Bank of America"];
      const shapleyBasisPoints = [6500, 3500]; // 65% and 35%
      const amountsWei = [ethers.parseEther("650"), ethers.parseEther("350")];

      await expect(
        contract.connect(coordinator).distributeIncentives(
          epochId,
          recipients,
          bankNames,
          shapleyBasisPoints,
          amountsWei,
          AUDIT_PROOF_HASH
        )
      )
        .to.emit(contract, "IncentivesDistributed")
        .withArgs(epochId, AUDIT_PROOF_HASH, 2, ethers.parseEther("1000"));

      expect(await contract.totalPoolBalanceWei()).to.equal(0);
      expect(await contract.getRecordedEpochsCount()).to.equal(1);

      const payoutA = await contract.getPayoutDetails(epochId, bankA.address);
      expect(payoutA.bankName).to.equal("JPMorgan");
      expect(payoutA.shapleyScoreBasisPoints).to.equal(6500);
      expect(payoutA.payoutAmountWei).to.equal(ethers.parseEther("650"));
      expect(payoutA.isClaimed).to.be.false;
      expect(payoutA.isQuarantined).to.be.false;
    });

    it("Should revert if parameter array lengths mismatch", async function () {
      await expect(
        contract.connect(coordinator).distributeIncentives(
          100,
          [bankA.address, bankB.address],
          ["Bank A"], // length 1
          [5000, 5000],
          [ethers.parseEther("500"), ethers.parseEther("500")],
          AUDIT_PROOF_HASH
        )
      ).to.be.revertedWith("ConsortiumIncentiveSettlement: Parameter array length mismatch");
    });

    it("Should revert if epoch is already settled", async function () {
      const epochId = 100;
      const recipients = [bankA.address];
      const bankNames = ["Bank A"];
      const shapleyBasisPoints = [10000];
      const amountsWei = [ethers.parseEther("500")];

      await contract.connect(coordinator).distributeIncentives(
        epochId, recipients, bankNames, shapleyBasisPoints, amountsWei, AUDIT_PROOF_HASH
      );

      await expect(
        contract.connect(coordinator).distributeIncentives(
          epochId, recipients, bankNames, shapleyBasisPoints, amountsWei, AUDIT_PROOF_HASH
        )
      ).to.be.revertedWith("ConsortiumIncentiveSettlement: Epoch already settled");
    });

    it("Should revert if pool balance is insufficient", async function () {
      const epochId = 101;
      const recipients = [bankA.address];
      const bankNames = ["Bank A"];
      const shapleyBasisPoints = [10000];
      const excessiveAmount = ethers.parseEther("5000"); // Pool has 1,000

      await expect(
        contract.connect(coordinator).distributeIncentives(
          epochId, recipients, bankNames, shapleyBasisPoints, [excessiveAmount], AUDIT_PROOF_HASH
        )
      ).to.be.revertedWith("ConsortiumIncentiveSettlement: Insufficient pool balance");
    });
  });

  describe("Claiming Payouts & Quarantine Governance", function () {
    const epochId = 200;

    beforeEach(async function () {
      await contract.connect(coordinator).depositPool(epochId, ethers.parseEther("1000"));
      await contract.connect(coordinator).distributeIncentives(
        epochId,
        [bankA.address, bankB.address],
        ["Bank A", "Bank B"],
        [6000, 4000],
        [ethers.parseEther("600"), ethers.parseEther("400")],
        AUDIT_PROOF_HASH
      );
    });

    it("Should allow participants to claim payouts", async function () {
      await expect(contract.connect(bankA).claimPayout(epochId))
        .to.emit(contract, "PayoutClaimed")
        .withArgs(epochId, bankA.address, ethers.parseEther("600"));

      const payoutA = await contract.getPayoutDetails(epochId, bankA.address);
      expect(payoutA.isClaimed).to.be.true;
    });

    it("Should prevent double claiming of payouts", async function () {
      await contract.connect(bankA).claimPayout(epochId);

      await expect(contract.connect(bankA).claimPayout(epochId))
        .to.be.revertedWith("ConsortiumIncentiveSettlement: Payout already claimed");
    });

    it("Should prevent unallocated participants from claiming", async function () {
      await expect(contract.connect(attacker).claimPayout(epochId))
        .to.be.revertedWith("ConsortiumIncentiveSettlement: No payout allocated");
    });

    it("Should prevent quarantined malicious participants from claiming payout", async function () {
      // Quarantine bankB for gradient poisoning
      await expect(contract.connect(coordinator).quarantineParticipant(bankB.address, "Gradient Poisoning Attack Detected"))
        .to.emit(contract, "ParticipantQuarantined")
        .withArgs(bankB.address, "Gradient Poisoning Attack Detected");

      await expect(contract.connect(bankB).claimPayout(epochId))
        .to.be.revertedWith("ConsortiumIncentiveSettlement: Participant is quarantined");
    });

    it("Should allow coordinator to clear quarantine status", async function () {
      await contract.connect(coordinator).quarantineParticipant(bankB.address, "False Positive Resolution");
      await expect(contract.connect(coordinator).clearQuarantine(bankB.address))
        .to.emit(contract, "ParticipantCleared")
        .withArgs(bankB.address);

      // Now bankB can claim
      await expect(contract.connect(bankB).claimPayout(epochId))
        .to.emit(contract, "PayoutClaimed")
        .withArgs(epochId, bankB.address, ethers.parseEther("400"));
    });
  });
});
