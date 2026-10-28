# GitHub Actions — guia para agentes

`workflows/ci.yml` executa qualidade, build e smoke da pilha integrada, testes
unitários e integrações com infraestrutura descartável. `deploy.yml` planeja e
executa deploy Render para `develop` e `main` após a CI do commit; a notificação
Discord usa workflow separado.

Ao mudar a CI, mantenha as suítes e imagens dos cinco serviços, os artefatos e a
limpeza das dependências de teste. O deploy depende do resultado da CI e de
segredos configurados no GitHub; validar um YAML localmente não prova execução
nem implantação remota. Consulte [CONTRIBUTING.md](../CONTRIBUTING.md),
[deploy Render](../docs/deploy-render.md) e os testes do planejador antes de
alterar seleção de serviços ou regras de publicação.

O planejador seleciona um serviço por alterações em qualquer arquivo sob seu
diretório `apps/<serviço>/`, inclusive documentação. Alterações em `packages/` e
outros caminhos compartilhados selecionados pelo planejador afetam todos os
serviços. Em pushes para `develop` ou `main`, essas mudanças podem acionar o
deploy após a CI.
