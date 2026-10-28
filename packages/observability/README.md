# Observabilidade compartilhada

Este pacote define observações técnicas tipadas, correlação, sanitização,
medição, cálculo de custos e coleta para os serviços. Há adapters de
persistência Mongo e contador Redis; Main API e A2A conectam esses componentes
aos respectivos runtimes. O pacote não inicia um serviço por conta própria.

```powershell
python -m pytest packages/observability/tests -q
```

Os campos, retenção, rastreamento e consultas estão em
[observability-operations.md](docs/observability-operations.md) e no
[dicionário Mongo](../../infrastructure/mongodb/mongodb-analytics-dictionary.md).
Consulte [agent.md](agent.md) para mudanças que afetem consumidores.
