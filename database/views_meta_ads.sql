-- ======================================================================
-- META ADS -- views analiticas de performance de campanhas de trafego pago.
--
-- Reaplique sempre que mudar (create or replace view e seguro rodar de novo).
-- Depende so das tabelas meta_* (ver database/models.py) -- nao depende de
-- nenhuma view do RD CRM.
--
-- IMPORTANTE (limitacao conhecida, ver conversa que originou este arquivo): hoje
-- NAO ha cruzamento com o funil do RD CRM (custo por reuniao realizada, por
-- etapa) porque nenhuma negociacao no CRM carrega atribuicao de campanha/UTM --
-- `crm_deals.raw.custom_fields` esta vazio em 100% das negociacoes atuais. Esse
-- cruzamento fica pronto pra ser adicionado assim que o RD Station Marketing
-- passar a gravar a UTM/campanha de origem no card da negociacao (configuracao
-- dentro do RD, fora do escopo deste repositorio).
-- ======================================================================


-- Extrai de dentro do array JSONB `actions` (formato do Meta: uma lista de
-- {"action_type": "...", "value": "..."}) as acoes que representam GERACAO DE LEAD.
-- Nao ha um `action_type` unico e universal pra "lead" -- varia conforme o objetivo
-- da campanha e o metodo de captura (formulario nativo do Meta, pixel no site,
-- conversao offline). Por isso soma qualquer action_type que contenha "lead", em vez
-- de travar num nome exato -- mais abrangente, ao custo de exigir revisao manual se
-- o Meta introduzir um action_type nao relacionado que tambem contenha a palavra.
create or replace view v_meta_insights_enriched as
select
    i.*,
    (
        select coalesce(sum((a->>'value')::numeric), 0)
        from jsonb_array_elements(coalesce(i.actions, '[]'::jsonb)) a
        where a->>'action_type' ilike '%lead%'
    ) as leads_estimados,
    (
        select coalesce(sum((a->>'value')::numeric), 0)
        from jsonb_array_elements(coalesce(i.actions, '[]'::jsonb)) a
        where a->>'action_type' = 'link_click'
    ) as link_clicks
from meta_insights_daily i;


-- Performance agregada por campanha (vida inteira dos dados sincronizados).
create or replace view v_meta_campaign_performance as
select
    c.meta_id as campaign_id,
    c.name as campaign_name,
    c.objective,
    c.status,
    c.effective_status,
    min(i.date) as primeiro_dia_com_dado,
    max(i.date) as ultimo_dia_com_dado,
    coalesce(sum(i.spend), 0) as investimento_total,
    coalesce(sum(i.impressions), 0) as impressoes,
    coalesce(sum(i.clicks), 0) as cliques,
    coalesce(sum(i.leads_estimados), 0) as leads_estimados,
    round(
        100.0 * sum(i.clicks) / nullif(sum(i.impressions), 0), 2
    ) as ctr_pct,
    round(sum(i.spend) / nullif(sum(i.clicks), 0), 2) as cpc_medio,
    round(1000.0 * sum(i.spend) / nullif(sum(i.impressions), 0), 2) as cpm_medio,
    round(sum(i.spend) / nullif(sum(i.leads_estimados), 0), 2) as custo_por_lead_estimado
from meta_campaigns c
left join v_meta_insights_enriched i on i.campaign_meta_id = c.meta_id
group by c.meta_id, c.name, c.objective, c.status, c.effective_status
order by investimento_total desc;


-- Performance agregada por conjunto de anuncios.
create or replace view v_meta_adset_performance as
select
    s.meta_id as adset_id,
    s.name as adset_name,
    s.campaign_meta_id as campaign_id,
    c.name as campaign_name,
    s.status,
    s.effective_status,
    coalesce(sum(i.spend), 0) as investimento_total,
    coalesce(sum(i.impressions), 0) as impressoes,
    coalesce(sum(i.clicks), 0) as cliques,
    coalesce(sum(i.leads_estimados), 0) as leads_estimados,
    round(100.0 * sum(i.clicks) / nullif(sum(i.impressions), 0), 2) as ctr_pct,
    round(sum(i.spend) / nullif(sum(i.clicks), 0), 2) as cpc_medio
from meta_adsets s
left join meta_campaigns c on c.meta_id = s.campaign_meta_id
left join v_meta_insights_enriched i on i.adset_meta_id = s.meta_id
group by s.meta_id, s.name, s.campaign_meta_id, c.name, s.status, s.effective_status
order by investimento_total desc;


-- Performance agregada por anuncio (nivel de criativo).
create or replace view v_meta_ad_performance as
select
    a.meta_id as ad_id,
    a.name as ad_name,
    a.creative_thumbnail_url,
    a.adset_meta_id as adset_id,
    s.name as adset_name,
    a.campaign_meta_id as campaign_id,
    c.name as campaign_name,
    a.status,
    a.effective_status,
    coalesce(sum(i.spend), 0) as investimento_total,
    coalesce(sum(i.impressions), 0) as impressoes,
    coalesce(sum(i.clicks), 0) as cliques,
    coalesce(sum(i.leads_estimados), 0) as leads_estimados,
    round(100.0 * sum(i.clicks) / nullif(sum(i.impressions), 0), 2) as ctr_pct,
    round(sum(i.spend) / nullif(sum(i.clicks), 0), 2) as cpc_medio
from meta_ads a
left join meta_adsets s on s.meta_id = a.adset_meta_id
left join meta_campaigns c on c.meta_id = a.campaign_meta_id
left join v_meta_insights_enriched i on i.ad_meta_id = a.meta_id
group by a.meta_id, a.name, a.creative_thumbnail_url, a.adset_meta_id, s.name,
         a.campaign_meta_id, c.name, a.status, a.effective_status
order by investimento_total desc;


-- Serie diaria por campanha -- base do grafico de evolucao de gasto/performance
-- no tempo, com filtro por periodo.
create or replace view v_meta_daily_performance as
select
    i.date,
    i.campaign_meta_id as campaign_id,
    i.campaign_name,
    sum(i.spend) as investimento,
    sum(i.impressions) as impressoes,
    sum(i.clicks) as cliques,
    sum(i.leads_estimados) as leads_estimados,
    round(100.0 * sum(i.clicks) / nullif(sum(i.impressions), 0), 2) as ctr_pct
from v_meta_insights_enriched i
group by i.date, i.campaign_meta_id, i.campaign_name
order by i.date;
