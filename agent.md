# Mapa do repositório e índice de agentes

## Propósito

O Argos AI Service reúne uma API multiagente para consultas de pecuária,
medicamentos, estimativa de consumo, documentos e relatórios. A Main API usa
LangChain e LangGraph; comunica-se por MCP com três serviços e por A2A com o
serviço documental. As cinco aplicações são processos independentes neste
monorepositório. O backend cadastral e o aplicativo cliente estão em outros
repositórios.

## Serviços principais

| Serviço         | Código atual                                                                     | Documentação                                                                       |
| --------------- | -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Main API        | FastAPI, identidade, sessões, orquestração, memória e integrações.               | [agent.md](apps/main_api/agent.md) · [README](apps/main_api/README.md)             |
| A2A             | Consulta de documentos e fontes legais com recuperação no Qdrant.                | [agent.md](apps/a2a_agent/agent.md) · [README](apps/a2a_agent/README.md)           |
| MCP de dados    | Consultas autorizadas de pecuária e medicamentos e origem assinada de relatório. | [agent.md](apps/mcp_data/agent.md) · [README](apps/mcp_data/README.md)             |
| MCP de e-mail   | Preparação, envio idempotente e consulta de estado de relatórios.                | [agent.md](apps/mcp_email/agent.md) · [README](apps/mcp_email/README.md)           |
| MCP de predição | Estimativa determinística de consumo histórico por escopo autorizado.            | [agent.md](apps/mcp_prediction/agent.md) · [README](apps/mcp_prediction/README.md) |

## Pacotes e infraestrutura

| Diretório                | Papel atual                                                     | Guia                                                                                     |
| ------------------------ | --------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `packages/contracts`     | Contratos Pydantic compartilhados.                              | [agent.md](packages/contracts/agent.md) · [README](packages/contracts/README.md)         |
| `packages/security`      | Autorização, delegação, assinaturas e adapters cadastrais.      | [agent.md](packages/security/agent.md) · [README](packages/security/README.md)           |
| `packages/observability` | Modelos, coleta e persistência compartilhada de observações.    | [agent.md](packages/observability/agent.md) · [README](packages/observability/README.md) |
| `infrastructure`         | Imagem PostgreSQL local, schema e dados sintéticos.             | [agent.md](infrastructure/agent.md)                                                      |
| `k8s/local`              | Recursos para orquestração local.                               | [agent.md](k8s/agent.md) · [guia local](docs/kubernetes-local.md)                        |
| `scripts`                | Inicialização, locks, dados locais e deploy Render.             | [agent.md](scripts/agent.md)                                                             |
| `.github/workflows`      | CI, deploy e notificações.                                      | [agent.md](.github/agent.md)                                                             |
| `tests`                  | Testes transversais de planejamento e deploy.                   | [agent.md](tests/agent.md)                                                               |
| `docs`                   | Contratos e guias operacionais; há também registros históricos. | [agent.md](docs/agent.md)                                                                |

A raiz contém [README.md](README.md), [CONTRIBUTING.md](CONTRIBUTING.md),
`compose.yaml` para a pilha integrada e `pyproject.toml` para ferramentas e
coleta de testes. A documentação da API está em
[docs/api-main-api.md](docs/api-main-api.md) e os campos persistidos em
[docs/mongodb-collection-structures.md](docs/mongodb-collection-structures.md).

## Orientações de desenvolvimento

1. Antes de alterar um componente, leia seu `agent.md` e confirme no código e
   nos testes os detalhes relevantes. Planejamentos antigos não substituem o
   comportamento implementado.
2. Preserve as fronteiras e o estilo existentes no diretório. Ao mudar
   `packages`, revise consumidores, imagens e contratos públicos ou persistidos.
3. Não trate entrada do usuário ou saída do modelo como autorização. Preserve
   validação de identidade, escopo, delegação, assinatura, idempotência e
   privacidade aplicáveis ao fluxo alterado.
4. Use Python 3.14.3 e as instruções de [CONTRIBUTING.md](CONTRIBUTING.md). A CI
   executa `python -m pytest tests apps packages -m "not integration"`,
   verificações de qualidade, integrações com recursos descartáveis e builds.
   Cada README de integração descreve a infraestrutura e as variáveis exigidas.
5. Siga Conventional Commits e atualize documentação, exemplos e testes quando o
   contrato ou a configuração efetivamente mudar. Não exponha segredos em
   arquivos versionados ou logs.
