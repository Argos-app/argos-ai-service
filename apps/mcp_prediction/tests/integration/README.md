# Integração isolada do MCP de predição

Execute a partir da raiz do repositório com Docker Desktop disponível. A
infraestrutura usa uma instância PostgreSQL local, descartável e em `tmpfs`;
nenhuma base de usuário ou de desenvolvimento é utilizada.

```powershell
python -m venv .venv/mcp-prediction-integration
.venv/mcp-prediction-integration/Scripts/python.exe -m pip install -r apps/mcp_prediction/tests/integration/requirements.txt
docker compose -f apps/mcp_prediction/tests/integration/compose.yaml up -d --wait
$env:MCP_PREDICTION_RUN_INTEGRATION="1"
.venv/mcp-prediction-integration/Scripts/python.exe -m pytest apps/mcp_prediction/tests/integration -v -p no:cacheprovider
Remove-Item Env:MCP_PREDICTION_RUN_INTEGRATION
docker compose -f apps/mcp_prediction/tests/integration/compose.yaml down
```

Sem `MCP_PREDICTION_RUN_INTEGRATION=1`, os testes são ignorados na execução
habitual. Quando habilitados, ausência de PostgreSQL ou falha ao iniciar o MCP
faz a integração falhar. O fixture cria um banco com nome aleatório, insere o
schema mínimo exercitado pelos adapters, gera aplicações em cada mês da janela
anual e remove o banco e a role de leitura ao finalizar.

O processo MCP real recebe uma chave Ed25519 efêmera, token interno descartável
e configuração `MCP_PREDICTION_DATA_ORIGIN=synthetic`. A conta de preparação
cria schema e dados; o runtime usa uma role separada, com `SELECT` nos objetos
do fixture, além de `default_transaction_read_only=on` no pool.

## O que é verificado

- `initialize`, `list_tools` e `call_tool` pelo SDK MCP, com uma única
  ferramenta e argumento `payload`.
- Consulta ao PostgreSQL real e cálculo anual agregado, incluindo quantidade,
  eventos, cabeças-administrações, modo de simulação e cobertura desconhecida.
- Isolamento: propriedades externas ao contexto assinado não aparecem no
  resultado, e um filtro fora do contexto delegado é rejeitado.
- Bearer e delegação ausentes ou inválidos são rejeitados no transporte.
- A role usada pelo MCP não tem permissão de escrita e uma tentativa de `INSERT`
  é negada pelo PostgreSQL.
