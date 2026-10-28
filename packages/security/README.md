# Segurança compartilhada

O pacote fornece primitivas de acesso, autorização, delegação assinada,
verificação de assinaturas e adapters de consulta cadastral usados pelos
serviços. Os módulos são importados como `packages.security.*`; não há processo
ou endpoint autônomo.

As verificações são exercitadas nos testes de autenticação e delegação dos
serviços. Com as dependências de desenvolvimento instaladas:

```powershell
python -m pytest apps/main_api/tests/unit/test_auth.py apps/mcp_data/tests/unit/test_delegation.py apps/mcp_prediction/tests/unit/test_delegation.py -q
```

Veja [agent.md](agent.md) e os testes dos demais consumidores quando mudar
tokens, políticas ou adapters compartilhados.
