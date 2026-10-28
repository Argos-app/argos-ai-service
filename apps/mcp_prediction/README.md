# MCP de predição

O MCP de predição estima consumo sazonal de medicamentos a partir do histórico
autorizado e informa a cobertura e a origem dos dados.

## Tecnologias e execução

Usa Python 3.14.3, FastMCP, Pydantic, asyncpg/PostgreSQL e os pacotes
`contracts` e `security`. Na raiz, instale as dependências, configure no
ambiente do processo as variáveis de [.env.example](.env.example) e forneça um
PostgreSQL compatível. O Python não carrega o `.env` automaticamente:

```powershell
python -m pip install -r apps/mcp_prediction/requirements.txt
$env:PYTHONPATH = "apps/mcp_prediction/src"
python -m argos_mcp_prediction
```

Consulte [Docker local](../../infrastructure/docker/docker-local.md) para a
pilha integrada e [tests/integration/README.md](tests/integration/README.md)
para a validação isolada. Testes unitários:

```powershell
python -m pytest apps/mcp_prediction/tests/unit -q
```

## Contrato MCP

O transporte Streamable HTTP em `/mcp` expõe `estimate_medicine_consumption`,
com `payload`, Bearer de serviço e delegação assinada. A saída tipada descreve
estimativa, cobertura, limitações e origem; os modelos estão em
`packages/contracts/prediction.py`. O cálculo implementado é determinístico e
depende do histórico disponível.
