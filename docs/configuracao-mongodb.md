# Configuração MongoDB compartilhada

Os serviços deste repositório que persistem dados próprios usam o banco
`argos_ai`. A separação é feita por collections: o principal, o MCP de dados e o
MCP de e-mail não devem criar bancos independentes. O MCP de dados pode ler
collections de negócio já existentes no servidor; informe os nomes reais dessas
collections nos parâmetros próprios do serviço.

## Variáveis por sistema e modo

| Sistema       | Variáveis Mongo                                                                                                                                               | Individual no host                                                                                                                                                  | Integrado em Compose                                                                      |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `main_api`    | `MAIN_API_MONGO_URI`, `MAIN_API_MONGO_DATABASE`, `MAIN_API_MONGO_COLLECTION_*`, `MAIN_API_OBSERVABILITY_COLLECTION`                                           | Use o endereço publicado do replica set e `directConnection=true` quando o replica set anunciar um DNS interno inacessível pelo host. O processo exige replica set. | Compose integrado habilita `ai_observations` por padrão no mesmo Mongo/database.          |
| MCP de dados  | `MCP_DATA_MONGO_URI`, `MCP_DATA_MONGO_DATABASE`, `MCP_DATA_MONGO_CATTLE_COLLECTION`, `MCP_DATA_MONGO_MEDICINE_COLLECTION` e os formatos de ID correspondentes | Use o endpoint Mongo acessível pelo host e informe os nomes reais das collections de negócio.                                                                       | Use DNS do Mongo na rede do Compose e mantenha os nomes reais das collections de negócio. |
| MCP de e-mail | `MCP_EMAIL_MONGO_URI`, `MCP_EMAIL_MONGO_DATABASE`, `MCP_EMAIL_REPORTS_COLLECTION`, `MCP_EMAIL_DISPATCH_COLLECTION`                                            | Use o endpoint acessível pelo host.                                                                                                                                 | Use DNS do Mongo na rede do Compose.                                                      |
| A2A           | `A2A_OBSERVABILITY_MONGO_URI`, `A2A_OBSERVABILITY_MONGO_DATABASE`, `A2A_OBSERVABILITY_COLLECTION`                                                             | Opcional; URI e collection precisam ser fornecidas juntas.                                                                                                          | Compose integrado habilita `ai_a2a_observations` no mesmo Mongo/database.                 |

`MAIN_API_MONGO_DATABASE`, `MCP_DATA_MONGO_DATABASE` e
`MCP_EMAIL_MONGO_DATABASE` têm padrão `argos_ai`. Configure o mesmo valor nos
três serviços. O A2A usa `argos_ai` quando sua persistência opcional é ligada.
As collections de persistência principal e e-mail têm nomes padrão prefixados
por `ai_`; as duas collections de origem do MCP de dados devem ser nomeadas
explicitamente para corresponder ao ambiente.

O dicionário das collections, campos, origem, relações e retenção está em
[dicionario-mongodb-bi.md](dicionario-mongodb-bi.md). A consulta operacional de
observabilidade está em
[operacao-observabilidade.md](operacao-observabilidade.md).

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

Cada nome pode ser sobrescrito com a variável `MAIN_API_MONGO_COLLECTION_`
seguida do nome em maiúsculas:

| Sufixo                          | Padrão                             | Conteúdo                                                                              |
| ------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------- |
| `SESSIONS`                      | `ai_sessions`                      | Sessões e estado de acesso.                                                           |
| `INTERACTIONS`                  | `ai_interactions`                  | Interações e estado de execução; também usada pelo repositório de trabalho da sessão. |
| `MESSAGES`                      | `ai_messages`                      | Mensagens públicas da conversa; também usada pelo repositório de trabalho da sessão.  |
| `EXECUTION_EVENTS`              | `ai_execution_events`              | Eventos de execução.                                                                  |
| `SESSION_SUMMARIES`             | `ai_session_summaries`             | Resumos progressivos.                                                                 |
| `USER_MEMORY`                   | `ai_user_memory`                   | Preferências limitadas do usuário.                                                    |
| `REPORT_ARTIFACTS`              | `ai_report_artifacts`              | Artefatos de relatório.                                                               |
| `EMAIL_PREPARATIONS`            | `ai_email_preparations`            | Preparações de e-mail criadas pelo principal.                                         |
| `PENDING_RECIPIENT_RESOLUTIONS` | `ai_pending_recipient_resolutions` | Resoluções pendentes de destinatário.                                                 |
| `EMAIL_SEND_AUTHORIZATIONS`     | `ai_email_send_authorizations`     | Autorizações de envio.                                                                |
| `EMAIL_OPERATION_REFERENCES`    | `ai_email_operation_references`    | Referências idempotentes das operações de e-mail.                                     |
| `LANGGRAPH_CHECKPOINTS`         | `ai_langgraph_checkpoints`         | Checkpoints do grafo.                                                                 |
| `LANGGRAPH_CHECKPOINT_WRITES`   | `ai_langgraph_checkpoint_writes`   | Escritas do checkpointer.                                                             |

Os nomes precisam ser distintos dentro do serviço. A collection configurada para
observabilidade do principal também não pode coincidir com uma delas. As
collections do MCP de e-mail usam por padrão `ai_email_prepared_reports` e
`ai_email_dispatches`, distintas das collections internas do principal.

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
