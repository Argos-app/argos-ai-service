# Contratos compartilhados

Este pacote reúne modelos Pydantic usados pelas aplicações para documentos, A2A,
e-mail, relatório de lote e predição. Os tipos públicos são reexportados em
[**init**.py](__init__.py); os módulos correspondentes definem seus campos e
validações.

É importado como `packages.contracts` a partir da raiz do monorepositório e não
é um serviço HTTP. Para validar seus modelos com as dependências de
desenvolvimento instaladas:

```powershell
python -m pytest packages/contracts/tests -q
```

Consulte [AGENTS.md](AGENTS.md) antes de alterar schemas compartilhados.
