# Formal Threat Model & Adversarial Assessment
## Privacy-Preserving Collaborative Financial Crime Intelligence Platform (CF-Intelligence)

> **Canonical Specification**: The complete, 56KB multi-vector threat model and attack taxonomy is maintained at [`docs/threat_model.md`](file:///docs/threat_model.md).

---

### Executive Threat Model Summary

CF-Intelligence operates in an adversarial environment where participating banking institutions, network observers, and external API clients may attempt to undermine the system's privacy, integrity, or availability.

```mermaid
flowchart TD
    subgraph Adversaries ["Identified Adversary Classes"]
        Adv1["Malicious Consortium Participant<br/>(Attempts weight poisoning or sample reconstruction)"]
        Adv2["Compromised Client Edge Node<br/>(Submits arbitrary or inverted gradients)"]
        Adv3["Curious Central Coordinator<br/>(Attempts gradient inspection)"]
        Adv4["External API Adversary<br/>(Attempts BOLA/IDOR, SSRF, DoS, injection)"]
    end

    subgraph Defenses ["Defensive Controls Layer"]
        Def1["Byzantine Defenses<br/>(Krum, Trimmed Mean, Bulyan, Spectral SVD)"]
        Def2["Differential Privacy<br/>(Opacus DP-SGD, Rényi Accounting)"]
        Def3["Secure Aggregation<br/>(Curve25519 Pairwise DH, Shamir Dropout)"]
        Def4["Perimeter Security<br/>(RFC 1918/Loopback SSRF Block, ABAC, Rate Limiting)"]
    end

    Adv1 --> Def1
    Adv1 --> Def2
    Adv2 --> Def1
    Adv3 --> Def3
    Adv4 --> Def4
```

---

### Adversary Taxonomies & Capabilities

| Adversary Profile | Attacking Capabilities | Defense Mechanisms Applied | Guarantee / Bound |
|:---|:---|:---|:---|
| **Curious Coordinator** | Inspects aggregated and individual network payloads | Curve25519 Secure Aggregation (SecAgg) with zero-sum pairwise masking | Coordinator learns *only* $\sum \Delta w_k$; individual $\Delta w_k$ blinded |
| **Poisoned FL Client** | Injects label-flipping, sign-inversion, or backdoor triggers | Krum, Coordinate-wise Trimmed Mean, Bulyan, Spectral SVD | Tolerates up to $f < \frac{n-2}{2}$ Byzantine nodes |
| **Reconstruction Attacker** | Executes gradient inversion (DLG) to reconstruct transaction PII | PyTorch Opacus Differential Privacy (DP-SGD) | Bounded privacy loss $(\epsilon, \delta)$ with RDP accountant |
| **SSRF Webhook Exploiter** | Dispatches requests to internal microservices or cloud metadata | Perimeter WAF with DNS resolution pinning and RFC 1918 / 169.254 blocking | Hard network rejection with fail-closed architecture |
| **Cross-Tenant Intruder** | Requests transaction, case, or alert IDs belonging to rival banks | Repository-level tenant scoping and ABAC claim enforcement | Strict isolation (HTTP 403 / 404 Forbidden) |

For complete mathematical breakdown points, formal security proofs, and cryptographic assumptions, refer to the full document: [**Threat Model Specification (`docs/threat_model.md`)**](file:///docs/threat_model.md).
