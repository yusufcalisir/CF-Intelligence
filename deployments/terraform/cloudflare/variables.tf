variable "cloudflare_api_token" {
  description = "Cloudflare API Token with Zone.WAF, Zone.Settings, and Zone.DNS permissions"
  type        = string
  sensitive   = true
}

variable "cloudflare_zone_id" {
  description = "Target Cloudflare Zone ID for the platform domain"
  type        = string
}

variable "domain_name" {
  description = "The primary domain name (e.g., cf-intelligence.internal or yourdomain.com)"
  type        = string
  default     = "cf-intelligence.org"
}

variable "vercel_cname_target" {
  description = "Vercel CNAME target for frontend deployment"
  type        = string
  default     = "cname.vercel-dns.com"
}
