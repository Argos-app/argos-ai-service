# MongoDB — guia para agentes

[Configuração](mongodb-configuration.md),
[estruturas](mongodb-collection-structures.md) e
[dicionário BI](mongodb-analytics-dictionary.md) descrevem collections dos
serviços, produtores, campos, retenção e limites de leitura. Os modelos e
repositórios dos serviços são a fonte para schemas e índices.

Ao alterar um documento ou coleção, confira os consumidores, índices, TTL,
checkpointer e os guias de observabilidade em `packages/observability/docs`. O
MCP de dados lê suas collections de negócio; a Main API e o MCP de e-mail
possuem estados de e-mail separados.
