# Extração bruta de observabilidade pelo MongoDB

Este contrato disponibiliza os documentos de observabilidade diretamente da
coleção Mongo configurada pelo `main_api`. A aplicação não cria uma API de
exportação nem transforma os documentos. O consumidor externo lê o BSON
persistido e mantém seu próprio checkpoint de extração.

## Limites da coleta de uso e custo

A coleta existente é de melhor esforço, com fila limitada somente em memória.
Documentos confirmados no MongoDB permanecem armazenados, mas observações
pendentes podem ser perdidas em reinício, queda, fila cheia ou esgotamento de
retries. Não existe um ledger durável separado de uso e custo, nem sua criação
faz parte do plano vigente, conforme decisão de 13/10/2026.

Preserve `usage` e `billing` nos documentos. O custo pode ser reportado pelo
provider (`billing.cost_source="provider"`) ou estimado pelas tarifas
configuradas (`billing.cost_source="configured_rate"`). As tarifas opcionais
`MODEL_INPUT_COST_PER_MILLION_TOKENS` e `MODEL_OUTPUT_COST_PER_MILLION_TOKENS`
são decimais em USD por milhão de tokens; chat exige contagens completas de
entrada/saída e as duas tarifas, enquanto embeddings usam somente tokens e
tarifa de entrada. A tarifa configurada é um fallback uniforme, não um preço
oficial por modelo. Valores ausentes não significam consumo ou custo zero. A
extração cobre os documentos efetivamente persistidos; sua retomada não recupera
observações que nunca chegaram ao Mongo. Totais derivados não comprovam
faturamento completo.

## Conexão e permissões

- Database: valor de `MAIN_API_MONGO_DATABASE` (padrão atual: `argos_ai`).
- Coleção: valor de `MAIN_API_OBSERVABILITY_COLLECTION`, definido no ambiente do
  `main_api`.
- O consumidor BI deve usar uma credencial própria, guardada no secret store do
  ambiente de BI. Conceda somente `find` na coleção configurada, por uma role
  Mongo com recurso limitado a `{ db: <database>, collection: <coleção> }`.
- Não reutilize `MAIN_API_MONGO_URI` no BI. A aplicação precisa gravar e criar
  os índices da coleção; a credencial do BI precisa apenas ler.
- A rede do Mongo deve aceitar conexões do ambiente BI somente pelo caminho
  privado aprovado. TLS deve permanecer habilitado fora do ambiente local.

O runtime cria os índices `ix_observability_correlation_started` em
`(correlation_id ASC, started_at ASC)`, `ix_observability_started_id` em
`(started_at ASC, _id ASC)` e `ix_observability_updated_id` em
`(updated_at ASC, _id ASC)`. Para junções de BI, também cria
`ix_observability_interaction_started` em `(interaction_id ASC, started_at ASC)`
e `ix_observability_session_started` em `(session_id ASC, started_at ASC)`. O
usuário da aplicação precisa poder criar índices na coleção durante a
inicialização; o usuário BI não precisa dessa permissão.

## Documento e preservação

Cada `_id` identifica uma tentativa observada. A aplicação inicialmente pode
gravar `status="started"` e atualizar o mesmo documento ao terminar a tentativa.
O documento terminal mantém o `_id` e recebe `updated_at` novo. Por isso, o BI
deve tratar uma página novamente recebida como uma atualização idempotente pela
chave `_id`, substituindo a versão anterior desse registro.

Leia e preserve todos os campos diretamente do Mongo. Datas são BSON Date em
UTC, durações são milissegundos, custos e recursos decimais são BSON Decimal128,
campos ausentes continuam ausentes e enums permanecem strings. Preserve `_id` e
`schema_version` para identificar a tentativa e interpretar a versão do
contrato. Uma conversão para JSON deve usar Extended JSON do Mongo para não
perder tipos BSON.

Novos documentos podem conter `correlation_id`, `parent_observation_id`,
`session_id` e `interaction_id` como UUIDs em strings canônicas. São campos
aditivos e opcionais: documentos anteriores não os têm; observações raiz podem
não ter pai; operações fora de uma interação não têm sessão/interação. Os dois
últimos ligam diretamente a observação à interação e sessão persistidas pelo
principal. `correlation_id` identifica a requisição HTTP correlacionada e
`parent_observation_id` identifica o `_id` do span pai. A2A mantém também a
observação do próprio protocolo e recebe os IDs funcionais pela delegação
assinada. Novos documentos usam `schema_version="2"`; consumidores devem
continuar aceitando documentos legados com `schema_version="1"`. Veja o fluxo e
as limitações em [operacao-observabilidade.md](operacao-observabilidade.md).

Não há índice TTL nem expiração automática configurada para essa coleção.
Registros só deixam o Mongo por exclusão operacional explícita, fora deste
contrato.

## Paginação incremental retomável

Use `updated_at` como marca de alteração e `_id` como desempate estável. Ordene
ascendentemente pelos dois campos. Antes de uma execução, leia o maior
`updated_at` existente como limite superior (`run_watermark`). Leia páginas até
esse limite e salve-o como checkpoint somente depois que todas as páginas forem
persistidas no destino. `page_size` deve ser limitado no cliente para controlar
memória e duração das leituras; um ponto inicial recomendado é 500 documentos.

Exemplo com PyMongo, usando a URI lida do secret store do BI e sem modificar a
coleção:

```python
import os
from datetime import datetime, timedelta

from pymongo import ASCENDING, MongoClient

client = MongoClient(os.environ["BI_OBSERVABILITY_MONGO_URI"], tz_aware=True)
collection = client[os.environ["BI_OBSERVABILITY_DATABASE"]][
    os.environ["BI_OBSERVABILITY_COLLECTION"]
]


def read_page(
    run_watermark: datetime,
    checkpoint_updated_at: datetime | None,
    page_after: tuple[datetime, str] | None,
    page_size: int = 500,
):
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size deve estar entre 1 e 1000")
    # Releia uma janela para absorver atualizações no mesmo milissegundo e
    # diferenças pequenas entre relógios. O destino deve fazer upsert por _id.
    clauses = [{"updated_at": {"$lte": run_watermark}}]
    if checkpoint_updated_at is not None:
        lower_bound = checkpoint_updated_at - timedelta(minutes=5)
        clauses.append({"updated_at": {"$gte": lower_bound}})
    if page_after is not None:
        page_updated_at, page_id = page_after
        clauses.append(
            {
                "$or": [
                    {"updated_at": {"$gt": page_updated_at}},
                    {"updated_at": page_updated_at, "_id": {"$gt": page_id}},
                ]
            }
        )
    query = {"$and": clauses}
    return list(
        collection.find(query)
        .sort([("updated_at", ASCENDING), ("_id", ASCENDING)])
        .limit(page_size)
    )
```

Na primeira carga, não há checkpoint e a leitura cobre os documentos existentes
até `run_watermark`. Dentro da execução, use o último par `(updated_at, _id)` da
página como `page_after`. Ao reiniciar após uma falha, descarte o cursor local
da página e recomece da janela do checkpoint confirmado anteriormente; o upsert
por `_id` torna a repetição segura. Se a gravação no destino falhar, não avance
o checkpoint. O intervalo de cinco minutos é uma premissa inicial do consumidor;
ajuste-o para cobrir o atraso máximo esperado de gravação e a diferença de
relógio entre serviços.

O maior valor pode ser obtido com
`find_one({}, projection={"updated_at": 1}, sort=[("updated_at", -1), ("_id", -1)])`,
usando o índice existente. O consumidor também pode limitar uma carga histórica
por `started_at` para selecionar tentativas iniciadas em um período. Para
sincronização recorrente, `updated_at` é a referência: quando uma tentativa
antiga termina, seu documento é atualizado e volta a aparecer na janela de
releitura. A atualização terminal não cria um novo documento nem remove o
registro inicial do Mongo. Não use somente `started_at` como checkpoint
incremental, pois isso deixaria de observar o término tardio de tentativas
iniciadas antes do período consultado.

O contrato de paginação não oferece snapshot transacional de toda a coleção.
Novas inserções e atualizações continuam sendo capturadas em páginas seguintes
ou na próxima execução, conforme seu `updated_at`. A carga externa deve ser
idempotente por `_id` e só confirmar checkpoints depois de persistir as páginas.
