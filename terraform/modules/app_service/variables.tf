variable "name" {
  description = "App Service name (globally unique — forms <name>.azurewebsites.net)"
  type        = string
}

variable "resource_group_name" {
  type = string
}

variable "location" {
  type = string
}

variable "sku_name" {
  description = "Service Plan SKU — Basic B1 is the cheapest tier that supports a custom domain + managed certificate (issue #57)"
  type        = string
  default     = "B1"
}

variable "docker_image_name" {
  description = "Public GHCR image (owner/repo:tag) the App Service pulls at startup"
  type        = string
}

variable "app_settings" {
  description = "Environment variables exposed to the app (see src/app/config.py for the full list the app reads)"
  type        = map(string)
  sensitive   = true
}
