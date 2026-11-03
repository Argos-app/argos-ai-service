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

A [pilha integrada](../../infrastructure/docker/README.md) usa Mailpit local por
padrão. Para testes unitários e integração com Mongo, PostgreSQL e Mailpit
descartáveis:

```powershell
python -m pytest apps/mcp_email/tests/unit -q
```

Siga [tests/integration/README.md](tests/integration/README.md) para subir os
serviços e ativar os testes de integração.

## Contrato MCP

O transporte Streamable HTTP em `/mcp` expõe `prepare_report`, `send_report` e
`get_report_status`. O envelope de origem é assinado e a autorização de envio é
validada pela Main API. `accepted` indica aceite SMTP, não entrega ou leitura;
operações pendentes ou desconhecidas precisam de reconciliação antes de um novo
pedido. Os schemas estão em `packages/contracts/email.py`; campos, exemplos,
estados e persistência estão em [MCP.md](MCP.md).
