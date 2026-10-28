# MCP de dados

O MCP de dados oferece consultas autorizadas de pecuária e medicamentos e uma
origem assinada para relatórios de lote.

## Tecnologias e execução

Usa Python 3.14.3, FastMCP, asyncpg, Motor, cryptography, PostgreSQL, MongoDB e
os pacotes `contracts` e `security`. Na raiz, instale e configure as variáveis
documentadas em [.env.example](.env.example) no ambiente do processo; o Python
não carrega o arquivo automaticamente. Por exemplo, no PowerShell:

```powershell
python -m pip install -r apps/mcp_data/requirements.txt
$env:PYTHONPATH = "apps/mcp_data/src"
python -m argos_mcp_data
```

O processo exige serviços e credenciais configurados. A execução integrada e o
build estão em [Docker local](../../docs/docker-local.md). Para validar:

```powershell
python -m pytest apps/mcp_data/tests/unit -q
```

O [guia de integração](tests/integration/README.md) descreve os bancos
descartáveis. [MCP.md](MCP.md) detalha configuração, autorização e contratos.

## Contrato MCP

O transporte Streamable HTTP usa `/mcp`, Bearer de serviço e delegação assinada
em `X-Argos-Delegation`. As tools recebem `payload` tipado e incluem:

- `list_properties`, `list_herds`, `list_lots`, `list_exception_animals`;
- `list_applications`, `list_application_animals`, `list_occurrences`,
  `list_animal_lot_history`, `list_cattle_documents`;
- `get_farm_summary`, `summarize_exception_animals`, `get_lot_report_source`;
- `list_medicines` e `list_medicine_documents`.

Os modelos de entrada e saída estão em `packages/contracts` e nos schemas usados
pelo servidor. `get_lot_report_source` retorna um envelope assinado de curta
validade; o MCP não envia e-mails.
