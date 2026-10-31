# MCP de e-mail — guia para agentes

## Fluxo e organização

O serviço recebe um relatório atestado, valida o envelope, resolve
destinatários, persiste o relatório e envia somente quando recebe a operação de
envio. O código separa `domain`, `application`, `ports` e `adapters`;
`server.py` registra as três tools e `bootstrap.py` conecta recursos.

## Regras de segurança e persistência

- Preserve verificação da assinatura e validade do envelope, hash de conteúdo e
  resolução de destinatários cadastrados. Não aceite texto gerado pelo modelo
  como autorização ou como substituto do relatório atestado.
- A operação de envio tem chave de idempotência e claim persistido no Mongo. Um
  timeout ou erro com possível aceite pode gerar estado desconhecido; não
  reenvie automaticamente quando isso puder duplicar uma mensagem.
- `ai_email_prepared_reports` e `ai_email_dispatches` pertencem a este serviço;
  o estado `ai_email_workflows` da Main API é separado. Não confunda os ciclos.
- Preserve a distinção entre aceite SMTP e entrega efetiva.

## Dependências e verificações

Usa MongoDB para preparação e despacho, PostgreSQL para destinatários e SMTP
para entrega; compartilha `packages/contracts` e `packages/security`. A Main API
é cliente MCP. Comandos e integração descartável estão em
[README.md](README.md); o [contrato MCP](MCP.md) detalha os estados e a
persistência.
