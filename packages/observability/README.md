# Observabilidade compartilhada

Este pacote define observações técnicas tipadas, correlação, sanitização,
medição, cálculo de custos e coleta para os serviços. Há adapters de
persistência Mongo e contador Redis; Main API e A2A conectam esses componentes
aos respectivos runtimes. O pacote não inicia um serviço por conta própria.

```powershell
python -m pytest packages/observability/tests -q
```

Os campos, retenção, rastreamento e consultas estão em
[operations.md](docs/operations.md) e no
[dicionário Mongo](../../infrastructure/mongodb/analytics-dictionary.md).
Consulte [AGENTS.md](AGENTS.md) para mudanças que afetem consumidores.
