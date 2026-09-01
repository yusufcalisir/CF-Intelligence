# ── Cloudflare WAF Custom Rules (Bot, Threat & Geolocation Screening) ────────
resource "cloudflare_ruleset" "waf_custom_security_rules" {
  zone_id     = var.cloudflare_zone_id
  name        = "CFI Platform WAF Security Ruleset"
  description = "Custom WAF rules for automated bot mitigation, high-threat screening, and API shield"
  kind        = "zone"
  phase       = "http_request_firewall_custom"

  # Rule 1: Managed Challenge for High Threat Score / Suspicious Clients
  rules {
    action      = "managed_challenge"
    expression  = "(cf.threat_score gt 15 and not cf.client.bot)"
    description = "Challenge suspicious visitors with elevated threat scores"
    enabled     = true
  }

  # Rule 2: Challenge automated bot scrapers on sensitive API endpoints
  rules {
    action      = "managed_challenge"
    expression  = "(http.request.uri.path wildcard \"/api/v1/predict*\" and cf.client.bot)"
    description = "Challenge automated bot scrapers attempting to abuse ML inference endpoints"
    enabled     = true
  }

  # Rule 3: Block known malicious or anonymized proxy crawlers on auth/admin paths
  rules {
    action      = "block"
    expression  = "(http.request.uri.path wildcard \"/api/v1/security*\" and cf.threat_score gt 40)"
    description = "Block high-risk traffic attempting to query security infrastructure APIs"
    enabled     = true
  }
}
