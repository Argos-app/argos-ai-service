# API HTTP principal

Estado vigente; contrato HTTP `/v1` e OpenAPI 3.1.0. Atualizado em 16/10/2026.

OpenAPI vigente: [openapi-main-api.json](openapi-main-api.json), gerado a partir
de `app.openapi()` no `main_api`. O servidor interativo também publica `/docs` e
`/openapi.json`.

## Autenticação e correlação

As rotas `/v1/` protegidas exigem o JWT Bearer EdDSA próprio do `main_api`,
emitido após validar um Firebase ID token em `POST /v1/auth/firebase`. O token
próprio contém o UUID interno no `sub`; cadastro e fazendas são recarregados do
PostgreSQL em cada chamada. Tokens assinados pelo backend não são aceitos.
`GET /health/live` e `GET /health/ready` não usam esse token. Consulte
`apps/main_api/.env.example` para configurar Firebase, banco e chave privada do
emissor; mantenha essa chave em um secret e nunca no repositório.

Cada resposta de `/v1/` inclui `X-Argos-Correlation-ID` (UUID). Registre o valor
para suporte e diagnóstico; ele não substitui `session_id`, `interaction_id` ou
`public_request_id`. As respostas e erros mantêm seus contratos atuais, e o
header está declarado nas respostas OpenAPI.

## Rotas principais

| Método e caminho                              | Uso                                                         | Corpo / parâmetros                                                                                                                             | Resposta                                                      |
| --------------------------------------------- | ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- |
| `POST /v1/auth/firebase`                      | Troca identidade Firebase por JWT próprio                   | Sem corpo; `Authorization: Bearer <Firebase ID token>`                                                                                         | `200 {"access_token":"<JWT próprio>"}`                        |
| `POST /v1/sessions`                           | Cria sessão para usuário e fazendas autorizadas no cadastro | Sem corpo                                                                                                                                      | `201 Session`                                                 |
| `GET /v1/sessions`                            | Lista sessões do titular                                    | Sem parâmetros                                                                                                                                 | `200 Session[]`                                               |
| `POST /v1/sessions/{session_id}/interactions` | Envia mensagem ou resposta de esclarecimento                | discriminador `kind`: `message` com `client_message_id` e texto, ou `answer` com IDs da pergunta e resposta `text`, `choice` ou `confirmation` | `200 InteractionResult`; `status` é `completed` ou `degraded` |

`status=degraded` indica que pelo menos uma etapa de agente, ferramenta ou
integração necessária falhou. `degradation_reasons` contém códigos técnicos
estáveis para diagnóstico (por exemplo, `farm_failed` ou
`required_signed_report_source_missing`); não contém credenciais nem texto do
usuário. Clientes devem usar `status` para distinguir uma resposta degradada de
uma execução concluída e podem registrar os códigos para suporte. |
`GET /v1/sessions/{session_id}/messages` | Lê histórico público paginado, sem
invocar modelo | `limit` 1–100 (padrão 50), `cursor` opaco opcional |
`200 SessionMessagesPage` |

Exemplo de mensagem (substitua token e IDs por valores do ambiente):

```http
POST /v1/sessions/00000000-0000-0000-0000-000000000001/interactions
Authorization: Bearer <JWT próprio da IA>
Content-Type: application/json

{"kind":"message","client_message_id":"00000000-0000-0000-0000-000000000002","message":"Resuma o lote L-10"}
```

Para obter esse JWT, envie primeiro o Firebase ID token do usuário, sem corpo:

```http
POST /v1/auth/firebase
Authorization: Bearer <Firebase ID token>
```

A resposta contém somente `access_token`. O cliente usa esse valor como Bearer
nas chamadas protegidas seguintes.

Os erros de aplicação retornam `{"detail":"..."}` com status HTTP 400, 403, 404,
409, 422 ou 503 conforme a classe do erro. `401`/`403` também podem vir do
middleware de autenticação. Consulte o schema OpenAPI para respostas por rota;
não trate mensagens de erro como dados estruturados estáveis. Timeout ou
desconexão pode deixar o cliente sem resposta embora a operação tenha sido
aceita; use os identificadores de interação/idempotência previstos no contrato.

## Referências de integração

O [guia operacional de observabilidade](operacao-observabilidade.md) explica o
header de correlação e a coleta local sem LangSmith. Contratos MCP estão em
`apps/mcp_data/MCP.md`, `apps/mcp_prediction/MCP.md` e `apps/mcp_email/MCP.md`;
a interface A2A está em `apps/a2a_agent/README.md`.
