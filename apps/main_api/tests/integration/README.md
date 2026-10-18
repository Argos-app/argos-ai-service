# Integração Mongo do `main_api`

Os testes usam MongoDB 7 em replica set de um membro. O Compose publica a porta
`27027` apenas em `127.0.0.1` e guarda os dados em `tmpfs`; nenhuma base local
de desenvolvimento ou produção é usada. Execute a partir da raiz do repositório
com Docker Desktop disponível.

```powershell
docker compose --file apps/main_api/tests/integration/compose.yaml up -d --wait mongo
docker compose --file apps/main_api/tests/integration/compose.yaml run --rm mongo-init
docker compose --file apps/main_api/tests/integration/compose.yaml exec -T mongo mongosh --quiet --eval 'let primary = false; for (let n = 0; n < 60; n++) { try { if (rs.status().myState === 1) { primary = true; break; } } catch (e) {} sleep(500); } if (!primary) quit(1)'
$env:MAIN_API_MONGO_TEST_URI = "mongodb://127.0.0.1:27027/?directConnection=true"
New-Item -ItemType Directory -Force reports | Out-Null
python -m pytest apps/main_api/tests/integration -v --junitxml=reports/main-api-integration.xml
Remove-Item Env:MAIN_API_MONGO_TEST_URI
docker compose --file apps/main_api/tests/integration/compose.yaml down --volumes --remove-orphans
```

Em Bash, use `export MAIN_API_MONGO_TEST_URI=...` e
`unset MAIN_API_MONGO_TEST_URI`. O URI usa conexão direta para que o driver
executado no host não tente resolver o nome interno `mongo` anunciado pelo
replica set do Compose. Cada teste usa um database aleatório; o teste de
persistência o remove ao terminar, e o database do checkpointer é descartado
junto com o container Mongo em `down`. Sem a variável de ambiente, os testes de
integração são ignorados; quando a variável está definida e o Mongo não
responde, o teste falha.

## Cobertura

- Replica de checkpoint LangGraph e retomada de pergunta após recriar o saver.
- Persistência de sessão, interação, mensagens, evento de agente, resumo e
  perfil entre clientes Mongo; transação de aceitação/finalização e
  deduplicação.
- Aquisição concorrente de lease, sequência e contador, leitura paginada do
  histórico, validação de titularidade e redação conservadora quando o acesso a
  uma fazenda da sessão é revogado.
- Isolamento do perfil por usuário e recuperação dos dados após reconexão.

A CI executa esses testes em um job separado após qualidade e testes unitários,
publica o relatório JUnit como artefato e encerra os containers mesmo se um
teste falhar. Os agentes e sumarizadores usam respostas determinísticas nos
testes unitários; a integração Mongo não chama provedores de modelo nem exige
credenciais pagas.
