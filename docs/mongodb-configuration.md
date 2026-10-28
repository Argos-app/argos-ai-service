# Configuração MongoDB compartilhada

Os serviços deste repositório que persistem dados próprios usam o banco
`argos_ai`. A separação é feita por collections: o principal, o MCP de dados e o
MCP de e-mail não devem criar bancos independentes. O MCP de dados pode ler
collections de negócio já existentes no servidor; informe os nomes reais dessas
collections nos parâmetros próprios do serviço.

## Variáveis por sistema e modo

| Sistema       | Variáveis Mongo                                                                     | Individual no host                                                                                                                                                  | Integrado em Compose                                                         |
| ------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `main_api`    | `MAIN_API_MONGO_URI`, `MAIN_API_MONGO_DATABASE`                                     | Use o endereço publicado do replica set e `directConnection=true` quando o replica set anunciar um DNS interno inacessível pelo host. O processo exige replica set. | Usa collections fixas, incluindo `ai_observations`, no mesmo Mongo/database. |
| MCP de dados  | `MCP_DATA_MONGO_URI`, `MCP_DATA_MONGO_DATABASE` e os formatos de ID correspondentes | Use endpoint Mongo acessível pelo host. As collections fixas são `cattle_documents` e `medicine_documents`; o database diferencia os ambientes.                     | Use DNS do Mongo na rede do Compose; os nomes das collections são os mesmos. |
| MCP de e-mail | `MCP_EMAIL_MONGO_URI`, `MCP_EMAIL_MONGO_DATABASE`                                   | Use o endpoint acessível pelo host.                                                                                                                                 | Use DNS do Mongo na rede do Compose.                                         |
| A2A           | `A2A_OBSERVABILITY_MONGO_URI`, `A2A_OBSERVABILITY_MONGO_DATABASE`                   | Opcional; sem URI, a persistência de observabilidade fica desativada. A collection fixa é `ai_a2a_observations`.                                                    | Compose integrado habilita `ai_a2a_observations` no mesmo Mongo/database.    |

`MAIN_API_MONGO_DATABASE`, `MCP_DATA_MONGO_DATABASE` e
`MCP_EMAIL_MONGO_DATABASE` têm padrão `argos_ai`. Configure o mesmo valor nos
três serviços. O A2A usa `argos_ai` quando sua persistência opcional é ligada.
Os nomes das collections são fixos no código para manter consistência entre
serviços, migrations operacionais e consultas BI; alterar esses nomes exige uma
mudança versionada no serviço.

O dicionário das collections, campos, origem, relações e retenção está em
[mongodb-analytics-dictionary.md](mongodb-analytics-dictionary.md). A consulta
operacional de observabilidade está em
[observability-operations.md](observability-operations.md).

O `.env` gerado na raiz é a configuração única do Compose integrado; o Compose
encaminha seletivamente cada variável ao serviço correspondente. O
`.env.example` da raiz documenta essa configuração. Cada aplicação também tem um
exemplo próprio em `apps/<serviço>/.env.example` para execução individual. O
Python lê somente o ambiente do processo e não carrega arquivos `.env`
automaticamente. Em execução individual, exporte as variáveis ou use uma
ferramenta como `dotenv`. Os exemplos de URI nesta tabela indicam o DNS interno
esperado; os arquivos Compose vigentes determinam os valores efetivamente
passados aos containers.

## Collections do `main_api`

Os nomes são fixos no código e não podem ser sobrescritos por ambiente:

| Sufixo                        | Padrão                           | Conteúdo                                                                              |
| ----------------------------- | -------------------------------- | ------------------------------------------------------------------------------------- |
| `SESSIONS`                    | `ai_sessions`                    | Sessões e estado de acesso.                                                           |
| `INTERACTIONS`                | `ai_interactions`                | Interações e estado de execução; também usada pelo repositório de trabalho da sessão. |
| `MESSAGES`                    | `ai_messages`                    | Mensagens públicas da conversa; também usada pelo repositório de trabalho da sessão.  |
| `EXECUTION_EVENTS`            | `ai_execution_events`            | Eventos de execução.                                                                  |
| `SESSION_SUMMARIES`           | `ai_session_summaries`           | Resumos progressivos.                                                                 |
| `USER_MEMORY`                 | `ai_user_memory`                 | Preferências limitadas do usuário.                                                    |
| `REPORT_ARTIFACTS`            | `ai_report_artifacts`            | Artefatos de relatório.                                                               |
| `EMAIL_WORKFLOWS`             | `ai_email_workflows`             | Preparações, perguntas pendentes, autorizações e referências de operação de e-mail.   |
| `LANGGRAPH_CHECKPOINTS`       | `ai_langgraph_checkpoints`       | Checkpoints do grafo.                                                                 |
| `LANGGRAPH_CHECKPOINT_WRITES` | `ai_langgraph_checkpoint_writes` | Escritas do checkpointer.                                                             |

Os nomes são distintos dentro do serviço. A observabilidade principal usa
`ai_observations`. As collections do MCP de e-mail são
`ai_email_prepared_reports` e `ai_email_dispatches`, distintas das collections
internas do principal.

`ai_email_workflows` discrimina os documentos por `record_type` e preserva os
IDs e prazos de cada tipo. A referência da operação usa `_id="operation:<UUID>"`
para coexistir com a autorização de mesmo ID. O índice TTL usa `purge_at`.

## Migração dos dados antigos do MCP de e-mail

Versões anteriores usavam o banco `argos_email` e as collections
`prepared_reports` e `email_dispatches`. A mudança de configuração para
`argos_ai.ai_email_prepared_reports` e `argos_ai.ai_email_dispatches` não copia
nem remove dados existentes automaticamente. Antes de atualizar um ambiente com
dados persistidos:

1. Interrompa o MCP de e-mail para evitar novas gravações durante a cópia e faça
   backup consistente das duas collections antigas.
2. Copie os documentos mantendo `_id`, campos, tipos BSON e índices para as
   collections destino no banco `argos_ai`. Não sobrescreva documentos destino
   sem comparar IDs e conteúdo.
3. Compare contagens, IDs, índices e amostras anonimizadas; então aponte o MCP
   para o banco e collections novos e valide preparação, consulta de status e
   idempotência em ambiente controlado.
4. Mantenha o backup e as collections antigas intactos durante a janela de
   observação. Para reverter, pare o serviço e restaure a configuração anterior
   (`argos_email.prepared_reports` e `argos_email.email_dispatches`). Não
   escreva simultaneamente nos dois destinos.

Este repositório não executou a migração: ela depende de acesso autorizado à
instância e de inventário dos dados de cada ambiente.
