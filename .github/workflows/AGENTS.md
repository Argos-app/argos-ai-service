# GitHub Actions — guia para agentes

`ci.yml` executa qualidade, build e smoke da pilha integrada, testes unitários e
integrações com infraestrutura descartável. `deploy.yml` planeja e executa
deploy Render para `develop` e `main` após a CI do commit; a notificação Discord
usa workflow separado.

Ao mudar a CI, mantenha as suítes e imagens dos cinco serviços, os artefatos e a
limpeza das dependências de teste. O deploy depende do resultado da CI e de
segredos configurados no GitHub; validar um YAML localmente não prova execução
nem implantação remota. Consulte [CONTRIBUTING.md](../../CONTRIBUTING.md),
[deploy Render](../../scripts/render/deploy-render.md) e os testes do planejador
antes de alterar seleção de serviços ou regras de publicação.

O planejador ignora documentação, exceto contratos OpenAPI/Swagger, e seleciona
um serviço por alterações em `apps/<serviço>/`. Alterações em `packages/` e
outros caminhos compartilhados selecionados pelo planejador afetam todos os
serviços. Pushes em `develop` ou `main` podem acionar deploy após a CI do mesmo
commit; o deploy manual também aceita CI executada manualmente para o mesmo SHA.
