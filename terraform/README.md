# terraform/

> Migração CodePenguinsCorp: os recursos descritos abaixo são da equipe anterior.
> Siga o [guia da nova infraestrutura](https://github.com/CodePenguinsCorp/docs.ludens/blob/master/team/infra-codepenguins.md).
> Copie e ajuste `backend.hcl.example` e use `terraform init -backend-config=backend.hcl`.

Infraestrutura Azure de `api.ludens`: Resource Group, Azure Database for PostgreSQL Flexible
Server, Azure Communication Services (e-mail transacional) e o Azure App Service (Linux,
container) que roda a API. Segredos (senha do Postgres, `JWT_SECRET_KEY`, connection string do
ACS) são gerados/lidos pelo próprio Terraform e expostos só via Application Settings do App
Service — sem Key Vault, proporcional à escala do projeto.

## E-mail transacional (Azure Communication Services)

O módulo `acs` provisiona um `azurerm_communication_service` + `azurerm_email_communication_service`
com domínio **gerenciado pelo Azure** (`AzureManagedDomain` — algo como `xxxx.azurecomm.net`,
verificado automaticamente, sem precisar de registro DNS) — mesma lógica de "sem domínio próprio
por enquanto" da seção abaixo. `EMAIL_BACKEND` já vai como `"acs"` nas Application Settings do
App Service, com `ACS_CONNECTION_STRING`/`ACS_SENDER_ADDRESS` preenchidos pelos outputs desse
módulo. Quando existir um domínio próprio de verdade, trocar `domain_management` pra
`"CustomerManaged"` em `modules/acs/main.tf` (exige verificação DNS, que hoje não existe).

## Bootstrap do state remoto (uma vez só, manual)

**Já feito** (2026-09-21) — `rg-ludens-tfstate`/`ludensterraformstate` existem em Brazil South.
A região do state remoto é independente de `var.location` (a infra real hoje vive em Central US,
ver `variables.tf`) — não precisa mover o Storage Account do state pra acompanhar, não há
latência relevante entre Terraform rodando localmente/no CI e o Storage Account do state.

O backend `azurerm` (`backend.tf`) guarda o `.tfstate` num Storage Account — mas esse Storage
Account não pode ser gerenciado pelo próprio Terraform que depende dele pra existir primeiro.
Criar uma vez, à mão, com a Azure CLI (`az login` antes) — comandos abaixo só se precisar
recriar do zero (ex. novo ambiente):

```bash
az group create --name rg-ludens-tfstate --location "Brazil South"

az storage account create \
  --name ludensterraformstate \
  --resource-group rg-ludens-tfstate \
  --location "Brazil South" \
  --sku Standard_LRS \
  --encryption-services blob

az storage container create \
  --name tfstate \
  --account-name ludensterraformstate
```

Se `ludensterraformstate` já estiver em uso por outra conta Azure (nome de Storage Account é
único globalmente), troque o nome aqui e em `backend.tf`.

## Uso

```bash
cd terraform
terraform init      # baixa os providers e conecta no backend remoto
terraform fmt -check
terraform validate
terraform plan       # nunca cria/muda nada sozinho
terraform apply       # só depois de revisar o plan — cria recursos reais, tem custo
```

Autenticação com o Azure: `az login` (interativo) ou variáveis `ARM_CLIENT_ID` /
`ARM_CLIENT_SECRET` / `ARM_SUBSCRIPTION_ID` / `ARM_TENANT_ID` (service principal — é assim que o
pipeline de deploy (`.github/workflows/deploy.yml`) autentica via GitHub Actions).

## Sem domínio próprio

Sem orçamento pra registrar um domínio — a API fica no hostname gratuito padrão do App Service
(`output.app_service_hostname`, `ludens-api.azurewebsites.net`). Domínio próprio ficaria pra uma
issue nova se/quando isso for viável (a antiga #57 partia de uma premissa errada sobre o domínio
já ter sido resgatado e foi excluída) — não bloqueia o resto.

## Status (2026-09-21)

Deploy real rodado — os 12 recursos deste diretório existem em produção (Central US), a imagem
`ghcr.io/gcarvalhow/api.ludens` está publicada e **pública** no GHCR, as migrations do Alembic já
rodaram contra o Postgres real, e `/health`/`/api/catalog/shows` respondem em produção. Nada
pendente na Workstream D de infra.
