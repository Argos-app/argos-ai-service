# Resultado da homologação local integrada

**Data:** 14/10/2026  
**Branch:** `fix/general`

**Cluster:** Docker Desktop Kubernetes, contexto `docker-desktop`, Kubernetes v1.34.1
**Resultado:** etapa 11 parcialmente homologada; o aceite ponta a ponta permanece pendente.

## Resumo

O cluster local iniciou os dez workloads esperados: API principal, MCPs de dados,
predição e e-mail, agente A2A, MongoDB, PostgreSQL, Redis, Qdrant e Mailpit. Os
dez Pods ficaram `1/1 Running`; quatro PVCs (MongoDB, PostgreSQL, Qdrant e estado
A2A) ficaram `Bound`. A API retornou readiness e executou jornadas de consulta,
predição e A2A. Os resultados dos agentes continuam simulados e as respostas
públicas de consulta e predição são genéricas, sem demonstrar os dados ao
usuário.

A jornada de relatório não passou: Fazenda chamou `list_lots` e
`get_lot_report_source` com sucesso, mas a ferramenta
`resolve_report_lot_report_source` terminou em erro antes de persistir um
artefato. A API agora apresenta fallback seguro em vez de declarar sucesso. Não
foi criada preparação de e-mail e o Mailpit não recebeu mensagem. A verificação
isolada do lote e da assinatura do envelope passou; a causa da falha dentro da
execução da ferramenta ainda não foi localizada.

Redis recebeu os contadores do usuário sintético (`calls`, `model_calls` e
`unknown_token_calls`) com expiração observada de 86.177 segundos. O contador de
tokens permaneceu desconhecido porque o modelo simulado não fornece consumo de
tokens. Foi identificada e corrigida a ausência de `redis` na lock da API,
embora o build integral pelo Dockerfile com essa lock atualizada não tenha
terminado nesta rodada. Para executar os testes Kubernetes, a imagem local foi
atualizada a partir da imagem já construída, adicionando Redis e o código do
simulador; portanto, esse rollout não substitui a validação reprodutível do
Dockerfile.

As suítes Compose, MCP, A2A e API descritas abaixo já haviam passado em
homologações anteriores desta etapa. MongoDB e PostgreSQL foram reiniciados
nesta rodada sem remover PVCs: o replica set voltou como `PRIMARY` e manteve 175
observações; PostgreSQL voltou pronto e manteve `ready=true` e 240 lotes.

Não foram configuradas credenciais externas. Modelos reais, provider de e-mail
e LangSmith continuam pendentes para uma etapa posterior. O corpus Qdrant e as
collections de documentos empresariais estão vazios; a consulta A2A confirma o
transporte e a execução, não a recuperação de documentos de negócio.

## Verificações executadas

| Verificação | Resultado | Evidência e limite |
| --- | --- | --- |
| Testes focados do modelo simulado | Passou | `pytest apps/main_api/tests/unit/test_model_factory.py -q`: 19 passaram. |
| Ruff nos arquivos do simulador e testes | Passou | `ruff check` nos dois arquivos alterados. |
| Suíte unitária completa do CI | Passou em execução anterior | 646 passaram; 33 testes de integração foram excluídos pela marca `integration`. |
| Integração MCP de dados, predição e e-mail | Passou em execução anterior | 12, 4 e 2 testes, respectivamente; e-mail capturado pelo Mailpit de teste. |
| Integração A2A/Qdrant | Passou em execução anterior | 10 testes; timeout alinhado ao limite de 30 s. O corpus local da homologação Kubernetes está vazio. |
| Integração da API para preparação/envio de e-mail | Passou em execução anterior | 2 cenários do consumidor da API e testes de persistência/checkpoint passaram com adaptadores controlados. Isso não comprova o fluxo do modelo simulado no cluster. |
| Compose e imagens das aplicações | Passou em execução anterior | Os cinco serviços da aplicação foram construídos; Composes renderizaram e o perfil integrado ficou saudável. O build posterior à alteração de `pylock.toml` não foi concluído. |
| Kubernetes local | Passou para disponibilidade | 10 Pods `1/1 Running`; quatro PVCs `Bound`; `docker-desktop` Ready. |
| Persistência após reinício de MongoDB | Passou | Pod recriado sem apagar PVC; `rs.status().ok=1`; `ai_observations` permaneceu com 175 documentos. |
| Persistência após reinício de PostgreSQL | Passou | Pod recriado sem apagar PVC; `pg_isready` aceitou conexões; marcador `ready=true`; tabela `lot` permaneceu com 240 registros. |
| Jornada de consulta pela API em Kubernetes | Parcial | A chamada MCP de dados foi concluída; resposta do modo simulado não apresenta o conteúdo consultado. |
| Jornada de predição pela API em Kubernetes | Parcial | Chamada `estimate_medicine_consumption` concluiu em validação anterior; resposta simulada genérica não demonstra o resultado calculado. |
| Jornada A2A pela API em Kubernetes | Parcial | Tarefa A2A chegou a `completed`; não há corpus empresarial para validar o conteúdo recuperado. |
| Jornada de relatório e e-mail em Kubernetes | Não passou | MCP de dados respondeu, mas o agente Fazenda falhou antes de persistir `ai_report_artifacts`; `ai_email_preparations` e autorizações permaneceram sem registros; Mailpit recebeu zero mensagens. |
| Fallback após falha da ferramenta | Passou | A última chamada de relatório retornou “Não tenho informação suficiente para responder com segurança agora.”, sem alegar envio ou consulta concluída. |
| Contadores por usuário no Redis | Passou parcialmente | Chave do usuário sintético criada; chamadas e invocações de modelo incrementadas; TTL observado de 86.177 s. Tokens não foram reportados pelo modelo simulado. |
| Build completo da API com lock atualizada | Não concluído | A tentativa de instalar toda a lock sofreu interrupções de download e foi interrompida; imagem de teste usou camada temporária baseada na imagem já construída. |

## Estado do aceite

O cluster local, os serviços principais, a prontidão das dependências e a
persistência de MongoDB/PostgreSQL após reinício foram validados. A etapa 11
continua **incompleta** porque consulta e predição não demonstram os dados
simulados ao usuário, não há corpus para a jornada documental, o fluxo de
relatório falha antes da Secretaria e nenhum e-mail foi preparado ou capturado.
Também falta concluir o build integral da imagem da API com a lock atualizada.

## Próximas validações

1. Localizar a exceção da ferramenta de Fazenda depois das chamadas MCP e antes
   da persistência de `ai_report_artifacts`; validar depois o fluxo
   Fazenda → Secretaria → MCP de e-mail → Mailpit.
2. Ajustar o modo simulado para apresentar os resultados MCP/predição de forma
   verificável, sem fabricar dados; adicionar fixtures documentais se a jornada
   A2A com conteúdo fizer parte do aceite local.
3. Concluir o build com `apps/main_api/Dockerfile` e `apps/main_api/pylock.toml`
   atualizados, executar os fluxos na imagem resultante e verificar Compose e
   Kubernetes novamente.
4. Só então marcar o aceite da etapa 11 como concluído. Providers reais de
   modelo/e-mail e LangSmith permanecem fora desta validação sem credenciais.
