# Integração do MCP de dados

Os testes desta pasta sobem o serviço MCP real e chamam as oito ferramentas pelo
SDK. Eles carregam em um PostgreSQL descartável os módulos SQL 01–09 do commit
remoto `origin/develop` `2f24af7abd25ab4afda7cb1561f02973db0b19d5`, criam dados
sintéticos e removem o banco ao final. PostgreSQL e Mongo usam volumes em
memória no Compose; Mongo é necessário para iniciar o runtime, embora esta etapa
valide somente as consultas relacionais.

Requisitos: Docker Compose, Python do projeto com as dependências de integração
e um clone local de `argos-data-engineering` que contenha o commit acima. O
clone é procurado por padrão como diretório irmão deste repositório; defina
`ARGOS_DATA_ENGINEERING_REPO` para outro caminho se necessário. Os scripts são
lidos pelo SHA fixo, sem executar arquivos locais fora dessa revisão remota.

Na raiz do monorepositório:

```powershell
docker compose -f apps/mcp_data/tests/integration/compose.yaml up -d --wait
$env:MCP_DATA_RUN_INTEGRATION = "1"
python -m pytest apps/mcp_data/tests/integration -q
docker compose -f apps/mcp_data/tests/integration/compose.yaml down -v
```

Sem `MCP_DATA_RUN_INTEGRATION=1`, a fixture pula os testes. As portas locais
reservadas são `54329` (PostgreSQL) e `27029` (MongoDB). Não aponte essa suíte
para bancos compartilhados ou de produção: ela cria e remove bancos com prefixo
`mcp_data_it_`.
