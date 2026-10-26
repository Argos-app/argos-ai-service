# Integração Mongo e MCP do `main_api`

Os testes usam serviços descartáveis locais. A persistência do principal usa
MongoDB 7 em replica set na porta `27027`; o harness do MCP de e-mail usa
MongoDB, PostgreSQL e Mailpit nas portas `27028`, `54328` e `8028`. Não use
bases de desenvolvimento ou produção. Execute da raiz com Docker Desktop ativo.

```powershell
docker compose --file apps/main_api/tests/integration/compose.yaml up -d --wait auth-postgres mongo
docker compose --file apps/main_api/tests/integration/compose.yaml run --rm mongo-init
docker compose --file apps/main_api/tests/integration/compose.yaml exec -T mongo mongosh --quiet --eval 'let primary = false; for (let n = 0; n < 60; n++) { try { if (rs.status().myState === 1) { primary = true; break; } } catch (e) {} sleep(500); } if (!primary) quit(1)'
docker compose --file apps/mcp_email/tests/integration/compose.yaml up -d --wait
$env:MAIN_API_MONGO_TEST_URI = "mongodb://127.0.0.1:27027/?directConnection=true"
$env:MAIN_API_TEST_AUTH_DATABASE_URL = "postgresql://auth_test:auth_test@127.0.0.1:54329/auth_test"
$env:MCP_EMAIL_RUN_INTEGRATION = "1"
New-Item -ItemType Directory -Force reports | Out-Null
python -m pytest apps/main_api/tests/integration -v --junitxml=reports/main-api-integration.xml
Remove-Item Env:MAIN_API_MONGO_TEST_URI
Remove-Item Env:MAIN_API_TEST_AUTH_DATABASE_URL
Remove-Item Env:MCP_EMAIL_RUN_INTEGRATION
docker compose --file apps/mcp_email/tests/integration/compose.yaml down --volumes --remove-orphans
docker compose --file apps/main_api/tests/integration/compose.yaml down --volumes --remove-orphans
```

Em Bash, use `export`/`unset` para as três variáveis. A conexão direta de Mongo
impede o driver do host de tentar resolver o nome interno `mongo` anunciado pelo
replica set. Cada teste cria e remove bancos com nomes aleatórios. Sem
`MAIN_API_MONGO_TEST_URI`, os testes do principal são ignorados; os testes do
repositório de autenticação também exigem `MAIN_API_TEST_AUTH_DATABASE_URL`; o
fluxo contra o MCP real exige `MCP_EMAIL_RUN_INTEGRATION=1`. Com as variáveis
definidas, infraestrutura ausente causa falha, não aprovação.

## Cobertura

- Retomada de pergunta após recriar o saver Mongo.
- Persistência, concorrência, isolamento por usuário, leases, sequência,
  deduplicação, histórico e revogação de acesso.
- Fluxo `API → grafo → cliente MCP → MCP de e-mail real → Mongo/Mailpit` com
  agentes determinísticos: preparação sem envio, envio explícito, resposta
  pública sem e-mail/corpo, replay HTTP e replay após reinício do principal.

A origem Fazenda desse fluxo é uma fixture determinística assinada com a chave
de teste confiada pelo MCP de e-mail. Ela verifica o contrato consumidor e a
validação da assinatura no MCP, mas não substitui uma origem assinada produzida
pelo MCP de dados real.

A API e o MCP rodam sem provedores de modelo pagos. O processo MCP de e-mail é
real; o SMTP é capturado localmente pelo Mailpit e não envia mensagens externas.
