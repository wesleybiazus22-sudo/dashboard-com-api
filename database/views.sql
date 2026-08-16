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
create or replace view v_deal_stage_aging as
select
    sh.deal_rd_id as deal_id,
    d.name as deal_name,
    d.status as deal_status,
    p.product_group,
    sh.pipeline_rd_id,
    sh.stage_rd_id,
    s.name as stage_name,
    s.canonical_stage,
    s."order" as stage_order,
    sh.owner_rd_id,
    sh.entered_at,
    sh.exited_at,
    extract(epoch from (coalesce(sh.exited_at, now()) - sh.entered_at)) / 3600 as duration_hours
from crm_deal_stage_history sh
join crm_deals d on d.id = sh.deal_id
left join crm_pipelines p on p.rd_id = sh.pipeline_rd_id
left join crm_stages s on s.rd_id = sh.stage_rd_id;


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
    mcc.suggested_score
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
    exists (
        select 1
        from crm_deal_stage_history sh
        join crm_stages s on s.rd_id = sh.stage_rd_id
        join closer_pipeline cp on cp.rd_id = sh.pipeline_rd_id
        where sh.deal_rd_id = d.rd_id and s."order" >= (select ord from reuniao_realizada)
    ) as sdr_ganhou,
    exists (
        select 1
        from crm_deal_stage_history sh
        join crm_stages s on s.rd_id = sh.stage_rd_id
        join closer_pipeline cp on cp.rd_id = sh.pipeline_rd_id
        where sh.deal_rd_id = d.rd_id and s."order" >= (select ord from freemium)
    ) as closer_ganhou
from crm_deals d
join crm_pipelines p on p.rd_id = d.pipeline_rd_id
left join crm_stages s_now on s_now.rd_id = d.stage_rd_id
left join crm_users su on su.rd_id = d.sdr_owner_rd_id
left join crm_users cu on cu.rd_id = d.closer_owner_rd_id
where p.product_group = 'Máquina ISP';
