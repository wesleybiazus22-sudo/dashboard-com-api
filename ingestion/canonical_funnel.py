"""
Mapeamento do funil canonico -- traduz as etapas nativas de cada pipeline do RD CRM
para um vocabulario unico (LEAD, MQL, SQL, OPPORTUNITY, DISCOVERY, PROPOSAL,
NEGOTIATION, LOST) usado nas views e no dashboard, independente de qual pipeline/
produto a negociacao pertence.

Ganho/perda "de verdade" (won/lost) vem do campo `status` da negociacao, nao daqui --
uma negociacao pode ser perdida em qualquer etapa. Este mapeamento e so pra dar um
"nivel" comparavel a cada etapa nativa.

Uso: python -m ingestion.canonical_funnel
Reaplique sempre que uma etapa nova for criada em algum pipeline no RD Station.
"""

from database.connection import session_scope
from database.models import CrmPipeline, CrmStage

# pipeline_id (rd_id) -> grupo de produto. Une pipelines do mesmo produto (ex:
# Qualificação + Closer da Máquina ISP) num funil so. Pipelines fora deste dict
# (Projetos/Alocações, Relacionamento/CS) ficam de fora do funil de vendas por
# decisão do usuário -- nao aparecem no dashboard comercial por enquanto.
PIPELINE_GROUP_MAPPING: dict[str, str] = {
    "687fe8cbd5677c001aa540b2": "Máquina ISP",  # [Máquina ISP] - Qualificação
    "6a455fb78017b2001dacd8ed": "Máquina ISP",  # [Máquina ISP] Closer
    "6a42569b40a047001d6ce6ad": "ThunderIA",  # [ThunderIA] - Closer
}

# stage_id (rd_id) -> canonical_stage
STAGE_MAPPING: dict[str, str] = {
    # [Máquina ISP] - Qualificação
    "687fe8cbd5677c001aa540b4": "LEAD",  # Primeira Conexão
    "687fe8cbd5677c001aa540b6": "LEAD",  # Em Prospecção
    "687fe8cbd5677c001aa540b7": "MQL",  # Interesse Identificado
    "6a4febe620cf310024567a82": "SQL",  # Reunião Agendada
    "6a7a21e89b898f00253e5577": "SQL",  # No-show
    "6a7a2222dd552b0020f59a17": "LOST",  # Encerrado/Standby
    # [Máquina ISP] Closer
    "6a4579a20e7d7f001de48d12": "OPPORTUNITY",  # No Show
    "6a455fb78017b2001dacd8ef": "OPPORTUNITY",  # Reunião Marcada
    "6a455fb78017b2001dacd8f0": "DISCOVERY",  # Reunião Realizada
    "6a455fb78017b2001dacd8f1": "PROPOSAL",  # Proposta Enviada
    "6a455fb78017b2001dacd902": "NEGOTIATION",  # Freemium
    # [ThunderIA] - Closer
    "6a42569b40a047001d6ce6af": "LEAD",  # Sem contato
    "6a42569b40a047001d6ce6b0": "MQL",  # Contato feito
    "6a42569b40a047001d6ce6b1": "SQL",  # Identificação do interesse
    "6a42569b40a047001d6ce6b2": "DISCOVERY",  # Apresentação
    "6a42569b40a047001d6ce6b3": "PROPOSAL",  # Proposta enviada
    "6a4f8b5825b1c8001d6a625f": "NEGOTIATION",  # Freemium
}


def apply_canonical_mapping() -> tuple[int, int]:
    stages_updated = 0
    pipelines_updated = 0
    with session_scope() as db:
        for pipeline_id, group in PIPELINE_GROUP_MAPPING.items():
            pipeline = db.query(CrmPipeline).filter(CrmPipeline.rd_id == pipeline_id).one_or_none()
            if pipeline is None:
                print(f"  aviso: pipeline_id {pipeline_id} nao encontrado em crm_pipelines (ignorado)")
                continue
            pipeline.product_group = group
            pipelines_updated += 1

        for stage_id, canonical in STAGE_MAPPING.items():
            stage = db.query(CrmStage).filter(CrmStage.rd_id == stage_id).one_or_none()
            if stage is None:
                print(f"  aviso: stage_id {stage_id} nao encontrado em crm_stages (ignorado)")
                continue
            stage.canonical_stage = canonical
            stages_updated += 1

    return pipelines_updated, stages_updated


if __name__ == "__main__":
    p, s = apply_canonical_mapping()
    print(f"{p} pipelines agrupados, {s} etapas mapeadas para o funil canonico.")
