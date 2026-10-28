# MCP de dados — guia para agentes

## Fluxo e padrão vigente

O servidor FastMCP recebe uma chamada autenticada, extrai a delegação e chama
casos de uso de consulta. O código separa `domain`, `application` e `ports` de
`adapters` e `infrastructure`; `bootstrap.py` monta os recursos e `server.py`
registra as tools. Os contratos compartilhados estão em `packages/contracts`.

## Cuidados

- Revalide identidade e escopo de propriedades antes de consultar, agregar ou
  produzir relatório; filtros fornecidos pelo modelo não concedem acesso.
- PostgreSQL é consultado com pool e transações read-only; Mongo é consultado
  nas collections de negócio `cattle_documents` e `medicine_documents`. O
  serviço não deve receber SQL ou pipelines arbitrários do modelo.
- O relatório de lote usa assinatura Ed25519, validade curta e limites que
  rejeitam dados incompletos; consumidores validam o mesmo contrato e chave.
- Catálogo relacional e documentos de medicamentos são fontes distintas; não
  infira vínculo ou atualização entre elas sem o contrato do produtor.

## Dependências e verificações

Usa PostgreSQL, MongoDB, `packages/contracts` e `packages/security`. A Main API
o chama por MCP. Veja [MCP.md](MCP.md), [README.md](README.md) e os testes de
integração antes de alterar ferramentas, delegação, paginação ou assinaturas.
