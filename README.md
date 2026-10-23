# api.ludens

A fatia de conta do comprador usa os endpoints existentes de perfil, senha,
troca de e-mail e exclusão. As confirmações removem o cookie de refresh;
a exclusão desativa a conta preservando sua identidade para o histórico
comercial. Contratos e testes estão na
[documentação da conta](https://github.com/CodePenguinsCorp/docs.ludens/blob/feat/buyer-account-management/specs/identity-user-management/buyer-account.md).

Para usar o frontend Vercel com a API no seu PC, consulte o
[guia Docker + túnel](https://github.com/CodePenguinsCorp/docs.ludens/blob/master/team/local-api-vercel.md).

<p align="center">
  <img src=".github/assets/logo.png" alt="ludens" width="180">

  <h3 align="center">ludens</h3>

  <p align="center">
    Plataforma de Venda de Ingressos para Teatro Comunitário
  </p>
</p>

Backend da plataforma **Ludens** — a plataforma web de venda de ingressos de um
teatro comunitário: buscar espetáculos, reservar assentos, pagar por Pix e
receber o ingresso. API em **FastAPI**, com **monólito modular + DDD**,
**PostgreSQL** e **Docker**.

> Projeto acadêmico do Processo/Grupo 18 — disciplina de Manutenção e Melhoria de
> Software (Engenharia de Software, Centro Universitário Católica de Santa
> Catarina).

## O que este backend resolve

Hoje a venda acontece em dois canais desconectados (bilheteria física e vendas
informais pela internet) e **não existe uma fonte única de disponibilidade de
assentos por sessão** — o mesmo assento chega a ser vendido duas vezes. O
`api.ludens` é o serviço que se torna essa fonte única: expõe a API REST, valida
entrada, persiste estado, controla a disponibilidade de forma **atômica** (duas
compras simultâneas nunca excedem a capacidade da sessão) e dispara os efeitos
de pós-venda (e-mail de confirmação, estorno) de forma confiável.

O produto completo (problema, requisitos RF/RN, decisões de arquitetura) vive em
**[`gcarvalhow/docs.ludens`](https://github.com/gcarvalhow/docs.ludens)**. Este
README cobre o suficiente para entender, rodar e contribuir com o backend.

## Stack

| Camada | Tecnologia |
| --- | --- |
| Linguagem / framework | Python 3.12 · FastAPI |
| Persistência | PostgreSQL · SQLAlchemy (async) · Alembic (migrations) |
| Configuração | `pydantic-settings` (lê de `.env.local`) |
| Runtime | Docker / Docker Compose |
| Qualidade | Pytest (testes) — portão de merge |

## Arquitetura em resumo

Três padrões sustentam o backend (detalhe em
[`docs.ludens/backend/overview.md`](https://github.com/gcarvalhow/docs.ludens/blob/HEAD/backend/overview.md)
e nos [ADRs](https://github.com/gcarvalhow/docs.ludens/tree/HEAD/backend/design)):

- **DDD** — a regra de negócio vive isolada em `domain/`, em *aggregates* que só
  mudam de estado pelos próprios métodos (`raise_event → _apply → _when_*`).
- **Monólito modular** — um único serviço, dividido em cinco módulos de negócio
  isolados (`identity`, `catalog`, `booking`, `payment`, `notification`).
  Nenhum módulo importa o interno de outro — a comunicação é por evento ou por
  dependência pública exportada.
- **Outbox in-process** — um caso de uso nunca chama um efeito externo (e-mail,
  estorno) diretamente. O efeito vira uma linha na tabela `events`, na mesma
  transação que muda o domínio; um *relay* em background lê essa tabela a cada
  ~2 s e chama *handlers* Python registrados **no próprio processo — sem broker**.

### Estrutura do repositório

```text
src/app/
  config.py            # Settings (pydantic-settings)
  database.py           # AsyncEngine + AsyncSessionLocal
  dependencies.py       # get_db — uma transação por request
  main.py               # FastAPI app, CORS, /health, lifespan (sobe o relay)
  core/
    domain/             # Model, AggregateRoot, DomainEvent — base de todo módulo
    infrastructure/     # BaseRepository / AggregateRepository
    shared/             # errors, responses
  outbox/
    models.py           # tabela events
    registry.py         # register(event_type) / handlers_for(event_type)
    relay.py             # polling in-process, chama os handlers
  modules/               # identity, catalog, booking, payment, notification
                         #   (entram por spec, uma feature por vez)
  migrations/            # Alembic (env.py + versions/)
alembic.ini             # aponta para src/migrations; rodar da raiz do repo
docker/                  # docker-compose.Development.yml, docker-compose.Staging.yml
tests/                   # testes de domínio (sem DB) e de usecase (Postgres real)
```

> Os módulos de negócio são implementados **um por vez, a partir de uma spec** em
> [`docs.ludens/specs/`](https://github.com/gcarvalhow/docs.ludens/tree/HEAD/specs).
> Cada spec traz o mapa de arquivos por responsável e o passo a passo.

## Rodar localmente

A API sobe **via contêiner** — é o runtime padrão local e de pipeline (ver
[`docs.ludens/backend/overview.md`](https://github.com/gcarvalhow/docs.ludens/blob/HEAD/backend/overview.md)).
Não precisa de Python instalado na máquina, só **Docker** + **Docker Compose**.

```bash
git clone https://github.com/gcarvalhow/api.ludens
cd api.ludens

# 1. Configuração — copie o template e preencha o que estiver em branco
cp .env.example .env.local
#    gere a JWT_SECRET_KEY:  openssl rand -hex 32
#    DATABASE_URL já vem apontando para o hostname do contêiner do Postgres
#    (ludens-postgres-dev) — nunca localhost, a API roda na mesma rede Docker.

# 2. Suba o banco
docker compose -f docker/docker-compose.Development.yml up -d

# 3. Builde a imagem da API
docker build -t api-ludens .

# 4. Aplique as migrations (sobe um contêiner descartável, roda e sai)
docker run --rm --network ludens-dev --env-file .env.local api-ludens \
  alembic upgrade head

# 5. Rode a API
docker run -d --rm --name ludens-api --network ludens-dev \
  --env-file .env.local -p 8000:8000 api-ludens
```

- API em `http://localhost:8000` · OpenAPI interativo em `/docs` ·
  *health check* em `/health`.
- Logs: `docker logs -f ludens-api` · Parar: `docker stop ludens-api`.
- Depois de qualquer mudança de código, repita o passo 3 (rebuild da imagem)
  antes do passo 5 — o contêiner não recarrega sozinho (sem `--reload`, é o
  runtime de produção mesmo em dev).
- Semear o usuário admin (fora do fluxo público, ver `src/scripts/seed_admin.py`):
  ```bash
  docker run --rm --network ludens-dev --env-file .env.local \
    -e ADMIN_NAME="..." -e ADMIN_CPF="..." -e ADMIN_EMAIL="..." -e ADMIN_PASSWORD="..." \
    api-ludens python /app/src/scripts/seed_admin.py
  ```

Recriar o banco do zero (mudança de schema sem migration incremental):

```bash
docker compose -f docker/docker-compose.Development.yml down -v
docker compose -f docker/docker-compose.Development.yml up -d
docker run --rm --network ludens-dev --env-file .env.local api-ludens alembic upgrade head
```

## Variáveis de ambiente

Lidas de `.env.local` por `src/app/config.py`. Classificação de segurança em
[`docs.ludens/backend/security/configuration.md`](https://github.com/gcarvalhow/docs.ludens/blob/HEAD/backend/security/configuration.md):
**SECRET** (segredo forte, nunca no VCS), **SENSITIVE** (dado sensível),
**CONFIG** (parâmetro comum).

| Variável | Classe | Padrão (dev) | Para que serve |
| --- | --- | --- | --- |
| `ENVIRONMENT` | CONFIG | `development` | `development` / `staging` / `production`. Fora de dev, ativa TLS no transporte com o banco. |
| `DATABASE_URL` | SENSITIVE | `postgresql+asyncpg://ludens:ludens@ludens-postgres-dev:5432/ludens` | Conexão async com o Postgres. O host é o **nome do contêiner**, nunca `localhost`. |
| `JWT_SECRET_KEY` | SECRET | *(vazio)* | Assina o *access token* JWT. Gere com `openssl rand -hex 32`. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | CONFIG | `30` | Validade do *access token*. |
| `REFRESH_TOKEN_EXPIRE_DAYS` | CONFIG | `7` | Validade do *refresh token* (cookie `HttpOnly`). |
| `ALLOWED_ORIGINS` | CONFIG | `["http://localhost:3000"]` | Origens liberadas no CORS (lista JSON). Em produção inclui `https://web-ludens.vercel.app`. |
| `OUTBOX_RELAY_INTERVAL_SECONDS` | CONFIG | `2` | Intervalo de *polling* do relay do outbox. |

Cada feature acrescenta as próprias variáveis (ex.: `ABACATEPAY_*` e `SMTP_*` para
pagamento e e-mail, `RESERVATION_TTL_MINUTES` e `MAX_TICKETS_PER_CPF` para a
reserva). O `.env.example` lista todas, comentadas até a feature entrar.
**Nenhum segredo é versionado** — só o `.env.example`, com os campos SECRET em branco.

## Testes

Ao contrário da API em si, os testes rodam com Python local (é como o CI
roda também — ver [`backend/testing.md`](https://github.com/gcarvalhow/docs.ludens/blob/HEAD/backend/testing.md)),
não em contêiner:

```bash
pip install ".[dev]"      # Python 3.12
pytest -q                 # testes de domínio (sem DB) e de usecase (Postgres real via contêiner)
```

O foco dos testes são as **regras de negócio da camada de domínio** — reserva e
compra, controle de disponibilidade (concorrência), preço de meia-entrada,
reembolso. Estratégia completa em
[`docs.ludens/backend/testing.md`](https://github.com/gcarvalhow/docs.ludens/blob/HEAD/backend/testing.md).

**Portões de merge** (CI em `push`/PR para `master`): `pytest -q` verde ·
`docker build` limpo · **1 aprovação** de outro desenvolvedor.

## Plugin do time e fluxo de trabalho

O repo consome o plugin Claude Code
[`gcarvalhow/team.ludens`](https://github.com/gcarvalhow/team.ludens) — já
declarado em `.claude/settings.json` (`core` + `backend`). Para ativar na sua
máquina:

```bash
claude plugin marketplace add gcarvalhow/team.ludens
claude plugin install core@team-ludens --scope project
claude plugin install backend@team-ludens --scope project
# abra uma sessão nova do Claude Code e rode /team-ludens:setup
```

Isso carrega a skill `backend-architecture` (as regras de código deste repo) e o
fluxo Trunk-Based: `/team-ludens:tbd-start` (issue + branch) → implementação →
`/team-ludens:tbd-commit` → `/team-ludens:tbd-pr`.

**Convenções:** trunk é `master`; branches curtas em inglês (`feat/NN-slug`);
**Conventional Commits** em português no imperativo; issues e o backlog vivem no
[GitHub Project `@ludens`](https://github.com/orgs/gcarvalhow/projects/2).

## Documentação de referência

- [Arquitetura do backend](https://github.com/gcarvalhow/docs.ludens/blob/HEAD/backend/overview.md) · [ADRs](https://github.com/gcarvalhow/docs.ludens/tree/HEAD/backend/design)
- [Autenticação e variáveis de ambiente](https://github.com/gcarvalhow/docs.ludens/tree/HEAD/backend/security)
- [Guia de estilo](https://github.com/gcarvalhow/docs.ludens/blob/HEAD/backend/code-style.md) · [Testes e CI](https://github.com/gcarvalhow/docs.ludens/blob/HEAD/backend/testing.md)
- [Specs das features (N1)](https://github.com/gcarvalhow/docs.ludens/tree/HEAD/specs)
- [Ambiente de desenvolvimento](https://github.com/gcarvalhow/docs.ludens/blob/HEAD/team/development.md)

> **Status:** reservas, compra demonstrativa, emissão de ingressos e histórico
> do comprador estão implementados. A simulação não cria cobrança nem integra
> um gateway.
