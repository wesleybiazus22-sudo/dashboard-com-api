"""
Busca (o "R" do RAG) na base de conhecimento do agente. Chame
`buscar_contexto(pergunta)` no motor de conversa antes de responder o lead --
o resultado vira parte do system prompt/contexto daquele turno.

Implementacao: busca textual nativa do Postgres (`to_tsvector`/`plainto_tsquery`
+ `ts_rank`), nao embedding com vetor -- decisao deliberada, ver docstring de
`KnowledgeChunk` em database/models.py pro raciocinio completo. Em portugues
(`'portuguese'` como configuracao de idioma do Postgres) pra lidar direito com
acentuacao e stemming de palavras (ex: "cobranças"/"cobrar"/"cobrado" casam
entre si).
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from database.models import KnowledgeChunk


def buscar_contexto(db: Session, pergunta: str, *, limite: int = 4) -> list[KnowledgeChunk]:
    """Devolve os `limite` pedacos mais relevantes pra pergunta, ordenados por
    relevancia (`ts_rank`). Lista vazia se nada bater (o motor de conversa
    deve tratar isso como "nao sei" -- ver a regra de nunca inventar resposta
    fora da base, combinada com o usuario).

    `plainto_tsquery` sozinho exige TODOS os termos da pergunta (AND) -- rigido
    demais pra pergunta de lead de verdade, que raramente usa as mesmas
    palavras do texto da base inteiras. Por isso pegamos a versao ja
    normalizada/com stemming que `plainto_tsquery` gera (ex: "'quant' & 'cust'
    & 'mensal'") e trocamos os "&" por "|" antes de rodar como `to_tsquery` --
    vira "bate se tiver QUALQUER UM desses radicais", mantendo a normalizacao
    de acentuacao/stemming em portugues que faria a mao seria arriscado."""
    query = text(
        """
        select id, titulo, categoria, conteudo, fonte, updated_at,
               ts_rank(to_tsvector('portuguese', conteudo), q) as relevancia
        from knowledge_chunks,
             to_tsquery('portuguese', replace(plainto_tsquery('portuguese', :pergunta)::text, ' & ', ' | ')) as q
        where to_tsvector('portuguese', conteudo) @@ q
        order by relevancia desc
        limit :limite
        """
    )
    linhas = db.execute(query, {"pergunta": pergunta, "limite": limite}).mappings().all()
    return [
        KnowledgeChunk(
            id=r["id"], titulo=r["titulo"], categoria=r["categoria"],
            conteudo=r["conteudo"], fonte=r["fonte"], updated_at=r["updated_at"],
        )
        for r in linhas
    ]


def montar_bloco_contexto(chunks: list[KnowledgeChunk]) -> str:
    """Formata os pedacos encontrados como um bloco de texto pronto pra
    colar no prompt do Claude -- cada pedaco com seu titulo, pra o modelo
    poder citar de onde tirou a informacao se precisar."""
    if not chunks:
        return "(nenhum trecho da base de conhecimento encontrado pra essa pergunta)"
    return "\n\n".join(f"### {c.titulo}\n{c.conteudo}" for c in chunks)
