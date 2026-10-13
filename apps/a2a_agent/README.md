# Argos A2A de Informações

Serviço isolado que expõe as skills Documental e Legal por A2A e consulta o
corpus global de leis e termos/condições. O artefato contém somente
`apps/a2a_agent` e os contratos e primitivas de segurança compartilhados; não
instala nem importa o `main_api` ou outros MCPs.

## Build e execução

O build é feito na raiz do monorepo para copiar apenas as dependências
compartilhadas explicitamente:

```powershell
docker build -f apps/a2a_agent/Dockerfile -t argos-a2a-agent:local .
Copy-Item apps/a2a_agent/.env.example apps/a2a_agent/.env
# Edite .env com URLs, modelos e credenciais do deployment.
docker compose --env-file apps/a2a_agent/.env -f apps/a2a_agent/compose.yaml up --build -d
```

O Compose publica a porta somente em `127.0.0.1`, usa filesystem raiz somente
leitura, executa sem root, limita CPU/memória, monta PDFs e código de adapters
como somente leitura e preserva o manifesto em volume separado. O Qdrant é
externo ao Compose de produção e precisa expor chaves distintas de leitura e de
escrita do índice.

O corpus local padrão está em [`data/documents`](data/documents) e contém PDFs e
metadados sintéticos para demonstração e homologação; eles não são leis nem
contratos reais e não devem embasar orientação jurídica. `A2A_DOCUMENTS_PATH`
pode apontar para outro diretório local aprovado, sempre com `catalog.json` e
arquivos PDF allowlisted. O Compose monta a origem como somente leitura. A
origem cloud permanece fora desta entrega até a definição do provedor e de sua
configuração de acesso.

## Integrações do runtime

O serviço implementa clientes HTTP OpenAI-compatible para `/embeddings` e
`/chat/completions`. Configure os endpoints HTTPS, chaves, nomes dos modelos e
uma revisão imutável do modelo de embeddings. Essa revisão participa do
fingerprint de indexação; alterá-la força o sincronizador a criar uma nova
geração compatível com Qdrant. O modelo e a dimensão devem ser os mesmos usados
para indexar e consultar.

O adapter PostgreSQL consulta `app_user` e `permission` em cada operação. Todos
os códigos de papel recebem o mesmo acesso documental: a consulta verifica que o
usuário e seu registro de permissão estão ativos e que não há bloqueio vigente,
sem filtrar pelo código — inclusive para `SISTEMA`. A operação ainda exige
delegação assinada, válida e autorizada para `documents.read`. A conexão usa
transações read-only; configure um usuário de banco com privilégio somente de
leitura. A credencial de banco não é compartilhada com os clientes Qdrant.

Embeddings recebem texto extraído dos PDFs; o gerador recebe a pergunta e os
trechos recuperados. Use endpoints aprovados para esses dados e injete chaves,
Bearer, DSN e chave pública de delegação pelo gerenciador de segredos. Não
versione `apps/a2a_agent/.env`. O serviço falha fechado quando as configurações
estão ausentes, a permissão não pode ser confirmada ou um provedor retorna
resposta inválida.

Para persistir observabilidade bruta, configure `A2A_OBSERVABILITY_MONGO_URI`
como segredo e informe `A2A_OBSERVABILITY_COLLECTION` e, se necessário,
`A2A_OBSERVABILITY_MONGO_DATABASE` como configuração. URI e coleção precisam ser
informadas juntas; quando ambas forem omitidas, a coleta persistente fica
desativada. O writer usa uma fila limitada em memória, sem spool local; falhas
de configuração ou indisponibilidade do MongoDB desativam a persistência sem
interromper as respostas A2A. Os registros incluem tentativas de operações A2A,
agentes, provedores HTTP, embeddings, autorização PostgreSQL e adapters de
documentos/Qdrant, sem persistir argumentos, prompts, respostas ou
identificadores de usuário/task/contexto.

O tracing LangSmith é opcional. Configure `A2A_LANGSMITH_API_KEY` como segredo
para registrar o despacho do agente A2A e a chamada de geração de resposta no
projeto definido por `A2A_LANGSMITH_PROJECT`. Entradas e saídas permanecem
ocultas por padrão; `A2A_LANGSMITH_CAPTURE_CONTENT=true` habilita a captura de
perguntas, trechos e respostas. Com essa opção ativa, e-mails, UUIDs, CPFs e
tokens Bearer são anonimizados antes do envio. O endpoint HTTPS pode ser
definido por `A2A_LANGSMITH_ENDPOINT`.

## Operação e recuperação

- `GET /health/live` verifica o processo; `GET /health/ready` também depende da
  disponibilidade do Qdrant e exige Bearer de serviço.
- Preserve o volume `a2a-state` em reinícios. O manifesto é estado técnico da
  sincronização; não edite seu conteúdo manualmente.
- Fonte local (`data/documents` por padrão) é montada somente para leitura. A
  atualização do Qdrant acontece pelo sincronizador, que mantém a credencial de
  escrita atrás da porta de sincronização; consultas usam a credencial de
  leitura.
- O serviço rejeita configuração incompleta, dimensões incompatíveis e falhas de
  leitura do manifesto antes de aceitar tráfego. Em falha de Qdrant, deixe a
  prontidão indisponível, preserve o manifesto e recupere Qdrant conforme o
  procedimento operacional desse ambiente antes de reabrir tráfego.
- Os limites funcionais vigentes incluem 20 resultados por busca, 50 documentos
  por página, agregações limitadas a 10.000 documentos/100 grupos, trechos de
  até três páginas e timeout Qdrant de até 30 segundos. Os limites de container
  são 1 CPU e 1 GiB e devem ser revistos com medições do workload.
- A integração com Informações no principal e a escolha de uma origem cloud
  ainda são pendências separadas; este container usa o corpus local configurado.

## Verificações

```powershell
python -m ruff check apps/a2a_agent
python -m pytest apps/a2a_agent/tests/unit -q
docker compose --env-file apps/a2a_agent/.env -f apps/a2a_agent/compose.yaml config --quiet
```

A homologação com Qdrant real está documentada em
[`tests/integration/README.md`](tests/integration/README.md). Ela usa outro
Compose e credenciais descartáveis; não aponta para o Qdrant de produção.
