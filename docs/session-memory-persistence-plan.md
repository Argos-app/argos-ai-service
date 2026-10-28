# Plano de persistência de sessões, contexto e memória do usuário

Este documento registra as decisões, arquitetura e critérios da persistência de
sessões. As etapas 1 a 8 foram implementadas; a etapa 9 integra documentação,
testes Mongo descartáveis e CI. O código correspondente está em `apps/main_api`.

## 1. Decisões consolidadas e diagnóstico

| Tema                   | Decisão para esta entrega                                                                                                                                                                                                                                  |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Persistência           | Mongo técnico, acessado diretamente pelo `main_api`, para sessões, interações, mensagens, eventos, resumos, memória e checkpoints. Essa interpretação abrange a funcionalidade de persistência solicitada; não modifica armazenamento dos demais serviços. |
| Contagem               | Cada 15 entradas únicas do usuário, incluindo esclarecimentos e respostas estruturadas válidas. Respostas do assistente, agentes, tools e retries não incrementam o contador.                                                                              |
| Conteúdo               | Armazenar conteúdo sanitizado. Não criar arquivo paralelo de mensagens originais.                                                                                                                                                                          |
| Memória entre sessões  | Somente tom de comunicação e objetivo geral declarado pelo usuário. Não incluir preferência de nome, idioma, formato ou outros dados pessoais nesta entrega.                                                                                               |
| Atualização da memória | Automática, sem exigir pedido para lembrar nem confirmação adicional. Uma declaração do usuário pode atualizar o perfil. Não inferir perfil a partir do comportamento, de perguntas isoladas, de textos citados ou de resultados de tools.                 |
| Retenção               | Sessões e memória independente não expiram automaticamente. Para esta proposta, histórico, resumos e eventos vinculados também não têm exclusão automática por prazo.                                                                                      |
| Resumo de sessão       | Preservar atividade em andamento e últimos dados importantes, com teto de 2.000 tokens. Modelo específico será configurado depois.                                                                                                                         |
| Histórico público      | Rota paginada implementada; mensagens do assistente incluem o estado de degradação persistido quando o conteúdo está visível.                                                                                                                              |
| Retomada               | Carregar contexto e estado consistente quando o usuário enviar outra entrada pela rota existente de interações. Não criar rota de recuperação, fila, worker ou retomada automática de processamento interrompido.                                          |
| Concorrência           | Lease temporário por sessão para a função interna do main_api que processa a requisição. O vencimento libera o bloqueio; não dispara execução automática.                                                                                                  |
| Autenticação           | Firebase ID token trocado por JWT próprio; cadastro, permissão e fazendas são validados no PostgreSQL a cada chamada protegida. O stub existe somente em `APP_ENV=local` e `AUTH_MODE=stub`.                                                               |

As decisões do usuário substituem, neste escopo, as recomendações anteriores de
PostgreSQL técnico, TTL de sessão/memória e confirmação para persistir
preferências. Elas não removem validação de titularidade, sanitização,
idempotência ou isolamento por usuário.

### Fontes consultadas

Os diagnósticos e propostas foram atualizados para refletir a implementação
atual. As referências `argos-agent-system-plan.md` e outros planos externos
continuam sendo fontes de requisitos, não descrições da implementação vigente.

Todos os links abaixo apontam para arquivos existentes. Caminhos apresentados
posteriormente na árvore de organização são propostas de novos componentes.

| Fonte                                                                                                                                                                                                                                                                                                                                                                                           | Evidência e consequência                                                                                                                                                                          |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [README](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/README.md)                                                                                                                                                                                                                                                                                                     | O principal usa FastAPI, LangGraph, LangChain e Protocol + Factory + Registry. Documenta persistência Mongo, contexto, memória limitada, histórico, autenticação Firebase, configuração e testes. |
| [Planejamento: contratos públicos](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/docs/argos-agent-system-plan.md:480)                                                                                                                                                                                                                                                 | As três operações atuais são criar sessão, listar sessões e enviar interação. A nova consulta de mensagens é uma ampliação autorizada pelo usuário.                                               |
| [Planejamento: persistência técnica](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/docs/argos-agent-system-plan.md:779)                                                                                                                                                                                                                                               | Separação entre checkpoint e memória independente; armazenamento técnico separado de dados de negócio. PostgreSQL e expiração eram propostas anteriores, agora substituídas neste escopo.         |
| [Planejamento: PLN-09](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/docs/argos-agent-system-plan.md:1710) e [PLN-31](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/docs/argos-agent-system-plan.md:1798)                                                                                                                                   | Sessões duráveis, ownership, retomada e isolamento entre sessões. Os critérios precisam ser alinhados a Mongo, atualização automática e ausência de expiração.                                    |
| [Contratos de sessões](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/app/api/v1/models/sessions.py) e [interações](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/app/api/v1/models/interactions.py)                                                                                                             | Já existem UUIDs, `client_message_id`, variantes de mensagens/respostas e perguntas com `expires_at`. Reutilizar os contratos de escrita.                                                         |
| [Serviços](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/app/api/v1/dependencies/services.py) e [autenticação](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/app/api/v1/dependencies/auth.py)                                                                                                                   | As rotas usam serviços concretos de sessão e histórico. Em produção, o JWT próprio é validado e o Principal é reconstruído com cadastro e fazendas atuais; o stub fica restrito ao modo local.    |
| [Estado](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/app/graph/state.py) e [workflow](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/app/graph/workflow.py)                                                                                                                                                    | O grafo recebe contexto resumido e mensagens recentes, grava checkpoints e eventos de execução, e mantém perguntas pendentes para retomada após nova entrada do usuário.                          |
| [Factory LangChain](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/app/infrastructure/agents/langchain_factory.py) e [schemas de entrada](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/app/agent_schemas/inputs.py)                                                                                             | O adaptador fornece contexto conversacional limitado, mantém respostas estruturadas e propaga callbacks de uso de modelo; mensagens e eventos são persistidos de forma sanitizada.                |
| [Handoff](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/docs/agent-integration-handoff.md)                                                                                                                                                                                                                                                                            | Define integração por Factory, slots de ferramentas, sessão validada e callbacks. Preservar esses pontos de extensão.                                                                             |
| [Adapter Mongo de envio](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/mcp_email/src/argos_mcp_email/adapters/storage/mongo_dispatch.py)                                                                                                                                                                                                                         | Referência existente para collection injetada, índice único, tratamento de duplicação e claim atômico. O claim de envio não é uma implementação pronta de lease recuperável.                      |
| [Testes de rotas](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/tests/unit/test_routes.py), [grafo](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/tests/unit/test_graph.py) e [Factory](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/tests/unit/test_agent_factory.py) | Base de contratos e retomada com fakes/MemorySaver; não comprovam durabilidade Mongo.                                                                                                             |
| [Dependências](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/requirements.txt), [pyproject](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/pyproject.toml) e [CI](C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/.github/workflows/ci.yml)                                                            | Python 3.14.3 e dependências de FastAPI/LangChain/LangGraph. A CI agora tem job Mongo separado com relatório JUnit; o Compose usa replica set local descartável.                                  |

### Estado atual dos objetivos do plano

Os pontos abaixo eram lacunas no diagnóstico original e estão implementados no
código atual:

1. O contexto do agente inclui resumo e uma janela limitada de mensagens
   anteriores; o perfil entre sessões permanece restrito ao tom e ao objetivo
   declarado.
2. Chamadas de agentes e ferramentas são persistidas como eventos sanitizados,
   com estados, correlação e tentativas.
3. Callbacks de uso são enviados pelo adaptador de modelos.
4. A identidade da API contém várias fazendas autorizadas; as ferramentas
   recebem somente o escopo assinado e necessário à chamada.
5. Mensagens, resultados, resumos e eventos passam pelas rotinas de sanitização
   antes de persistir ou retornar ao cliente.
6. A continuidade exige nova requisição do usuário. Leases e fencing protegem
   contra processamento concorrente; a perda do lease cancela a execução atual e
   impede a finalização tardia.

## 2. Arquitetura e organização

### Componentes e responsabilidade

Preservar `apps/main_api/app`, sem migrar o principal para `src/argos_main`
nesta entrega. Domínio e aplicação permanecem independentes de FastAPI, Mongo e
tipos do SDK; infraestrutura implementa as portas.

Raiz dos caminhos propostos:
`C:/Users/matheusfazan-ieg/Programas/Argos/Repositorios/argos-ai-service/apps/main_api/`.

```text
app/
  domain/
    sessions.py                    # sessão, interação, mensagem e status
    memory.py                      # resumo de sessão e perfil restrito
    errors.py
  application/
    session_service.py             # implementação do Protocol existente
    process_interaction.py         # processamento interno da requisição
    context_builder.py
    summarize_session.py
    update_user_memory.py
    read_session_messages.py
  ports/
    session_repository.py
    conversation_repository.py
    interaction_repository.py
    execution_event_sink.py
    session_summarizer.py
    user_memory_repository.py
  infrastructure/
    persistence/
      mongo_sessions.py
      mongo_interactions.py
      mongo_messages.py
      mongo_events.py
      mongo_summaries.py
      mongo_user_memory.py
      indexes.py
    checkpoints/
      mongo.py                     # composição do checkpointer validado
    agents/
      langchain_factory.py         # existente: contexto + instrumentação
      execution_observer.py
    memory/
      llm_summarizer.py
      profile_extractor.py
  api/v1/
    models/messages.py
    routes/messages.py
  prompts/memory.py
  settings.py
  bootstrap.py
tests/unit/
tests/integration/
  compose.yaml
  conftest.py
  README.md
```

A árvore define responsabilidades, não exige uma classe por arquivo. Agrupar
arquivos pequenos quando isso preservar a clareza. Reutilizar a composição por
dependências injetadas e os protocolos existentes. Os adapters do principal não
importam implementações internas de outros serviços.

### Coleções Mongo

| Coleção proposta         | Conteúdo principal                                                                                                      | Índices/consistência                                                                                                       |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `ai_sessions`            | Titular, fazendas iniciais, datas, versão, contador de entradas, próximo marco de resumo e lease com fencing token.     | Índice por titular/data; `_id` único. Atualização de lease/versão por compare-and-set.                                     |
| `ai_interactions`        | Entrada sanitizada, tipo, chave de deduplicação, hash do payload sanitizado, status e resultado público.                | Índice único para deduplicação por sessão; consulta de status para reconhecer requisição concluída, ativa ou interrompida. |
| `ai_messages`            | Mensagens públicas sanitizadas, sequência, papel, entrada estruturada quando aplicável e referências de fontes/escopo.  | Único por sessão/seq; ID determinístico por interação/fase para replay seguro.                                             |
| `ai_execution_events`    | Chamadas reais de agentes/tools, mensagens internas permitidas, início/fim, tentativa, resultado, fontes e erro seguro. | Um documento por invocação, atualizado de `started` ao estado terminal; eventos históricos podem ter IDs separados.        |
| `ai_session_summaries`   | Resumo estruturado, texto, tokens, versão, marco de entradas e sequência de mensagens coberta.                          | Único por sessão/marco; publicar apenas com versão/lease válidos.                                                          |
| `ai_user_memory`         | Tom e objetivo geral, origem por campo, data, revisão e resumo do perfil.                                               | Um documento por usuário; atualização otimista por revisão.                                                                |
| Coleções do checkpointer | Checkpoints e escritas necessárias ao runtime LangGraph.                                                                | Nomes/schema definidos pela integração escolhida, isolamento por sessão e sem TTL automático.                              |

Não incorporar todo o histórico ao documento da sessão: documentos e índices
devem continuar utilizáveis conforme a conversa cresce. Nenhuma das coleções de
conteúdo acima recebe TTL de expiração automática.

Tokens, senhas, mapas reversíveis e raciocínio privado do modelo não são
persistidos nessas coleções. Preservar referências mínimas de execução quando
necessárias sem inseri-las nos prompts públicos.

### Durabilidade no Mongo

Recomendação técnica: ambiente Mongo com replica set e escrita confirmada,
permitindo transações para mudanças envolvendo coleções da aplicação. A
configuração efetiva do ambiente precisa ser verificada na implementação.

Uma transação de aceitação registra a interação, a mensagem e o contador.
Chamadas de LLM/tools ficam fora de transações. Na finalização, outra transação
registra resultado e mensagens públicas de forma idempotente. Não há tarefa
enfileirada, outbox ou worker neste escopo.

O checkpointer pode escrever fora dessas transações. Usar IDs de interação,
versão e marcador de conclusão para identificar o último estado consistente ao
receber nova requisição. Não presumir atomicidade entre SDK e coleções próprias.
Checkpoint parcial de uma requisição interrompida não deve ser retomado
automaticamente.

Antes de efeitos externos, registrar intenção de chamada e chave de
idempotência. Persistir resultado ao retornar. Uma chamada interrompida entre
efeito externo e escrita pode ter resultado desconhecido; preservar essa
condição, sem anunciar sucesso nem repetir automaticamente envio de e-mail. A
reconciliação da operação pertence ao serviço responsável e não cria um
mecanismo de recuperação automática da conversa.

O checkpointer Mongo deve ser escolhido e ter compatibilidade comprovada com as
versões do projeto. O repositório atual não contém essa integração; nenhum
pacote ou driver específico é considerado validado por este plano.

### Fluxo de uma interação

1. Obter Principal pela dependência existente e validar titularidade da sessão.
2. Sanitizar entrada e validar a variante message/answer. Revalidar pergunta e
   seleção de escopo quando aplicável.
3. Deduplicar: mesma chave e mesmo payload sanitizado devolvem resultado
   concluído, ou sinalizam requisição ativa/interrompida sem executar novamente;
   mesma chave com payload diferente retorna conflito.
4. Adquirir lease exclusivo da sessão. Reservar sequência, persistir entrada e
   incrementar o contador uma única vez.
5. Carregar checkpoint, resumo publicado, mensagens posteriores e perfil atual.
   Resolver acesso efetivo às fazendas.
6. Montar contexto e executar grafo dentro da requisição, registrando chamadas e
   resultados sanitizados. Renovar lease e recusar escritas de uma execução com
   fencing token antigo. O executor é essa função interna do main_api, não outro
   agente ou processo.
7. Persistir resposta final ou pergunta pendente. Para resposta estruturada do
   usuário, deduplicar pela pergunta/interação de origem; rejeitar resposta
   divergente após consumo da pergunta.
8. Publicar resumo quando houver marco pendente e atualizar perfil quando houver
   declaração elegível. Falhas deixam cobertura/revisão anteriores intactas e um
   marcador para nova tentativa na próxima interação, sem processamento em
   background.
9. Devolver o contrato público existente e liberar lease. O processamento
   permanece no main_api; Mongo é uma dependência de armazenamento, sem novo
   serviço de memória.

Leases, deadlines e perguntas podem ter validade técnica. Isso não implica
expiração da sessão nem perda de histórico. A política atual de `expires_at` das
perguntas permanece até decisão específica em contrário. Lease expirado permite
nova requisição; não inicia nem reexecuta uma atividade. Uma requisição
interrompida permanece identificável, e a nova entrada usa contexto consistente
sem apresentar resultados parciais como operações concluídas.

### Resumo progressivo da sessão

O contador considera entradas aceitas, únicas e persistidas do usuário. Erro de
validação, pergunta inexistente e replay não contam. Uma entrada válida que
resulte em resposta segura conta e preserva apenas o conteúdo permitido.

Marcos: 15, 30, 45 etc. Executar a sumarização depois de finalizar a entrada
correspondente, seja com resposta final, resposta segura ou pergunta pendente.
Antes da próxima execução de agentes, processar o marco ainda não publicado. A
criação do resumo não acrescenta entrada ao contador.

Entrada do sumarizador: resumo anterior válido + mensagens públicas e resultados
relevantes desde a última sequência coberta. Se o trecho ultrapassar a
capacidade de entrada do modelo, processar em blocos com proveniência, sem
truncamento silencioso.

Saída estruturada:

- O que o usuário estava fazendo: pedido, tarefa e estágio atual.
- Últimos dados importantes: valores, unidades, datas, entidades e referências
  mínimas de fonte.
- Decisões e esclarecimentos que alteram o entendimento do pedido.
- Pendência necessária para continuar a tarefa.
- Limitações e distinção entre resultado confirmado, simulado, indisponível ou
  incerto.

Teto de 2.000 tokens para o conteúdo de resumo efetivamente usado no contexto,
incluindo sua estrutura serializada. Contar com tokenizer compatível com o
modelo configurado; limitar apenas max_tokens de saída não é validação
suficiente. Excesso exige nova compactação controlada; não cortar
arbitrariamente uma frase ou descartar dado crítico.

Persistir versão, marco de entradas, intervalo de mensagens, tokens e
referências. Só publicar se a versão esperada ainda corresponder ao estado.
Falha mantém o resumo anterior e o intervalo não resumido; não avançar a
cobertura até sucesso.

O estado estruturado da pergunta e dos efeitos externos fica no checkpoint, sem
depender do resumo. O histórico permanece consultável integralmente após a
compactação, conforme sanitização e escopo.

### Contexto enviado aos agentes

Contexto lógico: perfil do usuário + resumo vigente + mensagens posteriores ao
resumo + entrada atual. Não duplicar a entrada atual ao incluí-la no trecho
recente e no schema do pedido.

O montador seleciona apenas os trechos necessários por agente: roteador recebe
continuidade da tarefa; especialista recebe contexto pertinente; orquestrador
recebe tom/objetivo e resultados; juiz mantém fundamentação em fontes atuais.
IDs e autorização continuam no runtime confiável, fora de texto gerado pelo
usuário.

Memória e histórico são dados, não instruções de sistema. Últimos dados
importantes ajudam a entender a continuidade, mas dados de negócio voláteis
precisam de consulta atual quando necessários para a resposta. Fonte documental
revogada/substituída deve ser excluída do contexto derivado, inclusive do
resumo, mesmo com sessão sem expiração.

### Memória independente do usuário

Perfil limitado a `tone` e `general_goal`, ambos opcionais. Para cada campo,
guardar valor sanitizado, origem em sessão/interação, trecho mínimo da
declaração e data de atualização. Manter revisão e resumo textual derivado
desses dois campos, sem expiração automática.

Exemplos elegíveis: "Prefiro que você fale de forma simples e acolhedora"; "Meu
objetivo é entender os medicamentos e prevenir novas perdas". Exemplos que não
alteram perfil automaticamente: pergunta sobre um medicamento; descrição de
saldo/perdas; "explique formalmente só esta resposta"; texto citado de
terceiros; conteúdo de tool.

Não exigir que o usuário diga "lembre". Atualizar após cada entrada elegível,
independentemente dos marcos de 15 mensagens. Uma declaração mais recente
substitui o campo anterior; alteração de tom não remove objetivo já registrado.
Usar ordem temporal/versionamento para uma extração atrasada não sobrescrever
uma declaração mais recente de outra sessão.

Quando o usuário pedir para deixar de considerar um tom ou objetivo, remover o
campo e recompor o resumo. Replay de tarefas antigas não pode restaurar um campo
removido. A operação é explícita; não depende de TTL.

O stub atual identifica todos os acessos locais como um usuário fixo. Isso
permite demonstrar persistência para um usuário, mas testes de isolamento
precisam de Principals distintos injetados. Mongo não passa a validar identidade
por armazenar user_id.

### Consulta pública de mensagens antigas

Nova rota proposta:
`GET /v1/sessions/{session_id}/messages?limit=50&cursor=...`.

- Resposta paginada com `schema_version`, `session_id`, `items`, `next_cursor` e
  `has_more`.
- Item com `message_id`, sequência, `interaction_id`, papel, timestamp e
  conteúdo público sanitizado. Preservar pergunta/resposta estruturada e
  respectivos rótulos/IDs quando forem parte da mensagem exibida.
- Página inicial mostra o trecho mais recente; itens dentro da página ficam em
  ordem cronológica. Cursor busca mensagens anteriores com fronteira estável;
  novas mensagens não provocam duplicação ou salto durante a navegação.
- Limite padrão 50, máximo 100. Cursor opaco e validado, associado à sessão e ao
  titular.
- Consulta sem LLM. Sessão inexistente ou de outro titular retorna 404 uniforme.
- Mensagens que contenham dados de fazenda com acesso revogado precisam ser
  filtradas ou redigidas antes da exposição. Como os documentos atuais não
  guardam a fazenda de origem por mensagem, a implementação oculta respostas do
  assistente quando qualquer fazenda da sessão perde autorização; manter
  granularidade por mensagem depende de adicionar proveniência no futuro.
- Eventos internos de agentes/tools ficam persistidos para rastreabilidade, mas
  não são incluídos nessa rota de conversa.

Não alterar os formatos de POST de sessões/interações. A listagem de sessões
mantém seu contrato atual; sua paginação é evolução independente, não requisito
deste escopo. Os testes e documentação precisam aceitar a nova rota de
mensagens.

## 3. Etapas, dependências e critérios de conclusão

| Etapa                          | Trabalho                                                                                                                         | Dependências                                                                 | Aceite                                                                                                                               |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 1. Contratos e configuração    | Modelos de sessão/mensagem/resumo/perfil, portas, configuração de limites e contrato de histórico.                               | Decisão de autenticação; modelo real pode ficar injetável.                   | Responsabilidades e schemas definidos, preservando contratos atuais de escrita.                                                      |
| 2. Infraestrutura Mongo        | Bootstrap/lifespan, índices, replica set de testes, escrita confirmada e adapters próprios.                                      | Etapa 1.                                                                     | Readiness reflete storage; conexão encerra corretamente; dados sobrevivem restart; nenhum TTL indevido.                              |
| 3. Sessões e processamento     | Implementar SessionService, ownership, deduplicação, sequência, contador e lease/fencing no processamento da requisição.         | Etapa 2.                                                                     | Rotas existentes funcionam; uma entrada conta uma vez; concorrência é controlada sem fila ou worker.                                 |
| 4. Registro de agentes e tools | Persistir eventos e instrumentar Factory/tools/callbacks.                                                                        | Etapa 3.                                                                     | Chamadas reais têm entrada/saída sanitizada, correlação e status; falhas ficam distinguíveis.                                        |
| 5. Contexto e retomada         | Validar checkpointer Mongo, salvar estado consistente, montar contexto por agente e retomar esclarecimentos pela rota existente. | Etapas 3 e 4.                                                                | Nova requisição continua conversa após restart; pergunta pendente preservada; nenhuma execução parcial é reiniciada automaticamente. |
| 6. Resumo progressivo          | Sumarizador estruturado, marcos de 15 entradas, teto de 2.000 tokens e publicação versionada.                                    | Etapa 5.                                                                     | Mensagem seguinte usa resumo; dados essenciais preservados; falha não avança cobertura e nova interação permite tentar novamente.    |
| 7. Memória entre sessões       | Extração restrita, atualização automática por campo, controle de revisão, leitura e remoção por pedido explícito.                | Etapas 5 e 6.                                                                | Sessão B usa tom/objetivo da A; outro usuário não recebe; dados fora da lista não persistem no perfil.                               |
| 8. Consulta do histórico       | GET messages com paginação estável, validação de titularidade e conteúdo público sanitizado.                                     | Etapa 3. Pode ocorrer em paralelo às etapas 4 a 7, após contratos definidos. | Histórico anterior disponível sem LLM, sem duplicação entre páginas e sem expor eventos internos.                                    |
| 9. Integração e documentação   | Testes de persistência, retomada, concorrência, escopo, paginação, resumo/perfil e atualização do README, planejamento e CI.     | Etapas anteriores.                                                           | Demonstração reproduzível; contratos atualizados; nenhuma configuração paga necessária para testes automatizados.                    |

**Andamento da etapa 9:** README e este plano atualizados; a suíte Mongo
descartável cobre persistência após reconexão, transações, deduplicação, lease
concorrente, escopo, paginação, eventos, resumo e perfil. A CI executa os testes
de integração em job próprio e publica JUnit. A retomada do checkpointer tem
teste Mongo separado. Os dois testes de integração passaram localmente contra o
replica set descartável; a CI instala as dependências declaradas e repete a
suíte.

As etapas de persistência, autenticação e histórico descritas neste plano foram
implementadas. As referências a PLN-06, PLN-09 e PLN-31 registram o backlog que
orientou o trabalho; não representam autenticação ou histórico ainda pendentes.
Para o contrato atual de autenticação e das rotas, consulte
[`docs/api-main-api.md`](api-main-api.md) e
[`docs/firebase-authentication-identity.md`](firebase-authentication-identity.md).

## 4. Plano de testes

### Unitários e de contrato

- Contador em 14/15/16 e 29/30/31; respostas válidas contam; replay, tools e
  assistente não contam.
- Sanitização em histórico, events, resumos, perfil, erros e conteúdo enviado
  aos agentes.
- Resumo preserva atividade, últimos dados, unidades/datas, negações, limitações
  e pendência; segunda versão retém fatos essenciais da primeira.
- Resumo abaixo, igual e acima de 2.000 tokens; revisão por excesso, falha de
  geração e versão concorrente.
- Perfil atualizado sem pedido para lembrar; ausência de evidência não altera
  perfil; declaração temporária/citação/tool não atualiza perfil; alterações
  parciais e remoção não ressuscitam dados antigos.
- Montagem de contexto efetivamente chega às entradas dos agentes e não duplica
  mensagem atual.
- Eventos registram chamada real, tentativa, mensagens de tool e erro;
  distinguir tools de negócio de mecanismos de saída estruturada.
- GET messages: contrato, limits, cursor inválido, ordenação, autorização,
  conteúdo estruturado e ausência de chamada a LLM.

### Integração com Mongo real

- Criar/listar sessão e consultar histórico após reinício do processo e do banco
  com volume persistente.
- Duas requisições simultâneas com mesmo ID contam e executam uma vez; payload
  diferente com mesmo ID gera conflito.
- Duas entradas diferentes na mesma sessão respeitam lease e sequência; executor
  antigo não publica após lease ser assumido por outro.
- Interrupção após aceitar entrada, após checkpoint, após resultado de agente e
  antes da resposta pública; nova requisição identifica o estado interrompido,
  preserva registros e usa contexto consistente sem reexecutar automaticamente
  tools.
- Resposta concluída salva antes da queda: repetição da mesma entrada devolve o
  resultado persistido sem nova execução do grafo.
- Falha de escrita de evento impede falsa conclusão; chamada externa com
  resultado incerto fica reconciliável.
- Marco de resumo pendente sobrevive restart; nova interação permite tentar
  sumarização novamente; falha mantém trecho não resumido; pergunta pendente
  continua respondível após compactação.
- Sessão A registra tom/objetivo; sessão B usa; usuário C não acessa; sessão
  concorrente com atualização atrasada não sobrescreve versão recente.
- Histórico com múltiplas páginas e inserções durante navegação não duplica nem
  omite itens anteriores ao cursor.
- Usuário/fazenda de terceiro negados; escopo revogado filtra
  histórico/contexto; fontes documentais substituídas não entram como evidência
  atual.
- Avançar relógio por meses: sessão, histórico e perfil permanecem; lease e
  pergunta podem expirar independentemente.

Usar Principals, modelos e tools controlados, incluindo dois usuários. Testes
pagos não são necessários. MemorySaver permanece apenas nos testes unitários
existentes; durabilidade exige checkpointer Mongo real.

Adicionar testes de integração do principal à CI e adaptar as asserções de
rotas/readiness. Manter relatórios de falhas e teste do contrato consumidor,
especialmente para a nova resposta paginada.

## 5. Pendências restantes

1. **Modelo e tokenizer:** modelo de resumo/extração será definido depois, como
   solicitado. Configuração permite injeção e os testes usam substitutos
   determinísticos; é preciso escolher tokenizer compatível para comprovar 2.000
   tokens em execução real.
2. **Deployment Mongo:** testes e CI validam replica set de um membro,
   transações e checkpointer local. Cada ambiente implantado ainda precisa
   fornecer URI/database e capacidade de replica set apropriadas.
3. **Configuração por ambiente:** o fluxo Firebase/JWT e a autorização
   PostgreSQL estão implementados. Cada ambiente implantado ainda precisa
   fornecer o projeto Firebase, credenciais do Admin SDK, DSN somente leitura,
   chaves Ed25519 e uma instalação Mongo compatível com transações; a ausência
   dessas configurações afeta a disponibilidade, não o contrato implementado.

A ausência de expiração, atualização automática de memória, conteúdo sanitizado,
campos restritos, contagem por entradas do usuário e rota de histórico já são
decisões; não precisam ser reabertas como aprovação.
