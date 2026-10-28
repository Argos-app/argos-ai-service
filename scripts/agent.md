# Scripts operacionais — guia para agentes

- `docker/` gera o `.env` integrado, carrega dados Mongo locais e regenera
  locks.
- `kubernetes/` prepara segredos locais e constrói/carrega imagens no Kind ou
  apenas constrói imagens com `-SkipKindLoad`.
- `render/` seleciona serviços afetados, instala, inicia e acompanha deploys.
- `observability/` consulta o ranking de uso.

Esses scripts têm efeitos reais sobre arquivos, bancos, imagens ou deploys.
Confira argumentos, diretórios e ambiente antes de executá-los; nunca use bases
de produção como fixtures. Mantenha consistente a lista de serviços e os
contratos de build com os workflows, Dockerfiles e guias locais. As alterações
do planejador Render são cobertas em `tests/test_render_deploy.py`. Para
alterações de implantação, leia [render/agent.md](render/agent.md).
