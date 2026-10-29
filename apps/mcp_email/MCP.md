# MCP de e-mail

Estado vigente do contrato MCP; schemas de tools gerados pelo SDK são a fonte
normativa. Atualizado em 16/10/2026.

Servidor MCP Streamable HTTP disponível em `/mcp`. As ferramentas recebem um
objeto `payload`; a autenticação usa Bearer interno. A origem do relatório é um
envelope assinado e a Main API controla a autorização da operação de envio.

## Tools

- `prepare_report`: valida o envelope de origem assinado, resolve os
  destinatários no diretório relacional, renderiza o e-mail, cria um anexo
  `text/plain` UTF-8 chamado `relatorio-{report_id}.txt` com os mesmos bytes do
  corpo e persiste a preparação. A resposta `PreparedReport` informa
  `attachments[]` com nome, MIME, tamanho e SHA-256; preparar não envia.
- `send_report`: envia a preparação aprovada com `report_id` e chave de
  idempotência. Repetir uma operação reclamada não reenvia automaticamente:
  consulte o estado e reconcilie casos `pending`.
- `get_report_status`: consulta o estado por `operation_id` e `requested_by`.

Exemplo de envio e consulta (UUIDs fictícios):

```json
{
	"payload": {
		"report_id": "00000000-0000-0000-0000-000000000001",
		"requested_by": "00000000-0000-0000-0000-000000000002",
		"idempotency_key": "argos:<authorization-id>"
	}
}
```

```json
{
	"payload": {
		"operation_id": "00000000-0000-0000-0000-000000000003",
		"requested_by": "00000000-0000-0000-0000-000000000002"
	}
}
```

`prepare_report` exige `report_source` (envelope assinado completo), uma lista
`recipients` de seletores, `report_title` e `requested_by` igual ao titular do
envelope. Como o envelope é assinado e vinculado a dados do ambiente, consulte
`packages/contracts/email.py` e `packages/contracts/lot_report.py` para criar
fixture válida, ou obtenha-o pela tool `get_lot_report_source` do MCP de dados.

O schema MCP retornado por `tools/list` é a fonte normativa de nomes de campos,
obrigatoriedade e limites. Não existe OpenAPI HTTP de negócio separado do
endpoint MCP. O comportamento de envio é `accepted`, `partial`, `failed` ou
`unknown`; aceite do provider não confirma entrega nem leitura. Falhas podem
ocorrer depois do aceite externo, por isso o cliente não deve criar nova chave
de idempotência e reenviar automaticamente.

## Persistência e diagnóstico

O MCP grava `ai_email_prepared_reports` e `ai_email_dispatches` no database
configurado. A preparação guarda destinatários, assunto, corpo e conteúdo base64
do anexo com SHA-256; a resposta pública expõe somente seus metadados. O
repositório atual exige o campo `attachments`. Limite acesso e retenção conforme
o [dicionário BI](../../infrastructure/mongodb/analytics-dictionary.md). O MCP
não grava observações técnicas próprias. A chamada de saída é observada no
`main_api`; a observação de entrada não fica persistida pelo MCP.

Veja [execução e variáveis](.env.example), testes de integração em
[`tests/integration/README.md`](tests/integration/README.md) e o guia comum de
[observabilidade](../../packages/observability/docs/operations.md). Nunca
exponha tokens, chaves SMTP ou credenciais do Mongo em exemplos.
