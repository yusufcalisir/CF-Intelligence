"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.computePublicKey = exports.recoverPublicKey = exports.SigningKey = void 0;

class SigningKey {
  constructor(privateKey) {
    this.privateKey = privateKey;
    this.curve = "secp256k1";
  }
  signDigest(digest) {
    throw new Error("signing-key stub: signing operations should use modern ethers v6 / hardhat-ethers");
  }
  computeSharedSecret(otherKey) {
    throw new Error("signing-key stub: ECDH operations should use modern ethers v6");
  }
}
exports.SigningKey = SigningKey;

function recoverPublicKey(digest, signature) {
  throw new Error("signing-key stub: recoverPublicKey should use modern ethers v6");
}
exports.recoverPublicKey = recoverPublicKey;

function computePublicKey(key, compressed) {
  throw new Error("signing-key stub: computePublicKey should use modern ethers v6");
}
exports.computePublicKey = computePublicKey;
