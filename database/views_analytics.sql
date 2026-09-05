-- ======================================================================
-- CAMADA ANALITICA AVANCADA -- geografia, origem, atividade e funil.
--
-- Complementa database/views.sql (que continua sendo a base: funil canonico,
-- aging, velocity, marcos de SDR/closer). Rode este arquivo DEPOIS daquele --
-- v_maquina_isp_deals_enriched depende de v_maquina_isp_deal_milestones.
-- Tudo aqui e "create or replace view", seguro reaplicar quantas vezes quiser.
-- ======================================================================


-- Geografia normalizada por empresa. A base do RD e suja em tres formas distintas,
-- e todas as tres precisam ser tratadas ou a cobertura cai de ~85% pra ~59%:
--   1. custom_fields.estado preenchido corretamente ("SC")   -> caminho feliz
--   2. estado VAZIO com a UF embutida no fim da cidade ("BRASILIA, DF")
--   3. estado e cidade vazios, mas address.line traz "... - CIDADE - UF - CEP"
-- Alem disso a UF as vezes vem por extenso ("BAHIA") em vez da sigla.
-- Cidades estrangeiras ("MEDELIN, COLOMBIA") caem com uf nula de proposito -- nao
-- sao praca de atuacao e nao devem poluir o mapa do Brasil.
create or replace view v_org_geo as
with bruto as (
    select
        rd_id as organization_rd_id,
        name as organization_name,
        legal_name,
        upper(trim(coalesce(raw->'custom_fields'->>'estado', ''))) as uf_raw,
        upper(trim(coalesce(raw->'custom_fields'->>'cidade', ''))) as cidade_raw,
        nullif(trim(coalesce(raw->'custom_fields'->>'bairro', '')), '') as bairro,
        nullif(trim(coalesce(raw->'custom_fields'->>'origem', '')), '') as origem_cadastro,
        upper(coalesce(raw->'address'->>'line', '')) as endereco
    from crm_organizations
),
resolvido as (
    select
        organization_rd_id, organization_name, legal_name, bairro, origem_cadastro,
        case
            when uf_raw ~ '^[A-Z]{2}$' then uf_raw
            when cidade_raw ~ ',\s*[A-Z]{2}$' then regexp_replace(cidade_raw, '^.*,\s*([A-Z]{2})$', '\1')
            when endereco ~ '\s-\s[A-Z]{2}\s-\s' then (regexp_match(endereco, '\s-\s([A-Z]{2})\s-\s'))[1]
            when uf_raw = 'BAHIA' then 'BA'
            else null
        end as uf,
        -- remove a UF grudada no fim do nome da cidade, senao "SAO PAULO" e
        -- "SAO PAULO, SP" viram duas cidades distintas na agregacao
        nullif(trim(regexp_replace(cidade_raw, ',\s*[A-Z]{2}$', '')), '') as cidade
    from bruto
)
select
    organization_rd_id, organization_name, legal_name, cidade, bairro, origem_cadastro,
    case when uf in (
        'AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA','PB',
        'PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO'
    ) then uf end as uf,
    case
        when uf in ('AC','AP','AM','PA','RO','RR','TO') then 'Norte'
        when uf in ('AL','BA','CE','MA','PB','PE','PI','RN','SE') then 'Nordeste'
        when uf in ('DF','GO','MT','MS') then 'Centro-Oeste'
        when uf in ('ES','MG','RJ','SP') then 'Sudeste'
        when uf in ('PR','RS','SC') then 'Sul'
    end as regiao
from resolvido;


-- Atividade comercial por negociacao, a partir das tarefas do RD. Separa os canais
-- de toque (whatsapp/ligacao/e-mail/reuniao/visita) porque a operacao e majoritaria-
-- mente WhatsApp -- somar tudo num "total de atividades" esconde o mix real de
-- esforco. primeiro_toque_at alimenta a metrica de tempo-ate-o-primeiro-contato.
create or replace view v_deal_activity as
select
    deal_rd_id as deal_id,
    count(*) as atividades,
    count(*) filter (where status = 'completed') as atividades_concluidas,
    count(*) filter (where status = 'open') as atividades_abertas,
    count(*) filter (where type = 'whatsapp') as whatsapp,
    count(*) filter (where type = 'call') as ligacoes,
    count(*) filter (where type = 'email') as emails,
    count(*) filter (where type = 'meeting') as reunioes,
    count(*) filter (where type = 'visit') as visitas,
    min(completed_at) as primeiro_toque_at,
    max(completed_at) as ultimo_toque_at
from crm_tasks
where deal_rd_id is not null
group by deal_rd_id;


-- FACT TABLE do funil Maquina ISP: uma linha por negociacao com TODAS as dimensoes
-- analiticas ja resolvidas (geografia, origem, campanha, motivo de perda, atividade,
-- marcos de SDR/closer). Herda integralmente o escopo de v_maquina_isp_deal_milestones
-- (criadas a partir de 01/04/2026, perfis internos excluidos) -- e a base unica de
-- tudo que o dashboard mostra, pra garantir que todo grafico conte a mesma historia.
create or replace view v_maquina_isp_deals_enriched as
select
    m.deal_id,
    m.deal_name,
    m.deal_status,
    m.amount,
    m.stage_rd_id,
    m.stage_name,
    m.canonical_stage,
    m.stage_order,
    m.sdr_owner_rd_id,
    coalesce(m.sdr_name, 'Sem SDR') as sdr_name,
    m.closer_owner_rd_id,
    coalesce(m.closer_name, 'Sem Closer') as closer_name,
    m.handoff_at,
    m.deal_created_at,
    m.closed_at,
    m.sdr_ganhou,
    m.closer_ganhou,
    p.name as pipeline_name,
    coalesce(src.name, 'Origem não informada') as origem,
    coalesce(cmp.name, 'Sem Campanha') as campanha,
    coalesce(lr.name, case when m.deal_status = 'lost' then 'Motivo não informado' end) as motivo_perda,
    d.organization_rd_id,
    g.organization_name,
    g.cidade,
    g.uf,
    coalesce(g.regiao, 'Não informada') as regiao,
    coalesce(a.atividades, 0) as atividades,
    coalesce(a.whatsapp, 0) as whatsapp,
    coalesce(a.ligacoes, 0) as ligacoes,
    coalesce(a.emails, 0) as emails,
    coalesce(a.reunioes, 0) as reunioes,
    a.primeiro_toque_at,
    a.ultimo_toque_at,
    extract(epoch from (a.primeiro_toque_at - m.deal_created_at)) / 3600 as horas_ate_primeiro_toque,
    extract(epoch from (coalesce(m.closed_at, now()) - m.deal_created_at)) / 86400 as dias_no_funil,
    date_trunc('month', m.deal_created_at) as mes_criacao,
    date_trunc('week', m.deal_created_at) as semana_criacao
from v_maquina_isp_deal_milestones m
join crm_deals d on d.rd_id = m.deal_id
left join crm_pipelines p on p.rd_id = d.pipeline_rd_id
left join crm_deal_sources src on src.rd_id = d.source
left join crm_campaigns cmp on cmp.rd_id = d.campaign
left join crm_lost_reasons lr on lr.rd_id = d.lost_reason_rd_id
left join v_org_geo g on g.organization_rd_id = d.organization_rd_id
left join v_deal_activity a on a.deal_id = m.deal_id;


-- Alcance de etapa por negociacao: uma linha por (negociacao, etapa do funil) sempre
-- que a negociacao ALCANCOU aquela etapa -- por historico de etapas OU por estar
-- atualmente nela ou depois dela.
--
-- Esta view existe pra corrigir o problema visual do funil: contar "quantas estao
-- nesta etapa AGORA" produz um funil furado/serrote (quem avancou some das etapas
-- anteriores, entao a etapa 3 pode ter mais que a etapa 2). Um funil so e legivel
-- quando e monotonicamente decrescente, e pra isso ele precisa contar "quantas JA
-- PASSARAM por aqui" -- que e exatamente o que esta view resolve.
create or replace view v_maquina_isp_stage_reach as
with escopo as (
    select deal_id, stage_rd_id, stage_order, deal_created_at from v_maquina_isp_deal_milestones
),
-- ordem global do funil: as duas pipelines do produto sao um funil so, com a de
-- Qualificacao vindo antes da de Closer. O "+ 100" garante que qualquer etapa do
-- Closer venha depois de qualquer etapa da Qualificacao, sem depender dos numeros
-- internos de cada pipeline (que reiniciam do 1 em cada uma).
funil as (
    select
        s.rd_id as stage_rd_id,
        s.name as etapa,
        case when p.name = '[Máquina ISP] Closer' then 100 + s."order" else s."order" end as passo
    from crm_stages s
    join crm_pipelines p on p.rd_id = s.pipeline_rd_id
    where p.product_group = 'Máquina ISP'
      -- etapas terminais/laterais nao fazem parte da progressao do funil -- "Desistiu"
      -- foi adicionada ao pipeline Closer depois da ordem de Freemium (order=6, uma a
      -- mais que Freemium=5); sem essa exclusao ela aparecia no funil como se fosse
      -- um passo MAIS AVANCADO que Freemium, quando na verdade e uma saida (o
      -- prospect desistiu), igual No-show/Encerrado-Standby.
      --
      -- "Reunião Marcada" (pipeline Closer) esta sendo descontinuada no RD por
      -- virar redundante com "Reunião Agendada" (pipeline Qualificacao/SDR, que
      -- ja e o passo real dessa etapa no funil) -- sem excluir, ela contaria como
      -- um segundo passo "alcancado" depois de Reunião Agendada, duplicando a
      -- mesma etapa de negocio sob dois nomes. O historico antigo com essa etapa
      -- continua existindo em crm_deal_stage_history (nao apagamos nada), so para
      -- de contar como progresso do funil daqui pra frente.
      and s.name not in ('Encerrado/Standby', 'No-show', 'No Show', 'Desistiu', 'Reunião Marcada')
),
-- Sinal bruto de alcance, ainda SEM propagar pra tras -- so "bateu direto" num
-- passo, via historico ou por estar la agora.
sinal_bruto as (
    select distinct e.deal_id, f.passo
    from escopo e
    join crm_deal_stage_history sh on sh.deal_rd_id = e.deal_id
    join funil f on f.stage_rd_id = sh.stage_rd_id
    union
    select distinct e.deal_id, atual.passo
    from escopo e
    join funil atual on atual.stage_rd_id = e.stage_rd_id
),
-- O passo MAIS ALTO que cada negociacao ja tocou, por qualquer sinal.
maximo_por_deal as (
    select deal_id, max(passo) as passo_maximo
    from sinal_bruto
    group by deal_id
),
alcancado as (
    -- Se uma negociacao alcancou o passo N, ela OBRIGATORIAMENTE passou por todo
    -- passo 1..N-1 antes -- e assim que "alcancar uma etapa" tem que funcionar
    -- num funil. Sem esse "credita tudo abaixo do maximo", uma negociacao cujo
    -- HISTORICO comeca no meio do caminho (a sincronizacao antiga so gravava a
    -- linha "seed" na etapa em que via a negociacao PELA PRIMEIRA VEZ, entao
    -- negociacoes ja avancadas quando o polling as viu nunca ganharam uma linha
    -- de "Primeira Conexao") e que hoje esta parada numa etapa terminal excluida
    -- (Desistiu/Encerrado-Standby/No-show -- que nao tem passo, entao a etapa
    -- atual tambem nao credita nada) perdia o credito da(s) etapa(s) inicial(is)
    -- mesmo tendo precisado passar por elas na pratica -- e o motivo do funil
    -- aparecer com uma etapa depois "maior" que a de antes (ex: 67 em "Em
    -- Prospeccao" contra so 48 em "Primeira Conexao").
    select m.deal_id, f.passo, f.etapa
    from maximo_por_deal m
    join funil f on f.passo <= m.passo_maximo
)
select deal_id, passo, etapa from alcancado;


-- Funil agregado: quantas negociacoes alcancaram cada etapa, ja com a taxa de
-- conversao passo-a-passo e a retencao acumulada desde o topo.
create or replace view v_maquina_isp_funnel as
with contagem as (
    select passo, etapa, count(distinct deal_id) as negociacoes
    from v_maquina_isp_stage_reach
    group by passo, etapa
),
com_topo as (
    select
        passo, etapa, negociacoes,
        max(negociacoes) over () as topo,
        lag(negociacoes) over (order by passo) as anterior
    from contagem
)
select
    passo, etapa, negociacoes,
    round(100.0 * negociacoes / nullif(anterior, 0), 1) as conversao_etapa_pct,
    round(100.0 * negociacoes / nullif(topo, 0), 1) as retencao_topo_pct,
    coalesce(anterior, negociacoes) - negociacoes as perdidas_no_passo
from com_topo
order by passo;


-- Empresas do Melhor Venda enriquecidas com geografia. A geografia so existe quando
-- a empresa foi casada com uma organizacao do CRM -- prospeccao que nunca virou
-- cadastro nao tem endereco em lugar nenhum, entao `uf` nulo aqui significa
-- "prospectada mas nunca cadastrada no CRM", nao "erro de dado".
create or replace view v_mv_company_geo as
select
    mc.id as campaign_id,
    mc.label as campaign_label,
    mc.sdr_name,
    mc.week_start,
    mc.week_end,
    mcc.id as company_id,
    mcc.company_name_mv,
    mcc.cnpj_mv,
    mcc.mv_status,
    mcc.matched_deal_rd_id,
    mcc.matched_organization_rd_id,
    g.cidade,
    g.uf,
    coalesce(g.regiao, 'Não informada') as regiao,
    d.status as deal_status,
    s.name as stage_name,
    s.canonical_stage
from mv_campaign_companies mcc
join mv_campaigns mc on mc.id = mcc.campaign_id
left join v_org_geo g on g.organization_rd_id = mcc.matched_organization_rd_id
left join crm_deals d on d.rd_id = mcc.matched_deal_rd_id
left join crm_stages s on s.rd_id = d.stage_rd_id;
