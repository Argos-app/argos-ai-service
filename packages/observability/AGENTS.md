# Observabilidade — guia para agentes

`models.py` define o contrato; `collector.py`, `context.py`, `timing.py` e
`sanitization.py` cuidam da coleta, correlação, tempo e minimização. Os módulos
Mongo e Redis fornecem persistência e projeção de uso; `billing.py` trata custo
do provider e tarifas configuradas. Os runtimes da Main API e A2A integram estes
componentes e possuem instrumentação própria.

Preserve limites de tamanho, fila best effort, deduplicação, IDs de correlação e
privacidade. Custo ou tokens ausentes não equivalem a zero; não exponha conteúdo
nem credenciais fora das políticas existentes. Mudanças no schema persistido ou
nos custos exigem revisão dos dois consumidores, consultas e documentação.
Execute testes do pacote e dos serviços afetados; veja [README.md](README.md). O
[guia de operação](docs/observability-operations.md) descreve correlação, custos
e uso Redis; o [guia de extração](docs/mongodb-observability-extraction.md)
documenta a leitura incremental das observações Mongo.
