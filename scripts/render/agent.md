# Deploy Render — guia para agentes

O [guia de deploy](deploy-render.md) documenta ambientes, seleção de serviços,
CI, segredos e comandos. `plan.py` seleciona serviços pelos caminhos alterados;
`deploy.py`, `build.sh` e `start.py` executam as etapas do deploy.

Alterações em qualquer arquivo de `scripts/render/`, inclusive documentação,
selecionam os cinco serviços em pushes elegíveis para `develop` ou `main`.
Preserve a espera pela CI do commit e execute
`python -m pytest tests/test_render_deploy.py -q` ao mudar seleção ou execução.
