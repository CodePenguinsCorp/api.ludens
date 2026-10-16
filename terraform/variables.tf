variable "docker_image_name" {
  description = "Public GHCR image with an immutable commit tag"
  type        = string
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "production"
}

# Select a region with App Service and PostgreSQL quota in the new subscription.
variable "location" {
  description = "Azure region"
  type        = string
  default     = "Central US"
}

variable "resource_group_name" {
  type    = string
  default = "rg-codepenguins-ludens-production"
}

# App Service and Postgres names form public hostnames
# (<name>.azurewebsites.net, <name>.postgres.database.azure.com) that are
# unique across the whole of Azure, not just this project — if the default
# is already taken, `terraform apply` fails with a clear error; just change
# the variable.
variable "app_service_name" {
  type    = string
  default = "codepenguins-ludens-api"
}

variable "postgres_server_name" {
  type    = string
  default = "codepenguins-ludens-psql"
}

variable "communication_service_name" {
  type    = string
  default = "codepenguins-ludens-acs"
}

variable "email_service_name" {
  type    = string
  default = "codepenguins-ludens-email"
}

variable "postgres_admin_username" {
  type    = string
  default = "ludensadmin"
}

# Explicitly configure the new frontend origins before planning production.
variable "allowed_origins" {
  description = "CORS-allowed origins"
  type        = list(string)
}

variable "frontend_base_url" {
  description = "Base URL used to build links in transactional e-mails"
  type        = string
}

variable "email_from_address" {
  type    = string
  default = "no-reply@ludens.local"
}

variable "email_from_name" {
  type    = string
  default = "Ludens"
}
