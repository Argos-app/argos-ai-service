# Resultado da homologação local integrada

**Data:** 14/10/2026  
**Branch:** `fix/general`  
**Base:** etapa 10 registrada em `300782a`  
**Resultado:** homologação Docker **parcial; aceite da etapa 11 pendente**.

## Resumo

As verificações sem serviços externos passaram: suíte unitária, lint,
formatação, validação dos sete arquivos Compose e renderização dos manifests
Kubernetes. Nesta tentativa, o engine Docker ficou acessível com elevação local:
as cinco imagens foram construídas, a pilha integrada subiu e todos os
containers ficaram saudáveis. MongoDB e PostgreSQL foram confirmados operantes;
o dataload do PostgreSQL apresentou 31 tabelas, 30 propriedades e 66 usuários.

As suítes isoladas de MCP de dados, predição e e-mail passaram. Após as
correções desta rodada, os dois cenários do consumidor de e-mail da API
passaram, a suíte integrada A2A passou (10/10) e a chamada da ferramenta MCP de
predição pela API foi registrada como bem-sucedida. A resposta final do modelo
simulado continua genérica e não apresenta os dados da predição; a execução
bem-sucedida da ferramenta não valida a qualidade semântica da resposta.
Portanto, containers saudáveis e interações HTTP 200 não comprovam por si só
todos os fluxos de negócio. O aceite da etapa 11 continua pendente pelas
verificações listadas abaixo.

O Kubernetes local continua sem validação: o contexto `docker-desktop` existe no
`kubectl`, mas o servidor `kubernetes.docker.internal:6443` não resolve.

Não foram configuradas nem usadas credenciais externas. A execução planejada
continua sendo `MAIN_API_MODEL_MODE=simulated`, `A2A_MODEL_MODE=simulated` e
Mailpit. A validação de providers reais de modelos, e-mail e LangSmith fica para
uma etapa posterior e não é necessária para este aceite interno.

## Verificações executadas

| Verificação                                                                       | Resultado                     | Observação                                                                                                                                                                                                                                                                                                                                                                               |
| --------------------------------------------------------------------------------- | ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Coleta de testes pela raiz                                                        | Passou                        | 679 itens coletados.                                                                                                                                                                                                                                                                                                                                                                     |
| Suíte unitária conforme o CI                                                      | Passou                        | `pytest apps packages -m "not integration" -q --junitxml=reports/pytest.xml --cov=apps --cov=packages --cov-report=xml:reports/coverage.xml`: 646 passaram, 33 foram excluídos pela marca `integration`.                                                                                                                                                                                 |
| Suíte marcada `integration`, sem serviços/flags                                   | Ignorada conforme previsto    | 33 testes ignorados porque os serviços Compose e flags de opt-in não estavam disponíveis. Não é evidência de integração aprovada.                                                                                                                                                                                                                                                        |
| `ruff check .`                                                                    | Passou após correções         | Ordenação de imports e linha longa corrigidas.                                                                                                                                                                                                                                                                                                                                           |
| `ruff format --check .`                                                           | Passou após correções         | Quatro arquivos previamente fora do formato foram formatados.                                                                                                                                                                                                                                                                                                                            |
| `docker compose config --quiet` para a pilha integrada e os sete arquivos Compose | Passou                        | Validou a configuração, sem iniciar serviços.                                                                                                                                                                                                                                                                                                                                            |
| Build das cinco imagens da aplicação                                              | Passou                        | `docker compose --env-file .env.local --profile apps --file compose.yaml up --build -d --wait` construiu `main_api`, `mcp_data`, `mcp_email`, `mcp_prediction` e `a2a_agent`.                                                                                                                                                                                                            |
| Health dos serviços integrados                                                    | Passou                        | API `GET /health/ready` retornou HTTP 200; A2A autenticado retornou `{"status":"ready"}`; todos os serviços do perfil `apps` estavam `healthy`.                                                                                                                                                                                                                                          |
| MongoDB e PostgreSQL integrados                                                   | Parcial                       | Mongo replica set retornou estado `PRIMARY` (1); PostgreSQL tinha 31 tabelas, 30 `property` e 66 `app_user`. Os containers/volumes já existiam antes desta tentativa; não foi validada inicialização do banco em volume limpo.                                                                                                                                                           |
| Integração MCP de dados                                                           | Passou                        | `pytest apps/mcp_data/tests/integration -v`: 12 testes passaram contra PostgreSQL e Mongo descartáveis.                                                                                                                                                                                                                                                                                  |
| Integração MCP de predição                                                        | Passou                        | `pytest apps/mcp_prediction/tests/integration -v`: 4 testes passaram contra PostgreSQL descartável.                                                                                                                                                                                                                                                                                      |
| Integração MCP de e-mail                                                          | Passou                        | `pytest apps/mcp_email/tests/integration -v -p no:cacheprovider`: 2 testes passaram; SMTP foi capturado pelo Mailpit.                                                                                                                                                                                                                                                                    |
| Integração A2A/Qdrant                                                             | Passou                        | 10 testes passaram contra Qdrant 1.15.4 sem definir `A2A_PERMISSION_SQL_DSN`; o harness injeta o adaptador de permissões. O cliente foi alinhado a 1.15.1, sem aviso de incompatibilidade. A chamada de consulta detalhada/agregada passou com timeout de cliente compatível com o prazo de 30 s da tarefa. Permanecem avisos esperados pelo uso de HTTP com API keys no ambiente local. |
| Integração Mongo da API principal                                                 | Passou nos cenários de e-mail | 3 testes de persistência, checkpoint e writer e os 2 cenários de preparo/envio passaram. O adaptador falso de Farm agora registra a evidência MCP que o grafo exige antes de encaminhar ao agente `secretary`; os 2 testes do MCP de e-mail/Mailpit também passaram.                                                                                                                     |
| Jornada integrada de consulta pela API                                            | Parcial                       | Requisição autenticada pelo stub local retornou resposta; observabilidade confirmou `mcp.data.call_tool.list_properties` com sucesso. A resposta simulada não apresenta os dados consultados.                                                                                                                                                                                            |
| Jornada integrada de predição pela API                                            | Parcial                       | A observação de `mcp.prediction.call_tool.estimate_medicine_consumption` terminou `succeeded` após o modelo simulado passar `as_of` (dia completo anterior) e `horizon_days` conforme o contrato MCP. A resposta gerada pelo modelo simulado é genérica e não demonstra que o resultado calculado chegou ao usuário.                                                                     |
| `kubectl kustomize k8s/local`                                                     | Passou                        | Renderizou 31 recursos com o Secret local existente; não submeteu os recursos a um API server Kubernetes.                                                                                                                                                                                                                                                                                |
| `docker info` / status do Docker Desktop                                          | Passou com elevação           | Acesso comum retornou permissão negada ao named pipe; a verificação com elevação local encontrou Docker Desktop 29.1.3.                                                                                                                                                                                                                                                                  |
| `kubectl get nodes`                                                               | Bloqueado                     | API Kubernetes indisponível; falha de resolução de `kubernetes.docker.internal`.                                                                                                                                                                                                                                                                                                         |

Durante a execução, duas falhas na suíte unitária foram corrigidas: o teste de
privacidade fornecia um DTO HTTP a um caso de uso que recebe `MessageCommand`, e
o runtime falso do teste de readiness não declarava `user_usage_counter`. Os
testes correspondentes passaram isoladamente e na execução completa subsequente.

## Jornadas e aceite

| Jornada/critério                           | Estado nesta homologação | Evidência ou bloqueio                                                                                                                                                                    |
| ------------------------------------------ | ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Consulta ao MCP de dados                   | Parcial                  | A integração isolada passou e a API alcançou `list_properties`; a resposta da API foi genérica e não expôs os dados sintéticos.                                                          |
| Predição                                   | Parcial                  | A chamada à ferramenta MCP pela API passou após corrigir o payload simulado. A resposta final do modo simulado não reproduz o resultado calculado.                                       |
| Interação, isolamento de sessão e acesso   | Parcial                  | A API criou sessão/interação com stub local; isolamento de sessões pela pilha completa não foi validado nesta chamada.                                                                   |
| Relatório e envio                          | Parcial                  | Os testes de preparo e envio da API passaram com MCP de e-mail/Mailpit. Ainda não foi feita uma jornada manual ponta a ponta com conteúdo de relatório validado pelo usuário.            |
| Falhas de dependência e recuperação        | Parcial                  | Comportamentos unitários passaram; indisponibilidade e recuperação real de serviços não foram exercitadas.                                                                               |
| Migrações/dataload em ambiente descartável | Não verificado           | Exige PostgreSQL em container e volume limpo; nenhum SQL foi aplicado nesta rodada.                                                                                                      |
| Reinício de Pods e preservação de dados    | Não verificado           | Nenhum cluster Kubernetes alcançável.                                                                                                                                                    |
| Preparação do zero seguindo os guias       | Parcial                  | A pilha integrada foi construída e iniciada com Docker Desktop acessível; MongoDB/PostgreSQL usaram containers e volumes existentes, sem comprovar bootstrap a partir de volumes vazios. |
| Uso de credenciais externas                | Fora desta etapa         | Modelos simulados e Mailpit devem ser usados; provider real permanece pendente.                                                                                                          |

O aceite da etapa 11 **não está concluído**: ainda faltam validar o bootstrap
com volumes vazios, a semântica das respostas simuladas e a execução no cluster
Kubernetes local.

## Próxima validação

Verificar a qualidade semântica da resposta simulada de predição e completar a
jornada de consulta com retorno dos dados ao usuário. Depois, habilitar um
cluster local (`docker-desktop` ou Kind), aplicar `kubectl apply -k k8s/local`,
confirmar readiness e repetir o teste de reinício dos Pods de Mongo, PostgreSQL,
Qdrant e A2A descrito no [guia Kubernetes local](kubernetes-local.md). Registrar
resultados antes de marcar o aceite como concluído.
