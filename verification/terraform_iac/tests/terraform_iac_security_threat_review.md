# Security & Threat Model Review — Multi-Cloud Terraform IaC

**Subsystem:** Multi-Cloud Terraform Infrastructure as Code (IaC)  
**Date:** August 2026  

---

1. **Public Subnet Exposure:** Worker nodes are provisioned strictly inside private subnets without public IPs. 🛡️ **MITIGATED**
2. **Accidental Key Purge / Ransomware:** Purge protection is enabled on KeyVault and deletion windows are set on KMS keys. 🛡️ **MITIGATED**
3. **Hardcoded Secrets in Source:** Regex scanning prevents plaintext credentials in HCL templates. 🛡️ **MITIGATED**
