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

-- Colunas novas em meta_insights_daily (necessarias antes das views abaixo --
-- `create_all` no scripts/init_db.py so cria tabelas que ainda nao existem, nao
-- adiciona coluna em tabela ja existente). NULL de verdade quando o anuncio nao
-- e de video (ver comentario no model MetaInsightDaily).
alter table meta_insights_daily add column if not exists video_thruplay integer;
alter table meta_insights_daily add column if not exists video_view_50 integer;


-- Extrai de dentro do array JSONB `actions` (formato do Meta: uma lista de
-- {"action_type": "...", "value": "..."}) as acoes que representam GERACAO DE LEAD.
-- Nao ha um `action_type` unico e universal pra "lead" -- varia conforme o objetivo
-- da campanha e o metodo de captura (formulario nativo do Meta, pixel no site,
-- conversao offline). Por isso soma qualquer action_type que contenha "lead", em vez
-- de travar num nome exato -- mais abrangente, ao custo de exigir revisao manual se
-- o Meta introduzir um action_type nao relacionado que tambem contenha a palavra.
--
-- CORRECAO (achado real, confirmado 3x em anuncios/dias diferentes -- "AD2 - Nao e
-- mais um bot" em 31/08, "AD1 Video - Ainda nao automatiza" e "AD4 Video - Utiliza
-- algum desses" em 04-05/09, todos na campanha PB LAL): toda vez que um lead real
-- acontece, o Meta reporta o MESMO evento em ATE QUATRO action_types diferentes ao
-- mesmo tempo, sempre juntos, sempre com o mesmo valor -- `lead`,
-- `offsite_conversion.fb_pixel_lead`, `onsite_web_lead` e a conversao customizada
-- `offsite_lead_add_20_s_calls`. Nao e erro de configuracao do usuario nos 2
-- primeiros (comportamento documentado da Graph API pra pixel), mas os outros 2
-- tambem batem toda vez que os 4 aparecem juntos -- ou sao o mesmo disparo do
-- rastreamento do site (JS chamando o pixel/CAPI mais de uma vez pro mesmo evento),
-- ou uma coincidencia repetida improvavel em 3 casos independentes. A confirmacao
-- veio do proprio usuario: no dia 31/08 a soma antiga dava "4 leads" pra ESSE
-- anuncio, e o CRM mostrava 2 leads reais NO DIA TODO (nao so nesse anuncio) --
-- colapsando os 4 pra 1 aqui sobra exatamente 1 pra explicar via outro anuncio/
-- adset com atividade no mesmo dia, o que fecha a conta. Por isso pega o MAIOR
-- valor entre os 4 (nao soma) como "1 evento", e soma normalmente qualquer OUTRO
-- action_type que contenha "lead" que apareca fora desse grupo -- ainda pode faltar
-- alguma variante nova que o Meta introduza, mas cobre 100% do que ja foi observado
-- nos dados reais desta conta.
--
-- `case ... when jsonb_typeof(...) = 'array'` em vez de `coalesce(i.actions, '[]')`:
-- quando o Meta nao retorna nenhuma acao pro anuncio/dia, `actions` pode ficar
-- gravado como o literal JSON `null` (um VALOR jsonb valido) em vez de NULL de
-- banco -- `coalesce` so troca NULL de banco, entao passaria o `null` do JSON
-- direto pro `jsonb_array_elements`, que quebra com "cannot extract elements
-- from a scalar". Checar `jsonb_typeof` cobre os dois casos (NULL de banco E
-- null dentro do JSON) e qualquer outro valor nao-array que apareca.
-- `create or replace view` nao aceita a coluna final de `i.*` mudar de posicao --
-- quando `meta_insights_daily` ganha uma coluna nova (ver ALTER acima), tudo que
-- vem DEPOIS de `i.*` neste SELECT (leads_estimados, link_clicks) desloca de
-- posicao, e o Postgres recusa com "cannot change name of view column". Precisa
-- dropar e recriar em vez de substituir -- seguro porque todas as views que
-- dependem desta sao recriadas logo abaixo neste mesmo arquivo.
drop view if exists v_meta_insights_enriched cascade;
create view v_meta_insights_enriched as
select
    i.*,
    (
        select
            coalesce(max((a->>'value')::numeric) filter (
                where a->>'action_type' in (
                    'lead', 'offsite_conversion.fb_pixel_lead',
                    'onsite_web_lead', 'offsite_lead_add_20_s_calls'
                )
            ), 0)
            + coalesce(sum((a->>'value')::numeric) filter (
                where a->>'action_type' ilike '%lead%'
                  and a->>'action_type' not in (
                      'lead', 'offsite_conversion.fb_pixel_lead',
                      'onsite_web_lead', 'offsite_lead_add_20_s_calls'
                  )
            ), 0)
        from jsonb_array_elements(
            case when jsonb_typeof(i.actions) = 'array' then i.actions else '[]'::jsonb end
        ) a
    ) as leads_estimados,
    (
        select coalesce(sum((a->>'value')::numeric), 0)
        from jsonb_array_elements(
            case when jsonb_typeof(i.actions) = 'array' then i.actions else '[]'::jsonb end
        ) a
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
