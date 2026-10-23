# Agente de atendimento (TEO) — arquitetura e operação

Documento de referência do agente de WhatsApp da Máquina.ISP.
Última revisão: 22/10/2026.

---

## 1. O que é

**TEO** é um agente de vendas que atende leads pelo WhatsApp, 24h, sem
intervenção humana. Ele conversa, responde dúvidas sobre o produto consultando
uma base de conhecimento própria, move o card no CRM conforme o lead avança e
entrega o agendamento para a consultora.

**O objetivo dele é a reunião marcada, não o contrato assinado.** Ele não fecha
venda por texto e não confirma reunião na agenda — sugere horários reais e
transfere para a consultora confirmar.

O cérebro é o Claude (`claude-sonnet-5`) rodando um laço de uso de ferramentas:
a cada mensagem do lead, o modelo decide se responde direto ou se precisa
consultar algo / agir no CRM antes.

---

## 2. Infraestrutura

Cinco peças, cada uma com um papel distinto:

| Onde | O que roda | Papel no agente |
|---|---|---|
| **Render** | FastAPI (`api/main.py`) | Recebe os webhooks do WhatsApp e do RD CRM. É o que acorda o agente. |
| **Azure Functions** | `function_app.py` | Dois timers de 15 em 15 min: sincronização do CRM e lembretes de reunião. |
| **Postgres (Supabase)** | — | Fonte única de verdade: conversas, espelho do CRM, base de conhecimento, custos. |
| **Streamlit Cloud** | `app/` | Dashboard. Páginas 6 (monitoramento do agente) e 7 (convite manual). |
| **APIs externas** | — | Meta WhatsApp Cloud API v21.0, Anthropic, Microsoft Graph, RD Station CRM. |

### Fluxo entre elas

```
Lead no WhatsApp
      │
      ▼
Meta Cloud API ──POST──► Render  /webhooks/whatsapp
                              │  (valida HMAC, grava, responde 200 na hora)
                              ▼
                         processamento em background
                              │
                              ▼
                    whatsapp/processor.py ──► ingestion/llm/agent.py
                              │                     │
                              │                     ├─► Anthropic (Claude)
                              │                     ├─► Postgres (base de conhecimento)
                              │                     ├─► RD CRM (mover etapa, criar card/tarefa)
                              │                     └─► Microsoft Graph (horários livres)
                              ▼
                    Meta Cloud API ──► resposta chega no WhatsApp do lead
```

O webhook **responde 200 imediatamente** e processa depois. O Meta considera
falha qualquer resposta que demore e reenvia o mesmo evento — por isso a
gravação e o processamento são separados, com deduplicação por hash do corpo.

### GitHub Actions está desligado

Os workflows `sync-incremental.yml` e `lembretes-reuniao.yml` existem, mas só
rodam manualmente (`workflow_dispatch`). O agendamento automático foi migrado
para as Azure Functions em 14/09/2026: repositório privado no GitHub Free tem
2.000 min/mês, dois crons de 15 em 15 min estouraram isso em duas semanas, e
sem limite de gasto configurado o GitHub **parou de criar runs em silêncio** —
sem erro, sem run vermelho.

---

## 3. Como uma conversa começa

Quatro portas de entrada. Todas desembocam no mesmo motor de conversa.

### 3.1 Lead de tráfego pago (automático, proativo)

Caminho principal. O lead preenche o formulário do anúncio, o RD Marketing cria
o card no CRM, o CRM dispara webhook para o Render, e
`webhooks/processor.py::_iniciar_atendimento_agente` manda a **mensagem de
abertura** sem esperar o lead escrever.

Três condições:

- origem em `WHATSAPP_AGENT_TRIGGER_SOURCE_RD_IDS` **ou** `utm_medium` em
  `WHATSAPP_AGENT_TRIGGER_UTM_MEDIUMS` (ambos `paid_social` por padrão)
- **e** o card na etapa "Primeira Conexão"

Origem e UTM são alternativos entre si porque o campo "Fonte" do RD é pouco
confiável: em 19/09/2026 uma negociação real de tráfego pago (com `fbclid` e
`utm_source=Instagram_Reels`) chegou com fonte "Desconhecido" e por isso nunca
recebeu a abertura. A etapa é condição adicional, a pedido do time: se o card
já saiu da coluna de entrada, alguém já fez contato manual e o agente não deve
entrar por cima.

A abertura é sempre **template pré-aprovado**, nunca texto livre — regra do
Meta: quem nunca escreveu para o nosso número só pode ser contatado por
template. A conversa de verdade (Claude, base de conhecimento, ações no CRM) só
começa quando o lead **responder**.

### 3.2 Lead escreve primeiro

O webhook grava a mensagem e o agente responde, desde que passe pelas travas da
seção 4.

### 3.3 Convite manual da SDR (Melhor Venda)

Prospecção ativa. A SDR usa a página **Convite Agente** do dashboard para
oferecer a um contato específico uma demonstração da conversa agêntica.

O convite é sempre para **um contato**, nunca para "a negociação": um card de
Melhor Venda costuma puxar vários contatos, e o RD não separa por pessoa dentro
da negociação.

### 3.4 QR code (desde 22/10/2026)

O lead escaneia um QR code e cai direto no WhatsApp do agente, **sem card
nenhum no CRM**.

O QR code aponta para um link com texto já preenchido:

```
https://wa.me/5554923675930?text=Quero%20conhecer%20a%20M%C3%A1quina%20ISP
```

Esse texto é o que identifica a origem. Sem ele, atender quem não tem card
significaria responder qualquer número desconhecido — abrindo o agente, e o
custo de API, para engano, spam e robô. A comparação ignora acento e caixa e
vale para qualquer mensagem recebida, então funciona mesmo se o lead editar o
texto antes de enviar, ou escanear o QR meses depois de uma conversa antiga.

Nesse fluxo o agente **coleta os dados e cria o card**: nome do provedor, nome
da pessoa, quantidade de assinantes e e-mail. O telefone vem do próprio número
que está conversando — ele nunca pergunta.

O card nasce em **Primeira Conexão**, origem **Feiras e Eventos**, com a
**Miriã** como dona.

---

## 4. Quem o agente atende

Três travas, nesta ordem, em `whatsapp/processor.py::_responder_com_agente`:

| Trava | O que exige | Por que existe |
|---|---|---|
| **Número liberado** | Se `WHATSAPP_AGENT_RESTRICT_TO_PHONE_NUMBERS` estiver preenchido, só responde quem está na lista | Piloto controlado. Hoje está vazio = sem restrição |
| **Origem** | Negociação de tráfego pago, **ou** veio do QR code, **ou** o agente já respondeu antes | Evita gastar API com número desconhecido |
| **Etapa** | Card em "Primeira Conexão" para **iniciar**; conversa já em andamento passa direto | Card adiante significa humano no comando |

**Exceções que liberam:** número na lista de teste
(`WHATSAPP_AGENT_TEST_PHONE_NUMBERS`) passa por tudo; e qualquer lead que já
recebeu mensagem do agente antes continua sendo atendido — é isso que faz um
lembrete de reunião abrir a porta para o lead responder "preciso remarcar".

### A corrida entre a mensagem e o card

O fluxo real é: lead preenche o formulário → é jogado no WhatsApp com mensagem
pré-preenchida → RD Marketing cria o card. **A mensagem costuma chegar antes do
card.** Na primeira tentativa o agente pula, porque a trava de origem não acha
negociação nenhuma.

Dois scripts fecham essa janela, rodando logo depois do sync:

- `reprocessar_whatsapp_pendentes.py` — lead escreveu antes de o card existir
- `reprocessar_leads_trafego_pago.py` — card nasceu antes de o contato
  sincronizar, então a abertura não achou telefone

---

## 5. Anatomia de uma resposta

O que acontece entre a mensagem do lead e a resposta:

1. **Grava a mensagem** e responde 200 ao Meta.
2. **Travas** da seção 4.
3. **Marca como lida** e mostra "digitando".
4. **Debounce de 3 segundos.** Se chegou mensagem mais nova do mesmo número
   nesse intervalo, desiste — a mais nova processa o lote inteiro. Sem isso,
   três mensagens seguidas disparavam três turnos em paralelo, cada um sem ver
   os outros dois, gerando respostas quase idênticas em sequência (visto em
   produção em 13/10/2026).
5. **Carrega as últimas 20 mensagens** da conversa como histórico.
6. **Resolve o nome do lead** — prioriza o CRM sobre o nome de perfil do
   WhatsApp, que o próprio dono escolhe e costuma ser apelido ou emoji.
7. **Chama o Claude**, com até 6 voltas de ferramenta e 1024 tokens de saída.
8. **Junta o texto** de todas as voltas, podando frase repetida.
9. **Envia** e grava a resposta.

Nada disso pode derrubar o recebimento do webhook: falha em qualquer etapa vira
log, não exceção.

### Cache do prompt

O system prompt é dividido em dois blocos. O **estático** (identidade, regras,
definição das ferramentas) leva marcação de cache e é idêntico em toda
chamada — paga preço cheio uma vez e depois lê do cache, cerca de 90% mais
barato. O **dinâmico** (data/hora, nome do lead, se o lead tem card) fica fora
do cache de propósito. Juntar a data ao bloco estático quebraria o cache em
silêncio, sem erro nenhum.

---

## 6. As ferramentas

O que o agente pode fazer, em `ingestion/llm/agent.py`:

| Ferramenta | O que faz |
|---|---|
| `consultar_base_conhecimento` | Busca na base antes de afirmar qualquer fato sobre o produto |
| `criar_negociacao_no_crm` | Cria organização + contato + negociação para lead de QR code sem card |
| `sinalizar_interesse` | Move o card para "Interesse Identificado" |
| `consultar_horarios_disponiveis` | Lê a agenda real e devolve horários livres para **sugerir** |
| `transferir_para_consultora` | Cria tarefa para a consultora confirmar o agendamento |
| `reagendar_reuniao` | Atualiza o horário de uma reunião já marcada |
| `encaminhar_para_humano` | Desconto fora da tabela, ou pergunta que a base não cobre |

### O fluxo de reunião, em três etapas

`sinalizar_interesse` → `consultar_horarios_disponiveis` → `transferir_para_consultora`

**O agente sugere horário real, mas nunca confirma.** Essa separação é
deliberada, de 15/10/2026: antes ele criava o evento direto na agenda e errava
demais tentando bater com a disponibilidade real. Hoje consulta o Microsoft
Graph só para sugerir, registra o horário aceito numa tarefa, e a consultora
confirma de verdade com o lead.

A tarefa é criada com tipo `call`, **nunca** `meeting`: o script de lembretes
trata tarefa `meeting` como reunião já confirmada e dispararia lembrete falso
antes de a consultora ter confirmado qualquer coisa.

A agenda é consultada por app, não por usuário — um App Registration no Azure
AD com permissão `Calendars.ReadWrite` de aplicação, o que permite ler a agenda
de qualquer pessoa do domínio sem cada uma precisar autorizar individualmente.

### Regra de preço

Quando o lead pergunta o valor, o agente informa **só a mensalidade e o setup**
correspondentes ao número de assinantes que o lead já informou. Ele nunca cita
a faixa em si ("você entra na faixa de 10.000 a 14.999") — só os dois valores
finais, como se fossem o preço dele, não o de uma categoria. Se o lead ainda
não disse quantos assinantes tem, ele pergunta antes. Nunca chuta nem
arredonda.

---

## 7. Base de conhecimento (RAG)

14 blocos em `knowledge_chunks`, em quatro categorias:

- **produto** — visão geral, dores que resolve, resultados esperados
- **agente** — o que cada agente faz (TEO vendas, Lia cobrança, Gabi suporte,
  Noah retenção)
- **objecao** — tabela de preços, teste grátis, custos adicionais, integrações
  com ERP, tempo de setup
- **processo** — implementação, próximo passo

A busca usa **busca textual nativa do Postgres** (`to_tsvector` / `ts_rank`) em
português, não embedding vetorial. A pergunta do lead é normalizada com
stemming, e os termos viram **OU** em vez de **E** — exigir todos os termos era
rígido demais para pergunta de lead real, que raramente usa as mesmas palavras
do texto da base.

Se a busca não traz nada, o agente admite que vai confirmar e chama
`encaminhar_para_humano`. **Ele nunca inventa fato sobre o produto.**

Para editar: mude `ingestion/llm/knowledge_base_seed.py` e rode
`python -m scripts.load_knowledge_base`.

---

## 8. Lembretes e no-show

`scripts/enviar_lembretes_reuniao.py`, a cada 15 min pela Azure Function.

Três lembretes por reunião: **véspera às 20h**, **manhã às 8h** e **1h antes**.
Se a tarefa continuar aberta no RD 45 min depois do horário, o card vai para
**No-show** — sinal de que a reunião não aconteceu e ninguém tratou isso.

A fonte da verdade é `crm_tasks.due_at`, o campo que a SDR já preenche
manualmente há meses ao marcar reunião no RD. Não é preciso consultar o
Microsoft Graph para saber quando é a reunião.

É idempotente: uma linha por negociação + horário. Reagendamento cria linha
nova e os lembretes recomeçam para o novo horário.

> **Atenção:** `whatsapp_agent_confirmacoes_reuniao_agendada_ativas` está
> **desligado** desde 17/09/2026. Negociações e tarefas sumiam do RD minutos
> depois de qualquer escrita do agente em quem já estava em "Reunião Agendada",
> reproduzido várias vezes no mesmo dia, inclusive com lead real. O RD não tem
> log de auditoria por API para investigar a causa. Está desligado em código,
> não em variável de ambiente, justamente para não ser reativado sem querer.

---

## 9. Dados

| Tabela | O que guarda |
|---|---|
| `whatsapp_webhook_events` | Payload cru de cada webhook, com deduplicação |
| `whatsapp_messages` | Toda mensagem, nos dois sentidos. **É o histórico da conversa** |
| `knowledge_chunks` | Base de conhecimento |
| `llm_call_log` | Uma linha por chamada ao Claude, com tokens e custo |
| `whatsapp_conversation_cost` | Custo de mensageria do Meta |
| `agenda_eventos_microsoft` | Eventos criados na agenda |
| `agente_lembretes_reuniao` | Controle de idempotência dos lembretes |
| `crm_*` | Espelho do RD CRM |

O agente **não tem memória própria**: o contexto da conversa é reconstruído a
cada mensagem a partir de `whatsapp_messages`. Por isso o comando `/reset`
apaga o histórico daquele telefone e recomeça do zero, sem tocar no CRM nem no
log de custo.

### O espelho local e o card recém-criado

O agente lê o CRM do espelho local, que o sync atualiza a cada 15 min. Quando
ele cria um card (fluxo de QR code), as três entidades são gravadas no espelho
**na mesma transação**. Sem isso, o card só apareceria no próximo sync e, até
lá, o agente trataria a mensagem seguinte como se o lead ainda não tivesse card
— criando card atrás de card na mesma conversa.

---

## 10. Custo

337 conversas reais e 102 de teste acumularam **US$ 3,83** em API do Claude —
cerca de **US$ 0,01 por conversa**, graças ao cache do prompt.

O custo de mensageria do WhatsApp é separado, vem da própria API do Meta e é
sincronizado para `whatsapp_conversation_cost`.

A página **Agente** do dashboard mostra os dois.

---

## 11. Configuração

Tudo em `config/settings.py`, sobrescrito por variáveis de ambiente.

| Variável | Para que serve |
|---|---|
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Cérebro. Modelo padrão `claude-sonnet-5` |
| `WHATSAPP_ACCESS_TOKEN` / `WHATSAPP_PHONE_NUMBER_ID` | Envio pelo Meta |
| `WHATSAPP_APP_SECRET` | Valida a assinatura HMAC do webhook. **Vazio desliga a validação** |
| `WHATSAPP_VERIFY_TOKEN` | Handshake inicial do webhook |
| `WHATSAPP_AGENT_TEMPLATE_NAME` | Template da abertura proativa |
| `WHATSAPP_AGENT_RESTRICT_TO_PHONE_NUMBERS` | Piloto. Vazio = sem restrição |
| `WHATSAPP_AGENT_TEST_PHONE_NUMBERS` | Números que passam por todas as travas |
| `WHATSAPP_AGENT_QRCODE_MARKER` | Texto do link do QR code |
| `WHATSAPP_AGENT_PAID_TRAFFIC_MARKER` | Marca que identifica tráfego pago |
| `WHATSAPP_AGENT_RESET_KEYWORD` | Palavra que zera a conversa (`/reset`) |
| `MICROSOFT_CLIENT_ID` / `_SECRET` / `_TENANT_ID` | Agenda |

Valores fixos em código, por decisão deliberada: etapas do funil, origem do QR
code (Feiras e Eventos) e dono padrão dos cards criados (Miriã Martins).

---

## 12. Operação

### Testar sem tocar em nada

```bash
python -m scripts.conversar_com_agente "mensagem do lead" --historico conversa.json
```

Roda sempre em modo teste: nenhuma escrita no CRM, nenhuma mensagem enviada.

### Quando o lead não recebeu resposta

Na ordem:

1. A mensagem foi gravada? Olhe `whatsapp_messages` pelo telefone.
2. Se foi gravada e não respondida, é trava. Os logs do Render dizem qual.
3. O card existe e é de tráfego pago? Se ainda não sincronizou, rode
   `python -m scripts.reprocessar_whatsapp_pendentes`.
4. O card passou de "Primeira Conexão" antes de o agente engajar? Então um
   humano assumiu — comportamento correto, não falha.

### Quando o agente responde errado sobre o produto

É a base de conhecimento, não o prompt. Edite
`ingestion/llm/knowledge_base_seed.py` e rode
`python -m scripts.load_knowledge_base`.

### Publicar mudanças

O webhook roda no Render a partir da branch `main` — mudança no agente só vale
em produção depois do push e do rebuild. As Azure Functions são publicadas à
parte.

---

## 13. Limitações conhecidas

- **Confirmação de reunião desligada** para quem já está em "Reunião Agendada"
  (seção 8), à espera do suporte do RD.
- **A resposta pode sair truncada.** Quando o modelo se interrompe no meio de
  uma frase para chamar uma ferramenta, a poda de repetição pode descartar a
  versão completa da frase e deixar só o fragmento. Observado em 22/10/2026.
- **O agente não envia e-mail.** Ele pede o e-mail como canal da consultora; a
  nutrição é do RD Marketing, por cadência baseada na etapa do funil.
- **A API do RD não apaga negociação.** Card de teste só pode ser arquivado como
  perdido por API, ou apagado na interface.
- **A listagem do RD não tem ordem estável.** Uma leitura completa devolve o
  total certo, mas de 441 a 591 ids distintos a cada passada. Onde isso importa,
  o código une passadas até fechar o total.
