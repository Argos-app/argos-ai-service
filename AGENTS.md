# Mapa do repositório e índice de agentes

## Propósito

O Argos AI Service reúne uma API multiagente para consultas de pecuária,
medicamentos, estimativa de consumo, documentos e relatórios. A Main API usa
LangChain e LangGraph; comunica-se por MCP com três serviços e por A2A com o
serviço documental. As cinco aplicações são processos independentes neste
monorepositório. O backend cadastral e o aplicativo cliente estão em outros
repositórios.

## Serviços principais

| Serviço         | Código atual                                                                     | Documentação                                                                         |
| --------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Main API        | FastAPI, identidade, sessões, orquestração, memória e integrações.               | [AGENTS.md](apps/main_api/AGENTS.md) · [README](apps/main_api/README.md)             |
| A2A             | Consulta de documentos e fontes legais com recuperação no Qdrant.                | [AGENTS.md](apps/a2a_agent/AGENTS.md) · [README](apps/a2a_agent/README.md)           |
| MCP de dados    | Consultas autorizadas de pecuária e medicamentos e origem assinada de relatório. | [AGENTS.md](apps/mcp_data/AGENTS.md) · [README](apps/mcp_data/README.md)             |
| MCP de e-mail   | Preparação, envio idempotente e consulta de estado de relatórios.                | [AGENTS.md](apps/mcp_email/AGENTS.md) · [README](apps/mcp_email/README.md)           |
| MCP de predição | Estimativa determinística de consumo histórico por escopo autorizado.            | [AGENTS.md](apps/mcp_prediction/AGENTS.md) · [README](apps/mcp_prediction/README.md) |

## Pacotes e infraestrutura

| Diretório                | Papel atual                                                  | Guia                                                                                                                          |
| ------------------------ | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `packages/contracts`     | Contratos Pydantic compartilhados.                           | [AGENTS.md](packages/contracts/AGENTS.md) · [README](packages/contracts/README.md)                                            |
| `packages/security`      | Autorização, delegação, assinaturas e adapters cadastrais.   | [AGENTS.md](packages/security/AGENTS.md) · [README](packages/security/README.md)                                              |
| `packages/observability` | Modelos, coleta e persistência compartilhada de observações. | [AGENTS.md](packages/observability/AGENTS.md) · [README](packages/observability/README.md)                                    |
| `infrastructure`         | Imagem PostgreSQL local, schema e dados sintéticos.          | [AGENTS.md](infrastructure/AGENTS.md) · [Docker](infrastructure/docker/AGENTS.md) · [Mongo](infrastructure/mongodb/AGENTS.md) |
| `k8s/local`              | Recursos para orquestração local.                            | [AGENTS.md](k8s/local/AGENTS.md) · [guia local](k8s/local/README.md)                                                          |
| `scripts`                | Inicialização, locks, dados locais e deploy Render.          | [AGENTS.md](scripts/AGENTS.md) · [Render](scripts/render/AGENTS.md)                                                           |
| `.github/workflows`      | CI, deploy e notificações.                                   | [AGENTS.md](.github/workflows/AGENTS.md)                                                                                      |
| `tests`                  | Testes transversais de planejamento e deploy.                | [AGENTS.md](tests/AGENTS.md)                                                                                                  |

A raiz contém [README.md](README.md), [CONTRIBUTING.md](CONTRIBUTING.md),
`compose.yaml` para a pilha integrada e `pyproject.toml` para ferramentas e
coleta de testes. A documentação da API está em
[contrato da API](apps/main_api/docs/api-contract.md) e os campos persistidos em
[estruturas Mongo](infrastructure/mongodb/collections.md).

## Orientações de desenvolvimento

1. Antes de alterar um componente, leia seu `AGENTS.md` e confirme no código e
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

As instruções do usuário e as instruções de sistema prevalecem sobre estes
guias.
