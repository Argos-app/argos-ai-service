# Sessões, histórico e memória da Main API

A Main API persiste estado conversacional no MongoDB e usa o checkpointer Mongo
do LangGraph. Este guia resume a implementação atual; os formatos e índices
estão em
[estruturas Mongo](../../../infrastructure/mongodb/mongodb-collection-structures.md)
e as rotas em [API principal](api-main-api.md).

## Sessão e interação

`ai_sessions`, `ai_interactions` e `ai_messages` armazenam sessões, interações e
mensagens sanitizadas. Cada sessão pertence a um usuário; a API revalida
identidade e acesso nas operações protegidas. A entrada de mensagem possui
identificador fornecido pelo cliente para deduplicação. Uma interação aceita é
processada em segundo plano e seu estado pode ser consultado pelo
`operation_id`.

O processamento usa lease por sessão com fencing token: o lease dura 120
segundos e é renovado a cada 30 segundos conforme `app/session_config.py`. O
fencing impede uma execução antiga de publicar como atual depois da perda do
lease. Isso protege concorrência e recuperação; a expiração não inicia por si só
uma nova execução. Leia `session_execution.py` e `session_recovery.py` antes de
alterar cancelamento ou retomada.

Perguntas pendentes têm validade de 15 minutos. A resposta é vinculada à
pergunta e interação, e a retomada revalida titularidade, acesso, tipo de
resposta e estado do checkpoint. O histórico público é paginado, com tamanho
padrão 50 e máximo 100 mensagens.

## Checkpoints, eventos e resumo

`ai_langgraph_checkpoints` guarda checkpoints; `ai_langgraph_checkpoint_writes`
guarda escritas associadas. O `thread_id` é o ID da sessão e o grafo raiz usa
namespace vazio. O formato interno pertence ao checkpointer; não o atualize
manualmente.

`ai_execution_events` registra uma invocação de agente ou ferramenta por
documento: começa em `started` e é atualizado ao terminar. O sumarizador usa
mensagens e eventos elegíveis. O resumo progressivo é publicado por marcos de 15
entradas de usuário com interações terminais; a saída tem limite de 2.000 tokens
e a entrada configurada tem padrão de 8.000. Quando não há gerador ou há falha,
a execução preserva o resumo anterior e pode tentar novamente depois. Confira o
fluxo e a publicação por revisão em `session_summary_service.py`.

## Memória entre sessões e e-mail

`ai_user_memory` guarda campos limitados de tom e objetivo geral declarados pelo
usuário. O serviço minimiza a entrada, valida alterações e usa revisão para
concorrência; pedidos explícitos de remoção são processados separadamente. Essa
memória é contexto, não uma fonte de fatos de negócio.

`ai_report_artifacts` guarda envelopes assinados; `ai_email_workflows` reúne
preparações, resoluções pendentes, autorizações e referências de operação da
Main API. As collections do MCP de e-mail são independentes. Os documentos de
artefato e fluxo têm política de expiração própria; sessões, mensagens, eventos,
resumos e memória não têm TTL configurado nesses repositórios.

## Verificação

Execute `python -m pytest apps/main_api/tests/unit -q`. O
[guia de integração](../tests/integration/README.md) descreve Mongo replica set,
PostgreSQL e MCP de e-mail descartáveis para persistência, concorrência,
autenticação e retomada. Esses testes não substituem validação de credenciais ou
dados de produção.
