from app.ports.agent import AgentId
from langchain.tools import tool
from langchain_core.runnables import RunnableConfig


@tool(description="Simula consulta de aplicações e lotes. Nunca acessa dados verídicos.")
def mock_consultar_fazenda(consulta: str, config: RunnableConfig) -> str:
    configuravel = (config or {}).get("configurable", {})
    farm_id = configuravel.get("farm_id")

    if farm_id != "farm-demo":
        return (
            "MOCK: nenhum dado disponível. Use farm_id='farm-demo' somente "
            "nos testes. Nenhum banco de dados foi consultado."
        )

    return (
        f"SIMULAÇÃO para a consulta '{consulta}'. "
        "Registro FICTÍCIO: bovino BOV-TESTE-01, lote L-TESTE, "
        "aplicação de MED-TESTE. Período de carência não informado. "
        "Fonte de teste: title='Registro sintético da fazenda', "
        "reference='mock://farm-demo/aplicacoes/1'. "
        "Nenhum banco de dados real foi consultado."
    )


@tool(description="Simula busca documental sem fornecer orientação clínica.")
def mock_consultar_medicamento(nome: str) -> str:
    return (
        f"SIMULAÇÃO: busca por '{nome}'. Nenhuma bula real foi consultada. "
        "Este mock não contém dose, indicação, contraindicação nem carência. "
        "Não use este resultado para orientar tratamento de animais."
    )


@tool(description="Simula previsão para testar o Analista; não usa dados verídicos.")
def mock_calcular_previsao(pergunta: str) -> str:
    return (
        f"SIMULAÇÃO para '{pergunta}'. Resultado FICTÍCIO: 12 aplicações "
        "no próximo período. Premissa: dados sintéticos de teste. "
        "Não é previsão validada para nenhuma fazenda. "
        "Fonte de teste: title='Simulação de predição', "
        "reference='mock://prediction/test-1'."
    )


@tool(description="Simula preparação de relatório. Nunca gera arquivo nem envia e-mail.")
def mock_preparar_relatorio(tipo: str, config: RunnableConfig) -> str:
    configuravel = (config or {}).get("configurable", {})
    farm_id = configuravel.get("farm_id")

    if farm_id != "farm-demo":
        return "MOCK: fazenda de teste não identificada. Relatório não preparado."

    return (
        f"SIMULAÇÃO: rascunho do relatório '{tipo}' para farm-demo. "
        "Nenhum arquivo real foi gerado e nenhum e-mail foi enviado. "
        "Referência de teste: mock://farm-demo/relatorios/rascunho-1."
    )


@tool(description="Simula indisponibilidade do Documental; não consulta PDFs.")
def mock_consultar_documental(pergunta: str) -> str:
    return (
        f"MOCK: pergunta recebida: '{pergunta}'. "
        "Serviço Documental indisponível neste teste. "
        "Nenhum PDF foi consultado; não há conteúdo ou fonte documental "
        "para responder."
    )


MOCK_FARM_TOOLS = [mock_consultar_fazenda]
MOCK_DOCTOR_TOOLS = [mock_consultar_medicamento]
MOCK_ANALYST_TOOLS = [mock_calcular_previsao]
MOCK_SECRETARY_TOOLS = [mock_preparar_relatorio]
MOCK_INFORMATION_TOOLS = [mock_consultar_documental]

MOCK_AGENT_TOOL_BINDINGS = {
    AgentId.FARM: {"farm_data": tuple(MOCK_FARM_TOOLS)},
    AgentId.DOCTOR: {"medicine_reference": tuple(MOCK_DOCTOR_TOOLS)},
    AgentId.ANALYST: {"prediction": tuple(MOCK_ANALYST_TOOLS)},
    AgentId.SECRETARY: {"report_delivery": (mock_preparar_relatorio,)},
    AgentId.INFORMATION: {"documental_a2a": tuple(MOCK_INFORMATION_TOOLS)},
}
