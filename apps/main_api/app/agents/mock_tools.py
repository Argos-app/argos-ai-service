from langchain.tools import tool
from langchain_core.runnables import RunnableConfig


@tool
def mock_consultar_fazenda(consulta: str, config: RunnableConfig) -> str:
    """Simula consulta de aplicações e lotes. Nunca acessa dados verídicos."""
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


@tool
def mock_consultar_medicamento(nome: str) -> str:
    """Simula busca documental sem fornecer orientação clínica."""
    return (
        f"SIMULAÇÃO: busca por '{nome}'. Nenhuma bula real foi consultada. "
        "Este mock não contém dose, indicação, contraindicação nem carência. "
        "Não use este resultado para orientar tratamento de animais."
    )


@tool
def mock_calcular_previsao(pergunta: str) -> str:
    """Simula previsão para testar o Analista; não usa dados verídicos."""
    return (
        f"SIMULAÇÃO para '{pergunta}'. Resultado FICTÍCIO: 12 aplicações "
        "no próximo período. Premissa: dados sintéticos de teste. "
        "Não é previsão validada para nenhuma fazenda. "
        "Fonte de teste: title='Simulação de predição', "
        "reference='mock://prediction/test-1'."
    )


@tool
def mock_preparar_relatorio(tipo: str, config: RunnableConfig) -> str:
    """Simula preparação de relatório. Nunca gera arquivo nem envia e-mail."""
    configuravel = (config or {}).get("configurable", {})
    farm_id = configuravel.get("farm_id")

    if farm_id != "farm-demo":
        return "MOCK: fazenda de teste não identificada. Relatório não preparado."

    return (
        f"SIMULAÇÃO: rascunho do relatório '{tipo}' para farm-demo. "
        "Nenhum arquivo real foi gerado e nenhum e-mail foi enviado. "
        "Referência de teste: mock://farm-demo/relatorios/rascunho-1."
    )


@tool
def mock_consultar_documental(pergunta: str) -> str:
    """Simula indisponibilidade do Documental; não consulta PDFs."""
    return (
        f"MOCK: pergunta recebida: '{pergunta}'. "
        "Serviço Documental indisponível neste teste. "
        "Nenhum PDF foi consultado; não há conteúdo ou fonte documental "
        "para responder."
    )


MOCK_FARM_TOOLS = [mock_consultar_fazenda]
MOCK_DOCTOR_TOOLS = [mock_consultar_medicamento]
MOCK_ANALYST_TOOLS = [mock_calcular_previsao]
MOCK_SECRETARY_TOOLS = [mock_consultar_fazenda, mock_preparar_relatorio]
MOCK_INFORMATION_TOOLS = [mock_consultar_documental]
