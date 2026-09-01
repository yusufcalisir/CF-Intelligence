terraform {
  required_version = ">= 1.5.0"
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.35"
    }
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

# ── Zone Settings & Security Posture ─────────────────────────────────────────
resource "cloudflare_zone_settings_override" "cfi_security_posture" {
  zone_id = var.cloudflare_zone_id

  settings {
    ssl                      = "strict"
    always_use_https         = "on"
    min_tls_version          = "1.2"
    tls_1_3                  = "on"
    automatic_https_rewrites = "on"
    opportunistic_encryption = "on"
    security_level           = "medium"
    browser_integrity_check  = "on"
    websockets               = "on"
    http3                    = "on"
    brotli                   = "on"
    early_hints              = "on"
  }
}

# ── DNS Records pointing to Vercel (Proxied) ──────────────────────────────────
resource "cloudflare_record" "apex" {
  zone_id = var.cloudflare_zone_id
  name    = "@"
  value   = "76.76.21.21"
  type    = "A"
  proxied = true
  ttl     = 1
}

resource "cloudflare_record" "www" {
  zone_id = var.cloudflare_zone_id
  name    = "www"
  value   = var.vercel_cname_target
  type    = "CNAME"
  proxied = true
  ttl     = 1
}
