const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("GnosisSafeMultiSigCoordinator", function () {
  let contract;
  let owner1;
  let owner2;
  let owner3;
  let nonOwner;

  const ACTION_DISTRIBUTE = 0; // DISTRIBUTE_INCENTIVES
  const ACTION_QUARANTINE = 1; // QUARANTINE_PARTICIPANT
  const ACTION_DEPOSIT = 2; // DEPOSIT_POOL
  const PAYLOAD_HASH = ethers.keccak256(ethers.toUtf8Bytes("EPOCH_101_SETTLEMENT_PAYLOAD"));

  beforeEach(async function () {
    [owner1, owner2, owner3, nonOwner] = await ethers.getSigners();

    const Factory = await ethers.getContractFactory("GnosisSafeMultiSigCoordinator");
    contract = await Factory.deploy(owner1.address, owner2.address, owner3.address);
    await contract.waitForDeployment();
  });

  describe("Deployment & Configuration", function () {
    it("Should correctly initialize 3 owners and threshold of 2", async function () {
      expect(await contract.THRESHOLD()).to.equal(2);
      expect(await contract.OWNER_COUNT()).to.equal(3);
      expect(await contract.owners(0)).to.equal(owner1.address);
      expect(await contract.owners(1)).to.equal(owner2.address);
      expect(await contract.owners(2)).to.equal(owner3.address);

      expect(await contract.isOwner(owner1.address)).to.be.true;
      expect(await contract.isOwner(owner2.address)).to.be.true;
      expect(await contract.isOwner(owner3.address)).to.be.true;
      expect(await contract.isOwner(nonOwner.address)).to.be.false;
      expect(await contract.transactionCount()).to.equal(0);
    });

    it("Should revert deployment if any owner address is zero", async function () {
      const Factory = await ethers.getContractFactory("GnosisSafeMultiSigCoordinator");
      await expect(
        Factory.deploy(ethers.ZeroAddress, owner2.address, owner3.address)
      ).to.be.revertedWith("GnosisSafeMultiSig: Invalid owner address");
    });

    it("Should revert deployment if owner addresses are not unique", async function () {
      const Factory = await ethers.getContractFactory("GnosisSafeMultiSigCoordinator");
      await expect(
        Factory.deploy(owner1.address, owner1.address, owner3.address)
      ).to.be.revertedWith("GnosisSafeMultiSig: Owners must be unique");
    });
  });

  describe("Proposal Submission", function () {
    it("Should allow an authorized owner to submit a proposal with auto-confirmation", async function () {
      await expect(contract.connect(owner1).submitProposal(ACTION_DISTRIBUTE, 101, PAYLOAD_HASH))
        .to.emit(contract, "ProposalCreated")
        .withArgs(0, ACTION_DISTRIBUTE, 101, owner1.address, PAYLOAD_HASH)
        .and.to.emit(contract, "ProposalConfirmed")
        .withArgs(0, owner1.address, 1);

      expect(await contract.transactionCount()).to.equal(1);

      const txn = await contract.getTransaction(0);
      expect(txn.txId).to.equal(0);
      expect(txn.action).to.equal(ACTION_DISTRIBUTE);
      expect(txn.epochId).to.equal(101);
      expect(txn.payloadHash).to.equal(PAYLOAD_HASH);
      expect(txn.confirmationCount).to.equal(1);
      expect(txn.executed).to.be.false;

      const confirmations = await contract.getConfirmations(0);
      expect(confirmations[0]).to.be.true;
      expect(confirmations[1]).to.be.false;
      expect(confirmations[2]).to.be.false;
    });

    it("Should revert proposal submission from a non-owner", async function () {
      await expect(
        contract.connect(nonOwner).submitProposal(ACTION_DEPOSIT, 102, PAYLOAD_HASH)
      ).to.be.revertedWith("GnosisSafeMultiSig: Caller is not an authorized trustee owner");
    });
  });

  describe("Confirmation & Threshold Execution", function () {
    beforeEach(async function () {
      await contract.connect(owner1).submitProposal(ACTION_QUARANTINE, 201, PAYLOAD_HASH);
    });

    it("Should execute proposal once 2-of-3 threshold is reached", async function () {
      await expect(contract.connect(owner2).confirmProposal(0))
        .to.emit(contract, "ProposalConfirmed")
        .withArgs(0, owner2.address, 2)
        .and.to.emit(contract, "ProposalExecuted")
        .withArgs(0, ACTION_QUARANTINE, 201);

      const txn = await contract.getTransaction(0);
      expect(txn.confirmationCount).to.equal(2);
      expect(txn.executed).to.be.true;
    });

    it("Should revert if an owner attempts duplicate confirmation", async function () {
      await expect(contract.connect(owner1).confirmProposal(0))
        .to.be.revertedWith("GnosisSafeMultiSig: Transaction already confirmed by caller");
    });

    it("Should revert if confirming an already executed proposal", async function () {
      await contract.connect(owner2).confirmProposal(0); // executed now
      await expect(contract.connect(owner3).confirmProposal(0))
        .to.be.revertedWith("GnosisSafeMultiSig: Transaction already executed");
    });

    it("Should revert if confirming a non-existent transaction", async function () {
      await expect(contract.connect(owner2).confirmProposal(999))
        .to.be.revertedWith("GnosisSafeMultiSig: Transaction does not exist");
    });
  });

  describe("Revocation of Confirmation", function () {
    beforeEach(async function () {
      await contract.connect(owner1).submitProposal(ACTION_DISTRIBUTE, 301, PAYLOAD_HASH);
    });

    it("Should allow proposer/signer to revoke their confirmation before execution", async function () {
      await expect(contract.connect(owner1).revokeConfirmation(0))
        .to.emit(contract, "ConfirmationRevoked")
        .withArgs(0, owner1.address);

      const txn = await contract.getTransaction(0);
      expect(txn.confirmationCount).to.equal(0);
      expect(txn.executed).to.be.false;

      const confirmations = await contract.getConfirmations(0);
      expect(confirmations[0]).to.be.false;
    });

    it("Should revert if owner did not confirm transaction", async function () {
      await expect(contract.connect(owner2).revokeConfirmation(0))
        .to.be.revertedWith("GnosisSafeMultiSig: Transaction not confirmed by caller");
    });

    it("Should revert if trying to revoke an already executed proposal", async function () {
      await contract.connect(owner2).confirmProposal(0); // threshold reached & executed
      await expect(contract.connect(owner1).revokeConfirmation(0))
        .to.be.revertedWith("GnosisSafeMultiSig: Transaction already executed");
    });
  });
});
