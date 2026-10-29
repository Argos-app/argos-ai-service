# Main API — guia para agentes

## Fluxo e organização

A API recebe a interação de um usuário, valida identidade e escopo, executa o
grafo e persiste estado e resultado. `app/api/v1` contém rotas, schemas e
mapeadores; `application`, `domain` e `ports` abrigam casos de uso, modelos e
interfaces. `infrastructure` implementa persistência, agentes, modelos e
clientes; `graph` compõe o LangGraph. É uma organização com portas e adapters,
mas há composição e integrações concretas dentro da aplicação; siga a fronteira
real do trecho alterado.

`graph/workflow.py` compõe guardrails, roteador, os especialistas Fazenda,
Doutor, Analista, Secretário e Informações, Orquestrador, Juiz e formatador de
perguntas. A rota é executada conforme o estado e as dependências das tarefas;
consulte o [guia do fluxo](docs/agent-workflow.md), o grafo e seus testes antes
de alterar a ordem.

## Pontos de atenção

- Sessões pertencem ao usuário: preserve validação de identidade e fazendas,
  idempotência da interação, lease e fencing token, recuperação e checkpoints.
- `ai_email_workflows` guarda o estado do fluxo de e-mail da Main API; o MCP
  mantém sua própria persistência de preparação e despacho. Preserve assinatura,
  destinatário, autorização, validade e tratamento de resultado desconhecido.
- Não transforme alegações do modelo em autorização ou fonte comprovada. O
  grafo, ferramentas e serviços validam guardrails, fontes e escopo.
- O resumo e a memória usam modelos configurados; mantenha a contagem de tokens,
  fallback, limites e observabilidade ao alterar `model_factory.py`.
- Campos HTTP e Mongo são contratos; confira consumidores, índices e
  [documentação Mongo](../../infrastructure/mongodb/mongodb-collection-structures.md)
  antes de alterá-los.

## Dependências e verificações

MongoDB fornece persistência e checkpointer; PostgreSQL consulta cadastro e
permissões; Redis é usado no ranking de uso quando configurado. O processo chama
MCPs de dados, e-mail e predição, e o A2A documental; também pode chamar
providers e LangSmith. Compartilha `packages/contracts`, `security` e
`observability`. O [guia de sessões e memória](docs/session-memory.md) descreve
os limites e a retomada. Comandos e contrato em [README.md](README.md); testes
de integração usam serviços descartáveis conforme seu README.

O [contrato HTTP](docs/api-main-api.md), o
[OpenAPI versionado](docs/openapi-main-api.json) e o
[guia de autenticação](docs/firebase-authentication-identity.md) acompanham esta
aplicação. Atualize o snapshot OpenAPI e seus testes ao alterar o contrato.
