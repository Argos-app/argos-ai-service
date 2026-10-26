# Operação e diagnóstico de observabilidade

Este é o guia operacional vigente para a telemetria do `main_api` e do A2A. Ele
funciona sem LangSmith. A coleta Mongo e os contadores Redis são best effort. No
Compose integrado, `ai_observations` e `ai_a2a_observations` são ativadas por
padrão; em execução individual, configure `MAIN_API_OBSERVABILITY_COLLECTION` no
`main_api` junto com `MAIN_API_MONGO_URI`; para observações do A2A, configure em
conjunto `A2A_OBSERVABILITY_MONGO_URI`, `A2A_OBSERVABILITY_MONGO_DATABASE` e
`A2A_OBSERVABILITY_COLLECTION`. Os demais limites da fila podem ser ajustados
pelas variáveis `MAIN_API_OBSERVABILITY_*` e `A2A_OBSERVABILITY_*` mostradas nos
respectivos `.env.example`. Não é necessário configurar `LANGSMITH_API_KEY` para
usar Mongo ou o ranking local.

## Seguir uma interação

Cada requisição HTTP não-health recebe um UUID novo. A resposta expõe esse valor
em `X-Argos-Correlation-ID`; guarde-o junto ao horário e ao resultado reportado
pelo usuário. Observações aninhadas herdam `correlation_id`, enquanto
`parent_observation_id` aponta para o `_id` da observação que iniciou a chamada
filha. Nas observações da execução do grafo e A2A, `interaction_id` e
`session_id` também ligam diretamente cada operação à interação e à sessão do
Mongo; a API usa índices `(interaction_id, started_at)` e
`(session_id, started_at)` para essas consultas. Operações de entrada anteriores
à criação da interação e chamadas de health podem ter somente a correlação. A
busca por requisição é indexada por `(correlation_id, started_at)`:

```python
import os
from pymongo import ASCENDING, MongoClient

client = MongoClient(os.environ["MAIN_API_MONGO_URI"], tz_aware=True)
collection = client[os.getenv("MAIN_API_MONGO_DATABASE", "argos_ai")][
    os.environ["MAIN_API_OBSERVABILITY_COLLECTION"]
]
correlation_id = "UUID recebido no header X-Argos-Correlation-ID"
for item in collection.find({"correlation_id": correlation_id}).sort(
    [("started_at", ASCENDING), ("_id", ASCENDING)]
):
    print(
        item["_id"],
        item.get("parent_observation_id"),
        item["service"],
        item["operation_name"],
        item["status"],
    )
```

Para buscar diretamente todas as observações da interação persistida, use seu
`interaction_id`; para agregar conversas, use `session_id`. Esses filtros usam
índices próprios e evitam depender da reconstrução da relação apenas pela
correlação HTTP:

```python
interaction_id = "UUID da interação armazenada em ai_interactions"
for item in collection.find({"interaction_id": interaction_id}).sort(
    [("started_at", ASCENDING), ("_id", ASCENDING)]
):
    print(
        item["_id"],
        item.get("parent_observation_id"),
        item["service"],
        item["operation_name"],
        item["status"],
    )
```

Use os `_id` e `parent_observation_id` para montar a árvore. O identificador é
por requisição HTTP; uma nova mensagem, retry do cliente ou continuação da mesma
sessão cria outra correlação. A sessão e a interação funcionais têm seus
próprios IDs e continuam sendo a referência para unir mensagens da conversa. O
cliente também envia `X-Argos-Correlation-ID` às chamadas MCP. Para MCP de dados
e predição, a correlação e o span pai também viajam nos claims assinados da
delegação; A2A recebe os mesmos claims assinados. O header MCP serve para
correlação diagnóstica e não concede acesso.

No LangSmith, os valores UUID são anonimizados como `[UUID]`, inclusive quando
aparecem em metadados estruturados. Para localizar o trace externo, os serviços
também gravam `argos_correlation_key`, um SHA-256 determinístico calculado a
partir do UUID da resposta. A mesma chave é propagada pelo principal e pelo A2A;
ela não permite recuperar o UUID original. Calcule-a localmente com
`packages.observability.correlation_key(UUID(header))` e filtre os traces pelo
metadado `argos_correlation_key`. O identificador canônico continua disponível
nas observações Mongo locais.

O principal persiste observações de suas chamadas MCP e A2A, incluindo os IDs
funcionais da interação e sessão nos spans criados dentro do grafo. A2A recebe
esses IDs na delegação assinada e pode persistir as próprias observações em sua
collection opcional. MCP de dados, predição e e-mail não gravam atualmente uma
collection de observabilidade própria; assim, uma chamada pode ser vista no lado
do principal sem uma observação servidor a ser correlacionada. A requisição A2A
também possui ID de protocolo próprio, distinto da correlação de ponta a ponta.

## Interpretar uso e falhas

Uso e cobrança são coletados por melhor esforço. `usage.availability` diferencia
`returned`, `partial`, `absent` e `not_applicable`; campos ausentes e `absent`
significam desconhecido, não zero. `billing.reported_cost` pode ser o custo
explícito do provider (`cost_source="provider"`) ou uma estimativa pelas tarifas
configuradas (`cost_source="configured_rate"`). Ambas são persistidas no Mongo
em `billing` e propagadas aos traces LangSmith em `metadata.argos_billing`; para
custo USD, também são preenchidos os campos de custo reconhecidos pelo
LangSmith. O esquema das observações não muda conforme a origem.

As tarifas opcionais são `MODEL_INPUT_COST_PER_MILLION_TOKENS` e
`MODEL_OUTPUT_COST_PER_MILLION_TOKENS`, decimais em USD por milhão de tokens.
São aplicadas uniformemente aos modelos e serviços do processo. O preço
informado pelo provider tem precedência. Para chat, a estimativa exige contagens
de entrada e saída e ambas as tarifas, evitando subestimar uso parcial; para
embeddings, considera somente tokens de entrada e a tarifa de entrada. Sem
contagens ou tarifa necessária, o custo permanece ausente. Essa estimativa não é
uma cobrança oficial nem reconcilia faturas. Providers compatíveis com OpenAI,
incluindo NVIDIA NIM, podem retornar tokens sem preço por chamada. Algumas
bibliotecas fazem retries internos invisíveis: consulte `attempt_visibility` e
`attempt_reason` e não interprete contagem de spans como contagem exata de
chamadas faturadas.

As filas são limitadas e mantidas somente em memória. Observações podem se
perder quando a fila enche, o processo encerra antes do flush, Mongo fica
indisponível após as tentativas ou um documento excede o limite de tamanho. A
telemetria não bloqueia o fluxo principal; ausência de documento não prova que a
operação não ocorreu. Mongo não aplica TTL à collection de observabilidade;
retenção e exclusão são decisão operacional.

O Redis mantém um hash por `user_id`, compartilhado pelo `main_api` e pelo A2A.
Cada requisição com identidade válida incrementa `calls`; cada observação
terminal de modelo/embedding incrementa `model_calls` e soma os tokens
conhecidos. Quando o provider não retorna tokens completos, o sistema soma o que
conhece e incrementa `unknown_token_calls`. Os tokens são deduplicados pelo ID
da observação. O hash expira 24 horas depois do último incremento; cada
atualização renova o TTL. Isso mede uso acumulado durante a atividade recente do
usuário, e não uma janela móvel exata de 24 horas. Os contadores são
independentes do registro Mongo, mas são best effort: falhas Redis,
encerramentos antes do flush da fila ou reinício do Redis podem perder
incrementos. O Redis do Compose local não persiste em disco.

Para execução individual, configure `MAIN_API_OBSERVABILITY_REDIS_URL` e/ou
`A2A_OBSERVABILITY_REDIS_URL` para o mesmo Redis e o mesmo
`USER_USAGE_REDIS_KEY_PREFIX`. A API registra as chamadas autenticadas. O A2A
contribui com tokens quando recebe contexto de usuário delegado.

O relatório de uso por usuário lê esses contadores Redis. Instale as
dependências do serviço principal e configure `OBSERVABILITY_REDIS_URL` com o
mesmo Redis e o mesmo prefixo utilizados pelos serviços:

```powershell
python -m pip install -r apps/main_api/requirements.txt
$env:OBSERVABILITY_REDIS_URL = "redis://localhost:6379/0"
$env:USER_USAGE_REDIS_KEY_PREFIX = "argos:usage:users:v1"
python scripts/observability/rank_usage_24h.py
```

O relatório fornece ordenações por chamadas e por tokens, além de chamadas de
modelo com contagem de tokens incompleta. Ele inclui somente usuários cujas
chaves ainda não expiraram e marca `completeness=best_effort_incomplete`; não é
um ledger de faturamento nem um histórico durável.

Para exportação de BI, use o contrato e a paginação idempotente descritos em
[observabilidade-extracao-mongo.md](observabilidade-extracao-mongo.md). Para
campos e origem de dados de negócio, consulte o
[dicionário MongoDB para BI](dicionario-mongodb-bi.md).
