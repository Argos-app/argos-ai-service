# Redis: ranking de uso por usuário

O Redis armazena contadores de uso atualizados durante a operação da Main API e
do A2A. O relatório lê os valores disponíveis e ordena usuários por chamadas,
tokens e custo conhecido em USD. A atualização ocorre durante o processamento; o
ranking é calculado no momento da consulta pelo script, sem uma fila Redis.

## Configuração

| Variável                           | Uso                                                                                     |
| ---------------------------------- | --------------------------------------------------------------------------------------- |
| `MAIN_API_OBSERVABILITY_REDIS_URL` | Conexão usada pela Main API para registrar chamadas e uso de modelos.                   |
| `A2A_OBSERVABILITY_REDIS_URL`      | Conexão usada pelo A2A para registrar uso de modelos e embeddings com usuário delegado. |
| `USER_USAGE_REDIS_KEY_PREFIX`      | Prefixo das chaves; padrão `argos:usage:users:v1`.                                      |
| `OBSERVABILITY_REDIS_URL`          | Conexão usada pelo script de consulta do ranking.                                       |

Configure o mesmo Redis, database e prefixo nos serviços e no script para
combinar seus contadores. As URLs são variáveis de ambiente dos respectivos
processos. Na configuração atual, a observabilidade Redis é opcional; para usar
o ranking, configure a URL no serviço e mantenha o Redis acessível. No A2A, a
criação do contador ocorre junto à configuração de observabilidade Mongo.

## Chaves e contadores

Cada usuário interno tem um hash `<prefixo>:<user_id>` com:

| Campo                                    | Significado                                                                                       |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `name`, `email`                          | Identificação atualizada pela Main API a partir do cadastro do usuário.                           |
| `calls`                                  | Requisições autenticadas contadas pela Main API.                                                  |
| `model_calls`                            | Observações terminais de chamadas de modelo e embedding.                                          |
| `tokens`                                 | Soma dos tokens informados pelos providers.                                                       |
| `unknown_token_calls`                    | Chamadas com contagem de tokens ausente ou incompleta.                                            |
| `cost_usd`                               | Soma dos custos conhecidos em USD, informados pelo provider ou calculados por tarifa configurada. |
| `known_cost_calls`, `unknown_cost_calls` | Quantidade de chamadas com custo conhecido e desconhecido.                                        |

O conjunto `<prefixo>:_seen:<user_id>` guarda IDs de observação para evitar
contagem duplicada. Cada atualização renova o TTL de 24 horas do hash; o
conjunto de IDs recebe TTL de 24 horas ao registrar uma observação nova. Os
valores acumulam enquanto o usuário permanece ativo: não representam uma janela
móvel exata de 24 horas. Custos parciais devem ser interpretados junto de
`unknown_cost_calls`, e não como cobrança financeira.

A gravação é best effort: falhas de Redis, encerramento antes do processamento
da fila de observabilidade e perda de dados do Redis podem eliminar incrementos.
O Redis do Compose local não persiste em disco. A fila de observabilidade é
mantida na memória do processo, e o Redis armazena os contadores do ranking.

## Consultar o ranking

Na raiz do repositório, com as dependências da Main API instaladas e a URL do
Redis acessível ao processo:

```powershell
$env:OBSERVABILITY_REDIS_URL = "redis://localhost:6379/0"
$env:USER_USAGE_REDIS_KEY_PREFIX = "argos:usage:users:v1"
python scripts/observability/rank_usage_24h.py
```

O resultado JSON contém `rankings.by_calls`, `rankings.by_tokens` e
`rankings.by_cost_usd`, além dos limites de completude e dos campos por usuário.
Nome e e-mail são dados pessoais; restrinja o acesso ao Redis e ao relatório.
Para o fluxo de observações e custos, consulte [operações](operations.md).
