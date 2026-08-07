const { ethers, network } = require("hardhat");

async function main() {
  console.log("==================================================================");
  console.log("Deploying ConsortiumIncentiveSettlement Smart Contract...");
  console.log(`Target Network: ${network.name}`);
  console.log("==================================================================");

  const [deployer] = await ethers.getSigners();
  console.log(`Deploying with account address: ${deployer.address}`);

  const balanceWei = await ethers.provider.getBalance(deployer.address);
  console.log(`Deployer balance: ${ethers.formatEther(balanceWei)} ETH`);

  const SETTLEMENT_CURRENCY = process.env.SETTLEMENT_CURRENCY || "e-TRY";

  const Factory = await ethers.getContractFactory("ConsortiumIncentiveSettlement");
  const contract = await Factory.deploy(SETTLEMENT_CURRENCY);

  await contract.waitForDeployment();
  const address = await contract.getAddress();

  console.log("\n------------------------------------------------------------------");
  console.log(`✅ ConsortiumIncentiveSettlement deployed successfully!`);
  console.log(`Contract Address:   ${address}`);
  console.log(`FL Coordinator:     ${await contract.coordinator()}`);
  console.log(`Currency Symbol:    ${await contract.settlementCurrency()}`);
  console.log("------------------------------------------------------------------\n");
}

main().catch((error) => {
  console.error("❌ Deployment failed:", error);
  process.exitCode = 1;
});
