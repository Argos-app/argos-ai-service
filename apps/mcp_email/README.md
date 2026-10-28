# MCP de e-mail

O MCP de e-mail prepara relatórios assinados, envia-os por SMTP e permite
consultar o estado da operação.

## Tecnologias e execução

Usa Python 3.14.3, FastMCP, Motor/MongoDB, asyncpg/PostgreSQL, SMTP e os pacotes
`contracts` e `security`. Na raiz, instale as dependências, configure no
ambiente do processo as variáveis de [.env.example](.env.example) e
disponibilize os serviços necessários. O Python não lê o arquivo `.env`
automaticamente:

```powershell
python -m pip install -r apps/mcp_email/requirements.txt
$env:PYTHONPATH = "apps/mcp_email/src"
python -m argos_mcp_email
```

A [pilha integrada](../../docs/docker-local.md) usa Mailpit local por padrão.
Para testes unitários e integração com Mongo, PostgreSQL e Mailpit descartáveis:

```powershell
python -m pytest apps/mcp_email/tests/unit -q
```

Siga [tests/integration/README.md](tests/integration/README.md) para subir os
serviços e ativar os testes de integração.

## Contrato MCP

O Streamable HTTP em `/mcp` aceita as tools `prepare_report`, `send_report` e
`get_report_status`, com `payload` e autenticação Bearer de serviço. A origem do
relatório é validada por assinatura; destinatários são resolvidos no banco. Os
tipos de requisição e resposta estão em `packages/contracts/email.py`. O estado
`accepted` registra aceite do provedor SMTP, não leitura ou entrega ao
destinatário.

`prepare_report` recebe `report_source`, seletores em `recipients`,
`report_title` e `requested_by`, vinculado ao titular do envelope. Além do
corpo, gera um anexo UTF-8 `relatorio-{report_id}.txt`; a resposta informa
metadados como tamanho e SHA-256. Preparar não envia. Para obter uma origem
válida, use `get_lot_report_source` do MCP de dados ou uma fixture assinada
conforme os contratos `email.py` e `lot_report.py`.

Exemplos reduzidos de `payload` para envio e consulta:

```json
{
	"report_id": "00000000-0000-0000-0000-000000000001",
	"requested_by": "00000000-0000-0000-0000-000000000002",
	"idempotency_key": "argos:<authorization-id>"
}
```

```json
{
	"operation_id": "00000000-0000-0000-0000-000000000003",
	"requested_by": "00000000-0000-0000-0000-000000000002"
}
```

O schema retornado por `tools/list` define nomes, obrigatoriedade e limites. Uma
operação reclamada que permaneça `pending` precisa de reconciliação; não crie
uma nova chave e reenvie automaticamente após um resultado desconhecido. O Mongo
armazena corpo, destinatários e anexo; a resposta pública apresenta metadados.
