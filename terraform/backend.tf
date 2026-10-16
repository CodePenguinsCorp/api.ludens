# Remote state — the storage account/container below are NOT managed by this
# Terraform (a backend can't provision the thing it depends on to exist first).
# Bootstrap them once by hand — see README.md.
terraform {
  backend "azurerm" {
    # Supplied through -backend-config; see backend.hcl.example.
  }
}
