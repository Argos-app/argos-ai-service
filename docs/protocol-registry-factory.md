# Protocol, Registry e Factory de agentes

Esses três componentes separam o **contrato de uso**, a **configuração** e a **criação** dos agentes.

| Componente | Função geral | Aplicação no projeto |
| --- | --- | --- |
| **Protocol** | Define quais operações um objeto deve oferecer, sem determinar sua implementação. | [`Agent`](../apps/main_api/app/ports/agent.py) exige `async run(input)`; [`AgentFactory`](../apps/main_api/app/ports/agent_factory.py) exige `create(agent_id)`. O `AgentId` limita os identificadores aceitos. |
| **Registry** | Mantém um catálogo de definições consultáveis por identificador. | [`AgentRegistry`](../apps/main_api/app/agents/registry.py) relaciona cada agente a seus schemas de entrada e saída, prompt, política de modelo e ferramentas permitidas. Valida as definições, mas não guarda instâncias de agentes. |
| **Factory** | Usa as definições e as dependências disponíveis para construir uma instância. | [`LangChainAgentFactory`](../apps/main_api/app/infrastructure/agents/langchain_factory.py) consulta o Registry, resolve modelo, prompt e ferramentas, cria o agente LangChain e devolve um objeto que cumpre o Protocol `Agent`. |

## Como se relacionam

1. [`build_agent_factory`](../apps/main_api/app/agents/specialists.py) reúne os prompts e as ferramentas mock do projeto, recebe os modelos configurados e monta a Factory com o Registry padrão.
2. O consumidor chama `factory.create(AgentId.FARM)`, por exemplo. A Factory consulta a definição `FARM` no Registry e monta o agente com apenas as ferramentas declaradas para ele.
3. O consumidor chama `await agent.run(entrada)`. A implementação valida a entrada e a saída com os schemas registrados.

O [`grafo LangGraph`](../apps/main_api/app/graph/agent_execution.py) consome o contrato `AgentFactory`: após o guardrail de entrada, ele cria e executa o roteador e os especialistas escolhidos, sem depender diretamente da implementação LangChain. O guardrail é uma política separada; não é uma definição de agente no Registry.

Atualmente, o Registry padrão contém o roteador e cinco especialistas (`farm`, `doctor`, `analyst`, `secretary` e `information`). `orchestrator` e `judge` existem em `AgentId`, mas ainda não possuem definição nesse Registry. A composição exige modelos injetados; o grafo ainda não está conectado ao fluxo HTTP da aplicação.
