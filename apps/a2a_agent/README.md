# Argos A2A de Informações

Guia vigente da interface A2A JSON-RPC 1.0.0. Contratos tipados em
`packages/contracts/a2a.py` são normativos.

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
`/chat/completions`, e um adapter HTTP para os embeddings Voyage. Configure as
URLs base `OPENAI_BASE_URL` e `ANTHROPIC_BASE_URL`, os endpoints completos
`OPENAI_EMBEDDINGS_URL` e `VOYAGE_EMBEDDINGS_URL`, além das chaves, nomes dos
modelos e dimensões dos dois espaços. O código não presume URLs de provedores:
informe endpoints compatíveis com o protocolo esperado, incluindo gateways como
OpenRouter quando oferecerem a API adequada. A sincronização indexa os
documentos em vetores Qdrant nomeados (`openai` e `voyage`); a consulta usa o
índice correspondente ao provedor que conseguiu gerar o vetor. A revisão dos
modelos e dimensões participa do fingerprint de indexação; alterá-la força o
sincronizador a criar uma nova geração compatível com Qdrant. Cada chamada de
resposta também recebe o teto `A2A_MODEL_MAX_OUTPUT_TOKENS` (padrão 1200); o
deadline delegado e os timeouts existentes limitam a duração da tarefa.

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
como segredo e, se necessário, `A2A_OBSERVABILITY_MONGO_DATABASE` como
configuração. A collection fixa é `ai_a2a_observations`; sem URI, a coleta
persistente fica desativada. O writer usa uma fila limitada em memória, sem
spool local; falhas de configuração ou indisponibilidade do MongoDB desativam a
persistência sem interromper as respostas A2A. Os registros incluem tentativas
de operações A2A, agentes, provedores HTTP, embeddings, autorização PostgreSQL e
adapters de documentos/Qdrant, sem persistir argumentos, prompts, respostas, ID
do usuário, ID da task ou ID de contexto do protocolo. Nas chamadas delegadas
pelo principal, persistem `session_id` e `interaction_id` funcionais para
permitir a junção com as conversas; esses IDs não substituem nem expõem os IDs
de task/contexto A2A.

O principal inclui correlation ID, ID do span pai, `session_id` e
`interaction_id` na delegação assinada. O executor valida esses claims e grava
`correlation_id`, `parent_observation_id`, `session_id` e `interaction_id` na
observação A2A e nos spans de execução. O ID de protocolo da task continua
distinto. A resposta da API principal expõe `X-Argos-Correlation-ID`; veja o
guia de [operação](../../docs/observability-operations.md) para seguir a
requisição nas collections de ambos os serviços. A coleta Mongo funciona sem
LangSmith.

O tracing LangSmith é opcional. Configure `A2A_LANGSMITH_API_KEY` como segredo
para registrar o despacho do agente A2A e a chamada de geração de resposta no
Agent definido por `A2A_LANGSMITH_AGENT_ID`, no ambiente derivado de `APP_ENV`
ou sobrescrito por `A2A_LANGSMITH_AGENT_ENVIRONMENT`. Use o mesmo Agent ID entre
ambientes e habilite Agent addressing no workspace. Entradas e saídas permanecem
ocultas por padrão; `A2A_LANGSMITH_CAPTURE_CONTENT=true` habilita a captura de
perguntas, trechos e respostas. Com essa opção ativa, UUIDs, CPFs, CNPJs,
telefones e tokens Bearer são anonimizados antes do envio; e-mails permanecem
sem anonimização por decisão do projeto e podem ser enviados ao LangSmith nessa
modalidade. Considere essa exposição ao habilitar captura de conteúdo. O
endpoint HTTPS pode ser definido por `A2A_LANGSMITH_ENDPOINT`. Para chaves de
escopo organizacional, configure `A2A_LANGSMITH_WORKSPACE_ID` com o ID do
workspace. Quando há uso de tokens, o serviço persiste custo explícito do
provider ou uma estimativa configurada no Mongo e o propaga ao LangSmith. As
tarifas opcionais em USD por milhão são configuradas separadamente por
especialista, fallback especialista, embedding OpenAI e fallback Voyage, pelas
variáveis `MODEL_SPECIALIST_*`, `MODEL_SPECIALIST_FALLBACK_*`,
`MODEL_EMBEDDING_INPUT_COST_PER_MILLION_TOKENS` e
`MODEL_EMBEDDING_FALLBACK_INPUT_COST_PER_MILLION_TOKENS`. Embeddings usam só
tarifa de entrada. `billing.cost_source` identifica `provider` ou
`configured_rate`. Sem uso/tarifa suficientes, o custo fica ausente. Consulte o
guia operacional para limites e semântica dos valores.

## Contrato A2A

`GET /.well-known/agent-card.json` publica o Agent Card JSON com protocolo
JSON-RPC A2A 1.0.0, skills `documental` e `legal`, autenticação Bearer e modos
`application/json`. O endpoint JSON-RPC é o valor configurado em
`A2A_ENDPOINT_URL` (termina em `/a2a`). Cada chamada exige Bearer interno e
`X-Argos-Delegation` assinado, válido para `documents.read`; chamadas de task
também ficam limitadas ao usuário autenticado.

O conteúdo JSON da mensagem segue `DocumentQuestionRequest` em
`packages/contracts/a2a.py`. Exemplo reduzido para uso via SDK A2A:

```json
{
	"request_id": "00000000-0000-0000-0000-000000000001",
	"question": "Quais condições constam no documento?",
	"skill": "documental",
	"document_types": ["terms_conditions"],
	"evidence_requirements": ["source", "page"],
	"deadline_at": "2026-10-13T15:00:00Z"
}
```

O SDK encapsula esse JSON em uma mensagem A2A JSON-RPC com media type
`application/json`; envie a delegação em `X-Argos-Delegation`, fora do payload.
`DocumentAnswerResponse` retorna `request_id`, `status`, resposta, fontes com
documento/revisão/páginas/trecho, limitações, campos ausentes e estado de
atualização do corpus. `success` exige fonte; `needs_clarification` exige
`missing_fields`; indisponibilidade inclui limitações. Tasks vivem em memória e
não podem ser retomadas após reinício.

## Operação e recuperação

- Tasks A2A usam InMemoryTaskStore e não sobrevivem ao reinício do processo.
  Após reinício, uma task anterior não pode ser consultada nem retomada; o
  cliente deve iniciar uma nova consulta somente quando necessário. O executor
  não repete tasks automaticamente. A persistência de tasks não faz parte do
  contrato atual.
- `GET /health/live` verifica o processo; `GET /health/ready` exige Bearer de
  serviço, runtime de agentes configurado e disponibilidade do Qdrant. Em um
  ambiente limpo, a ausência inicial do alias é aceita porque a primeira
  consulta documental autorizada sincroniza e publica o corpus; se o alias já
  existir, sua dimensão e métrica precisam corresponder à configuração. Falhas
  de conexão ou incompatibilidades mantêm a readiness indisponível. A prontidão
  não executa uma inferência de teste no provider.
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
