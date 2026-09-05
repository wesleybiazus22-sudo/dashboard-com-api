-- Views analiticas do funil comercial (Maquina ISP + ThunderIA).
-- Rode isto no SQL Editor do Supabase depois de aplicar o mapeamento canonico
-- (python -m ingestion.canonical_funnel). Reaplique sempre que uma view mudar --
-- "create or replace view" e seguro rodar de novo a qualquer momento.

-- Coluna nova em crm_pipelines (necessaria antes das views abaixo).
alter table crm_pipelines add column if not exists product_group varchar;
create index if not exists ix_crm_pipelines_product_group on crm_pipelines (product_group);

-- Colunas novas nas tabelas do Melhor Venda (seguro rodar mesmo se as tabelas
-- ja tiverem sido criadas antes por scripts/init_db.py sem essas colunas).
alter table mv_campaigns add column if not exists sdr_name varchar;
alter table mv_campaigns add column if not exists label varchar;
alter table mv_campaign_companies add column if not exists cnpj_mv varchar;
create index if not exists ix_mv_campaign_companies_cnpj_mv on mv_campaign_companies (cnpj_mv);
alter table mv_campaign_companies add column if not exists suggested_deal_rd_id varchar;
alter table mv_campaign_companies add column if not exists suggested_organization_rd_id varchar;
alter table mv_campaign_companies add column if not exists suggested_score numeric(4, 3);

-- Razao social (custom_fields['razao-social']) -- essencial pro cruzamento com o
-- Melhor Venda, que exporta razao social, nao o nome fantasia.
alter table crm_organizations add column if not exists legal_name varchar;
create index if not exists ix_crm_organizations_legal_name on crm_organizations (legal_name);


-- Uma linha por negociacao, com pipeline/etapa ja resolvidos e agrupados por produto.
-- So inclui negociacoes de pipelines mapeados em PIPELINE_GROUP_MAPPING (Maquina ISP,
-- ThunderIA) -- Projetos/Alocacoes e Relacionamento/CS ficam de fora do funil comercial.
create or replace view v_deal_funnel as
select
    d.rd_id as deal_id,
    d.name as deal_name,
    d.amount,
    d.status,
    d.pipeline_rd_id,
    p.product_group,
    p.name as pipeline_name,
    d.stage_rd_id,
    s.name as stage_name,
    s.canonical_stage,
    s."order" as stage_order,
    d.current_owner_rd_id,
    d.sdr_owner_rd_id,
    d.closer_owner_rd_id,
    d.handoff_at,
    d.organization_rd_id,
    d.lost_reason_rd_id,
    d.deal_created_at,
    d.deal_updated_at,
    d.closed_at
from crm_deals d
join crm_pipelines p on p.rd_id = d.pipeline_rd_id
left join crm_stages s on s.rd_id = d.stage_rd_id
where p.product_group is not null;


-- Funil consolidado: quantidade e valor de pipeline aberto por etapa canonica,
-- separado por produto. Base do grafico de funil da pagina executiva/marketing.
create or replace view v_funnel_summary as
select
    product_group,
    canonical_stage,
    count(*) as deals,
    sum(amount) as pipeline_value
from v_deal_funnel
where status = 'ongoing'
group by product_group, canonical_stage;


-- Uma linha por periodo em que uma negociacao ficou parada numa etapa -- base de
-- aging/velocity. "duration_hours" com exited_at nulo mede o tempo corrido ate agora
-- (etapa ainda aberta).
--
-- Reclassificacao de perdidas (pedido do usuario): quando uma negociacao e marcada
-- "lost" mas ninguem move o card no RD pra etapa de encerramento (fica parada em
-- "Primeira Conexao"/"Em Prospeccao"/etc pra sempre), o "tempo parado nessa etapa"
-- cresce indefinidamente e polui a metrica de velocity -- a etapa parece um gargalo
-- gigante quando na verdade e so lead morto que ninguem arquivou. Por isso, a linha
-- de historico ainda ABERTA (exited_at nulo) de uma negociacao "lost" e tratada aqui
-- como se tivesse migrado pra "Encerrado/Standby" (canonical_stage LOST) no momento
-- em que foi perdida (d.closed_at = a "data de evolucao" pedida), em vez de continuar
-- contando tempo ate agora na etapa original. Isso e só uma reclassificacao de
-- RELATORIO -- nao mexe no card real do RD nem no historico bruto (crm_deal_stage_history).
-- Horas UTEIS entre dois instantes -- desconta sabado e domingo INTEIROS do
-- periodo, em vez do tempo corrido cru. Pedido do usuario: a metrica de
-- velocidade nao deve fazer uma negociacao parecer mais lenta so porque o
-- intervalo cruzou um fim de semana (ninguem trabalha nela nesses dias).
--
-- Estrategia: passa por cada DIA (meia-noite a meia-noite, no fuso de Sao Paulo
-- -- "sabado"/"domingo" tem que ser o fim de semana local, nao o dia em UTC, que
-- pode cair no dia errado perto da virada) dentro do intervalo, soma so a fatia
-- de cada dia que caiu dentro de [inicio, fim] E que nao e sabado/domingo. Um
-- intervalo que comeca sexta 15h e termina segunda 10h conta so "sexta 15h-24h"
-- + "segunda 00h-10h" = 19h -- as 48h do fim de semana nunca entram na soma.
-- Retorna numeric de proposito: e o tipo que `extract(epoch from ...) / 3600` ja
-- produzia antes (EXTRACT no Postgres devolve numeric, nao double precision), e
-- "create or replace view" recusa mudar o TIPO de uma coluna existente
-- (duration_hours/transicao_horas) -- so recriar a view do zero resolveria,
-- quebrando tudo que depende dela nesse meio tempo.
create or replace function horas_uteis_entre(inicio timestamptz, fim timestamptz)
returns numeric
language sql
immutable
as $$
    select coalesce(sum(
        greatest(0, extract(epoch from (
            least(fim, (dia + interval '1 day') at time zone 'America/Sao_Paulo')
            - greatest(inicio, dia at time zone 'America/Sao_Paulo')
        )) / 3600)
    ), 0)
    from generate_series(
        date_trunc('day', inicio at time zone 'America/Sao_Paulo'),
        date_trunc('day', fim at time zone 'America/Sao_Paulo'),
        interval '1 day'
    ) as dia
    where extract(dow from dia) not in (0, 6)  -- 0 = domingo, 6 = sabado
      and inicio is not null and fim is not null and fim > inicio
$$;


create or replace view v_deal_stage_aging as
with base as (
    select
        sh.deal_rd_id, sh.pipeline_rd_id, sh.stage_rd_id, sh.owner_rd_id,
        sh.entered_at, sh.exited_at as raw_exited_at,
        d.name as deal_name, d.status as deal_status, d.closed_at,
        p.product_group,
        s.name as stage_name, s.canonical_stage, s."order" as stage_order,
        (d.status = 'lost' and sh.exited_at is null) as vira_encerrado_standby
    from crm_deal_stage_history sh
    join crm_deals d on d.id = sh.deal_id
    left join crm_pipelines p on p.rd_id = sh.pipeline_rd_id
    left join crm_stages s on s.rd_id = sh.stage_rd_id
)
select
    deal_rd_id as deal_id,
    deal_name,
    deal_status,
    product_group,
    pipeline_rd_id,
    stage_rd_id,
    case when vira_encerrado_standby then 'Encerrado/Standby' else stage_name end as stage_name,
    case when vira_encerrado_standby then 'LOST' else canonical_stage end as canonical_stage,
    case
        when vira_encerrado_standby
        -- por produto, nao por pipeline: "Encerrado/Standby" so existe como etapa de
        -- verdade na pipeline de Qualificacao -- sem isso, negociacoes perdidas que
        -- morreram dentro da pipeline Closer cairiam num stage_order nulo e vira-
        -- riam um segundo grupo "Encerrado/Standby" fantasma no velocity.
        then (
            select min(s2."order") from crm_stages s2
            join crm_pipelines p2 on p2.rd_id = s2.pipeline_rd_id
            where p2.product_group = base.product_group and s2.canonical_stage = 'LOST'
        )
        else stage_order
    end as stage_order,
    owner_rd_id,
    entered_at,
    case when vira_encerrado_standby then coalesce(closed_at, now()) else raw_exited_at end as exited_at,
    horas_uteis_entre(
        entered_at,
        coalesce(case when vira_encerrado_standby then coalesce(closed_at, now()) else raw_exited_at end, now())
    ) as duration_hours
from base;


-- So as etapas correntes (onde cada negociacao esta agora) com o tempo ja decorrido --
-- direto pro card de "negociacoes paradas ha mais tempo" / aging por etapa.
create or replace view v_current_deal_aging as
select *
from v_deal_stage_aging
where exited_at is null;


-- Velocity: quanto tempo, em media/mediana, as negociacoes ficam em cada etapa --
-- separa passagens ja concluidas (exited_at preenchido) de negociacoes paradas
-- AGORA na etapa (uteis pra achar gargalos correntes vs. tempo historico normal).
create or replace view v_stage_velocity as
select
    product_group,
    canonical_stage,
    stage_name,
    stage_order,
    count(*) filter (where exited_at is not null) as passagens_concluidas,
    round(avg(duration_hours) filter (where exited_at is not null)::numeric, 1) as media_horas,
    round(
        percentile_cont(0.5) within group (order by duration_hours)
        filter (where exited_at is not null)::numeric, 1
    ) as mediana_horas,
    count(*) filter (where exited_at is null) as parados_agora,
    round(avg(duration_hours) filter (where exited_at is null)::numeric, 1) as media_horas_parados_agora
from v_deal_stage_aging
where product_group is not null
group by product_group, canonical_stage, stage_name, stage_order
order by product_group, stage_order;


-- Tempo de MOVIMENTACAO entre etapas -- diferente de v_stage_velocity (que mede
-- quanto tempo uma negociacao FICA parada numa etapa), esta mede quanto tempo leva
-- entre ENTRAR numa etapa e ENTRAR na proxima -- a passagem de uma etapa pra outra
-- de verdade, olhando so a sequencia que cada negociacao realmente percorreu (usa
-- v_deal_stage_aging como base, entao ja herda a reclassificacao de perdidas como
-- Encerrado/Standby -- uma negociacao que morre logo depois de entrar numa etapa
-- aparece aqui como "<etapa anterior> -> Encerrado/Standby").
create or replace view v_deal_stage_transitions as
with ordenado as (
    select
        deal_id, pipeline_rd_id, product_group,
        canonical_stage as de_etapa, stage_name as de_etapa_nome, stage_order as de_ordem,
        entered_at as de_entrada,
        lead(canonical_stage) over (partition by deal_id order by entered_at) as para_etapa,
        lead(stage_name) over (partition by deal_id order by entered_at) as para_etapa_nome,
        lead(entered_at) over (partition by deal_id order by entered_at) as para_entrada
    from v_deal_stage_aging
)
select
    deal_id, pipeline_rd_id, product_group,
    de_etapa, de_etapa_nome, de_ordem,
    para_etapa, para_etapa_nome,
    de_entrada, para_entrada,
    horas_uteis_entre(de_entrada, para_entrada) as transicao_horas
from ordenado
where para_entrada is not null;


-- Agregado por par de etapas (de -> para): media/mediana do tempo de transicao e
-- quantas negociacoes fizeram esse movimento especifico.
create or replace view v_stage_transition_velocity as
select
    product_group,
    de_etapa, de_etapa_nome, de_ordem,
    para_etapa, para_etapa_nome,
    count(*) as transicoes,
    round(avg(transicao_horas)::numeric, 1) as media_horas,
    round(percentile_cont(0.5) within group (order by transicao_horas)::numeric, 1) as mediana_horas
from v_deal_stage_transitions
where product_group is not null
group by product_group, de_etapa, de_etapa_nome, de_ordem, para_etapa, para_etapa_nome
order by product_group, de_ordem;


-- Performance de SDR: originacao (quem trouxe a negociacao), independente de quem
-- fechou depois. sdr_owner_rd_id/handoff_at sao calculados pelo webhook processor a
-- partir do primeiro dono da negociacao.
create or replace view v_sdr_performance as
select
    u.name as sdr_name,
    d.sdr_owner_rd_id,
    p.product_group,
    count(*) as leads_originados,
    count(*) filter (
        where s.canonical_stage in ('SQL', 'OPPORTUNITY', 'DISCOVERY', 'PROPOSAL', 'NEGOTIATION')
           or d.handoff_at is not null
    ) as sqls_gerados,
    count(*) filter (where d.handoff_at is not null) as oportunidades_repassadas,
    count(*) filter (where d.status = 'won') as vendas_originadas,
    coalesce(sum(d.amount) filter (where d.status = 'won'), 0) as receita_originada
from crm_deals d
join crm_pipelines p on p.rd_id = d.pipeline_rd_id
left join crm_stages s on s.rd_id = d.stage_rd_id
left join crm_users u on u.rd_id = d.sdr_owner_rd_id
where p.product_group is not null
  and d.sdr_owner_rd_id is not null
group by u.name, d.sdr_owner_rd_id, p.product_group
order by p.product_group, leads_originados desc;


-- Performance de Closer: negociacoes recebidas via handoff (ou owner atual, se nao
-- houve handoff detectado), taxa de vitoria, ticket medio e ciclo apos o handoff.
create or replace view v_closer_performance as
select
    u.name as closer_name,
    coalesce(d.closer_owner_rd_id, d.current_owner_rd_id) as closer_rd_id,
    p.product_group,
    count(*) as oportunidades,
    count(*) filter (where d.status = 'ongoing') as em_andamento,
    count(*) filter (where d.status = 'won') as ganhas,
    count(*) filter (where d.status = 'lost') as perdidas,
    round(
        100.0 * count(*) filter (where d.status = 'won')
        / nullif(count(*) filter (where d.status in ('won', 'lost')), 0), 1
    ) as win_rate_pct,
    coalesce(sum(d.amount) filter (where d.status = 'won'), 0) as receita_fechada,
    round(avg(d.amount) filter (where d.status = 'won')::numeric, 2) as ticket_medio,
    round(
        avg(extract(epoch from (d.closed_at - d.handoff_at)) / 86400)
        filter (where d.status = 'won' and d.handoff_at is not null)::numeric, 1
    ) as ciclo_medio_dias_pos_handoff
from crm_deals d
join crm_pipelines p on p.rd_id = d.pipeline_rd_id
left join crm_users u on u.rd_id = coalesce(d.closer_owner_rd_id, d.current_owner_rd_id)
where p.product_group is not null
  and coalesce(d.closer_owner_rd_id, d.current_owner_rd_id) is not null
group by u.name, coalesce(d.closer_owner_rd_id, d.current_owner_rd_id), p.product_group
order by p.product_group, receita_fechada desc;


-- Pipeline movement: eventos de entrada/ganho/perda por mes -- base do grafico em
-- cascata (pipeline inicio + novo + ganho - perdido = pipeline fim). Aproximado: usa
-- o valor ATUAL da negociacao, nao um snapshot historico do valor no momento do evento
-- (ainda nao temos snapshot de valor ao longo do tempo).
create or replace view v_pipeline_movement as
select product_group, 'novo' as evento, date_trunc('month', deal_created_at) as mes, deal_id, amount
from v_deal_funnel
where deal_created_at is not null
union all
select product_group, 'ganho' as evento, date_trunc('month', closed_at) as mes, deal_id, amount
from v_deal_funnel
where status = 'won' and closed_at is not null
union all
select product_group, 'perdido' as evento, date_trunc('month', closed_at) as mes, deal_id, amount
from v_deal_funnel
where status = 'lost' and closed_at is not null;


create or replace view v_pipeline_movement_summary as
select product_group, mes, evento, count(*) as negociacoes, coalesce(sum(amount), 0) as valor
from v_pipeline_movement
group by product_group, mes, evento
order by product_group, mes, evento;


-- Status de cada empresa de cada campanha do Melhor Venda, ja cruzada com a etapa
-- atual no CRM quando ha match. company_name_mv/mv_status sempre aparecem mesmo sem
-- match, pra dar visao completa do funil MV -> CRM (quantas conectaram, quantas
-- viraram negociacao, em que etapa estao agora).
-- IMPORTANTE: campaign_label NAO e unico -- duas SDRs podem ter uma campanha com o
-- mesmo rotulo de semana (ex: "Agosto/Semana 2" da Miriã e da Letícia, coincidencia
-- de nome, campanhas de verdade diferentes). Filtros/joins devem usar campaign_id.
create or replace view v_mv_campaign_status as
select
    mc.week_start,
    mc.week_end,
    mc.sdr_name,
    mc.label as campaign_label,
    mcc.id as company_id,
    mcc.company_name_mv,
    mcc.cnpj_mv,
    mcc.mv_status,
    mcc.match_confidence,
    d.rd_id as deal_id,
    d.name as deal_name,
    d.status as deal_status,
    s.name as stage_name,
    s.canonical_stage,
    u.name as owner_name,
    -- so preenchidos quando ha uma sugestao por nome pendente de revisao (nao
    -- confirmada) -- deal_id/deal_name acima ficam vazios nesse caso.
    mcc.suggested_deal_rd_id,
    sd.name as suggested_deal_name,
    mcc.suggested_score,
    mc.id as campaign_id
from mv_campaign_companies mcc
join mv_campaigns mc on mc.id = mcc.campaign_id
left join crm_deals d on d.rd_id = mcc.matched_deal_rd_id
left join crm_stages s on s.rd_id = d.stage_rd_id
left join crm_users u on u.rd_id = d.current_owner_rd_id
left join crm_deals sd on sd.rd_id = mcc.suggested_deal_rd_id
order by mc.week_start desc, mcc.company_name_mv;


-- Metricas de canal por campanha do Melhor Venda -- base da pagina "Melhor Venda"
-- do dashboard (volume, taxa de conexao, quanto virou negociacao no CRM).
create or replace view v_mv_channel_summary as
select
    mc.id as campaign_id,
    mc.label as campaign_label,
    mc.sdr_name,
    mc.week_start,
    mc.week_end,
    count(*) as leads_total,
    count(*) filter (where mcc.mv_status = 'Conectado') as leads_conectados,
    round(
        100.0 * count(*) filter (where mcc.mv_status = 'Conectado') / nullif(count(*), 0), 1
    ) as pct_conexao,
    count(*) filter (where mcc.matched_deal_rd_id is not null) as leads_no_crm,
    count(*) filter (
        where mcc.matched_deal_rd_id is not null and mcc.mv_status = 'Conectado'
    ) as conectados_no_crm
from mv_campaigns mc
join mv_campaign_companies mcc on mcc.campaign_id = mc.id
group by mc.id, mc.label, mc.sdr_name, mc.week_start, mc.week_end
order by mc.week_start;


-- Marcos de negocio do funil Maquina ISP, conforme definido pelo usuario:
-- "ganho SDR" = a negociacao chegou em "Reuniao Realizada" (ou etapa posterior) no
-- pipeline Closer -- e o criterio de entrega da SDR, independente do resultado final.
-- "ganho Closer" = a negociacao chegou em "Freemium" -- e o fechamento de verdade
-- pra esse produto, diferente do status generico won/lost do RD (uma negociacao pode
-- estar "ongoing" e ja ter alcancado Freemium, ou "lost" depois de ter chegado la).
-- Usa a ORDEM da etapa dentro do pipeline Closer (nao so o nome exato), pra nao
-- perder casos onde a negociacao pulou uma etapa no caminho.
--
-- IMPORTANTE: o EXISTS contra crm_deal_stage_history sozinho SUBESTIMA os ganhos --
-- ate a correcao em ingestion/rd_crm/deal_history.py, a sincronizacao via polling so
-- gravava a linha "seed" na primeira vez que via a negociacao e nunca mais atualizava
-- o historico quando a etapa avancava (soh o webhook fazia isso, e ainda assim tinha
-- um bug de identity-map do SQLAlchemy que fazia a comparacao "antes vs depois" nunca
-- detectar mudanca -- ver commit que corrigiu). Isso deixava o historico "congelado" no
-- estado inicial pra varias negociacoes, mesmo com elas ja tendo avancado de verdade.
-- Por isso o criterio agora tambem usa a ETAPA ATUAL da negociacao (d.stage_rd_id,
-- que a sincronizacao sempre mantem correta) como sinal adicional -- se ela esta
-- atualmente numa etapa >= o marco, conta como "ganho", mesmo que o historico nao
-- tenha uma linha provando a passagem por la.
--
-- Escopo do funil (definido pelo usuario):
-- - Negociacoes vinculadas (como SDR, closer OU dono atual) a Jonatas dos Reis da
--   Silva, Adriano Lopes, Wesley Biazus ou Ingrid sao excluidas -- esses perfis nao
--   sao SDR/Closer reais do Maquina ISP (contas internas/teste), entao contaminam a
--   performance por SDR/Closer se entrarem na conta.
-- - So entram negociacoes criadas a partir de 01/04/2026 -- e quando o Maquina ISP
--   comecou de fato como produto; qualquer coisa antes disso e ruido de antes do
--   funil existir.
create or replace view v_maquina_isp_deal_milestones as
with closer_pipeline as (
    select rd_id from crm_pipelines where name = '[Máquina ISP] Closer'
),
reuniao_realizada as (
    select s."order" as ord from crm_stages s, closer_pipeline cp
    where s.pipeline_rd_id = cp.rd_id and s.name = 'Reunião Realizada'
),
freemium as (
    select s."order" as ord from crm_stages s, closer_pipeline cp
    where s.pipeline_rd_id = cp.rd_id and s.name = 'Freemium'
),
perfis_excluidos as (
    select rd_id from crm_users
    where name in ('Jônatas dos Reis da Silva', 'Adriano Lopes', 'Wesley Biazus', 'Ingrid')
)
select
    d.rd_id as deal_id,
    d.name as deal_name,
    d.status as deal_status,
    d.amount,
    d.stage_rd_id,
    s_now.name as stage_name,
    d.sdr_owner_rd_id,
    su.name as sdr_name,
    d.closer_owner_rd_id,
    cu.name as closer_name,
    d.handoff_at,
    d.deal_created_at,
    d.closed_at,
    (
        (d.pipeline_rd_id = (select rd_id from closer_pipeline) and s_now."order" >= (select ord from reuniao_realizada))
        or exists (
            select 1
            from crm_deal_stage_history sh
            join crm_stages s on s.rd_id = sh.stage_rd_id
            join closer_pipeline cp on cp.rd_id = sh.pipeline_rd_id
            where sh.deal_rd_id = d.rd_id and s."order" >= (select ord from reuniao_realizada)
        )
    ) as sdr_ganhou,
    (
        (d.pipeline_rd_id = (select rd_id from closer_pipeline) and s_now."order" >= (select ord from freemium))
        or exists (
            select 1
            from crm_deal_stage_history sh
            join crm_stages s on s.rd_id = sh.stage_rd_id
            join closer_pipeline cp on cp.rd_id = sh.pipeline_rd_id
            where sh.deal_rd_id = d.rd_id and s."order" >= (select ord from freemium)
        )
    ) as closer_ganhou,
    s_now.canonical_stage,
    s_now."order" as stage_order
from crm_deals d
join crm_pipelines p on p.rd_id = d.pipeline_rd_id
left join crm_stages s_now on s_now.rd_id = d.stage_rd_id
left join crm_users su on su.rd_id = d.sdr_owner_rd_id
left join crm_users cu on cu.rd_id = d.closer_owner_rd_id
where p.product_group = 'Máquina ISP'
  and d.deal_created_at >= '2026-04-01'
  and (d.sdr_owner_rd_id is null or d.sdr_owner_rd_id not in (select rd_id from perfis_excluidos))
  and (d.closer_owner_rd_id is null or d.closer_owner_rd_id not in (select rd_id from perfis_excluidos))
  and (d.current_owner_rd_id is null or d.current_owner_rd_id not in (select rd_id from perfis_excluidos));
