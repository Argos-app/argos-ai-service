# Estruturas das collections MongoDB

Este documento descreve as collections MongoDB usadas pelos serviços deste
repositório e mostra um esqueleto dos documentos persistidos ou lidos. Os
exemplos são JSONC esquemático: comentários, `...` e valores entre `< >` são
explicativos e não devem ser copiados como documentos válidos.

As estruturas do `main_api`, do MCP de e-mail e da observabilidade são definidas
pelo código deste repositório. O MCP de dados lê duas collections de negócio; a
estrutura completa delas é controlada pelo produtor desses dados e não pode ser
deduzida integralmente daqui. Datas persistidas são datas BSON em UTC. O
`main_api` representa UUIDs como strings canônicas, exceto nos documentos de
checkpoints geridos pelo LangGraph. O MCP de dados aceita IDs relacionais como
string ou UUID BSON, conforme configuração.

## Inventário

| Serviço       | Collection                       | Acesso                           | Finalidade                                                      |
| ------------- | -------------------------------- | -------------------------------- | --------------------------------------------------------------- |
| `main_api`    | `ai_sessions`                    | Leitura e escrita                | Sessões, titularidade, fazendas e lease de processamento.       |
| `main_api`    | `ai_interactions`                | Leitura e escrita                | Entrada, deduplicação, estado e resultado público da interação. |
| `main_api`    | `ai_messages`                    | Leitura e escrita                | Mensagens sanitizadas da conversa e respostas estruturadas.     |
| `main_api`    | `ai_execution_events`            | Leitura e escrita                | Eventos sanitizados de execução de agentes e ferramentas.       |
| `main_api`    | `ai_session_summaries`           | Leitura e escrita                | Resumo corrente de cada sessão.                                 |
| `main_api`    | `ai_user_memory`                 | Leitura e escrita                | Preferências limitadas do usuário entre sessões.                |
| `main_api`    | `ai_report_artifacts`            | Leitura e escrita                | Envelopes assinados de relatórios, com escopo e validade.       |
| `main_api`    | `ai_email_workflows`             | Leitura e escrita                | Preparações, perguntas, autorizações e recibos de envio.        |
| `main_api`    | `ai_langgraph_checkpoints`       | Leitura e escrita pelo LangGraph | Checkpoints de estado do grafo.                                 |
| `main_api`    | `ai_langgraph_checkpoint_writes` | Leitura e escrita pelo LangGraph | Escritas pendentes/associadas aos checkpoints.                  |
| `main_api`    | `ai_observations`                | Leitura e escrita                | Observabilidade técnica do serviço principal.                   |
| MCP de e-mail | `ai_email_prepared_reports`      | Leitura e escrita                | Relatórios e anexos prontos para envio.                         |
| MCP de e-mail | `ai_email_dispatches`            | Leitura e escrita                | Estado e idempotência das tentativas de envio.                  |
| MCP de dados  | `cattle_documents`               | Somente leitura                  | Histórico documental de bovinos por propriedade.                |
| MCP de dados  | `medicine_documents`             | Somente leitura                  | Detalhes documentais de medicamentos.                           |
| A2A           | `ai_a2a_observations`            | Leitura e escrita                | Observabilidade técnica do serviço A2A.                         |

Os nomes, databases e configurações estão em
[`configuration.md`](configuration.md). O dicionário voltado a BI está em
[`analytics-dictionary.md`](analytics-dictionary.md).

## Collections do `main_api`

### `ai_sessions`

Um documento por sessão. `_id` é o UUID da sessão; `user_id` identifica o
proprietário interno; `farm_ids` registra as fazendas autorizadas quando a
sessão foi criada. `processing_lease` coordena processamento concorrente e pode
ser `null`.

```jsonc
{
	"_id": "<session UUID>",
	"user_id": "<internal user UUID>",
	"farm_ids": ["<farm UUID>"],
	"created_at": "<BSON UTC datetime>",
	"updated_at": "<BSON UTC datetime>",
	"user_message_count": 0,
	"next_message_sequence": 1,
	"version": 1,
	"lease_fencing_token": 0,
	"processing_lease": null, // ou { owner_id, fencing_token, acquired_at, expires_at }
}
```

O contador de sequência e a versão são atualizados de forma atômica; `farm_ids`
é usado para verificar se a sessão ainda pode continuar com seu escopo original.

### `ai_interactions`

Um documento por envio ou resposta do usuário. `deduplication_key` e
`input_hash` evitam processar novamente uma solicitação já aceita. `result_json`
contém o resultado público serializado quando disponível.

```jsonc
{
	"_id": "<interaction UUID>",
	"session_id": "<session UUID>",
	"user_id": "<internal user UUID>",
	"deduplication_key": "<key>",
	"kind": "message | answer",
	"sanitized_input": "<sanitized user input>",
	"input_hash": "<hash>",
	"status": "accepted | running | completed | degraded | failed | interrupted",
	"created_at": "<BSON UTC datetime>",
	"updated_at": "<BSON UTC datetime>",
	"result_json": "<serialized public outcome or null>",
	"version": 1,
}
```

### `ai_messages`

Um documento por mensagem pública. A sequência é monotônica dentro da sessão.
`payload_json` preserva a resposta estruturada e os metadados públicos da
resposta; não substitui nem altera o contrato HTTP de perguntas e respostas.

```jsonc
{
	"_id": "<message UUID>",
	"session_id": "<session UUID>",
	"user_id": "<internal user UUID>",
	"interaction_id": "<interaction UUID>",
	"sequence": 1,
	"role": "user | assistant",
	"kind": "user_input | assistant_answer | clarification_question",
	"sanitized_content": "<sanitized text>",
	"created_at": "<BSON UTC datetime>",
	"user_message_number": 1, // null para mensagem do assistente
	"payload_json": "<serialized structured payload or null>",
}
```

### `ai_execution_events`

Um documento por chamada de agente ou ferramenta. Começa com `started` e recebe
o resultado terminal no mesmo `_id` (UUID da invocação); os dois identificadores
devem ser iguais.

```jsonc
{
	"_id": "<invocation UUID>",
	"invocation_id": "<invocation UUID>",
	"session_id": "<session UUID>",
	"interaction_id": "<interaction UUID>",
	"user_id": "<internal user UUID>",
	"target": "agent | tool",
	"target_name": "<agent or tool name>",
	"status": "started | succeeded | failed",
	"created_at": "<BSON UTC datetime>",
	"completed_at": "<BSON UTC datetime or null>",
	"attempt": 1,
	"sanitized_input": "<text or null>",
	"sanitized_output": "<text or null>",
	"messages": [
		{
			"role": "assistant_tool_call | tool_response | agent_response",
			"sanitized_content": "<text>",
			"tool_name": "<name or null>",
			"tool_call_id": "<id or null>",
		},
	],
	"safe_error_code": "<code or null>",
}
```

### `ai_session_summaries`

Mantém somente a revisão corrente do resumo por sessão. `_id` é o UUID da
sessão. `through_sequence` e `through_user_message_number` indicam até onde o
resumo cobre a conversa.

```jsonc
{
	"_id": "<session UUID>",
	"revision": 1,
	"through_sequence": 1,
	"through_user_message_number": 1,
	"activity": "<current activity>",
	"important_facts": [
		{ "content": "<fact>", "source_references": ["<ref>"] },
	],
	"decisions": ["<decision>"],
	"pending_activity": "<text or null>",
	"limitations": ["<limitation>"],
	"summary_text": "<summary>",
	"token_count": 1,
	"updated_at": "<BSON UTC datetime>",
}
```

### `ai_user_memory`

Um documento por usuário; `_id` é o UUID interno. A memória é limitada ao tom de
comunicação e ao objetivo geral. Os campos de origem registram a sessão e a
interação que justificam cada preferência.

```jsonc
{
	"_id": "<internal user UUID>",
	"tone": "<text or null>",
	"general_goal": "<text or null>",
	"tone_source_session_id": "<session UUID or null>",
	"tone_source_interaction_id": "<interaction UUID or null>",
	"goal_source_session_id": "<session UUID or null>",
	"goal_source_interaction_id": "<interaction UUID or null>",
	"tone_source_excerpt": "<excerpt or null>",
	"goal_source_excerpt": "<excerpt or null>",
	"tone_updated_at": "<BSON UTC datetime or null>",
	"goal_updated_at": "<BSON UTC datetime or null>",
	"revision": 1,
	"updated_at": "<BSON UTC datetime>",
}
```

### `ai_report_artifacts`

Guarda o envelope assinado de um relatório separado do estado visível ao agente.
As leituras exigem usuário, sessão e interação correspondentes. `purge_at` é
usado pelo índice TTL e fica um dia após `expires_at`.

```jsonc
{
	"_id": "<artifact reference UUID>",
	"principal_user_id": "<internal user UUID>",
	"session_id": "<session UUID>",
	"interaction_id": "<interaction UUID>",
	"task_id": "<task ID>",
	"created_at": "<BSON UTC datetime>",
	"expires_at": "<BSON UTC datetime>",
	"purge_at": "<expires_at plus one day>",
	"envelope": {
		"report": {
			"schema_version": "lot-report.v1",
			"requested_lot": "<...>",
			"applications": [],
			"exception_animals": [],
		},
		"principal_user_id": "<internal user UUID>",
		"authorized_property_ids": ["<farm UUID>"],
		"source_service": "mcp_data",
		"valid_until": "<BSON UTC datetime>",
		"payload_hash": "<hash>",
		"signature": "<signature>",
		"signature_key_id": "<key ID>",
	},
}
```

### `ai_email_workflows`

Consolida os quatro tipos de estado de e-mail da Main API. `record_type`
identifica preparação, pergunta pendente, autorização ou referência de operação.
Os campos e prazos individuais são preservados e o índice TTL remove registros
conforme `purge_at`. Os MCPs mantêm suas collections próprias.

```jsonc
{
	"_id": "<preparation reference UUID>",
	"record_type": "preparation",
	"principal_user_id": "<internal user UUID>",
	"session_id": "<session UUID>",
	"interaction_id": "<interaction UUID>",
	"report_artifact_ref": "<artifact reference UUID>",
	"request_fingerprint": "<hash>",
	"created_at": "<BSON UTC datetime>",
	"expires_at": "<BSON UTC datetime>",
	"purge_at": "<expires_at plus one day>",
	"prepared_report": {
		"report_id": "<UUID>",
		"status": "prepared | needs_recipients | rejected",
		"requested_by": "<internal user UUID>",
		"recipients": [
			{ "recipient_id": "<UUID>", "name": "<name>", "email": "<email>" },
		],
		"subject": "<text or null>",
		"body": "<text or null>",
		"attachments": [
			{
				"filename": "<name>",
				"content_type": "<mime>",
				"sha256": "<hash>",
				"size_bytes": 1,
			},
		],
		"created_at": "<BSON UTC datetime>",
		"expires_at": "<BSON UTC datetime>",
		"unresolved": [],
	},
}
```

As perguntas pendentes usam `record_type: "pending_resolution"` e
`_id=question_id`. Autorizações usam `record_type: "authorization"` e
`_id=authorization_id`. Referências usam `record_type: "operation_reference"` e
`_id="operation:<authorization UUID>"`. Os campos específicos permanecem iguais
aos modelos atuais.

### Campos de pergunta pendente

Armazena uma pergunta pendente de resolução de destinatário. As opções mantêm um
rótulo de apresentação e um `RecipientSelector`; o prazo de validade também é
usado para rejeitar respostas atrasadas. `purge_at` é um dia após a validade.

```jsonc
{
	"_id": "<question UUID>",
	"principal_user_id": "<internal user UUID>",
	"session_id": "<session UUID>",
	"interaction_id": "<interaction UUID>",
	"report_artifact_ref": "<artifact UUID>",
	"input_type": "<type>",
	"reason_code": "<code>",
	"question_goal": "<goal>",
	"unresolved_selector": { "kind": "name | email", "value": "<input>" },
	"options": [
		{
			"option_id": "<id>",
			"label": "<label>",
			"selector": { "kind": "name | email", "value": "<value>" },
		},
	],
	"expires_at": "<BSON UTC datetime>",
	"purge_at": "<expires_at plus one day>",
}
```

### Campos de autorização

Registra uma autorização imutável para uma ação de envio específica. Os
fingerprints impedem trocar a preparação, o conteúdo ou os destinatários após a
confirmação. Para `confirmed_question`, `question_id` e `question_expires_at`
ficam vinculados à autorização.

```jsonc
{
	"_id": "<authorization UUID>",
	"principal_user_id": "<internal user UUID>",
	"session_id": "<session UUID>",
	"source_interaction_id": "<interaction UUID>",
	"preparation_reference": "<preparation UUID>",
	"report_id": "<report UUID>",
	"request_fingerprint": "<hash>",
	"prepared_fingerprint": "<hash>",
	"recipients_fingerprint": "<hash>",
	"origin": "explicit_request | confirmed_question",
	"idempotency_key": "<key>",
	"authorized_at": "<BSON UTC datetime>",
	"expires_at": "<BSON UTC datetime>",
	"question_id": "<question UUID or null>",
	"question_expires_at": "<BSON UTC datetime or null>",
	"purge_at": "<expires_at plus one day>",
}
```

### `ai_langgraph_checkpoints` e `ai_langgraph_checkpoint_writes`

São collections internas do `MongoDBSaver` de `langgraph-checkpoint-mongodb`,
usadas para retomar o estado do grafo. O formato serializado é controlado pela
versão da dependência e não deve ser escrito manualmente nem tratado como
contrato de API.

```jsonc
// ai_langgraph_checkpoints — representação conceitual, gerida pela biblioteca
{
	"thread_id": "<session/thread ID>",
	"checkpoint_ns": "<namespace>",
	"checkpoint_id": "<checkpoint ID>",
	"parent_checkpoint_id": "<parent ID or null>",
	"type": "<serializer type>",
	"checkpoint": "<serialized checkpoint>",
	"metadata": "<serialized metadata>",
}
```

```jsonc
// ai_langgraph_checkpoint_writes — representação conceitual, gerida pela biblioteca
{
	"thread_id": "<session/thread ID>",
	"checkpoint_ns": "<namespace>",
	"checkpoint_id": "<checkpoint ID>",
	"task_id": "<task ID>",
	"idx": 0,
	"channel": "<channel>",
	"type": "<serializer type>",
	"value": "<serialized write>",
}
```

Esses exemplos indicam apenas conceitos e identificadores. Para campos exatos da
versão instalada, consulte o pacote `langgraph-checkpoint-mongodb` e os testes
de integração de retomada.

### `ai_observations`

Observações técnicas do `main_api`, uma por operação/tentativa, com estados
atualizados do início ao término. O schema versionado é `Observation` em
`packages/observability/models.py`; campos opcionais nulos são omitidos na
serialização. UUIDs aninhados viram strings e valores `Decimal` viram
`Decimal128` BSON.

```jsonc
{
	"_id": "<observation UUID>",
	"schema_version": "2",
	"correlation_id": "<UUID or omitted>",
	"parent_observation_id": "<UUID or omitted>",
	"session_id": "<UUID or omitted>",
	"interaction_id": "<UUID or omitted>",
	"service": "<service>",
	"environment": "<environment>",
	"service_version": "<version or omitted>",
	"operation_kind": "http | interaction | graph | agent | model | embedding | tool | mcp | a2a | workflow | storage | external",
	"operation_name": "<operation>",
	"operation_phase": "initialization | execution | validation | recovery",
	"observation_level": "local_operation | sdk_invocation | transport_attempt",
	"agent": "<agent or omitted>",
	"execution_mode": "real | mock | unavailable",
	"started_at": "<UTC datetime, millisecond precision>",
	"ended_at": "<UTC datetime or omitted>",
	"duration_ms": 0.0,
	"updated_at": "<UTC datetime, millisecond precision>",
	"status": "started | succeeded | failed | timeout | cancelled | rejected",
	"http": { "method": "POST", "route": "<route>", "status_code": 200 },
	"protocol": { "name": "mcp | a2a", "operation": "<name>" },
	"result": {
		"source": "<source>",
		"code": "<code>",
		"confirmed": true,
		"recovered": false,
	},
	"error": {
		"type": "<type>",
		"origin": "application | provider | transport | protocol | validation | storage | unknown",
		"code": "<safe code>",
		"message_sanitized": "<sanitized message>",
	},
	"provider": "<provider>",
	"model_requested": "<model>",
	"model_returned": "<model>",
	"usage": {
		"availability": "returned | partial | absent | not_applicable",
		"input_tokens": 1,
		"output_tokens": 1,
		"total_tokens": 2,
		"details": {},
	},
	"billing": {
		"reported_cost": "<Decimal128>",
		"currency": "USD",
		"cost_source": "provider | configured_rate",
	},
	"resources": [
		{
			"name": "<resource>",
			"quantity": "<Decimal128>",
			"unit": "<unit>",
			"source": "provider | sdk | application",
		},
	],
	"timeout_ms": 1000.0,
	"timeout_phase": "<phase>",
	"cancellation_state": "requested | observed | confirmed",
	"attempt_number": 1,
	"attempt_reason": "initial | retry_transient | fallback | repair | judge_revision | source_renewal | unknown",
	"attempt_visibility": "individual | internal_retries_unknown",
	"replayed_result": false,
}
```

`ai_a2a_observations` usa o mesmo schema; muda o serviço produtor e a
configuração de conexão.

## Collections do MCP de e-mail

### `ai_email_prepared_reports`

Documento canônico do relatório preparado no MCP de e-mail. Contém corpo,
destinatários e anexos codificados em Base64. `attachments` e `state` são campos
obrigatórios no documento persistido.

```jsonc
{
	"_id": "<report UUID>",
	"requested_by": "<user UUID from the signed report>",
	"recipients": [
		{ "recipient_id": "<UUID>", "name": "<name>", "email": "<email>" },
	],
	"subject": "<subject>",
	"body": "<body>",
	"attachments": [
		{
			"filename": "<file name>",
			"content_type": "text/plain",
			"content_base64": "<base64 bytes>",
			"sha256": "<hex digest>",
		},
	],
	"source_payload_hash": "<hash>",
	"created_at": "<BSON UTC datetime>",
	"expires_at": "<BSON UTC datetime>",
	"state": "prepared | expired | rejected",
}
```

O registro é a origem usada para envio e consulta; `requested_by` é serializado
como UUID e vinculado ao titular do envelope de relatório validado.

### `ai_email_dispatches`

Uma operação idempotente de envio por documento. `idempotency_key` tem índice
único. `send_claimed` é adicionado quando uma execução reivindica o envio; uma
operação pendente reivindicada não é reenviada automaticamente.

```jsonc
{
	"_id": "<operation UUID>",
	"report_id": "<report UUID>",
	"requested_by": "<requesting user UUID>",
	"idempotency_key": "<key>",
	"state": "pending | accepted | partial | failed | unknown",
	"provider_message_id": "<provider ID or null>",
	"error_code": "<safe code or null>",
	"created_at": "<BSON UTC datetime>",
	"updated_at": "<BSON UTC datetime>",
	"send_claimed": true, // opcional; aparece após a reivindicação
}
```

## Collections de leitura do MCP de dados

O MCP de dados não grava nessas collections. A aplicação valida os documentos
retornados, filtra bovinos pelo `relational_property_id` dentro do escopo
assinado e aplica os filtros de consulta. Estes exemplos mostram os campos
confirmados pelo leitor, não um schema completo do produtor.

### `cattle_documents`

Documento agregado de histórico de uma propriedade/rebanho, com lotes e
subdocumentos. Os campos adicionais são aceitos e preservados pelo modelo; os
IDs relacionais devem ser UUIDs representados como string ou UUID BSON de acordo
com `MCP_DATA_MONGO_RELATIONAL_ID_FORMAT`.

```jsonc
{
	"_id": "<Mongo ID>",
	"relational_property_id": "<property UUID>",
	"herd": { "herd_id": "<herd UUID>", "...": "<producer fields>" },
	"lots": [
		{
			"lot_id": "<lot UUID>",
			"applications": [
				{ "application_id": "<application UUID>", "...": "<fields>" },
			],
			"animal": [
				{
					"animal_exception_id": "<animal UUID>",
					"lot_id": "<lot UUID>",
					"origin_lot_id": "<lot UUID>",
					"exception_applications": [
						{ "lot_id": "<lot UUID>", "...": "<fields>" },
					],
					"occurrences": [
						{
							"occurrence_id": "<UUID>",
							"resolved": false,
							"...": "<fields>",
						},
					],
					"lot_history": [
						{ "lot_id": "<lot UUID>", "...": "<fields>" },
					],
				},
			],
			"...": "<producer fields>",
		},
	],
	"history_summary": {
		"total_lots": 0,
		"total_exception_lots": 0,
		"total_exception_animals": 0,
		"total_lot_applications": 0,
		"total_exception_applications": 0,
		"total_occurrences": 0,
		"open_occurrence_count": 0,
	},
	"report_created_at": "<BSON UTC datetime or omitted>",
	"last_updated_at": "<BSON UTC datetime or omitted>",
}
```

`history_summary` é opcional; se presente, seus contadores precisam concordar
com os itens detalhados. Cada lote deve ter arrays `applications` e `animal`; o
modelo também valida os vínculos entre IDs dos animais, lotes e histórico.

### `medicine_documents`

Documento de detalhes de medicamento associado ao cadastro relacional por
`relational_medicine_id`. O leitor aceita UUID BSON de 16 bytes ou formato de
string de acordo com `MCP_DATA_MONGO_MEDICINE_ID_FORMAT`; campos extras do
documento de origem são ignorados pelo modelo.

```jsonc
{
	"_id": "<Mongo ID>",
	"relational_medicine_id": "<medicine UUID>",
	"report_created_at": "<BSON UTC datetime or omitted>",
	"last_updated_at": "<BSON UTC datetime or omitted>",
	"brand_name": "<name>",
	"active_ingredient": "<ingredient>",
	"mapa_registration": "<registration or null>",
	"indication": "<text>",
	"description": "<text or null>",
	"therapeutic_category": {
		"therapeutic_category_id": "<UUID>",
		"name": "<name>",
		"description": "<text or null>",
	},
	"supplier": {
		"supplier_id": "<UUID>",
		"company_name": "<name>",
		"cnpj": "<value or null>",
		"email": "<value or null>",
		"phone": "<value or null>",
		"address": "<value or null>",
	},
	"needs_prescription": false,
	"active": true,
	"meat_withdrawal_days": 0,
	"dose": "<positive decimal>",
	"unit": "ml | mg | g | UI | dose",
	"route": "INTRAMUSCULAR | SUBCUTANEA | ORAL | TOPICA | INTRAVENOSA | null",
}
```

## Observações sobre evolução e leitura

- A documentação registra os documentos que o código serializa ou valida; os
  exemplos não criam validação adicional nem alteram contratos de API.
- Campos opcionais podem ser persistidos como `null` em serializers do
  `main_api`; observabilidade omite campos nulos.
- Índices, unicidade, TTL e transações continuam definidos pelo bootstrap e
  pelos repositórios. Consulte os adapters antes de planejar uma migração.
- Não aplique atualizações manuais ao formato interno do checkpointer. Para
  `cattle_documents`, confirme o schema e a compatibilidade com o produtor dos
  documentos antes de qualquer alteração.

## Referências do código

- Configuração e nomes das collections: `apps/main_api/app/settings.py`,
  configurações dos serviços e `apps/main_api/app/infrastructure/bootstrap.py`.
- Sessões, interações, mensagens, eventos, resumos e memória:
  `apps/main_api/app/infrastructure/persistence/mongo.py`.
- Artefatos e preparações de e-mail:
  `apps/main_api/app/infrastructure/persistence/report_artifacts.py` e
  `apps/main_api/app/infrastructure/persistence/email_preparations.py`.
- Checkpoints: `apps/main_api/app/infrastructure/checkpoints/mongo.py`.
- Observabilidade: `packages/observability/models.py` e os adapters Mongo dos
  serviços.
- MCP de e-mail: `apps/mcp_email/src/argos_mcp_email/adapters/storage/`.
- Documentos de bovinos e medicamentos: modelos e repositórios em
  `apps/mcp_data/src/argos_mcp_data/domain/` e
  `apps/mcp_data/src/argos_mcp_data/infrastructure/`.
