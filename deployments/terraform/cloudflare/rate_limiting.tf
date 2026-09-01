# ── Cloudflare L7 Rate Limiting Rules (Anti-Flood & Script Bombardment) ───────
resource "cloudflare_ruleset" "rate_limiting_rules" {
  zone_id     = var.cloudflare_zone_id
  name        = "CFI Platform L7 Rate Limiting Ruleset"
  description = "Layer-7 sliding-window rate limiting to prevent automated script bombardment and volumetric API abuse"
  kind        = "zone"
  phase       = "http_ratelimit"

  # Rule 1: Global API Rate Limiting (60 requests per 10 seconds per IP)
  rules {
    action      = "block"
    expression  = "(http.request.uri.path wildcard \"/api/*\")"
    description = "Global API rate limit: Max 60 requests in 10s window per client IP"
    enabled     = true

    ratelimit {
      characteristics     = ["cf.unique_visitor_id"]
      period              = 10
      requests_per_period = 60
      mitigation_timeout  = 60
    }
  }

  # Rule 2: Heavy ML Inference Rate Limiting (20 requests per 60 seconds per IP)
  rules {
    action      = "managed_challenge"
    expression  = "(http.request.uri.path wildcard \"/api/v1/predict*\")"
    description = "Strict ML Inference rate limit: Max 20 requests per 60s before challenge"
    enabled     = true

    ratelimit {
      characteristics     = ["cf.unique_visitor_id"]
      period              = 60
      requests_per_period = 20
      mitigation_timeout  = 120
    }
  }
}
