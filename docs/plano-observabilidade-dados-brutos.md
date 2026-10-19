# Plano de implementação: coleta e persistência de observabilidade bruta

**Documento histórico de planejamento (12/10/2026), não descreve sozinho o
estado atual.**

**Errata de implementação (18/10/2026):** desde a implementação posterior,
`packages/observability/billing.py` aceita custo reportado pelo provider e
estima o custo quando as tarifas globais `MODEL_INPUT_COST_PER_MILLION_TOKENS` e
`MODEL_OUTPUT_COST_PER_MILLION_TOKENS` estão definidas. O schema atual aceita
`billing.cost_source` igual a `provider` ou `configured_rate`; os valores são
persistidos nas observações Mongo e propagados aos traces LangSmith. As linhas
deste plano que registram custo como “não demonstrado” descrevem a análise na
data do plano, não o estado atual. Use os guias vigentes de operação e extração
para o contrato atual.

O plano foi usado como referência para a implementação posterior. A coleta
best-effort está implementada no `main_api` e no A2A; correlação de requisição
entre a API e as chamadas MCP/A2A, ranking exploratório de 24 horas, extração e
dicionário BI estão documentados em
[observabilidade-extracao-mongo.md](observabilidade-extracao-mongo.md),
[dicionario-mongodb-bi.md](dicionario-mongodb-bi.md) e
[operacao-observabilidade.md](operacao-observabilidade.md). Consulte esses guias
como estado vigente. As lacunas descritas abaixo refletem a análise que precedeu
essas implementações e não devem ser lidas como diagnóstico atual.

Data de consolidação: 12/10/2026.

Atualização de escopo em 13/10/2026: manter a coleta de melhor esforço já
implementada. A inspeção de `packages/observability`, dos adapters de
observabilidade do principal e do A2A não identificou um ledger durável separado
de uso e custo. Portanto, não implementar sua criação nem uma etapa de separação
entre telemetria e esse registro. Os documentos confirmados no Mongo persistem,
mas isso não garante a gravação de todas as observações.

Base examinada: branch `feat/observability`, commit `49e6017`. A análise inicial
usava `343d242`; este documento incorpora as mudanças identificadas na branch e
as decisões posteriores do usuário. Antes da implementação, verificar o diff em
relação a esta base e atualizar o diagnóstico se necessário.

## 1. Objetivo, escopo e decisões

Capturar observações individuais de execução, duração, erros, resultados,
consumo e metadados de cobrança, com foco no `main_api` e nas interações A2A.
Persistir esses dados no MongoDB para extração por um ambiente externo de BI.

### Decisões confirmadas

- MongoDB é o armazenamento das métricas brutas.
- A coleção será específica para observabilidade e seu nome será definido pelo
  usuário depois, por variável de configuração. Não fixar nome no código nem
  assumir database exclusivo como decisão confirmada.
- Os registros permanecem no MongoDB **sem expiração automática**, até exclusão
  explícita. Não criar índice TTL nem campo `expires_at` para essa coleção.
- A extração pelo BI não remove os registros do MongoDB.
- Usar um documento por tentativa observada, atualizado no término com início,
  fim, duração e resultado. Não manter um histórico imutável das versões
  intermediárias; cada nova tentativa conserva seu próprio documento.
- Não haverá spool nem outro buffer persistente local. Usar somente fila
  limitada em memória e escrita assíncrona no MongoDB. Registros ainda não
  confirmados podem ser perdidos em reinício, queda ou esgotamento das
  tentativas.
- Quando a persistência ou os buffers de observabilidade estiverem indisponíveis
  ou cheios, continuar atendendo e sinalizar a falha de coleta. A
  indisponibilidade da telemetria não deve bloquear o atendimento; perdas em
  falhas extremas são uma consequência reconhecida dessa política.
- Não implementar agregações, médias, percentis, taxas, correlações, dashboards,
  reconstrução de fluxos, rastreamento distribuído ou indicadores de negócio.
- Não implementar cálculo de custo por sessão, custo por resolução ou ROI.
- Preservar os campos de uso e cobrança informados pelo provider já existentes,
  sem criar ledger financeiro, outbox, spool ou reconciliação durável de custo.
  Falta de uso/cobrança permanece explicitamente desconhecida, nunca zero.
- Memória conversacional e estado funcional dos workflows ficam separados da
  observabilidade. Sua retenção não é redefinida por este plano.
- Nesta entrega documental, não implementar código nem modificar configuração,
  dependências, bancos ou infraestrutura.

Medir uma duração local é permitido e necessário: guardar o intervalo observado
da própria operação. Não reconstruir durações juntando eventos posteriormente.
As fórmulas de orçamento deste documento não serão implementadas na aplicação.

### Classificação usada

- **Existente:** comprovado pelos arquivos examinados.
- **Proposto:** componente ou adaptação a implementar.
- **A confirmar:** informação ou decisão ainda indisponível.

Recomendação: instrumentação própria, com persistência direta no MongoDB.
Grafana, Prometheus e LangSmith não são necessários para este escopo.

## 2. Diagnóstico atual e fontes

Os links locais deste documento são relativos à pasta `docs`, para continuar
válidos em outros checkouts do repositório.

### 2.1. Documentos e estrutura

| Fonte                                                                                                                                                                                      | Evidência e uso no plano                                                                                                           |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| [README](../README.md) e [guia de implementação](../agent.md)                                                                                                                              | Aplicações independentes, contratos compartilhados, Factory/Registry e limites entre código atual e integrações planejadas.        |
| [Planejamento técnico](planejamento-sistema-agentes-argos.md)                                                                                                                              | Arquitetura e backlog. A seção 7.6 e PLN-23/PLN-48 incluem traces, correlação, dashboards e cálculos que ficam fora desta entrega. |
| [Handoff dos agentes](agent-integration-handoff.md)                                                                                                                                        | Slots de ferramentas e diferença entre agentes lógicos e chamadas reais aos modelos. Conferir seu conteúdo com o código atual.     |
| [Plano A2A](plano-implementacao-a2a-informacoes.md) e [README A2A](../apps/a2a_agent/README.md)                                                                                            | Serviço documental isolado, especialistas, Qdrant, autorização e provedores.                                                       |
| [Plano de sessões e memória](plano-persistencia-sessoes-memoria.md)                                                                                                                        | Persistência conversacional, resumos progressivos, perfil e retomada.                                                              |
| [Plano de e-mail](plano-integracao-mcp-email-principal.md) e [plano de Dados](plano-implementacao-mcp-dados.md)                                                                            | Contratos funcionais de origem do relatório, autorização, preparação e envio.                                                      |
| [Pyproject](../pyproject.toml), [dependências do principal](../apps/main_api/requirements.txt), [dependências A2A](../apps/a2a_agent/requirements.txt) e [CI](../.github/workflows/ci.yml) | Python 3.14.3; FastAPI, LangChain, LangGraph, Motor, checkpointer Mongo, MCP, HTTPX, cryptography, A2A SDK, asyncpg e Qdrant.      |

### 2.2. Existente

1. **Persistência conversacional Mongo:** sessões, interações, mensagens,
   eventos de execução, resumos, preferências e checkpoints. O
   [bootstrap](../apps/main_api/app/infrastructure/bootstrap.py) usa cliente
   assíncrono, UTC, replica set e escrita `majority`.
2. **Observador parcial:**
   [ExecutionObserver](../apps/main_api/app/infrastructure/agents/execution_observer.py)
   registra início/término de agentes e tools, conteúdo sanitizado e códigos de
   erro seguros. Sua gravação é aguardada e pode lançar
   `ExecutionEventWriteError`.
3. **Deduplicação existente:**
   [MongoExecutionEventRepository](../apps/main_api/app/infrastructure/persistence/mongo.py)
   usa `_id` único e verifica conflitos de conteúdo. Esse padrão é reutilizável,
   mas o repositório atual pertence à persistência conversacional.
4. **Identidade do backend:**
   [IdentityTokenVerifier](../apps/main_api/app/api/v1/dependencies/auth.py)
   valida JWT EdDSA, issuer, audience, validade e operações delegadas. Há
   autorização por operação nas dependências HTTP.
5. **MCP real no principal:**
   [McpToolServerClient](../apps/main_api/app/infrastructure/mcp/client.py) abre
   sessão Streamable HTTP por operação, inicializa o protocolo, aplica
   allowlist, timeout e limite de resposta. `call_tool` não faz retry
   automático. Os
   [bindings](../apps/main_api/app/infrastructure/mcp/langchain_tools.py)
   conectam Dados, Predição e E-mail quando configurados.
6. **Mocks explícitos:**
   [build_agent_factory](../apps/main_api/app/agents/specialists.py) usa
   `use_mock_tools=False` por padrão. No runtime, serviços não configurados
   recebem ferramentas de indisponibilidade; não tratar isso como serviço real.
7. **Workflow de e-mail:** existem preparação, autorização persistida, envio
   protegido e recuperação de estado. A
   [tool local send_report](../apps/main_api/app/agents/tools/email.py) está
   exposta ao Secretário, com schema sem argumentos de envio e validação
   determinística no workflow. Não descrevê-la como removida das tools do LLM.
8. **Proteção de destinatários:**
   [protect_user_input](../apps/main_api/app/privacy.py) substitui seletores
   explícitos por referências `[DESTINATARIO_n]`. O contexto protegido fica no
   workflow/checkpoint durante esclarecimentos.
9. **A2A isolado:** SDK oficial, autenticação/delegação, Documental, Legal e
   Mixed, geração e embeddings HTTP OpenAI-compatible, PostgreSQL e Qdrant.
10. **Persistência funcional adicional:** `ai_report_artifacts`,
    `ai_email_preparations`, `ai_pending_recipient_resolutions`,
    `ai_email_send_authorizations` e `ai_email_operation_references`.

### 2.3. Lacunas

- Não foi encontrada medição sistemática por relógio monotônico, captura de uso
  dos modelos ou coleção própria de observabilidade bruta.
- `packages/observability` ainda não existe. Fila, writer e extração BI deste
  plano são propostos.
- Os eventos conversacionais contêm conteúdo e alimentam os resumos. Não
  convertê-los na coleção de métricas nem alterar sua finalidade nesta entrega.
- Eventos de agentes e tools não têm semântica temporal uniforme. Não calcular
  durações históricas a partir deles.
- [llms.py](../apps/main_api/app/llms.py) contém modelos `None`; os provedores e
  modelos de produção dependem da composição/configuração.
- A integração A2A real no principal continua pendente.
- [McpDataClient.get_lot_report_source](../apps/main_api/app/infrastructure/mcp/data_client.py)
  lança `DataReportSourceUnavailable`: falta a origem `LotReportEnvelope`
  assinada produzida pelo MCP de Dados. Há teste com fixture assinada, não prova
  de integração completa com a origem real.
- O README informa que o backend ainda não fornece `farm_ids`. Isso é uma
  pendência documentada; o repositório do backend não foi verificado nesta
  análise.
- Não existe definição comprovada de solicitação resolvida com sucesso.
- Alguns documentos descrevem estados anteriores. Para diagnóstico, conferir
  sempre os símbolos atuais; o nome da branch não comprova instrumentação
  pronta.

## 3. Pontos de coleta e semântica

### 3.1. Fronteiras existentes ou propostas

| Operação               | Local/símbolo                                                                                                                                                                                                                                           | Captura                                                                                                                                                               |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Solicitação HTTP       | [main.py](../apps/main_api/app/main.py), `create_app`                                                                                                                                                                                                   | Instrumentação ASGI proposta: método, rota parametrizada, início, término, duração, HTTP, exceção e cancelamento. Abranger autenticação, validação e limite de corpo. |
| Interação              | [session_service.py](../apps/main_api/app/application/session_service.py), `create_interaction`                                                                                                                                                         | Estado persistido, resultado `answer`/`question`, replay, conflitos, duração e falha.                                                                                 |
| Grafo/retomada         | [workflow.py](../apps/main_api/app/graph/workflow.py), `executar_fluxo`/`executar_resposta`                                                                                                                                                             | Duração e resultado da invocação local; excluir espera humana entre requisições.                                                                                      |
| Agente                 | [langchain_factory.py](../apps/main_api/app/infrastructure/agents/langchain_factory.py), `_LangChainAgent.run`                                                                                                                                          | Agente, duração, resultado tipado, validação de schema, erro, timeout e cancelamento.                                                                                 |
| Modelo principal       | Factory e callbacks LangChain                                                                                                                                                                                                                           | Uma observação por chamada real observável, com modelo/provedor, uso e duração.                                                                                       |
| Tool local             | [execution_observer.py](../apps/main_api/app/infrastructure/agents/execution_observer.py), `_ToolExecutionCallback`                                                                                                                                     | Nome, duração, agente quando conhecido, status e modo real/simulado/indisponível.                                                                                     |
| Sessão MCP             | [client.py](../apps/main_api/app/infrastructure/mcp/client.py), `_session`                                                                                                                                                                              | Abertura/inicialização, duração, erro e timeout da fase.                                                                                                              |
| Tool MCP               | Mesmo arquivo, `call_tool`                                                                                                                                                                                                                              | Serviço, operação, duração e `isError` antes de encapsular a resposta. Códigos HTTP/JSON-RPC somente quando acessíveis.                                               |
| Descoberta MCP         | Mesmo arquivo, `list_tools`                                                                                                                                                                                                                             | Observação própria quando executada; não confundir descoberta com operação de negócio.                                                                                |
| Consulta paginada      | [data_client.py](../apps/main_api/app/infrastructure/mcp/data_client.py), `resolve_report_lot`                                                                                                                                                          | Cada chamada efetiva e página individual; uma tool local pode produzir várias chamadas MCP.                                                                           |
| Preparação de e-mail   | [email_workflow.py](../apps/main_api/app/application/email_workflow.py), `prepare`                                                                                                                                                                      | Duração, resultado, reutilização e falhas.                                                                                                                            |
| Envio protegido        | [email_dispatch_workflow.py](../apps/main_api/app/application/email_dispatch_workflow.py), `send`                                                                                                                                                       | Autorização, envio ou consulta de operação existente; resultado tipado e confirmação disponível.                                                                      |
| Recuperação de envio   | Mesmo arquivo, `recover_interaction_outcome`/`_refresh_status`                                                                                                                                                                                          | Observar diretamente: o grafo também recupera estado fora dos callbacks de tools.                                                                                     |
| Resumo                 | [session_summary_generator.py](../apps/main_api/app/infrastructure/agents/session_summary_generator.py), `_invoke`                                                                                                                                      | Cada geração por bloco e compactação, uso retornado e duração.                                                                                                        |
| Perfil                 | [user_memory_extractor.py](../apps/main_api/app/infrastructure/agents/user_memory_extractor.py), `extract`                                                                                                                                              | Chamada de modelo e falha; sem conteúdo do perfil/entrada.                                                                                                            |
| Cliente A2A            | Adaptador ainda a criar, no slot `documental_a2a`                                                                                                                                                                                                       | Duração do cliente, serviço/skill, HTTP/JSON-RPC e resultado do artefato.                                                                                             |
| Executor A2A           | [a2a_server.py](../apps/a2a_agent/src/argos_a2a_agent/adapters/inbound/a2a_server.py), `execute`/`cancel`                                                                                                                                               | Duração local, rejeição, estado protocolar e resultado documental.                                                                                                    |
| Especialistas A2A      | [specialists.py](../apps/a2a_agent/src/argos_a2a_agent/agents/specialists.py)                                                                                                                                                                           | Documental, Legal, Mixed, duração e resultado; Mixed pode chamar vários modelos.                                                                                      |
| Geração/embedding A2A  | [http.py](../apps/a2a_agent/src/argos_a2a_agent/adapters/providers/http.py), [answers.py](../apps/a2a_agent/src/argos_a2a_agent/adapters/providers/answers.py), [embeddings.py](../apps/a2a_agent/src/argos_a2a_agent/adapters/providers/embeddings.py) | Capturar uso, modelo e HTTP antes de reduzir resposta a texto estruturado ou vetores.                                                                                 |
| Serviços A2A           | Adaptadores PostgreSQL/Qdrant, fonte documental e [synchronizer.py](../apps/a2a_agent/src/argos_a2a_agent/infrastructure/synchronizer.py)                                                                                                               | Consultas, leitura/preparação/indexação e recursos diretamente observados.                                                                                            |
| Persistência funcional | Repositórios Mongo e [checkpointer](../apps/main_api/app/infrastructure/checkpoints/mongo.py)                                                                                                                                                           | Duração/resultado das operações instrumentáveis; excluir o writer de telemetria da instrumentação recursiva.                                                          |

Capturar tempos internos dos MCPs exige instrumentação em cada serviço. O tempo
do cliente não permite inferir duração de SQL, Mongo ou SMTP no servidor.

### 3.2. Regras

- Tempo total de resposta: entrada no ASGI até envio do último fragmento da
  resposta. Inclui operações aguardadas; exclui rede até o celular e o backend
  chamador. Desconexão não comprova cancelamento de operações remotas.
- Duração: `perf_counter_ns` local convertido para milissegundos. Início/fim
  civil em UTC. Não usar diferença entre relógios de processos distintos.
- A2A: cliente e servidor produzem observações independentes. Não combinar,
  subtrair ou vincular seus intervalos.
- MCP: o timeout padrão configurado é 10 s. Não apresentá-lo como limite total
  comprovado da tool; inicialização e execução têm fronteiras próprias.
- Status técnico, HTTP, estado protocolar e resultado de domínio são distintos.
  HTTP 200, Juiz `approved`, A2A `completed` e resultado `success` não são
  definições de resolução de negócio.
- Envio pode retornar normalmente com `unknown` após falha de comunicação.
  Registrar origem local e ausência de confirmação; não convertê-lo em sucesso.
- `accepted` de e-mail não comprova entrega ou leitura; `partial` não autoriza
  reenvio automático.
- `retry_count` do grafo conta revisões do Juiz. A renovação de origem expirada,
  controlada por `report_source_renewal_count`, é outra causa de nova execução.
  Nenhuma delas é retry interno do provedor.
- Cada tentativa real observável recebe sua própria observação. Retries
  invisíveis do SDK permanecem desconhecidos; não estimar sua quantidade.
- Uma duração de wrapper/tool e outra de cliente medem fronteiras diferentes.
  Não tratar ambas como duas chamadas faturáveis do mesmo provedor.

## 4. Dicionário de dados proposto

Datas: BSON Date em UTC, precisão de milissegundos, exportação ISO 8601 com `Z`.
Durações: Double em ms. Contagens: inteiros não negativos. Ausência é ausência
ou `null`, nunca zero automático. `O` = obrigatório; `C` = condicional; `P` =
opcional. Não armazenar IDs pessoais, de sessão ou interação neste schema.

### 4.1. Envelope e tempo

| Campo                    | Significado                                                                          | Tipo/unidade        | Origem/disponibilidade     | Regra       |
| ------------------------ | ------------------------------------------------------------------------------------ | ------------------- | -------------------------- | ----------- |
| `_id`                    | Identidade/idempotência de uma observação                                            | UUID em string      | Instrumentação proposta    | O           |
| `schema_version`         | Versão do contrato                                                                   | String              | Configuração proposta      | O           |
| `service`, `environment` | Produtor e ambiente                                                                  | Strings controladas | Bootstrap/configuração     | O           |
| `service_version`        | Versão/commit implantado                                                             | String              | Deployment a configurar    | P           |
| `operation_kind`         | HTTP, grafo, agente, modelo, embedding, tool, MCP, A2A, workflow, storage ou externo | Enum                | Fronteira instrumentada    | O           |
| `operation_name`         | Nome conhecido da operação                                                           | String limitada     | Símbolo/Registry/allowlist | O           |
| `operation_phase`        | Inicialização, execução, validação ou recuperação                                    | Enum                | Fronteira conhecida        | P           |
| `observation_level`      | Operação local, invocação SDK ou tentativa de transporte                             | Enum                | Adaptador                  | O           |
| `agent`                  | Agente responsável conhecido                                                         | Enum                | Composição/Registry        | C           |
| `execution_mode`         | Real, mock/teste ou indisponível sem chamada                                         | Enum                | Binding efetivo            | O           |
| `started_at`             | Início observado                                                                     | BSON Date, UTC      | Relógio civil local        | O           |
| `ended_at`               | Término observado                                                                    | BSON Date, UTC      | Relógio civil local        | C: terminal |
| `duration_ms`            | Intervalo diretamente medido                                                         | Double, ms          | Relógio monotônico local   | C: terminal |
| `updated_at`             | Última alteração persistida                                                          | BSON Date, UTC      | Writer Mongo               | O           |

### 4.2. Status, erros e resultado

| Campo                                 | Significado                                                                          | Tipo/unidade                         | Origem/disponibilidade                                    | Regra              |
| ------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------ | --------------------------------------------------------- | ------------------ |
| `status`                              | started, succeeded, failed, timeout, cancelled, rejected                             | Enum                                 | Classificação técnica proposta                            | O                  |
| `http.method`, `http.route`           | Método e rota parametrizada                                                          | Strings                              | ASGI                                                      | C: HTTP            |
| `http.status_code`                    | Código recebido/enviado                                                              | Int                                  | ASGI/HTTPX quando acessível                               | P                  |
| `protocol.name`, `protocol.operation` | MCP/A2A e operação efetiva                                                           | Strings controladas                  | Cliente/servidor                                          | C: protocolo       |
| `protocol.task_state`                 | Estado A2A retornado/emitido                                                         | String                               | SDK existente                                             | P                  |
| `protocol.error_code`                 | Código JSON-RPC/MCP                                                                  | Int/string tipada                    | Transporte/SDK quando acessível                           | P                  |
| `result.source`, `result.code`        | Origem e resultado tipado                                                            | Strings controladas                  | Interação, especialista, Juiz, MCP, A2A ou workflow local | P                  |
| `result.confirmed`                    | Confirmação explicitamente disponível                                                | Boolean                              | Copiar resposta/indicação existente                       | P                  |
| `result.recovered`                    | Resultado obtido por recuperação                                                     | Boolean                              | Workflow de envio quando disponível                       | P                  |
| `error.type`, `error.origin`          | Categoria segura e camada de origem                                                  | Strings/enums                        | Captura antes de encapsulamento                           | C: falha conhecida |
| `error.code`                          | Código nativo seguro ou interno                                                      | Int/string tipada                    | Adaptador                                                 | P                  |
| `error.message_sanitized`             | Mensagem curta, sem conteúdo sensível                                                | String, até 512 caracteres propostos | Catálogo seguro/sanitização                               | P                  |
| `timeout_ms`, `timeout_phase`         | Limite e fase conhecida                                                              | Double ms/enum                       | Configuração/HTTPX/deadline                               | P                  |
| `cancellation_state`                  | Solicitado, observado ou confirmado                                                  | Enum                                 | Exceção/SDK/resposta                                      | C: cancelamento    |
| `attempt_number`                      | Ordem local conhecida                                                                | Int                                  | Wrapper explícito                                         | P                  |
| `attempt_reason`                      | Inicial, retry transitório, fallback, reparo, revisão do Juiz ou renovação de origem | Enum                                 | Controle que iniciou a execução                           | P                  |
| `attempt_visibility`                  | Tentativa individual ou retries internos desconhecidos                               | Enum                                 | Capacidade do adaptador                                   | C: modelo/externo  |
| `replayed_result`                     | Resultado funcional reutilizado sem nova execução                                    | Boolean                              | Serviço de sessão/workflow                                | P                  |

Preservar estados nativos de preparação e envio, incluindo pending, accepted,
partial, failed e unknown. Não inferir resultado por texto livre nem criar
`resolved=true`.

### 4.3. Consumo e cobrança

| Campo                                                 | Significado                                                                   | Tipo/unidade                       | Origem/disponibilidade                 | Regra                          |
| ----------------------------------------------------- | ----------------------------------------------------------------------------- | ---------------------------------- | -------------------------------------- | ------------------------------ |
| `provider`                                            | Provedor efetivo                                                              | String controlada                  | Configuração validada, ainda a fechar  | C: conhecido                   |
| `model_requested`                                     | Modelo solicitado                                                             | String                             | Configuração/payload técnico           | C: modelo                      |
| `model_returned`, `model_revision`                    | Modelo/revisão informados                                                     | String                             | Resposta/configuração                  | P                              |
| `usage.availability`                                  | Retornado, parcial, ausente ou não aplicável                                  | Enum                               | Adaptador                              | C: recurso faturável           |
| `usage.source`                                        | Provedor direto ou SDK                                                        | Enum                               | Adaptador                              | C: uso disponível              |
| `usage.input_tokens`, `usage.output_tokens`           | Tokens informados                                                             | Int, tokens                        | usage/usage_metadata; captura proposta | P                              |
| `usage.total_tokens`                                  | Total retornado, sem calculá-lo                                               | Int, tokens                        | Provedor/SDK                           | P                              |
| `usage.cache_read_tokens`, `usage.cache_write_tokens` | Categorias de cache                                                           | Int, tokens                        | Detalhes retornados                    | P                              |
| `usage.reasoning_tokens`                              | Quantidade, nunca conteúdo do raciocínio                                      | Int, tokens                        | Detalhes retornados                    | P                              |
| `usage.details`, `usage.provider_fields`              | Outras categorias e nomes originais necessários                               | Objeto limitado                    | Allowlist do bloco de uso por provedor | P                              |
| `resources`                                           | Quantidade diretamente observada de bytes, páginas, itens, destinatários etc. | Lista de name/quantity/unit/source | Adaptador, sem conteúdo                | P                              |
| `billing.reported_cost`, `billing.currency`           | Custo diretamente informado e moeda                                           | Decimal128/ISO 4217                | Não demonstrado no código atual        | P; moeda obrigatória com custo |
| `billing.cost_source`                                 | Identifica valor informado pelo provedor                                      | Enum                               | Adaptador                              | C: custo disponível            |
| `billing.service_tier`, `billing.price_reference`     | Modalidade e referência/versionamento de tarifa                               | String/data                        | Provedor/configuração administrativa   | P                              |

Não deduzir o provedor de um endpoint apenas OpenAI-compatible. Não implementar
custo estimado por chamada. Tokenizer local do resumo serve ao limite de
contexto e não representa uso faturado. Se estimativas de tokens forem adotadas
futuramente, terão campo e origem separados; não são necessárias nesta entrega.
Cache/reasoning podem ser subconjuntos dos totais: conservar nomes e semântica
do provedor, sem somar categorias sobrepostas. Uma fonte autoritativa por
chamada evita duplicar uso entre wrapper, callback e transporte.

## 5. Persistência Mongo e extração

### 5.1. Coleção e documento

**Definido:** coleção específica, com nome ainda a ser escolhido pelo usuário.
Propor as variáveis `MAIN_API_OBSERVABILITY_COLLECTION` no principal e
`A2A_OBSERVABILITY_COLLECTION` no A2A, seguindo o prefixo de configuração de
cada aplicação. Ambas apontarão para a coleção escolhida quando compartilharem o
mesmo destino Mongo. Não criar nome padrão silencioso; informar o nome quando a
coleta for habilitada. O database hospedeiro e as credenciais ainda serão
configurados; um database exclusivo não está confirmado. Uma coleção atende ao
envelope comum e aos blocos opcionais. Nenhuma coleção analítica.

**Decisão confirmada:** um documento por operação/tentativa observada:

1. Gerar `_id` antes da operação.
2. Publicar início como `started`.
3. Atualizar o mesmo documento no término com duração medida localmente.
4. Nova execução real recebe outro `_id`; retry de gravação conserva o ID.
5. Queda abrupta pode deixar documento `started`. Não inventar término ou causa.

O ID não vincula operações diferentes. Não adicionar trace_id, parent_id, IDs
propagados ou chaves de usuário/sessão para reconstruir fluxos.

#### Documento atualizado, sem histórico de versões intermediárias

No formato escolhido, uma chamada começa com `status=started` e `started_at`. Ao
terminar, o mesmo documento recebe `status=succeeded` ou outro estado terminal,
`ended_at`, `duration_ms` e o resultado. O horário de início é preservado; o que
deixa de existir como registro separado é a versão anterior do documento, quando
ele ainda estava em execução. O BI recebe a observação com os dados completos da
tentativa, não todas as versões intermediárias.

Não criar registros imutáveis separados para cada mudança de estado. Os campos
solicitados de início, fim, duração e resultado ficam no documento da tentativa.
A duração é medida localmente, nunca reconstruída juntando eventos. Não criar
rastreamento ou vínculos entre operações diferentes.

Uma tentativa falha seguida de outra tentativa bem-sucedida sempre gera duas
observações próprias. A segunda não sobrescreve a falha da primeira. A decisão
de atualizar o documento de uma tentativa não autoriza descartar tentativas
anteriores.

Exemplo ilustrativo; não representa operação real já coletada:

```json
{
	"_id": "c29b7f6d-fbd4-4d70-b1e9-a1f9067c86b0",
	"schema_version": "1",
	"service": "main_api",
	"environment": "test",
	"operation_kind": "mcp",
	"operation_name": "list_lots",
	"observation_level": "sdk_invocation",
	"execution_mode": "real",
	"started_at": "2026-10-12T12:00:00.000Z",
	"ended_at": "2026-10-12T12:00:00.254Z",
	"duration_ms": 253.7,
	"updated_at": "2026-10-12T12:00:00.500Z",
	"status": "succeeded",
	"protocol": { "name": "mcp", "operation": "tools/call" }
}
```

No Mongo, as três datas serão BSON Date; strings ISO no exemplo representam a
forma legível de exportação. Duração monotônica pode diferir do arredondamento
dos timestamps civis.

### 5.2. Índices mínimos

| Índice                    | Finalidade                                     |
| ------------------------- | ---------------------------------------------- |
| `_id`, único automático   | Deduplicação                                   |
| `{started_at: 1, _id: 1}` | Extração por período de ocorrência             |
| `{updated_at: 1, _id: 1}` | Extração incremental, inclusive término tardio |

Sem TTL, sem `expires_at`. Não indexar inicialmente agente, modelo, status ou
erro sem uma necessidade de leitura comprovada.

### 5.3. Writer, fila e deduplicação

Fluxo proposto: observação validada/sanitizada → fila limitada → writer
assíncrono → Mongo. Sem espera de rede Mongo em cada emissão normal.

- Escrita confirmada `majority`, seguindo o padrão atual.
- Lotes limitados e ordem preservada para alterações da mesma observação.
- Retry transitório com backoff/jitter; timeout de confirmação é incerto e exige
  reenvio do mesmo ID.
- Falha parcial de lote: repetir apenas itens não confirmados/incertos.
- Replay de início não pode sobrescrever estado terminal.
- Terminal idêntico repetido é idempotente. Conteúdo terminal divergente para o
  mesmo ID gera conflito seguro, sem sobrescrever silenciosamente.
- O writer nunca reexecuta modelo, tool, envio ou A2A.
- Não instrumentar suas próprias gravações pelo mesmo coletor.

Limites iniciais propostos, sujeitos a benchmark:

| Parâmetro      | Hipótese inicial                                                  |
| -------------- | ----------------------------------------------------------------- |
| Fila           | Até 2.048 itens e 8 MiB, o primeiro limite atingido               |
| Documento      | Até 16 KiB; objetivo típico de 2 KiB                              |
| Lote           | Até 100 alterações ou intervalo de 250 ms                         |
| Retry imediato | Até 3 tentativas                                                  |
| Shutdown       | Drenagem por prazo limitado; sinalizar pendências não confirmadas |

**Decisão confirmada: sem spool.** A fila existe somente na memória do processo,
com limites por instância. Os limites precisam abranger também lotes e itens em
retry, para não criar buffers ilimitados fora da fila. Não exigir volume local,
arquivos de pendências, chave de criptografia de spool ou serviço alternativo de
armazenamento para telemetria.

Mongo indisponível: o writer tenta gravar os itens aceitos dentro do limite de
tentativas e prazos. Ao esgotá-los ou em falha permanente, sinalizar a perda em
log seguro e liberar o item. Se a fila estiver cheia, recusar a nova observação
sem bloquear a solicitação, sinalizando a falha de coleta. Não criar tarefas
ilimitadas para contornar a fila. Registros já confirmados no Mongo permanecem.

Em reinício ou queda abrupta, itens ainda somente em memória podem ser perdidos;
não haverá recuperação desses itens após iniciar outro processo. Uma escrita com
confirmação incerta pode ter ocorrido no Mongo, portanto não tratar todas as
pendências como perdas definitivas comprovadas. Drenar no shutdown por prazo
limitado e sinalizar itens não confirmados. Uma atualização terminal pode fazer
upsert do documento completo se o início não tiver sido persistido, mantendo o
mesmo ID e a duração já medida localmente.

**Continuar atendendo:** falhas da coleta não propagam erro ao fluxo funcional
nem bloqueiam a prontidão apenas por indisponibilidade da telemetria. A
sinalização precisa de canal independente, por exemplo log seguro, sem depender
do Mongo indisponível ou provocar recursão. Evitar inundar logs em
indisponibilidade prolongada. Essa decisão não muda falhas da persistência
funcional de sessões, checkpoints ou envio; se o Mongo conversacional falhar, o
principal pode ficar indisponível por sua dependência funcional.

### 5.4. Retenção permanente e extração

- Registros definitivos permanecem até exclusão explícita autorizada.
- Sem purge por idade, job periódico de exclusão ou mecanismo de expiração.
- Exclusão futura precisa de escopo, responsável e autorização explícitos.
- Backups e arquivos exportados têm política própria; excluir do Mongo não os
  elimina automaticamente. Essa política adicional está a confirmar.
- BI: credencial somente leitura, rede privada/TLS e acesso apenas à coleção.
- Extração paginada por período; incremental por `(updated_at, _id)`, com janela
  sobreposta para atualizações tardias e deduplicação no destino.
- Alternativa: JSON Lines/Extended JSON, preservando UTC, Decimal128, versão e
  ausência de valores. Extração não apaga dados nem executa joins
  conversacionais.
- Documentos atualizados permitem ler o estado atual com início e término; não
  são histórico imutável de todas as transições. Esse formato está confirmado.

## 6. Arquitetura, organização e segurança

### 6.1. Componentes propostos

| Caminho relativo à raiz, ainda a criar                                  | Responsabilidade                                                           |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| `packages/observability/models.py`                                      | Envelope tipado/versionado                                                 |
| `packages/observability/ports.py`                                       | Protocol do coletor e do writer                                            |
| `packages/observability/timing.py`                                      | Medição local                                                              |
| `packages/observability/sanitization.py`                                | Allowlist e mensagens seguras                                              |
| `apps/main_api/app/infrastructure/observability/asgi.py`                | Fronteira HTTP                                                             |
| `apps/main_api/app/infrastructure/observability/langchain_callbacks.py` | Modelo/tool por chamada                                                    |
| `apps/main_api/app/infrastructure/observability/mongo_repository.py`    | Collection, validação e índices                                            |
| `apps/main_api/app/infrastructure/observability/writer.py`              | Fila limitada em memória, lotes, retry e encerramento                      |
| `apps/main_api/app/infrastructure/integrations/a2a_client.py`           | Futuro cliente real; ajustar nome/local ao padrão vigente ao implementá-lo |
| `apps/a2a_agent/src/argos_a2a_agent/adapters/observability/`            | Composição do coletor e persistência local ao serviço A2A                  |

Adaptar Settings, bootstrap/lifespan, Factory, clientes MCP e workflows já
existentes. O núcleo compartilhado não depende de FastAPI, LangChain ou Mongo.
As aplicações não importam internals umas das outras. Novos pacotes
compartilhados precisam ser incluídos explicitamente nos builds dos
consumidores.

O observador conversacional continua separado. Reaproveitar princípios de
injetabilidade e sanitização, sem retirar registros necessários aos resumos.

Capturar modelos antes de reduzir saída a `structured_response`. Nas chamadas de
resumo/perfil, callback ou `include_raw=True`, se suportado, pode preservar
metadados; conteúdo é descartado após a extração. Não usar acumulador global de
tokens para substituir registros individuais.

Configurações novas: habilitação, URI/database/collection, ambiente/versão,
limites de fila/documento/lote, timeout/retries e prazo de shutdown. **Não
adicionar configuração de retenção por idade ou TTL.** `.env.example` contém
apenas exemplos sem segredos. Para o nome da coleção, prever
`MAIN_API_OBSERVABILITY_COLLECTION` e `A2A_OBSERVABILITY_COLLECTION`, sem fixar
seu valor antes da definição do usuário.

Dependências e driver: manter interface assíncrona com collection injetada.
Avaliar PyMongo Async para o writer novo, validando Python 3.14.3 e versões
efetivas. A persistência atual usa Motor; migrar memória/checkpoints é trabalho
separado. A análise anterior identificou depreciação no upstream; confirmar
compatibilidade e suporte antes de implementar. Não instalar outra plataforma de
observabilidade sem necessidade demonstrada.

### 6.2. Proteção e acesso

- Sanitização antes da fila em memória, não apenas antes do Mongo.
- Não guardar prompts/respostas completas, texto de PDF, vetores, argumentos
  completos de tools ou conteúdo de raciocínio.
- Não serializar Principal, RunnableConfig/configurable, estado do grafo ou
  checkpoints. Eles podem conter identidade, seletores e contexto protegido.
- Excluir `recipient_selectors`, envelopes assinados, destinatários, títulos,
  corpo de relatório/e-mail e contexto de autorização.
- Excluir JWT/Bearer, delegação, cookies, DSNs, chaves privadas, headers
  completos e URLs com query string.
- Não exportar identificadores `argos_*` para vincular observações.
- Usar nomes de serviço/rota parametrizada e catálogos controlados.
- Preferir mensagem de erro de catálogo; texto do provedor exige sanitização e
  truncamento. Capturar classificação antes de wrappers genéricos.
- Writer com insert/update e leitura mínima necessária à deduplicação na
  coleção; leitor BI somente leitura. Recomenda-se provisionar índices por conta
  administrativa, sem exigir permissão de criação de índices do writer. Se o
  deployment optar por criação automática, conferir `createIndex` na credencial.
  TLS, rede restrita e criptografia do armazenamento Mongo.
- Rever `logger.exception` antes de qualquer exportação automática de logs.

### 6.3. Configuração de produção e secrets

Configuração não é sinônimo de secret. Nome de database/coleção, habilitação,
ambiente, versão, limites da fila/lote/documento, timeout/retries e prazo de
shutdown são parâmetros comuns de deployment. Credenciais, URI com senha
embutida e chaves privadas de autenticação são segredos e não devem ser
versionados nem registrados em logs.

O secret apenas fornece credenciais: as permissões já são associadas ao usuário
no MongoDB. Se o usuário tiver as permissões necessárias, informar a conexão é
suficiente; não cadastrar permissões novamente na aplicação. Não presumir que
qualquer URI/senha autorize gravar ou criar índices.

| Item em produção       | Necessidade                                                                                                               |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Mongo                  | URI/database e usuário autorizado por serviço produtor; o A2A ainda precisa de composição de persistência de métricas.    |
| Coleção                | Nome definido pelo usuário nas variáveis MAIN_API_OBSERVABILITY_COLLECTION/A2A_OBSERVABILITY_COLLECTION.                  |
| Rede e acesso          | Caminho de rede liberado, TLS e privilégios necessários no Mongo; acesso BI restrito à coleção.                           |
| Índices                | `_id` e os dois índices de período/incremental, sem TTL; criação prévia recomendada ou permissão para criação automática. |
| Memória e concorrência | Limites por processo/réplica, incluindo itens em lote/retry; multiplicar capacidade pelo número de workers.               |
| Encerramento           | Prazo limitado para drenagem; reinício pode perder itens não confirmados.                                                 |
| Diagnóstico            | Log seguro independente do Mongo, sem transformar falha da telemetria em falha funcional ou de prontidão.                 |
| Disco local            | Nenhum volume persistente específico para a coleta; spool não será usado.                                                 |

A persistência funcional do principal continua com sua configuração e exigência
de replica set. Sua indisponibilidade não é contornada por esta coleta. Os nomes
novos de configuração são propostos; nenhuma variável ou infraestrutura de
observabilidade foi implementada nesta entrega documental.

## 7. Contexto entre sessões, separado da observabilidade

**Existente:**
[build_session_context](../apps/main_api/app/application/context_builder.py) usa
resumo, mensagens posteriores de interações terminais, tom e objetivo geral. A
entrada atual é separada. O
[serviço de resumo](../apps/main_api/app/application/session_summary_service.py)
publica marcos de 15 entradas únicas e o resumo tem teto de 2.000 tokens; pode
exigir blocos/compactações. A
[memória do usuário](../apps/main_api/app/application/user_memory_service.py) é
limitada a preferências elegíveis, com revisão e remoção explícita.

A branch também preserva seletores e intenção de envio durante esclarecimentos
da interação. É estado protegido de workflow, não perfil geral entre sessões.

Sessões/checkpoints permitem retomada. O A2A usa `InMemoryTaskStore`, sem a
mesma durabilidade do checkpointer do principal. Transferência automática de
atividade completa entre novas sessões e invalidação integrada de evidências
documentais obsoletas em resumos/checkpoints não estão comprovadas.

Este plano apenas observa duração, uso e falha dessas operações. Não muda seus
conteúdos, sua retenção nem o comportamento de memória conversacional.

## 8. Ferramentas avaliadas

| Ferramenta             | Captura/exportação                                                                    | Limitação, custo e privacidade                                                                                                                         | Decisão                   |
| ---------------------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------- |
| Grafana                | Consulta fontes já existentes e disponibiliza resultados de consultas                 | Não captura sozinho as chamadas; acrescenta serviço e acesso. Plugin oficial Mongo é Enterprise, com licença/plano. Recursos de dashboards ficam fora. | Não adicionar             |
| Prometheus             | Amostras de séries por scrape e API HTTP                                              | Counters/histogramas não conservam cada chamada e erro. Série por chamada é inadequada. OSS exige compute/storage/operação e cuidado com labels.       | Não usar como fonte bruta |
| LangSmith              | Chamadas de modelo/tools, tempos, erros e uso disponível; consulta/exportação SDK/API | Centrado em tracing, exige adaptação para A2A customizado, SaaS/retenção/custos e ocultação de conteúdo. Custo exibido pode ser estimativa.            | Dispensável; outro escopo |
| Instrumentação + Mongo | Documentos individuais com unidades explícitas e extração direta                      | Exige adapters/writer; evita cópia adicional de dados em SaaS                                                                                          | Recomendada               |

Fontes oficiais consultadas na análise inicial de 11/10/2026:

- [Tipos Prometheus](https://prometheus.io/docs/concepts/metric_types/) e
  [API](https://prometheus.io/docs/prometheus/latest/querying/api/).
- [Plugin MongoDB Grafana](https://grafana.com/docs/plugins/grafana-mongodb-datasource/latest/).
- [Exportação LangSmith](https://docs.langchain.com/langsmith/export-traces),
  [ocultação de conteúdo](https://docs.langchain.com/langsmith/mask-inputs-outputs)
  e [preços](https://www.langchain.com/pricing).
- [Metadados e saída estruturada LangChain](https://docs.langchain.com/oss/python/langchain/models).
- [Categorias de uso OpenAI](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/list).
- [Aviso de suporte do Motor](https://github.com/mongodb/motor).

LangSmith apresentava Developer com 5 mil traces base/mês e Plus a US$ 39 por
assento/mês com 10 mil traces base e excedentes pagos. Não utilizar preço antigo
por mil traces sem cotação. Orçamento recomendado de ferramentas adicionais:
US$ 0. Verificar preços/licenças novamente se alguma for adotada.

## 9. Estimativas de planejamento e crescimento permanente

Valores em USD, sem impostos, câmbio, descontos, mão de obra ou compromisso de
capacidade. São referências consultadas em **11/10/2026**, não cotação nova.
Modelos de referência não são escolha de produção. Todas as fórmulas pertencem
somente a este documento.

### 9.1. Preços de referência

| Item                                | Referência e fonte                                                                                                                                                 |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| GPT-4.1 mini                        | US$ 0,40/M entrada, 0,10/M entrada em cache, 1,60/M saída. [Fonte](https://developers.openai.com/api/docs/models/gpt-4.1-mini).                                    |
| text-embedding-3-small              | US$ 0,02/M tokens. [Fonte](https://developers.openai.com/api/docs/models/text-embedding-3-small).                                                                  |
| Atlas Flex                          | US$ 8–30 por 30 dias, 5 GB e faixa base de 100 operações/s. [Fonte](https://www.mongodb.com/docs/atlas/billing/atlas-flex-costs/).                                 |
| Atlas dedicado                      | Aproximadamente US$ 60/mês como piso; região, storage e backup alteram preço. [Fonte](https://www.mongodb.com/docs/atlas/billing/billing-breakdown-optimization/). |
| Compute ilustrativo Lightsail Linux | 2 GiB US$ 12/mês; 4 GiB US$ 24; 8 GiB US$ 44. [Fonte](https://aws.amazon.com/lightsail/pricing/).                                                                  |

### 9.2. Premissas base, ainda não medidas

- 10 solicitações por usuário ativo/semana; conversão mensal de 52/12 semanas.
- 8 gerações principais por solicitação, incluindo perfil e chamadas adicionais
  de tools, cada uma com 2.000 tokens de entrada/400 de saída.
- Uma geração de resumo por 15 solicitações, de 8.000/1.000 tokens. Sessões
  curtas, blocos e compactações podem mudar essa quantidade.
- 20% com A2A: uma geração 3.000/600 e um embedding de 500 tokens.
- Reserva de 5% de consumo para repetições faturáveis; sem desconto de cache.
- 24 documentos/solicitação, 2 KiB por documento e fator de 1,5 para
  índices/margem.
- Retenção permanente: armazenamento acumulado ao longo dos meses.

As integrações da branch exigem refinar o workload em consulta simples, consulta
paginada e relatório/e-mail. Inicializações MCP, páginas, recuperação e
renovação de origem aumentam observações em proporção não medida. Não aplicar
aumento percentual arbitrário. O cenário base abaixo é provisório.

### 9.3. Fórmulas e volumes

```text
N_mensal = usuários × solicitações_semanais × 52/12
C_geração = (tokens_entrada × preço_entrada + tokens_saída × preço_saída) / 1.000.000

C_base_solicitação = 1,05 × [
  8 × (2.000 × 0,40 + 400 × 1,60) / 1.000.000
  + (8.000 × 0,40 + 1.000 × 1,60) / 1.000.000 / 15
  + 0,20 × (3.000 × 0,40 + 600 × 1,60) / 1.000.000
  + 0,20 × 500 × 0,02 / 1.000.000
] = US$ 0,0128877

Documentos_mensais = N_mensal × eventos_por_solicitação
Storage_acumulado_GB(meses) = documentos_mensais × meses × 2.048 × 1,5 / 1.000.000.000
```

| Item base                                   | 100 usuários/semana | 1.000 usuários/semana |
| ------------------------------------------- | ------------------- | --------------------- |
| Solicitações/mês                            | 4.333               | 43.333                |
| Documentos/mês                              | 104.000             | 1.040.000             |
| Inserção + atualização/mês, aproximadamente | 208.000             | 2.080.000             |
| Modelos/embeddings por mês                  | US$ 55,85           | US$ 558,47            |
| Storage após 1 mês                          | 0,32 GB             | 3,19 GB               |
| Storage após 3 meses                        | 0,96 GB             | 9,58 GB               |
| Storage após 12 meses                       | 3,83 GB             | 38,34 GB              |
| Storage após 24 meses                       | 7,67 GB             | 76,68 GB              |

GB decimais. Esses valores não incluem histórico conversacional, checkpoints,
coleções de relatório, backups ou replicação física. Não haverá custo de spool.
Não há teto de armazenamento de 90 dias: o prazo anteriormente sugerido foi
rejeitado. Gravações dependem da política de início/término e não representam
operações/s de pico do Mongo; testar concorrência e também leituras de extração.

### 9.4. Infraestrutura ilustrativa e limites do orçamento

Hipótese: cinco aplicações independentes, uma instância PostgreSQL e uma Qdrant.
Não é dimensionamento homologado. O Compose A2A limita seu container a 1 CPU/1
GiB, mas não demonstra capacidade desse conjunto de instâncias.

| Item mensal                     | 100 usuários              | 1.000 usuários           |
| ------------------------------- | ------------------------- | ------------------------ |
| Cinco aplicações                | 5 × US$ 12 = US$ 60       | 5 × US$ 24 = US$ 120     |
| PostgreSQL + Qdrant             | 2 × US$ 24 = US$ 48       | 2 × US$ 44 = US$ 88      |
| Compute subtotal                | US$ 108                   | US$ 208                  |
| Reserva Mongo inicial           | US$ 8–30 se Flex adequado | US$ 60–150 para dedicado |
| Ferramentas adicionais          | US$ 0                     | US$ 0                    |
| Subtotal mensal inicial coberto | US$ 171,85–193,85         | US$ 826,47–916,47        |

US$ 60–150 é reserva preliminar, não cotação de cluster dimensionado. Retenção
permanente exige recotar storage/backup por horizonte: 12/24 meses acima não
garantem manutenção dessa tarifa. No cenário de 100 usuários, o volume de
métricas sozinho supera 5 GB antes de 24 meses; no de 1.000, antes de dois
meses. Outras coleções e folga operacional antecipam mudança de capacidade.

Produção pode exigir dedicado também com 100 usuários: subtotal inicial
US$
223,85–313,85 usando a mesma reserva. Piloto apenas principal/A2A reduz compute
das aplicações para US$
24/48. Infra já disponível com capacidade livre reduz o custo incremental; não
confundir implantação completa com acréscimo da coleta.

### 9.5. Sensibilidade e despesas desconhecidas

Hipótese intensa: 30 solicitações/usuário/semana, 12 gerações principais de
4.000/800 tokens e 36 documentos/solicitação; outras premissas iguais.

| Item                       | 100 usuários | 1.000 usuários |
| -------------------------- | ------------ | -------------- |
| Solicitações/mês           | 13.000       | 130.000        |
| Documentos/mês             | 468.000      | 4.680.000      |
| Modelos/embeddings por mês | US$ 482,04   | US$ 4.820,36   |
| Storage após 12 meses      | 17,25 GB     | 172,52 GB      |

Recotar compute/Mongo nesse cenário; não reutilizar a faixa base
automaticamente.

Custos a confirmar: SMTP por mensagem/destinatário faturável, corpus alterado
para reembedding, backup/HA, região, tráfego e extração. SQL/Mongo/Qdrant e
predição determinística não têm tarifa comercial por tool demonstrada no código;
consomem infraestrutura. Não considerar recursos desconhecidos como gratuitos.

BI terá tokens/categorias, modelos, quantidades observadas e resultados
técnicos. Custo por resolução/ROI ainda exigem definição de resolução, evidência
de resultado, tarifas, benefícios e custos humanos. A atribuição entre chamadas
e resoluções não é criada aqui; qualquer requisito futuro desse tipo exige novo
escopo.

## 10. Etapas e critérios verificáveis

Nenhuma pendência funcional impede iniciar observabilidade. A2A real no
principal, origem assinada de relatório e farm_ids do backend afetam cobertura
completa, não as três primeiras etapas da implementação. Elas produzem código de
contrato, medição, sanitização, fila e persistência; não são uma fase adicional
de planejamento. A implementação continua não iniciada nesta entrega documental.

| Etapa                      | Dependências                         | Conclusão verificável                                                                        |
| -------------------------- | ------------------------------------ | -------------------------------------------------------------------------------------------- |
| 1. Contrato                | Diagnóstico e decisões confirmadas   | Schema/versionamento, unidades, estados, obrigatoriedade e ausência de TTL documentados.     |
| 2. Núcleo compartilhado    | Etapa 1                              | Timer, sanitização e Protocols independentes, testados com fakes.                            |
| 3. Persistência Mongo      | Etapas 1–2                           | Índices, deduplicação, limites, retry e comportamento de falhas comprovados.                 |
| 4. Principal/MCP/workflows | Etapas 2–3                           | HTTP, interação, grafo, agentes, tools, cliente MCP e recuperação determinística observados. |
| 5. Uso de modelos/memória  | Etapa 4 e SDK efetivo                | Uso por chamada, ausência explícita, compactações/perfil capturados sem conteúdo.            |
| 6. A2A isolado             | Etapas 2–3                           | Executor, especialistas, geração, embeddings e serviços locais observados.                   |
| 7. Cliente A2A             | Cliente/autenticação/delegação reais | Chamada pelo slot existente observada sem IDs de correlação entre processos.                 |
| 8. Extração/acesso         | Contrato BI                          | Paginação/incremental/tipos, leitura restrita e extração sem exclusão comprovados.           |
| 9. Homologação             | Etapas anteriores                    | Desempenho, volume, falhas e limites verificados; orçamento revisto com medições.            |

A prioridade após preparar o writer é instrumentar cliente MCP e workflows
existentes, antes de depender do futuro cliente A2A. Não implementar a origem
assinada nem a autenticação do backend como efeitos colaterais desta entrega.
Criar o cliente funcional A2A é uma dependência de integração separada; a etapa
7 acrescenta sua observação quando esse cliente estiver disponível. Etapas 1–6
podem ser concluídas sem esperar essa integração.

### 10.1. Entregas de implementação por etapa

#### Etapa 1 — Implementar o contrato

- Criar modelos e enums versionados em `packages/observability/models.py`, com
  validação de UTC, duração em ms, contagens não negativas e tamanho dos campos.
- Separar envelope, status técnico, resultado tipado, erro seguro, consumo e
  cobrança informada. Campos do provedor ausentes permanecem ausentes.
- Exigir início, término e duração no documento terminal; a tentativa em curso
  pode não ter término. Uma tentativa nova tem novo ID.
- Criar testes do contrato e exemplos de início, sucesso, falha e uso ausente.

Aceite: modelos serializáveis para Mongo e exportação, sem TTL, conteúdo
conversacional, correlação ou campos de indicadores calculados.

#### Etapa 2 — Implementar medição e sanitização compartilhadas

- Criar Protocols em `ports.py`, timer em `timing.py` e allowlist em
  `sanitization.py`, dentro de `packages/observability`.
- Medir diretamente a operação local; classificar timeout/cancelamento/erro sem
  enviar exceções ou conteúdo sensível para o writer.
- Definir emissão não bloqueante em rede e coletor fake/desabilitado para testes
  e para falhas da configuração de telemetria, com diagnóstico seguro.
- Preservar resposta/exceção funcional da operação mesmo se a emissão falhar.

Aceite: testes com relógio fake, dados sensíveis e sink com falha passam sem
dependência de banco, FastAPI, LangChain ou serviço externo.

#### Etapa 3 — Implementar persistência e ciclo de vida

- Criar adapter Mongo e writer nos caminhos propostos da seção 6, com fila
  limitada incluindo itens em lote/retry, escrita assíncrona e deduplicação.
- Implementar início e atualização terminal por ID, upsert terminal completo,
  proteção contra regressão de estado e conflito de conteúdo terminal.
- Conectar criação/encerramento do writer ao bootstrap/lifespan; adicionar
  configurações de conexão, coleção, limites, retries e prazo de drenagem.
- Preparar os índices por provisionamento ou inicialização autorizada; falha de
  coleta/configuração não torna a aplicação indisponível por si só.
- Sem spool: esgotamento, fila cheia ou shutdown incompleto geram diagnóstico
  seguro e não bloqueiam atendimento.

Aceite: integração com Mongo descartável valida escrita, duplicidade, falha
parcial, limite de memória e continuidade funcional durante indisponibilidade.

#### Etapa 4 — Instrumentar principal, MCP e workflows

- Adicionar observação ASGI, serviço de interação, execução/retomada do grafo e
  fronteira de agentes/tools, preservando o observador conversacional existente.
- Instrumentar `_session`, `call_tool`, `list_tools` e chamadas paginadas do
  cliente MCP; capturar erros antes de encapsulá-los.
- Instrumentar `EmailWorkflow` e `EmailDispatchWorkflow`, incluindo recuperação
  fora dos callbacks; registrar estados locais e retornados sem inferir entrega.
- Classificar bindings reais, mocks e indisponibilidade sem chamada externa.

Aceite: fixtures determinísticas geram observações das chamadas efetivas,
incluindo replay e recuperação, sem repetir efeitos de negócio por falha da
coleta.

#### Etapa 5 — Capturar uso dos modelos e operações de memória

- Acrescentar callbacks/adapters por chamada, antes da redução à saída
  estruturada; escolher uma fonte autoritativa para cada uso observado.
- Cobrir agentes, resumo por blocos, compactações e extração de preferências.
- Conservar categorias, nomes e origem do uso; não usar acumulador global,
  tokenizer local como faturamento ou estimativa de custo da aplicação.

Aceite: uma execução com múltiplas chamadas preserva consumo individual,
inclusive quando parsing falha depois de o provedor responder. Ausências são
explícitas, sem duplicação ou captura de conteúdo.

#### Etapa 6 — Instrumentar A2A isolado

- Incluir núcleo compartilhado e adapter local de persistência no build A2A, sem
  importar internals do principal.
- Compor fila/writer e configuração de Mongo no bootstrap/settings do A2A,
  aplicando a mesma política de continuidade sem spool.
- Observar executor, especialistas, HTTP de geração/embedding e fronteiras
  instrumentáveis de PostgreSQL/Qdrant/documentos.
- Separar estado de tarefa, resultado documental, timeout e cancelamento.

Aceite: testes A2A registram duração local e uso disponível; tarefa completed
com resultado unavailable não é classificada como resolução bem-sucedida.

#### Etapa 7 — Instrumentar o futuro cliente A2A

- Quando o cliente funcional existir, conectar o coletor à fronteira de chamada
  no slot `documental_a2a`, com nome/skill, duração, protocolo e resultado.
- Não criar propagação de IDs, joins ou duração calculada entre os processos.

Aceite: integração real demonstra a observação do cliente, independente da
observação do servidor. Esta etapa aguarda a dependência funcional separada.

#### Etapa 8 — Disponibilizar extração bruta

- Preparar conexão de leitura restrita à coleção e contrato de paginação por
  período/alteração. Preferir leitura direta pelo consumidor se suficiente.
- Se for necessário exportador, limitar sua função a selecionar e serializar
  registros brutos em formato acordado, sem agregação, join ou exclusão.
- Preservar tipos, UTC, valores ausentes, ID e versão de schema.

Aceite: extração retomável inclui términos tardios e deixa registros no Mongo; o
BI recebe os dados sem processamento de negócio na aplicação.

#### Etapa 9 — Homologar e integrar ao deployment/CI

- Executar unitários e integrações descartáveis da seção 11; incluir testes do
  pacote compartilhado e dependências MCP/Mailpit no job adequado.
- Validar operação com coleta ligada/desligada, memória limitada por processo,
  logs seguros, falha Mongo e encerramento.
- Conferir secrets apenas para credenciais, permissões do usuário já cadastrado,
  nomes de coleção configurados e índices sem TTL.
- Ajustar limites e orçamento com medições; não criar dashboards ou indicadores
  como parte da homologação.

Aceite: checks pertinentes passam; coleta não altera a resposta funcional nem
bloqueia prontidão por falha própria. Configuração e limitações ficam
documentadas.

## 11. Plano de testes

| Área             | Casos e aceite                                                                                                                                              |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tempo            | UTC, ns→ms, relógio civil ajustado, duração não negativa e nenhuma reconstrução entre documentos.                                                           |
| HTTP/autorização | 200, 401 JWT inválido/expirado, 403 operação negada, 409, 413, 422, 503, exceção e cancelamento; captura até último fragmento e rota segura.                |
| MCP              | Inicialização falha, isError, timeout, resposta grande, descoberta, páginas e lotes de propriedades; uma observação por chamada real.                       |
| Agente/tool      | Exceção, schema inválido, indisponível, simulado e sucesso técnico separado do domínio.                                                                     |
| Modelos          | Múltiplas chamadas, uso parcial/ausente, cache/reasoning preservados e falha de parsing sem perder uso já retornado.                                        |
| Memória          | Cada bloco/compactação; token_count local não vira faturamento; perfil/entrada excluídos.                                                                   |
| E-mail           | Recuperação fora de callback, unknown local versus retorno confirmado, autorização negada, replay/reinício sem novo envio.                                  |
| A2A              | Rejeição, JSON-RPC, completed com unavailable, timeout/cancelamento e Mixed; medições independentes.                                                        |
| Tentativas       | Nova execução versus retry de Mongo; revisão do Juiz e renovação de origem distintas; retries invisíveis desconhecidos.                                     |
| Duplicidade      | Confirmação incerta, lote parcial, replay fora de ordem, terminal não regride e conflito não sobrescreve.                                                   |
| Falhas           | Mongo offline, eleição, fila cheia, queda e shutdown; memória/retries limitados, ausência de spool, continuidade e pendências não confirmadas sinalizadas.  |
| Segurança        | Segredos aninhados, erro JSON, headers/DSNs/URLs, PII, seletores e envelopes; sem conteúdo completo ou estado protegido.                                    |
| Retenção         | Nenhum TTL/expires_at/purge por idade; registros antigos continuam disponíveis; extração não apaga nada.                                                    |
| Extração         | Timestamps iguais, término tardio, janela sobreposta, paginação, Decimal128/UTC e versões.                                                                  |
| Desempenho       | Comparar solicitações equivalentes com coleta ligada/desligada, durações individuais e consumo de memória, sem adicionar indicadores analíticos ao sistema. |

Meta inicial proposta: emissão local até 5 ms e acréscimo até 20 ms por
solicitação no cenário controlado, sem espera de rede Mongo. Validar com número
real de emissões e limites da fila em memória; não tratar esses limites como
SLA.

Reutilizar
[testes do observador](../apps/main_api/tests/unit/test_execution_observer.py),
[persistência Mongo](../apps/main_api/tests/integration/test_mongo_persistence.py),
[checkpoint](../apps/main_api/tests/integration/test_mongo_checkpoint_resume.py),
[cliente MCP](../apps/main_api/tests/unit/test_mcp_data_client.py),
[privacidade](../apps/main_api/tests/unit/test_privacy.py),
[consumidor de e-mail](../apps/main_api/tests/integration/test_email_consumer.py)
e [A2A](../apps/a2a_agent/tests/integration/test_a2a_runtime.py).

O consumidor de e-mail usa MCP real e Mailpit, com origem Fazenda assinada de
teste. Exige MAIN_API_MONGO_TEST_URI, MCP_EMAIL_RUN_INTEGRATION=1 e serviços
descartáveis. A CI atual configura apenas Mongo do principal nessa etapa;
ajustá-la para não declarar essa cobertura executada quando o teste for
ignorado. Também incluir explicitamente testes novos de packages/observability
na seleção da CI. Testes não usam bancos produtivos, SMTP externo nem modelos
pagos.

## 12. Pendências e limites

| Pendência                                      | Impacto                                                                               |
| ---------------------------------------------- | ------------------------------------------------------------------------------------- |
| Provedores/modelos/SDKs e categorias de uso    | Fecha captura por chamada e visibilidade de retries.                                  |
| Workload por tipo de solicitação               | Refina eventos, tokens, páginas e volume acumulado.                                   |
| Cluster/região/HA/backups Mongo                | Fecha custo crescente e permissões; Mongo e ausência de expiração já estão decididos. |
| Extração BI e políticas de cópias              | Formato, periodicidade, atrasos e retenção dos arquivos exportados/backups.           |
| Backend com farm_ids                           | Homologação funcional da identidade e do escopo.                                      |
| LotReportEnvelope real do MCP de Dados         | Integração completa do relatório; fixture não substitui produtor real.                |
| Cliente A2A real                               | Observação do fluxo principal→A2A.                                                    |
| Corpus e alterações                            | Consumo de embeddings/indexação.                                                      |
| Resolução e contexto amplo entre novas sessões | Requisitos separados; não inferir do status técnico.                                  |

Critério final: observações individuais corretas, protegidas e extraíveis do
MongoDB, sem expiração automática e sem processamento analítico implementado.
