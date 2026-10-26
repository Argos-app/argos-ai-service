# Resultado da homologação local integrada

**Data:** 15/10/2026 **Branch:** `fix/general`

**Ambientes:** Docker Compose integrado e Docker Desktop Kubernetes, contexto
`docker-desktop`, Kubernetes v1.34.1 **Resultado:** etapa 11 homologada com
dependências locais e provedores simulados. As jornadas de consulta, predição,
recuperação A2A e envio ao Mailpit passaram nos dois ambientes. Conteúdo
empresarial e providers externos reais permanecem para validação posterior.

## Resumo

O cluster local iniciou os dez workloads esperados: API principal, MCPs de
dados, predição e e-mail, agente A2A, MongoDB, PostgreSQL, Redis, Qdrant e
Mailpit. Os dez Pods ficaram `1/1 Running`; quatro PVCs (MongoDB, PostgreSQL,
Qdrant e estado A2A) ficaram `Bound`. A API retornou readiness e executou
jornadas de consulta, predição, recuperação A2A e envio de relatório.

Nesta rodada, o Docker Compose renderizou a configuração, construiu sem cache as
cinco imagens de aplicação e instalou as dependências das cinco locks. Os dez
serviços ficaram saudáveis; a API retornou `live=200` e `ready=200`. As quatro
jornadas integradas foram executadas pela API do Compose e repetidas no cluster
Kubernetes durante a homologação anterior.

A falha inicial da jornada de relatório estava na construção lazy do grafo: o
verificador da assinatura confiável do MCP de dados existia no estado da API,
mas não era injetado na factory dos agentes. Fazenda recebia
`signature_checker=None` e falhava após `list_lots` e `get_lot_report_source`. A
dependência foi conectada e uma regressão unitária foi adicionada. A retomada de
sessão também consultava um namespace de checkpoint diferente do namespace raiz
gravado pelo LangGraph; a configuração foi uniformizada para o namespace raiz.

Após as correções, a jornada completa passou no cluster: Fazenda consultou os
dois MCPs de dados, Secretaria chamou `prepare_report`, o runtime persistiu a
autorização explícita, `send_report` chamou o MCP de e-mail e o Mailpit recebeu
uma mensagem. A observabilidade registrou as operações sob o mesmo
`correlation_id`; o MCP retornou `accepted`. Isso comprova captura local no
Mailpit, não entrega a uma caixa de entrada externa.

O modo simulado foi ajustado para encadear `send_report` apenas quando a
preparação retorna `prepared` e o pedido de origem contém intenção explícita de
envio. Pedidos apenas de preparação e destinatários não resolvidos não disparam
envio. O exemplo de data usado pela predição também passou a usar a data local
consumida pela validação do contrato, evitando falha de teste na virada UTC.

O modo simulado da API apresenta o conteúdo real retornado pelas ferramentas,
com minimização de dados pessoais. A predição pela API retornou a cobertura
histórica e os itens calculados pelo MCP, sem inventar campos ausentes. A
consulta documental passou pela API, MCP A2A, sincronização de corpus, permissão
PostgreSQL e busca Qdrant e retornou o trecho da fixture com fonte, página,
revisão e citação. O texto gerado continua explicitamente ilustrativo.

O A2A local não conseguia persistir o manifesto no PVC, criado `root:root` com
permissão `755` enquanto o serviço roda como UID `10001`. O Deployment agora
prepara o volume com init container restrito antes de iniciar o processo sem
root. Depois disso, a indexação persistiu dois PDFs sintéticos. A primeira
consulta ainda falhava porque o provider simulado listava IDs de evidência sem
marcá-los no texto; o validador rejeitava a resposta. O provider agora inclui
somente os marcadores dos IDs recebidos e a API retornou uma resposta citada.

Redis recebeu os contadores do usuário sintético (`calls`, `model_calls` e
`unknown_token_calls`) com expiração observada de 86.177 segundos. O contador de
tokens permaneceu desconhecido porque o modelo simulado não fornece consumo de
tokens. A imagem da API foi construída pelo Dockerfile com a lock atualizada e
implantada como `argos-main-api:stage11-mailpit-fix`; a camada de instalação da
lock já havia sido concluída em build integral anterior e foi reutilizada no
build final. O rollout e readiness passaram.

As suítes Compose, MCP, A2A e API descritas abaixo já haviam passado em
homologações anteriores desta etapa. MongoDB e PostgreSQL foram reiniciados
nesta rodada sem remover PVCs: o replica set voltou como `PRIMARY` e manteve 175
observações; PostgreSQL voltou pronto e manteve `ready=true` e 240 lotes.

Não foram configuradas credenciais externas. Modelos reais, provider de e-mail e
LangSmith continuam pendentes para uma etapa posterior. Os dois documentos
indexados são fixtures sintéticas de demonstração, não conteúdo empresarial ou
jurídico; por isso a recuperação, as permissões, citações e persistência foram
validadas, mas a qualidade da resposta sobre documentos reais não foi.

## Verificações executadas

| Verificação                                                               | Resultado                     | Evidência e limite                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------- | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Testes focados de API, grafo/sessão e contratos de relatório              | Passou                        | 27 testes passaram em execução fora do sandbox de rede; a execução no sandbox ficou bloqueada nos sockets asyncio do Windows.                                                                                                                                                             |
| Testes do provider simulado A2A                                           | Passou                        | 2 testes passaram; o provider agora garante que marcadores textuais e IDs de evidência correspondam.                                                                                                                                                                                      |
| Integração A2A/Qdrant via pytest                                          | Parcial                       | 6 testes foram pulados porque `A2A_RUN_INTEGRATION=1` e o Qdrant descartável do Compose não estavam configurados; a jornada foi validada pelo Qdrant integrado no Compose e no cluster Kubernetes.                                                                                        |
| Ruff e compilação Python                                                  | Passou                        | Ruff nos arquivos Python modificados do A2A; `compileall` no pacote A2A.                                                                                                                                                                                                                  |
| Suíte unitária completa do CI                                             | Passou em execução anterior   | 646 passaram; 33 testes de integração foram excluídos pela marca `integration`.                                                                                                                                                                                                           |
| Integração MCP de dados, predição e e-mail                                | Passou em execução anterior   | 12, 4 e 2 testes, respectivamente; e-mail capturado pelo Mailpit de teste.                                                                                                                                                                                                                |
| Integração A2A/Qdrant                                                     | Passou em execução anterior   | 10 testes; timeout alinhado ao limite de 30 s. Nesta rodada, a consulta real ao Deployment Kubernetes também recuperou a fixture sintética.                                                                                                                                               |
| Integração da API para preparação/envio de e-mail                         | Passou                        | Testes do consumidor com adaptadores controlados e a jornada real simulada no Compose e Kubernetes; Mailpit capturou as mensagens.                                                                                                                                                        |
| Docker Compose e build das aplicações                                     | Passou                        | `config --quiet`; `build --no-cache` instalou as locks e construiu `main_api`, `mcp_data`, `mcp_prediction`, `mcp_email` e `a2a_agent`; `up -d` deixou os dez serviços saudáveis, com `live=200` e `ready=200` na API.                                                                    |
| Kubernetes local                                                          | Passou para disponibilidade   | 10 Pods `1/1 Running`; quatro PVCs `Bound`; `docker-desktop` Ready.                                                                                                                                                                                                                       |
| Persistência após reinício de MongoDB                                     | Passou                        | Pod recriado sem apagar PVC; `rs.status().ok=1`; `ai_observations` permaneceu com 175 documentos.                                                                                                                                                                                         |
| Persistência após reinício de PostgreSQL                                  | Passou                        | Pod recriado sem apagar PVC; `pg_isready` aceitou conexões; marcador `ready=true`; tabela `lot` permaneceu com 240 registros.                                                                                                                                                             |
| Jornada de consulta pela API nos dois ambientes                           | Passou com dados sintéticos   | `list_properties` retornou registros do dataload local; a resposta incluiu o resultado MCP com identificadores sensíveis minimizados.                                                                                                                                                     |
| Jornada de predição pela API nos dois ambientes                           | Passou com dados sintéticos   | `estimate_medicine_consumption` retornou cobertura confirmada, janela e itens previstos; a resposta do simulador foi limitada a 2.000 caracteres e indicou o truncamento.                                                                                                                 |
| Jornada A2A pela API nos dois ambientes                                   | Passou com fixture documental | Retornou `status=success`, citação `[1]`, origem, página, revisão e trecho dos termos sintéticos. No Kubernetes, o manifesto persistiu no PVC; no Compose, no volume nomeado.                                                                                                             |
| Jornada Fazenda → Secretaria → MCP de e-mail → Mailpit nos dois ambientes | Passou                        | Despacho `accepted`; Mailpit capturou 1 mensagem nesta execução Compose e a contagem Kubernetes passou de 1 para 2 na execução anterior. A aceitação não prova entrega externa.                                                                                                           |
| Fallback após falha da ferramenta                                         | Passou em testes              | A suíte focada confirmou que falhas de ferramenta não são apresentadas como resultado verificado; a jornada de relatório desta rodada concluiu com sucesso.                                                                                                                               |
| Contadores por usuário no Redis                                           | Passou parcialmente           | Chave do usuário sintético criada; chamadas e invocações de modelo incrementadas; TTL observado de 86.177 s. Tokens não foram reportados pelo modelo simulado.                                                                                                                            |
| Build completo com locks atualizadas                                      | Passou                        | Registro histórico da execução de 15/10, anterior à unificação da configuração: `docker compose --parallel 1 --env-file .env.local --profile apps build --no-cache`; as cinco instalações por `pylock.toml` terminaram com sucesso. O comando atual usa o `.env` da raiz automaticamente. |

## Estado do aceite

Docker Compose e Kubernetes local, a prontidão das dependências, a persistência
após reinício e as jornadas de consulta, predição, A2A e relatório/e-mail foram
validados com dados locais sintéticos. Os bloqueadores internos da etapa 11 —
ferramenta Fazenda, envio ao Mailpit, build reproduzível das cinco imagens,
persistência do manifesto A2A e geração simulada com citações — estão
resolvidos. A etapa 11 está concluída no escopo de homologação sem credenciais
externas. A qualidade com conteúdo empresarial e providers reais pertence à
validação posterior de integrações externas.

## Próximas validações

1. Configurar e validar providers reais de modelo, e-mail e LangSmith na etapa
   de integrações externas, usando credenciais aprovadas e os guias
   operacionais.
2. Executar as jornadas documentais com corpus empresarial/jurídico aprovado
   para validar relevância, cobertura e qualidade sobre conteúdo real.
