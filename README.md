# Marketing & Sales Data Hub — API de ingestão do RD Station CRM

Fase 1 do projeto: uma API em FastAPI que autoriza sua conta no RD Station CRM,
extrai negociações/empresas/contatos/usuários/tarefas/reuniões via API, e recebe
webhooks para reconstruir o histórico real de movimentação do funil (etapas, donos,
handoff SDR → closer). Tudo grava num Postgres (Supabase), que depois alimenta o
dashboard em Python.

## 0. O que já está pronto neste repositório

```
config/settings.py          -> le variaveis do .env
database/models.py           -> todas as tabelas (raw, dim, fact)
database/connection.py       -> engine/session do SQLAlchemy
ingestion/rd_crm/auth.py     -> OAuth: troca de code, refresh automatico
ingestion/rd_crm/client.py   -> cliente HTTP com paginacao e retry
ingestion/rd_crm/*.py        -> um modulo por entidade (deals, contacts, ...)
ingestion/sync_all.py        -> orquestrador (full / incremental)
webhooks/processor.py        -> reconstroi stage_history/owner_history a partir dos webhooks
api/main.py + api/routes/*   -> FastAPI: /auth/rd/*, /webhooks/rd, /sync/rd/*, /health
scripts/init_db.py           -> cria as tabelas no banco
scripts/dump_sample.py       -> imprime 1 registro cru de cada entidade (para validar nomes de campo)
```

## 1. Criar o banco (Supabase)

1. Crie um projeto em [supabase.com](https://supabase.com) (ou use um Postgres seu).
2. Em **Project Settings → Database → Connection string → URI**, copie a string de conexão.
3. Cole em `DATABASE_URL` no seu `.env` (veja passo 3).

## 2. Criar o app no RD Station CRM

1. No RD Station CRM: **Configurações → Integrações → Central de Apps → Criar aplicativo**.
2. Defina a **Callback URL**. Em produção algo como:
   ```
   https://api.SEUDOMINIO.com.br/auth/rd/callback
   ```
   Em desenvolvimento, veja o passo 2.1 abaixo — precisa ser HTTPS.
3. Anote **Client ID** e **Client Secret**.
4. Cadastre também a **URL de Webhook** (mesma base, endpoint diferente):
   ```
   https://api.SEUDOMINIO.com.br/webhooks/rd?token=SEU_RD_WEBHOOK_TOKEN
   ```
   e assine os eventos `crm_deal_created` e `crm_deal_updated`.

### 2.1 Expondo seu localhost com HTTPS em desenvolvimento

O RD exige HTTPS na callback e no webhook. Enquanto não publica a API, use um túnel:

```bash
ngrok http 8000
```

Isso te dá algo como `https://abc123.ngrok-free.app`. Use:
- Callback: `https://abc123.ngrok-free.app/auth/rd/callback`
- Webhook: `https://abc123.ngrok-free.app/webhooks/rd?token=SEU_RD_WEBHOOK_TOKEN`

Troque pela URL definitiva assim que publicar a API (Render/Railway/AWS/etc).

## 3. Configurar o projeto

```bash
cp .env.example .env
```

Edite `.env` e preencha:
- `DATABASE_URL` (passo 1)
- `RD_CRM_CLIENT_ID`, `RD_CRM_CLIENT_SECRET`, `RD_CRM_REDIRECT_URI` (passo 2)
- `RD_WEBHOOK_TOKEN`: invente uma string aleatória forte — é o token que valida que o
  request em `/webhooks/rd` realmente veio do seu app RD (não há assinatura HMAC
  documentada de forma consistente, então usamos um token na querystring como camada
  mínima de proteção).

## 4. Instalar dependências e criar as tabelas

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell/cmd)
# source .venv/bin/activate     # Linux/Mac

pip install -r requirements.txt
python -m scripts.init_db
```

Isso cria todas as tabelas (`crm_deals`, `crm_deal_stage_history`, `crm_deal_owner_history`,
`crm_deal_events`, `crm_organizations`, `crm_contacts`, `crm_users`, `crm_tasks`,
`crm_meetings`, `crm_pipelines`, `crm_stages`, `crm_lost_reasons`, `rd_oauth_tokens`,
`raw_crm_webhook_events`, `sync_state`) no seu Postgres.

## 5. Subir a API

```bash
python run_api.py
```

A API sobe em `http://localhost:8000`. Documentação interativa em `http://localhost:8000/docs`.

Se estiver usando ngrok, deixe o túnel apontando para a porta 8000 (`ngrok http 8000`) rodando
em paralelo.

## 6. Autorizar o app (OAuth)

Abra no navegador:
```
http://localhost:8000/auth/rd/login
```
(ou, se preferir, monte a URL manualmente com os dados do passo 2 e abra direto).

Você será redirecionado para o RD Station, vai autorizar o app, e o RD vai chamar
`/auth/rd/callback?code=...` automaticamente. Se aparecer `"status": "success"`, o
token já está salvo em `rd_oauth_tokens` e o refresh automático está funcionando.

## 7. Validar os nomes de campo antes da carga completa

A API v2 do RD CRM pode variar nomes de campo entre contas/planos. Antes de rodar a
carga completa, confira um payload real:

```bash
python -m scripts.dump_sample deals
python -m scripts.dump_sample organizations
python -m scripts.dump_sample contacts
```

Compare o JSON impresso com o que `ingestion/rd_crm/deals.py::extract_deal_fields`
(e os equivalentes em `organizations.py`/`contacts.py`) estão lendo. Se algum campo
não bater (ex.: o dono da negociação vem em `owner` e não em `user`), ajuste **só
aquela função** — o resto do pipeline não muda.

Preste atenção especial em:
- `/deals` — como vêm `deal_stage`, `deal_pipeline`, `organization`, `contacts`, `user`/`owner`
- `/meetings` — confirme se esse endpoint existe mesmo, ou se reuniões vêm dentro de
  `/tasks` com `type == "meeting"` (nesse caso, ajuste `ingestion/rd_crm/meetings.py`
  ou passe a filtrar dentro de `tasks.py`)

## 8. Rodar a carga inicial completa

```bash
python -m ingestion.sync_all full
```

Isso busca TODOS os usuários, pipelines/etapas, motivos de perda, empresas, contatos,
negociações, tarefas e reuniões, e semeia a primeira linha de `crm_deal_stage_history`
e `crm_deal_owner_history` para cada negociação (o "estado atual" vira o ponto de
partida do histórico).

Alternativa via API (roda em background, útil se preferir disparar por HTTP):
```bash
curl -X POST http://localhost:8000/sync/rd/full
```

## 9. Manter atualizado: incremental + webhooks

Dali em diante, duas coisas mantêm o banco em dia:

1. **Webhooks** (`/webhooks/rd`) — chegam em tempo real a cada `crm_deal_created`/
   `crm_deal_updated` e são a fonte da verdade para o histórico de etapas e donos
   (é o `webhooks/processor.py` que fecha uma linha de `stage_history`/`owner_history`
   e abre a próxima, e deduz SDR x closer pelo primeiro/segundo dono da negociação).

2. **Sincronização incremental** — roda periodicamente como rede de segurança (cobre
   entidades sem webhook, como tarefas/reuniões/empresas/contatos, e qualquer evento
   que o webhook eventualmente perca):
   ```bash
   python -m ingestion.sync_all incremental
   ```
   Em produção isso roda sozinho pelo GitHub Actions
   (`.github/workflows/sync-incremental.yml`), a cada 15 min — ver seção 10.

## 10. Deploy: dashboard no ar + sync automático

### 10.1 Sincronização a cada 15 min (GitHub Actions)

O workflow `sync-incremental.yml` roda `python -m ingestion.sync_all incremental`
**dentro do próprio runner**, falando direto com o Postgres. Ele não passa pela API
do Render de propósito: no plano free o serviço dorme após ~15 min sem tráfego, e a
sincronização (que roda como `BackgroundTask`) podia ser morta no meio, deixando o
`sync_state` parcialmente avançado sem sinal nenhum de erro.

Configure em **Settings → Secrets and variables → Actions** do repositório:

| Secret | Para quê |
| --- | --- |
| `DATABASE_URL` | conexão do Postgres (Supabase) |
| `RD_CRM_CLIENT_ID` | OAuth do RD CRM |
| `RD_CRM_CLIENT_SECRET` | OAuth do RD CRM |
| `RD_CRM_REDIRECT_URI` | OAuth do RD CRM |

Duas limitações do agendador do GitHub, que valem conhecer:
- o disparo é *melhor esforço* — sob carga atrasa alguns minutos (a sincronização é
  incremental e idempotente, então atraso só adia dados, não duplica nada);
- o GitHub **desativa schedules em repositórios sem commits há 60 dias** — se o
  projeto ficar parado, reative o workflow na aba Actions.

O job termina com código de saída != 0 quando alguma entidade falha, então uma
rodada quebrada aparece vermelha na aba Actions em vez de passar despercebida.

### 10.2 Dashboard no Streamlit Community Cloud

1. Em [share.streamlit.io](https://share.streamlit.io), conecte este repositório.
2. **Main file path**: `app/Home.py`.
3. Em **Advanced settings → Secrets**, cole:
   ```toml
   DATABASE_URL = "postgresql://..."
   ```
   Só isso: o dashboard lê apenas o banco. As credenciais do RD CRM são opcionais no
   `config/settings.py` justamente para o dashboard não precisar delas.

As dependências vêm do `requirements.txt` da raiz — o Streamlit Cloud sempre usa esse
arquivo, por isso `streamlit`/`plotly`/`pandas` moram lá junto com as libs da API.

O app hiberna após alguns dias sem acesso e acorda no primeiro acesso seguinte. Se a
espera incomodar, um Web Service no Render (plano pago) não hiberna.

## 11. Onde isso te deixa

Depois desses passos você tem, no Postgres:
- `crm_deals` com o estado atual de cada negociação, incluindo `sdr_owner_rd_id`,
  `closer_owner_rd_id` e `handoff_at` calculados automaticamente
- `crm_deal_stage_history` pronta para calcular aging/velocity por etapa
- `crm_deal_owner_history` pronta para performance individual de SDR/closer
- `crm_deal_events` como trilha de auditoria de tudo que mudou em cada negociação
- `crm_organizations`, `crm_contacts`, `crm_users`, `crm_tasks`, `crm_meetings`,
  `crm_pipelines`, `crm_stages`, `crm_lost_reasons` como dimensões de apoio

## 12. Meta Ads (Marketing API)

Sincroniza campanha, conjunto de anúncios, anúncio e performance diária (gasto,
impressões, cliques, CTR, CPC, CPM, ações) da conta de anúncios do Meta
(Facebook/Instagram). Roda junto com o resto no mesmo `python -m ingestion.sync_all`
e no mesmo workflow do GitHub Actions — não precisa de agendador separado.

### 12.1 Criar o app e gerar um token que não expira

Diferente do RD CRM, aqui a automação **não usa** o fluxo de OAuth de usuário comum
(token expira em 60 dias, exigiria renovação manual). Use um **Usuário do Sistema**,
que pode gerar um token com expiração "Nunca":

1. [developers.facebook.com/apps](https://developers.facebook.com/apps) → **Criar
   app** → tipo **Empresa** → associe ao Business Manager da conta de anúncios.
2. **Adicionar produtos** → **Marketing API**.
3. Em **Configurações → Básico**, anote `App ID` e `Chave secreta do app`.
4. [business.facebook.com/settings](https://business.facebook.com/settings) →
   **Usuários do sistema** → criar um → em **Ativos atribuídos**, vincule a conta de
   anúncios (visualização é suficiente).
5. No usuário do sistema → **Gerar novo token** → selecione o app do passo 1 →
   permissão `ads_read` → expiração **Nunca**.
6. Anote o ID da conta de anúncios no formato `act_XXXXXXXXXXX` (Gerenciador de
   Anúncios).

### 12.2 Configurar

Adicione ao `.env` (local) e aos mesmos lugares onde já estão `DATABASE_URL`/
`RD_CRM_*` (GitHub Secrets para o sync automático; não precisa no Streamlit Cloud,
que só lê o banco):

```
META_APP_ID=...
META_APP_SECRET=...
META_ACCESS_TOKEN=...          # token do usuario do sistema, "Nunca" expira
META_AD_ACCOUNT_ID=act_...
```

Sem essas variáveis, o sync **pula** a etapa do Meta Ads silenciosamente (não
quebra o resto) — é seguro fazer merge/deploy deste código antes de ter as
credenciais em mãos.

### 12.3 Rodar a carga inicial

```bash
python -m ingestion.sync_all full
python -m scripts.init_db   # cria as tabelas meta_* se ainda nao existirem
```

A partir daí, a sincronização incremental já cuida da atualização — inclusive
re-buscando os últimos 8 dias a cada rodada, porque o Meta revisa métricas de
conversão por alguns dias após o fato (ver comentário em
`ingestion/meta_ads/sync.py`).

### 12.4 Limitação atual: sem cruzamento com o funil do CRM

`database/views_meta_ads.sql` traz a performance do Meta isolada (por campanha,
conjunto, anúncio e dia). Cruzar isso com o funil do RD CRM (custo por reunião
realizada, por etapa) exige que a negociação carregue a UTM/campanha de origem —
hoje `crm_deals.custom_fields` está vazio em todas as negociações, então esse
vínculo não existe ainda. Isso se resolve configurando o RD Station Marketing para
copiar a UTM/origem da conversão para o campo customizado da negociação; assim que
esse dado começar a chegar, o cruzamento entra como nova view.

## Próximos passos (fora do escopo desta primeira entrega)

- Views/materialized views em SQL para as métricas de SDR, Closer e Pipeline
  (aging, win rate, show rate, pipeline movement)
- Dashboard em Streamlit consumindo essas views (não a API do RD diretamente)
- Fase 2: RD Station Marketing, GA4, Google/LinkedIn Ads, attribution completo
  (Meta Ads já entrou — ver seção 12)
- Deploy da API (Render/Railway/Fly.io/AWS) com HTTPS definitivo e o agendador
  de sincronização incremental
