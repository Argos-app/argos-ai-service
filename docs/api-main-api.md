# API HTTP principal

Contrato vigente da API `/v1`, gerado a partir de `app.openapi()` do `main_api`.
O OpenAPI publicado pela aplicação está em `/openapi.json` e o Swagger UI em
`/docs`.

O arquivo versionado [openapi-main-api.json](openapi-main-api.json) é gerado
diretamente do código. Regere-o após alterações nos modelos, rotas ou respostas
HTTP.

## Autenticação

As rotas `/v1/` protegidas exigem o JWT Bearer próprio do `main_api`, assinado
com Ed25519. Para obtê-lo, o cliente envia o Firebase ID token em
`POST /v1/auth/firebase`, sem corpo:

```http
POST /v1/auth/firebase
Authorization: Bearer <Firebase ID token>
```

A resposta contém somente o JWT próprio:

```json
{ "access_token": "<JWT próprio da IA>" }
```

Nas chamadas seguintes, o cliente envia esse JWT como Bearer. A API valida o
token e consulta o PostgreSQL para revalidar cadastro e fazendas autorizadas.
Nome, e-mail, UID, permissões ou fazendas informados pelo cliente não concedem
acesso. O modo stub é permitido somente localmente com `APP_ENV=local` e
`AUTH_MODE=stub`. Detalhes de configuração e política estão em
[`autenticacao-firebase-identidade.md`](autenticacao-firebase-identidade.md).

`GET /health/live` e `GET /health/ready` não exigem o JWT da API. Cada resposta
de `/v1/` inclui `X-Argos-Correlation-ID` (UUID), útil para suporte e
diagnóstico.

## Rotas

| Método e caminho                              | Uso                                                | Corpo ou parâmetros                                    | Resposta de sucesso       |
| --------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------ | ------------------------- |
| `POST /v1/auth/firebase`                      | Troca o Firebase ID token por um JWT da IA         | Sem corpo; Bearer com Firebase ID token                | `200` com `access_token`  |
| `POST /v1/sessions`                           | Cria uma sessão com as fazendas autorizadas atuais | Sem corpo                                              | `201 Session`             |
| `GET /v1/sessions`                            | Lista as sessões do usuário autenticado            | Sem parâmetros                                         | `200 Session[]`           |
| `POST /v1/sessions/{session_id}/interactions` | Envia mensagem ou resposta a uma pergunta          | `MessageInteraction` ou `AnswerInteraction`            | `200 InteractionResult`   |
| `GET /v1/sessions/{session_id}/messages`      | Lê o histórico público paginado                    | `limit` de 1 a 100, padrão 50; `cursor` opaco opcional | `200 SessionMessagesPage` |

As rotas de sessões exigem o JWT próprio. O histórico valida titularidade e
acesso atual às fazendas; não executa agentes, modelos ou ferramentas.

## Enviar uma interação

Exemplo de mensagem:

```http
POST /v1/sessions/00000000-0000-0000-0000-000000000001/interactions
Authorization: Bearer <JWT próprio da IA>
Content-Type: application/json

{"kind":"message","client_message_id":"00000000-0000-0000-0000-000000000002","message":"Resuma o lote L-10"}
```

O resultado preserva a resposta estruturada existente (`answer` ou `question`) e
inclui o estado da execução e o horário da mensagem do assistente:

```json
{
	"schema_version": "1",
	"status": "degraded",
	"degradation_reasons": ["prediction_failed"],
	"created_at": "2026-10-21T14:30:00Z",
	"session_id": "00000000-0000-0000-0000-000000000001",
	"interaction_id": "00000000-0000-0000-0000-000000000003",
	"public_request_id": "00000000-0000-0000-0000-000000000004",
	"response": {
		"kind": "answer",
		"text": "Resposta disponível com limitações."
	}
}
```

`created_at` é o horário UTC da mensagem do assistente correspondente no
histórico. Em repetição idempotente de uma interação antiga, a API recupera o
horário registrado em `interaction.updated_at`.

`status` é `completed` ou `degraded`. O estado degradado indica falha em uma ou
mais etapas necessárias; `degradation_reasons` contém códigos técnicos
retornados pelo serviço, sem credenciais ou conteúdo privado. O app pode usar
`status` para mostrar o aviso de resposta parcial.

O corpo de entrada continua discriminado por `kind`. Mensagens usam
`client_message_id` e `message`. Respostas a perguntas usam `interaction_id`,
`question_id` e uma resposta `text`, `choice` ou `confirmation`. A resposta pode
ser texto ou uma pergunta estruturada com `question_id`, `input_type`, `prompt`,
`reason_code`, opções quando aplicáveis e `expires_at`.

## Consultar o histórico

`GET /v1/sessions/{session_id}/messages` retorna itens em ordem cronológica, com
`message_id`, `interaction_id`, `sequence`, `role`, `kind`, `content`,
`created_at` e os campos estruturados existentes quando aplicáveis. As mensagens
visíveis do assistente também retornam os valores persistidos de `status` e
`degradation_reasons`, para o app preservar o aviso de resposta parcial ao
reabrir a conversa. Mensagens do usuário não incluem esses dois campos.

Se o acesso a alguma fazenda original da sessão foi removido, o histórico mantém
a política de ocultação: o conteúdo e a resposta estruturada do assistente são
redigidos. Nesse caso, `status` é nulo e `degradation_reasons` é uma lista
vazia. Mensagens anteriores ao registro desses metadados podem não ter `status`
ou motivos disponíveis.

`next_cursor` é opaco e vinculado ao usuário e à sessão. Envie-o como `cursor`
para buscar a página anterior; `has_more` indica se há itens anteriores.

## Erros

Erros de aplicação usam `{"detail":"..."}`. Dependendo da rota e da falha, a API
pode responder com `400`, `401`, `403`, `404`, `409`, `413`, `422` ou `503`. O
schema OpenAPI lista as respostas declaradas. Não trate o texto de `detail` como
dado estruturado estável.

Uma desconexão ou timeout pode ocorrer depois que a interação foi aceita. Use os
identificadores e a deduplicação do contrato para reenviar com segurança.

## Referências

- [Autenticação Firebase e autorização por fazenda](autenticacao-firebase-identidade.md)
- [Operação e observabilidade](operacao-observabilidade.md)
- [OpenAPI versionado](openapi-main-api.json)
