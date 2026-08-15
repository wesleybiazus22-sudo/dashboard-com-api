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
from database.models import CrmStage

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


def apply_canonical_mapping() -> int:
    updated = 0
    with session_scope() as db:
        for stage_id, canonical in STAGE_MAPPING.items():
            stage = db.query(CrmStage).filter(CrmStage.rd_id == stage_id).one_or_none()
            if stage is None:
                print(f"  aviso: stage_id {stage_id} nao encontrado em crm_stages (ignorado)")
                continue
            stage.canonical_stage = canonical
            updated += 1
    return updated


if __name__ == "__main__":
    n = apply_canonical_mapping()
    print(f"{n} etapas mapeadas para o funil canonico.")
