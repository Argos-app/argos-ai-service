# Validação isolada do MCP de e-mail

Execute os comandos abaixo a partir da raiz do repositório, com Docker Desktop
em execução. A infraestrutura usa somente portas locais e dados descartáveis. O
Mailpit captura SMTP e não envia mensagens aos destinatários externos.

```powershell
python -m venv .venv/mcp-email-integration
.venv/mcp-email-integration/Scripts/python.exe -m pip install -r apps/mcp_email/tests/integration/requirements.txt
docker compose -f apps/mcp_email/tests/integration/compose.yaml up -d --wait
$env:MCP_EMAIL_RUN_INTEGRATION="1"
.venv/mcp-email-integration/Scripts/python.exe -m pytest apps/mcp_email/tests/integration -v -p no:cacheprovider
Remove-Item Env:MCP_EMAIL_RUN_INTEGRATION
docker compose -f apps/mcp_email/tests/integration/compose.yaml down
```

O ambiente separado mantém o SDK MCP 1.x declarado pelo serviço, sem modificar
as dependências do sistema principal. Sem `MCP_EMAIL_RUN_INTEGRATION=1`, os
testes de integração são ignorados na execução habitual de pytest. Quando
habilitados, ausência de infraestrutura produz falha, não resultado aprovado.

Os testes criam bancos SQL/Mongo com nomes exclusivos e os removem ao finalizar.
O SQL contém apenas a projeção `app_user` necessária ao adapter de
destinatários. O processo real `python -m argos_mcp_email` recebe uma chave
pública Ed25519 de teste e um token de serviço descartável; nenhum segredo de
produção é utilizado.

## O que é verificado

- Cliente e servidor MCP reais via Streamable HTTP, incluindo `initialize` e
  `list_tools`; os schemas não expõem token nem contexto como argumentos.
- `prepare_report` com assinatura Ed25519, resolução de nome no PostgreSQL e
  relatório preparado persistido no Mongo.
- `send_report` em seis chamadas concorrentes com a mesma chave de idempotência:
  uma única operação Mongo e uma única mensagem SMTP no Mailpit.
- `get_report_status` retornando o estado persistido `accepted`.
- Reinício do processo MCP e consulta/repetição da operação original sem novo
  envio, demonstrando persistência independente da memória do processo.
- Headers ausentes ou token inválido rejeitados com HTTP 401.
- Relatório alterado após a assinatura rejeitado sem persistência nem envio.

O teste confirma a aceitação SMTP e o conteúdo capturado no servidor local. Ele
não confirma entrega ou leitura em uma caixa postal externa.
