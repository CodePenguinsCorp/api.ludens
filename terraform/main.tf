resource "random_password" "postgres_admin" {
  length  = 24
  special = false
}

resource "random_password" "jwt_secret" {
  length  = 64
  special = false
}

module "resource_group" {
  source = "./modules/resource_group"

  name        = var.resource_group_name
  location    = var.location
  environment = var.environment
}

module "postgresql" {
  source = "./modules/postgresql"

  name                = var.postgres_server_name
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location
  admin_username      = var.postgres_admin_username
  admin_password      = random_password.postgres_admin.result
}

module "acs" {
  source = "./modules/acs"

  communication_service_name = var.communication_service_name
  email_service_name         = var.email_service_name
  resource_group_name        = module.resource_group.name
}

module "app_service" {
  source = "./modules/app_service"

  name                = var.app_service_name
  resource_group_name = module.resource_group.name
  location            = module.resource_group.location

  docker_image_name = var.docker_image_name

  app_settings = {
    ENVIRONMENT = var.environment

    DATABASE_URL = "postgresql+asyncpg://${var.postgres_admin_username}:${random_password.postgres_admin.result}@${module.postgresql.fqdn}:5432/${module.postgresql.database_name}"

    JWT_SECRET_KEY              = random_password.jwt_secret.result
    ACCESS_TOKEN_EXPIRE_MINUTES = "30"
    REFRESH_TOKEN_EXPIRE_DAYS   = "7"

    ALLOWED_ORIGINS = jsonencode(var.allowed_origins)

    OUTBOX_RELAY_INTERVAL_SECONDS = "2"

    # AcsEmailService (src/app/modules/notification/infrastructure/services/
    # acs_email_service.py) reads acs_connection_string/acs_sender_address
    # exclusively — email_from_address/email_from_name below are SMTP-only
    # settings, harmless leftovers once EMAIL_BACKEND=acs (get_email_service()
    # never instantiates SmtpEmailService in that case).
    EMAIL_BACKEND         = "acs"
    EMAIL_FROM_ADDRESS    = var.email_from_address
    EMAIL_FROM_NAME       = var.email_from_name
    ACS_CONNECTION_STRING = module.acs.connection_string
    ACS_SENDER_ADDRESS    = module.acs.sender_address

    FRONTEND_BASE_URL = var.frontend_base_url

    # Container listens on 8000 (Dockerfile), not the App Service Linux
    # default of 80 — without this the app comes up as unreachable.
    WEBSITES_PORT = "8000"
  }
}
