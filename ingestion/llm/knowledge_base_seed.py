"""
Conteudo curado da base de conhecimento do agente -- extraido dos materiais
reais da Máquina.ISP (landing page oficial, em
`Desktop/Máquina ISP - Landing page oficial (1)`, commit de 2026-09-06).

Cada item vira uma linha em `knowledge_chunks` (ver database/models.py) quando
`python -m scripts.load_knowledge_base` roda. `titulo` e a chave de upsert --
editar o texto aqui e rodar o loader de novo atualiza o conteudo sem duplicar.

Isso NAO e um substituto de revisao humana: o texto foi extraido e organizado
por mim a partir do codigo-fonte da landing page, mas quem conhece o produto
de verdade e a Develcode -- vale ler cada pedaco pelo menos uma vez antes do
agente comecar a citar isso pra lead de verdade.
"""

KNOWLEDGE_BASE: list[dict] = [
    {
        "titulo": "produto_visao_geral",
        "categoria": "produto",
        "fonte": "Hero.tsx",
        "conteudo": (
            "A Máquina.ISP é uma solução de agentes de Inteligência Artificial especializados para "
            "provedores de internet (ISPs). A proposta central: 'Coloque seu provedor para rodar "
            "sozinho'. Os agentes vendem, cobram, atendem e retêm clientes do provedor, 24 horas por "
            "dia, direto nos sistemas que o provedor já usa (ERP, CRM, WhatsApp) -- não é preciso "
            "trocar de sistema. Oferta de entrada: 60 dias de teste sem custo de implementação."
        ),
    },
    {
        "titulo": "agente_teo_vendas",
        "categoria": "agente",
        "fonte": "Section03.tsx",
        "conteudo": (
            "TEO é o Agente de Vendas da Máquina.ISP. Responsabilidades: atende o lead assim que ele "
            "chega (sem esperar horário comercial), qualifica o lead conforme o perfil, valida a "
            "cobertura técnica direto no ERP do provedor (confirma se o endereço do lead tem "
            "cobertura antes de avançar a conversa) e conduz o processo até a assinatura do "
            "contrato. É o agente que resolve o problema de 'leads perdidos' por falta de "
            "atendimento fora do horário comercial."
        ),
    },
    {
        "titulo": "agente_lia_cobranca",
        "categoria": "agente",
        "fonte": "Section03.tsx",
        "conteudo": (
            "LIA é o Agente de Cobrança da Máquina.ISP. Responsabilidades: executa uma régua de "
            "cobrança inteligente (contatos automáticos e escalonados conforme o atraso), negocia "
            "parcelamentos e condições de pagamento, realiza desbloqueios de confiança (libera o "
            "acesso do cliente mediante acordo) e acompanha o caso até a quitação completa. Resolve "
            "o problema de inadimplência crônica por cobrança manual e sem cadência."
        ),
    },
    {
        "titulo": "agente_gabi_suporte",
        "categoria": "agente",
        "fonte": "Section03.tsx",
        "conteudo": (
            "GABI é o Agente de Suporte da Máquina.ISP. Responsabilidades: faz triagem e resolve "
            "solicitações de primeiro nível sozinha, automatiza pesquisas de NPS, mantém um "
            "histórico unificado do relacionamento com o cliente e só encaminha pra um atendente "
            "humano os casos que são de fato exceção. Reduz o volume de chamados que precisam de "
            "uma pessoa da equipe."
        ),
    },
    {
        "titulo": "agente_noah_retencao",
        "categoria": "agente",
        "fonte": "Section03.tsx",
        "conteudo": (
            "NOAH é o Agente de Retenção da Máquina.ISP. Responsabilidades: recebe a solicitação de "
            "cancelamento do cliente, conduz uma retenção estruturada (não é só 'quer mesmo "
            "cancelar?', segue um processo), identifica a causa raiz do pedido de cancelamento e "
            "monta uma contraoferta adequada, e registra o desfecho de forma automatizada (o motivo "
            "real do cancelamento fica documentado, não se perde). Resolve o problema de churn "
            "silencioso."
        ),
    },
    {
        "titulo": "dores_que_resolve",
        "categoria": "produto",
        "fonte": "Section01.tsx",
        "conteudo": (
            "A Máquina.ISP resolve 4 dores centrais de um provedor de internet, todas ligadas a "
            "processos manuais: (1) Leads perdidos -- sem atendimento fora do horário comercial, o "
            "lead esfria e fecha com o concorrente que respondeu primeiro. (2) Inadimplência crônica "
            "-- cobrança manual e sem cadência corrói o caixa mês após mês, silenciosamente. (3) "
            "Churn silencioso -- clientes cancelam sem que a causa raiz seja entendida ou trabalhada. "
            "(4) Custo de contratação -- crescer a operação hoje exige contratar mais gente, o que "
            "aumenta o OPEX e a complexidade de gestão."
        ),
    },
    {
        "titulo": "resultados_esperados",
        "categoria": "produto",
        "fonte": "Section04.tsx",
        "conteudo": (
            "Resultados típicos comunicados pela Máquina.ISP (números de referência da própria "
            "campanha de marketing, não uma garantia contratual individual): redução de "
            "aproximadamente 30% na inadimplência, redução de aproximadamente 20% no churn, e "
            "aumento de aproximadamente 15% na base de assinantes. O provedor também passa a ter "
            "acesso a dashboards completos, análises avançadas e indicadores para cada área da "
            "operação (vendas, cobrança, suporte, retenção)."
        ),
    },
    {
        "titulo": "processo_implementacao",
        "categoria": "processo",
        "fonte": "Section05.tsx",
        "conteudo": (
            "A implementação da Máquina.ISP segue 4 etapas: (1) MAPEAMENTO -- reunião de onboarding, "
            "diagnóstico da operação atual do provedor e definição do escopo. (2) INTEGRAÇÃO -- "
            "conexão via API aos sistemas do provedor (ERP, CRM, WhatsApp) e ajuste dos fluxos. (3) "
            "CALIBRAÇÃO -- treino dos agentes em fluxos reais da operação, ajuste de scripts e "
            "regras de negócio específicas do provedor. (4) GO LIVE -- agentes ativos de verdade, "
            "com monitoramento e dashboard em tempo real. Todo o setup e implementação é por conta "
            "da Develcode, sem custo pro provedor."
        ),
    },
    {
        "titulo": "investimento_e_teste_gratuito",
        "categoria": "objecao",
        "fonte": "FAQ.tsx + Section05b.tsx",
        "conteudo": (
            "O provedor tem 60 dias de teste completo da Máquina.ISP sem nenhum custo da parte da "
            "Develcode. A cobrança só começa depois que o provedor testar, validar que faz sentido "
            "para a operação dele, e der o aceite final da proposta. Se ao final dos 60 dias o "
            "provedor decidir não seguir, é só encerrar sem pagar nada -- o custo só existe a partir "
            "do aceite. IMPORTANTE: o valor exato da mensalidade NÃO é informado neste material nem "
            "em nenhum lugar da landing page pública -- é definido em conversa comercial. O agente "
            "de atendimento NUNCA deve inventar ou estimar um valor de mensalidade; qualquer "
            "pergunta sobre preço/valor deve ser encaminhada pra um consultor humano."
        ),
    },
    {
        "titulo": "custos_adicionais",
        "categoria": "objecao",
        "fonte": "FAQ.tsx + Section05b.tsx",
        "conteudo": (
            "Além da mensalidade da Máquina.ISP, existem dois custos que já são do provedor hoje com "
            "qualquer solução parecida, e são cobrados diretamente pelos respectivos provedores "
            "(Meta e a empresa de IA), não pela Develcode: (1) o custo do token da API do WhatsApp "
            "(cobrado pela Meta), e (2) o custo do uso da LLM/IA (broker de inteligência artificial). "
            "Isso deve ficar claro pra não gerar surpresa: 'sem custo de implementação' se refere ao "
            "setup, não a esses dois custos de operação de terceiros."
        ),
    },
    {
        "titulo": "integracoes_erp",
        "categoria": "objecao",
        "fonte": "FAQ.tsx",
        "conteudo": (
            "A Máquina.ISP integra com qualquer ERP. Trabalha nativamente com os ERPs mais usados no "
            "mercado de provedores de internet -- IXC Soft, Hubbysoft e Voalle, entre outros -- e "
            "também se integra com ERPs próprios/desenvolvidos internamente pelo provedor (\"se você "
            "construiu o seu, a gente conecta\")."
        ),
    },
    {
        "titulo": "tempo_de_setup",
        "categoria": "objecao",
        "fonte": "FAQ.tsx",
        "conteudo": (
            "O tempo de setup envolve configurar as regras de negócio e ajustar a solução à operação "
            "específica de cada provedor -- por isso varia caso a caso. A cada evolução da "
            "plataforma esse tempo tem diminuído, caminhando pra um modelo cada vez mais "
            "'plug and play' (mais rápido de colocar no ar)."
        ),
    },
    {
        "titulo": "proximo_passo_demonstracao",
        "categoria": "processo",
        "fonte": "Section06.tsx",
        "conteudo": (
            "O próximo passo natural pra um lead interessado é agendar uma demonstração prática e "
            "gratuita dos agentes em ação. Na landing page isso acontece via formulário, que ao ser "
            "enviado redireciona o lead pro WhatsApp da empresa com uma mensagem pré-preenchida "
            "confirmando o interesse na demonstração. Ou seja: um lead que chega pelo WhatsApp já "
            "está, na maioria das vezes, no estágio de 'quero ver a solução funcionando' -- o "
            "objetivo da conversa é levá-lo até a reunião/demonstração marcada, não fechar venda "
            "sozinho por texto."
        ),
    },
]
