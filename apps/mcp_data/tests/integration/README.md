# Integração do MCP de dados

Os testes desta pasta sobem o serviço MCP real e registram treze ferramentas por
meio do SDK. Eles carregam em um PostgreSQL descartável os módulos SQL 01–09 dos
arquivos `infrastructure/docker/postgres/init/01`–`09` deste repositório, criam
dados sintéticos, inserem documentos de histórico bovino e medicamentos em
coleções Mongo descartáveis e removem os dois bancos ao final. Os documentos
Mongo são validados estruturalmente e pelo escopo de propriedades; não precisam
corresponder a linhas relacionais. A integração verifica paginação, seleção de
propriedade e rejeição de uma propriedade fora do contexto autorizado. Também
verifica o contexto atualizado em cada chamada dentro da mesma sessão MCP,
autenticação de serviço, consultas de medicamentos pelo Doutor e bloqueio de
escrita pelo leitor SQL sem alterar os registros.

Requisitos: Docker Compose e Python do projeto com as dependências de
integração. O harness usa o schema modular versionado neste repositório e não
depende de um clone local de `argos-data-engineering`.

Na raiz do monorepositório:

```powershell
docker compose -f apps/mcp_data/tests/integration/compose.yaml up -d --wait
$env:MCP_DATA_RUN_INTEGRATION = "1"
python -m pytest apps/mcp_data/tests/integration -q
docker compose -f apps/mcp_data/tests/integration/compose.yaml down -v
```

Sem `MCP_DATA_RUN_INTEGRATION=1`, a fixture pula os testes. As portas locais
reservadas são `54331` (PostgreSQL) e `27029` (MongoDB). Não aponte essa suíte
para bancos compartilhados ou de produção: ela cria e remove bancos com prefixo
`mcp_data_it_`.

No runtime de teste, as collections fixas são `cattle_documents` e
`medicine_documents`; os UUIDs relacionais são configurados como texto
(`string`). Os formatos de ID podem variar por implantação e são definidos por
`MCP_DATA_MONGO_RELATIONAL_ID_FORMAT` e `MCP_DATA_MONGO_MEDICINE_ID_FORMAT`.
