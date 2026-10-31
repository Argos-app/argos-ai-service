# Configuração MongoDB

A Main API e o MCP de e-mail persistem dados; o MCP de dados consulta
collections de negócio e o A2A pode persistir observações. Cada serviço recebe
URI e database próprios. `argos_ai` é o padrão no código; o Compose integrado
configura o mesmo Mongo e database local para os serviços. Execuções individuais
e implantações podem apontar para outros databases compatíveis com as fontes e
permissões de cada serviço.

| Serviço       | Configuração                                                      | Uso                                                                                                                 |
| ------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Main API      | `MAIN_API_MONGO_URI`, `MAIN_API_MONGO_DATABASE`                   | Sessões, memória, relatórios, checkpoints e observações. O checkpointer exige replica set.                          |
| MCP de dados  | `MCP_DATA_MONGO_URI`, `MCP_DATA_MONGO_DATABASE`                   | Leitura das collections fixas `cattle_documents` e `medicine_documents`; os formatos de ID também são configurados. |
| MCP de e-mail | `MCP_EMAIL_MONGO_URI`, `MCP_EMAIL_MONGO_DATABASE`                 | Relatórios preparados e operações de despacho.                                                                      |
| A2A           | `A2A_OBSERVABILITY_MONGO_URI`, `A2A_OBSERVABILITY_MONGO_DATABASE` | Observabilidade opcional em `ai_a2a_observations`; sem URI, não persiste observações.                               |

A configuração por serviço está nos arquivos `apps/<serviço>/.env.example`. O
Python lê as variáveis do processo e não carrega esses arquivos automaticamente.
Para o Compose integrado, gere o `.env` da raiz conforme
[Docker local](../docker/README.md). Em execução pelo host, use um endereço
acessível e, para o replica set de teste que anuncia DNS interno,
`directConnection=true`.

## Collections e retenção

As collections próprias dos serviços são criadas pelo MongoDB quando o código
cria um índice ou grava o primeiro documento. A Main API prepara seus índices na
inicialização; o checkpointer prepara os seus pelo pacote LangGraph. O MCP de
e-mail prepara o índice de despachos na inicialização e grava relatórios quando
necessário. A observabilidade prepara índices em segundo plano. Portanto, nem
todas as collections precisam aparecer antes do primeiro uso. As credenciais de
escrita precisam permitir também a criação das collections e dos índices.

`cattle_documents` e `medicine_documents` são fontes de negócio externas: o
MCP de dados só as consulta e não as cria nem popula. O produtor deve fornecer
os documentos no database configurado. O `mongo_seed` do Compose integrado gera
dados sintéticos locais; ele não abastece os ambientes de deploy.

Os nomes da Main API, inclusive `ai_email_workflows`, `ai_langgraph_checkpoints`
e `ai_langgraph_checkpoint_writes`, são definidos em `app/settings.py`. A
observabilidade usa `ai_observations`. O MCP de e-mail usa
`ai_email_prepared_reports` e `ai_email_dispatches`; o MCP de dados apenas lê as
collections de negócio. Ajustar o database não renomeia ou copia collections.

`ai_email_workflows` diferencia preparação, resolução, autorização e referência
de operação por `record_type`; seu TTL usa `purge_at`. Artefatos de relatório
possuem TTL próprio. Consulte [estruturas das collections](collections.md) para
campos e índices e o [dicionário de BI](analytics-dictionary.md) para origem,
dados sensíveis e limitações de consumo. A extração das observações está em
[mongodb-extraction.md](../../packages/observability/docs/mongodb-extraction.md).
