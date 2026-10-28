# MCP de predição — guia para agentes

## Fluxo e padrão

Após autenticação e delegação, a tool consulta histórico de aplicações e
cobertura para o escopo autorizado e executa regras determinísticas de consumo.
O serviço organiza `domain`, `application` e `ports` com adapters PostgreSQL e
transporte FastMCP. `bootstrap.py` monta as dependências; `server.py` expõe a
tool.

## Cuidados

- Preserve validação e reconsulta de acesso por propriedade, SQL parametrizado e
  transações de leitura. A entrada do modelo não amplia o escopo autorizado.
- Cobertura ausente ou dados inconsistentes devem continuar explícitos no
  resultado. Respeite as unidades, janelas, limites e a indicação de origem;
  dados sintéticos são identificados como simulação.
- Não apresente a estimativa determinística como modelo treinado ou prescrição.
  Os schemas compartilhados são usados pela Main API e devem evoluir com seus
  consumidores.

## Dependências e verificações

Depende de PostgreSQL e de `packages/contracts` e `packages/security`; a Main
API o chama por MCP. Veja [README.md](README.md), os testes unitários das regras
e o guia de integração antes de modificar cálculo, cobertura ou autorização.
