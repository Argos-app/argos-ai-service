# Índice da documentação

Os documentos abaixo descrevem o código e a operação atuais. Consulte também os
[guias dos componentes](../agent.md). Exemplos Mongo usam valores esquemáticos;
contratos e adapters são a fonte para campos e validações.

| Necessidade                          | Documento                                                                                                                                    |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| API HTTP e schemas                   | [API principal](api-main-api.md) · [OpenAPI](openapi-main-api.json)                                                                          |
| Identidade e autorização             | [Firebase e acesso](firebase-authentication-identity.md)                                                                                     |
| Grafo e ferramentas                  | [Fluxo dos agentes](agent-workflow.md)                                                                                                       |
| Sessões, memória e retomada          | [Persistência](session-memory.md)                                                                                                            |
| Configuração e documentos Mongo      | [Configuração](mongodb-configuration.md) · [Estruturas](mongodb-collection-structures.md) · [Dicionário BI](mongodb-analytics-dictionary.md) |
| Observabilidade, custos e exportação | [Operação](observability-operations.md) · [Extração Mongo](mongodb-observability-extraction.md)                                              |
| Infraestrutura local e implantação   | [Docker](docker-local.md) · [Kubernetes](kubernetes-local.md) · [Render](deploy-render.md)                                                   |

Os READMEs e `agent.md` dos serviços descrevem suas ferramentas, execução,
dependências e restrições. Os guias de integração dentro de `apps/` descrevem
fixtures e serviços descartáveis. Confira o código antes de alterar um contrato
ou procedimento e não inclua segredos ou dados de usuários nos exemplos.
