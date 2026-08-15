-- Views analiticas do funil comercial (Maquina ISP + ThunderIA).
-- Rode isto no SQL Editor do Supabase depois de aplicar o mapeamento canonico
-- (python -m ingestion.canonical_funnel). Reaplique sempre que uma view mudar --
-- "create or replace view" e seguro rodar de novo a qualquer momento.

-- Coluna nova em crm_pipelines (necessaria antes das views abaixo).
alter table crm_pipelines add column if not exists product_group varchar;
create index if not exists ix_crm_pipelines_product_group on crm_pipelines (product_group);


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
where status = 'open'
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
