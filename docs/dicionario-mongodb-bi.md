# Dicionário de dados MongoDB para BI

Estado vigente em 16/10/2026. Os sistemas gravam no database compartilhado
`argos_ai` e separam responsabilidades por collection. Nomes são configuráveis
conforme [configuracao-mongodb.md](configuracao-mongodb.md). Datas BSON são UTC;
UUIDs são persistidos como strings canônicas, exceto estruturas internas do
checkpointer LangGraph, cujo formato depende da versão da biblioteca. Exemplos
abaixo são esquemáticos e não contêm dados de produção.

## Mapa de collections

| Sistema                     | Collection padrão                                              | Uso e produtor                                                                | Vínculo e tempo                                                                                                                                                                                          | Retenção / consumo BI                                                                                                |
| --------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| API                         | `ai_sessions`                                                  | Sessão, usuário e escopo autorizado; criada/atualizada pelo fluxo de sessão   | `_id=session_id`, `user_id`, `farm_ids`, `created_at`, `updated_at`                                                                                                                                      | Sem TTL; contém IDs internos de usuário/fazenda. Não é fato de negócio agrícola.                                     |
| API                         | `ai_interactions`                                              | Entrada sanitizada, deduplicação e estado/resultado da interação              | `_id=interaction_id`, `session_id`, `user_id`, `created_at`, `updated_at`                                                                                                                                | Sem TTL; `sanitized_input` e `result_json` podem ainda conter conteúdo sensível.                                     |
| API                         | `ai_messages`                                                  | Mensagens públicas sanitizadas da conversa                                    | `_id=message_id`, `session_id`, `interaction_id`, `sequence`, `created_at`                                                                                                                               | Sem TTL; ordene por sessão e sequência.                                                                              |
| API                         | `ai_execution_events`                                          | Chamadas de agentes/ferramentas e resultados sanitizados                      | `_id=event_id`, `invocation_id`, `session_id`, `interaction_id`, `created_at`, `completed_at`                                                                                                            | Sem TTL; eventos de execução não equivalem a observações de custo.                                                   |
| API                         | `ai_session_summaries`                                         | Resumo progressivo gerado para contexto da conversa                           | `_id` e escopo definidos pelo repositório; ligado a sessão/interação                                                                                                                                     | Sem TTL; conteúdo gerado, não fonte de verdade de negócio.                                                           |
| API                         | `ai_user_memory`                                               | Preferências limitadas extraídas para personalização                          | Chave de usuário e data de atualização definidos pelo repositório                                                                                                                                        | Sem TTL; dado derivado e sujeito a política de privacidade.                                                          |
| API                         | `ai_report_artifacts`                                          | Envelope íntegro do relatório e metadados de execução                         | `_id=reference`, `principal_user_id`, `session_id`, `interaction_id`, `task_id`, `created_at`, `expires_at`                                                                                              | TTL em `purge_at=expires_at+1 dia`; payload contém dados de negócio.                                                 |
| API                         | `ai_email_preparations`                                        | Relatório preparado para envio, metadados do anexo e destinatários resolvidos | `_id=reference`, usuário/sessão/interação e artefato de origem, `created_at`, `expires_at`                                                                                                               | TTL um dia após expiração; contém e-mails, corpo e metadados do anexo.                                               |
| API                         | `ai_pending_recipient_resolutions`                             | Pergunta/opções aguardando desambiguação do destinatário                      | `_id=question_id`, sessão/interação e artefato; `expires_at`                                                                                                                                             | TTL um dia após expiração; opções refletem diretório relacional no momento da preparação.                            |
| API                         | `ai_email_send_authorizations`                                 | Autorização explícita, fingerprints e chave idempotente de envio              | `_id=authorization_id`, `source_interaction_id`, `preparation_reference`, `report_id`, `authorized_at`, `expires_at`                                                                                     | TTL um dia após expiração; não usar como histórico financeiro.                                                       |
| API                         | `ai_email_operation_references`                                | Referência da operação MCP de e-mail após envio/autorização                   | `_id=authorization_id`, `operation_id`, `recorded_at`, usuário/sessão                                                                                                                                    | TTL 31 dias após `recorded_at`; relaciona API à operação de envio.                                                   |
| API                         | `ai_langgraph_checkpoints`                                     | Estado técnico serializado dos grafos                                         | Chaves e estrutura controladas pelo LangGraph/checkpointer                                                                                                                                               | Sem contrato analítico estável; não consultar como dado de negócio.                                                  |
| API                         | `ai_langgraph_checkpoint_writes`                               | Escritas incrementais dos checkpoints                                         | Chaves e estrutura controladas pelo LangGraph/checkpointer                                                                                                                                               | Sem contrato analítico estável; retenção não definida por BI.                                                        |
| API (opcional)              | valor de `MAIN_API_OBSERVABILITY_COLLECTION`                   | Uma observação técnica por tentativa do principal                             | `_id`, `correlation_id`, `parent_observation_id`, `started_at`, `updated_at`                                                                                                                             | Sem TTL; formato detalhado em `observabilidade-extracao-mongo.md`.                                                   |
| MCP e-mail                  | `ai_email_prepared_reports`                                    | Conteúdo preparado para envio no adapter Mongo                                | `_id=report_id`, `requested_by`, `recipients[]`, `subject`, `body`, `attachments[]` (`filename`, `content_type`, `content_base64`, `sha256`), `source_payload_hash`, `created_at`, `expires_at`, `state` | Sem TTL atualmente no adapter; dados pessoais, corpo e anexo base64. Documentos legados podem não ter `attachments`. |
| MCP e-mail                  | `ai_email_dispatches`                                          | Estado/idempotência de envio e resposta do provider                           | `_id=operation_id`, `report_id`, `requested_by`, `idempotency_key`, `state`, `provider_message_id`, `error_code`, `created_at`, `updated_at`                                                             | Sem TTL no adapter; `pending` com `send_claimed=true` exige reconciliação manual.                                    |
| A2A (opcional)              | `A2A_OBSERVABILITY_COLLECTION` (exemplo `ai_a2a_observations`) | Observações de consulta documental/embeddings do A2A                          | Schema compartilhado `Observation`; correlação assinada com a API                                                                                                                                        | Sem TTL; mesma semântica best effort da observabilidade principal.                                                   |
| MCP dados (origem, leitura) | `MCP_DATA_MONGO_CATTLE_COLLECTION`                             | Documentos de histórico/consulta bovina existentes no ambiente de negócio     | Chaves e campos vêm do produtor de dados; MCP aplica projeções, filtros autorizados e paginação                                                                                                          | MCP não escreve nem migra. Use contrato do produtor e `apps/mcp_data/MCP.md`; nomes/configuração variam.             |
| MCP dados (origem, leitura) | `MCP_DATA_MONGO_MEDICINE_COLLECTION`                           | Documentos de bulas/detalhes de medicamentos existentes                       | Esquema do produtor; o MCP valida filtros e limita campos projetados                                                                                                                                     | MCP não escreve nem migra. Confirmar versão e linhagem no sistema produtor.                                          |

## Campos e interpretação das coleções analíticas

### Collections funcionais do principal

Os nomes abaixo são camel/snake case exatos do BSON atual. Campos assinalados
opcionais podem estar ausentes ou nulos em documentos antigos/estados
intermediários.

| Collection             | Campos persistidos e significado                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ai_sessions`          | `_id` UUID da sessão; `user_id` UUID titular; `farm_ids` UUIDs autorizados no momento da criação; `created_at`, `updated_at`; `user_message_count`, `next_message_sequence`, `version` contadores/concorrência; `lease_fencing_token`; `processing_lease` opcional (`owner_id`, `fencing_token`, `acquired_at`, `expires_at`) coordena processamento.                            |
| `ai_interactions`      | `_id` UUID da interação; `session_id`, `user_id`; `deduplication_key`; `kind` (`message`/`answer`); `sanitized_input`, `input_hash`; `status` (`accepted`, `running`, `completed`, `failed`, `interrupted`); `created_at`, `updated_at`; `result_json` opcional; `version`.                                                                                                      |
| `ai_messages`          | `_id` UUID da mensagem; `session_id`, `user_id`, `interaction_id`; `sequence`; `role` (`user`/`assistant`); `kind` (`user_input`, `assistant_answer`, `clarification_question`); `sanitized_content`; `created_at`; `user_message_number` opcional para mensagens do usuário; `payload_json` opcional para resposta estruturada.                                                 |
| `ai_execution_events`  | `_id` UUID do evento; `invocation_id`, `session_id`, `interaction_id`, `user_id`; `target` (`agent`/`tool`), `target_name`, `status` (`started`, `succeeded`, `failed`), `created_at`, `completed_at` opcional, `attempt`; `sanitized_input`, `sanitized_output` opcionais; `messages[]` (`role`, `sanitized_content`, `tool_name`, `tool_call_id`); `safe_error_code` opcional. |
| `ai_session_summaries` | `_id` UUID de sessão; `revision`; `through_sequence`, `through_user_message_number`; `activity`; `important_facts[]` (`content`, `source_references[]`); `decisions[]`; `pending_activity` opcional; `limitations[]`; `summary_text`; `token_count`; `updated_at`. Guarda somente a revisão corrente por sessão.                                                                 |
| `ai_user_memory`       | `_id` UUID do usuário; `tone`, `general_goal` opcionais; IDs de sessão/interação de origem de cada campo; trechos `tone_source_excerpt`/`goal_source_excerpt` opcionais; `tone_updated_at`/`goal_updated_at` opcionais; `revision`, `updated_at`. Conteúdo limitado pelo domínio a preferências de tom e objetivo geral.                                                         |

### Collections funcionais de relatórios e e-mail

| Collection                         | Campos persistidos e significado                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ai_report_artifacts`              | `_id` referência UUID; `principal_user_id`, `session_id`, `interaction_id`; `task_id`; `created_at`, `expires_at`, `purge_at`; `envelope` conforme `LotReportEnvelope` em `packages/contracts/lot_report.py`. O envelope é validado e assinado pelo MCP Data antes do armazenamento.                                                                                                                                                                                |
| `ai_email_preparations`            | `_id` referência UUID; `principal_user_id`, `session_id`, `interaction_id`, `report_artifact_ref`; `request_fingerprint`; `created_at`, `expires_at`, `purge_at`; `prepared_report` conforme `PreparedReport` em `packages/contracts/email.py`, incluindo `attachments[]` apenas com `filename`, `content_type`, `sha256` e `size_bytes`.                                                                                                                           |
| `ai_pending_recipient_resolutions` | `_id` question UUID; usuário/sessão/interação/artefato de origem; `input_type`, `reason_code`, `question_goal`; `unresolved_selector`; `options[]` com `option_id`, `label`, `selector`; `expires_at`, `purge_at`.                                                                                                                                                                                                                                                  |
| `ai_email_send_authorizations`     | `_id` authorization UUID; usuário/sessão/interação fonte; referências de preparação e relatório; `request_fingerprint`, `prepared_fingerprint`, `recipients_fingerprint`; `origin`; `idempotency_key`; `authorized_at`, `expires_at`; `question_id` e `question_expires_at` opcionais; `purge_at`.                                                                                                                                                                  |
| `ai_email_operation_references`    | `_id` authorization UUID; `principal_user_id`, `session_id`, `operation_id`; `recorded_at`, `purge_at`. Liga o recibo local à operação canônica do MCP.                                                                                                                                                                                                                                                                                                             |
| `ai_email_prepared_reports`        | `_id` report UUID; `requested_by`; `recipients[]` (`recipient_id`, `name`, `email`); `subject`, `body`; `attachments[]` (`filename`, `content_type`, `content_base64`, `sha256`); `source_payload_hash`; `created_at`, `expires_at`; `state`. O conteúdo renderizado e o anexo podem incluir dados pessoais e do lote; os bytes base64 do anexo decodificam para o corpo UTF-8 no formato atual. Documentos criados antes do suporte a anexos podem omitir o campo. |
| `ai_email_dispatches`              | `_id` operation UUID; `report_id`, `requested_by`, `idempotency_key`; `state`; `provider_message_id`, `error_code` opcionais; `created_at`, `updated_at`; `send_claimed` aparece após claim atômico.                                                                                                                                                                                                                                                                |

`ai_email_operation_references` também pode conter `last_known_status` e
`last_known_updated_at`, gravados somente após respostas confirmadas do MCP. O
Principal usa esses campos para apresentar o último estado conhecido caso uma
consulta posterior de status falhe; não persiste a resposta completa do
provider. Documentos existentes podem não ter esses campos.

Os campos de relatórios e e-mail acima são tipos BSON string/array/documento,
datas BSON Date e inteiros conforme seus modelos. UUIDs funcionais são strings,
não BSON Binary. O payload `envelope`/`prepared_report` segue os contratos
tipados e enumerados em `packages/contracts`; schema_version e evoluções devem
ser conferidos nesses modelos.

### Observações (`Observation`, schema_version `1` legado; `2` atual)

O produtor é o middleware/recorder do `main_api` ou A2A. Cada `_id` UUID
representa uma tentativa observada; o documento começa em `started` e é
atualizado no mesmo `_id` ao terminar. `correlation_id` agrupa os spans de uma
requisição HTTP; `parent_observation_id` aponta ao span pai e pode faltar em
observações raiz ou registros anteriores à mudança. `service`, `environment`,
`service_version`, `operation_kind`, `operation_name`, `operation_phase`,
`observation_level`, `agent` e `execution_mode` identificam contexto técnico.
Desde `schema_version="2"`, `session_id` e `interaction_id` identificam
diretamente a sessão e a interação funcional associadas às observações da
execução do grafo e aos spans A2A delegados. São strings UUID canônicas no BSON.
Observações anteriores e operações fora de uma interação (por exemplo,
requisições de saúde) podem não conter esses campos. Use `interaction_id` para
unir observações à collection `ai_interactions`; use `session_id` para análise
por conversa. `correlation_id` continua agrupando a requisição e os spans, e
`parent_observation_id` continua descrevendo a árvore técnica. `started_at`,
`ended_at`, `updated_at` são datas UTC; `duration_ms` é duração monotônica em
milissegundos. `status` e `error` descrevem resultado; `http`, `protocol`,
`result` e `resources` são metadados opcionais e sanitizados.

Para modelos, `provider`, `model_requested`, `model_returned` e `model_revision`
identificam a execução. `usage` contém disponibilidade, origem e contagens de
tokens; `billing` contém custo reportado pelo provider ou estimativa pela tarifa
configurada, com moeda e origem explícitas. Ausência nunca significa zero.
Campos de tentativa (`attempt_number`, `attempt_reason`, `attempt_visibility`)
descrevem apenas o que o wrapper conseguiu observar. `timeout_*`,
`cancellation_state` e `replayed_result` registram limites e recuperação quando
aplicável. Consulte o modelo em `packages/observability/models.py` para limites
e enums normativos.

Quando `billing.cost_source="configured_rate"`, o custo é estimado em USD pelas
variáveis globais opcionais `MODEL_INPUT_COST_PER_MILLION_TOKENS` e
`MODEL_OUTPUT_COST_PER_MILLION_TOKENS`. Para chat, exige tokens de entrada e
saída e as duas tarifas; para embeddings, usa somente entrada. Custo explícito
válido do provider (`cost_source="provider"`) tem precedência. `reported_cost` é
Decimal128 no Mongo, `currency` é código ISO de três letras e `cost_source`
identifica a origem; o mesmo objeto `billing` é encaminhado como
`metadata.argos_billing` ao LangSmith. Sem uso ou tarifa suficientes, `billing`
fica ausente, não representa zero nem valor faturado. Os campos nativos de custo
do LangSmith são preenchidos somente quando a moeda é USD.

### Documentos de e-mail e relatório

`ai_email_prepared_reports.recipients[]` contém `recipient_id`, `name` e
`email`; `subject`/`body` são o texto renderizado, não fatos estruturados. O
hash `source_payload_hash` liga ao payload validado sem armazená-lo por esse
campo. Em `ai_email_dispatches`, `state` pode ser `pending`, `accepted`,
`partial`, `failed` ou `unknown`; um ID de mensagem do provider confirma
aceitação técnica, não leitura pelo destinatário. `error_code` é sanitizado.

`ai_report_artifacts.envelope` guarda o `LotReportEnvelope` validado. Suas
unidades e campos de negócio são definidos em `packages/contracts`, e a origem é
a saída validada do MCP de dados assinada antes do uso pelo agente.
Transformações de apresentação no e-mail não alteram o envelope persistido. As
collections de preparação/autorização do principal guardam fingerprints,
referências e estado operacional; não são uma tabela fato para métricas de
envio.

## Relacionamentos, limitações e exemplos anonimizados

### Documentos de origem lidos pelo MCP de dados

`MCP_DATA_MONGO_CATTLE_COLLECTION` é lida por `CattleHistoryDocument`. Os campos
tipados garantidos pelo MCP são `_id` (convertido para `id` string),
`relational_property_id` (UUID), `herd` (objeto, exige `herd_id` UUID), `lots`
(array) e `report_created_at`/`last_updated_at` (datas opcionais). Em `lots[]`,
o MCP valida `lot_id`, `applications[]` com `application_id` e `animal[]` com
`animal_exception_id`, `lot_id`, `origin_lot_id`, `exception_applications[]`,
`occurrences[]` e `lot_history[]`; subdocumentos de aplicação exigem `lot_id`, e
ocorrências exigem `occurrence_id`. `history_summary` é opcional e, quando
presente, os contadores de lotes, animais excepcionais, aplicações e ocorrências
precisam bater com os arrays. O modelo bovino preserva campos extras, portanto
os demais atributos de negócio não formam contrato estável neste repositório.
Confirme-os com o produtor Mongo antes de criar métricas/joins.

`MCP_DATA_MONGO_MEDICINE_COLLECTION` segue `MedicineDetailDocument`: `_id` vira
`id` string; `relational_medicine_id` UUID; `report_created_at` e
`last_updated_at` opcionais; `brand_name`, `active_ingredient`,
`mapa_registration` opcional, `indication`, `description` opcional;
`therapeutic_category` com `therapeutic_category_id`, `name` e `description`
opcional; `supplier` com `supplier_id`, `company_name`, `cnpj`, `email`, `phone`
e `address` opcionais; `needs_prescription`, `active`, `meat_withdrawal_days`
(inteiro >= 0), `dose` (Decimal > 0), `unit` (ml, mg, g, UI ou dose) e `route`
(enum de via ou nulo). Campos adicionais são ignorados na resposta do MCP.
`relational_medicine_id` pode estar no Mongo como string ou UUID BSON conforme
`MCP_DATA_MONGO_MEDICINE_ID_FORMAT`. O MCP não compara esse documento com a
linha SQL de catálogo; analistas devem tratar divergências como possíveis e usar
`relational_medicine_id` como vínculo pretendido, não como integridade
referencial garantida.

O MCP filtra documentos bovinos pelas propriedades autorizadas da delegação e
aplica filtros/paginação; não altera nem retém a collection. O catálogo
relacional `medicine` é PostgreSQL, não uma collection Mongo. Retenção,
versionamento e campos fora dos contratos acima são controlados pelo produtor
externo e ainda precisam de confirmação para BI.

`session_id` une sessões, interações, mensagens, eventos e artefatos. Uma
interação pode produzir múltiplas mensagens e eventos. `interaction_id` da API é
um UUID distinto do `correlation_id` observacional, que é por requisição HTTP.
`report_artifact_ref` relaciona preparação de e-mail ao envelope; `report_id`
relaciona a API à collection de relatórios preparados do MCP; `operation_id`
liga autorização ao dispatch. Não há chave estrangeira Mongo; valide órfãos e
expiração antes de joins.

Exemplo sintético: `{ "_id": "<uuid>`, `"session_id": "<uuid>`,
`"interaction_id": "<uuid>`, `"status": "completed"`,
`"created_at": ISODate("2026-10-13T12:00:00Z") }`. Conteúdo de conversa,
relatório, e-mail e memória tem retenção e risco diferentes; permissões BI devem
ser somente leitura e limitadas às collections/campos aprovados. A
observabilidade não tem TTL e registros podem faltar por queda ou saturação da
fila. Veja os guias de [configuração](configuracao-mongodb.md),
[extração](observabilidade-extracao-mongo.md) e
[operação](operacao-observabilidade.md). Para schemas exatos dos documentos de
negócio, consulte os modelos/repos/repositórios citados em cada aplicação; as
collections de origem do MCP de dados exigem confirmação junto ao produtor.
