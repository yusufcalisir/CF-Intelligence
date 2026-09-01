# Cloudflare Perimeter Security (Layer 1 Defense)

This directory contains Terraform Infrastructure-as-Code (IaC) templates to configure Cloudflare as the outer perimeter defense for the **Collaborative Fraud Intelligence (CF-Intelligence)** platform.

---

## 1. Features Provided
- **Global DDoS Mitigation**: Automatic L3/L4 volumetric attack absorption on Cloudflare's Anycast network.
- **Bot Mitigation & Threat Screening**: Managed Challenge for suspicious clients and bot scrapers targeting `/api/v1/predict*`.
- **L7 Rate Limiting**:
  - Global API rate limit: 60 reqs / 10s per IP.
  - Heavy ML inference rate limit: 20 reqs / 60s per IP.
- **SSL/TLS Strict & TLS 1.3**: Complete end-to-end encryption between Client $\leftrightarrow$ Cloudflare $\leftrightarrow$ Vercel.
- **Real-Client IP Header Propagation**: Injects `CF-Connecting-IP` which is consumed natively by FastAPI's `DDoSProtectionMiddleware` and `slowapi`.

---

## 2. Deployment via Terraform

```bash
cd deployments/terraform/cloudflare

# Initialize Terraform Cloudflare Provider
terraform init

# Plan and Apply
terraform plan \
  -var="cloudflare_api_token=YOUR_CF_API_TOKEN" \
  -var="cloudflare_zone_id=YOUR_ZONE_ID" \
  -var="domain_name=yourdomain.com"

terraform apply -auto-approve \
  -var="cloudflare_api_token=YOUR_CF_API_TOKEN" \
  -var="cloudflare_zone_id=YOUR_ZONE_ID"
```

---

## 3. Deployment via Cloudflare Dashboard (Manual / Free Tier)

If deploying manually on the Cloudflare Web Dashboard without Terraform:

1. **DNS**:
   - Add `A` record `@` $\rightarrow$ `76.76.21.21` (Proxied 🟠).
   - Add `CNAME` record `www` $\rightarrow$ `cname.vercel-dns.com` (Proxied 🟠).
2. **SSL/TLS**:
   - Set encryption mode to **Full (Strict)**.
   - Enable **Always Use HTTPS** and **TLS 1.3**.
3. **Security**:
   - Set **Security Level** to `Medium`.
   - Enable **Bot Fight Mode** under *Security $\rightarrow$ Bots*.
   - Enable **Browser Integrity Check** under *Security $\rightarrow$ Settings*.
4. **WAF & Rate Limiting**:
   - Create Rate Limiting Rule: URI path contains `/api/*` $\rightarrow$ Max 60 reqs / 10s $\rightarrow$ Block for 1 minute.
