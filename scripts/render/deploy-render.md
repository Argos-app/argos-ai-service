# Deploy no Render

O workflow `.github/workflows/deploy.yml` atualiza serviços conectados ao Git
usando o runtime Python nativo do Render. Cada aplicação instala seu próprio
`apps/<serviço>/pylock.toml`; `packages/` acompanha o checkout. O Actions envia
apenas o SHA para a API do Render, que busca e constrói o código. Não há upload
de imagens nem publicação em registry.

## Ambientes e atualização automática

| Branch    | GitHub Environment | Serviços Render                          |
| --------- | ------------------ | ---------------------------------------- |
| `develop` | `hm`               | Cinco serviços exclusivos de homologação |
| `main`    | `prod`             | Cinco serviços exclusivos de produção    |

O evento `push` inclui merges normais, squash e rebase. Push direto nessas
branches também dispara o fluxo; configure proteção de branch para exigir PR e
CI aprovada se somente merges forem permitidos.

O workflow aguarda sucesso da execução `push` da CI (`ci.yml`) para o mesmo SHA,
por até 30 minutos. Os deploys dos serviços selecionados executam em paralelo,
sem cancelar os demais se um falhar. Cada job acompanha seu deploy por até 20
minutos e só conclui com `live` e o SHA esperado; isso confirma o deploy do
processo, não uma homologação funcional de todas as integrações externas.

A seleção compara o commit atual com o último workflow de deploy concluído com
sucesso na mesma branch, incluindo todas as alterações do intervalo:

- Alterações em `apps/<serviço>/` atualizam somente aquela aplicação, incluindo
  dependências, arquivos adicionados, removidos e renomeados.
- Alterações em `packages/`, `scripts/render/`, `.python-version`,
  `pyproject.toml`, `pylock.toml`, `render.yaml` ou no próprio `deploy.yml`
  atualizam os cinco serviços. A regra de `packages/` é conservadora e inclui
  qualquer pacote compartilhado atual ou futuro.
- Alterações somente em documentação na raiz, testes da raiz ou infraestrutura
  local não geram deploy. Um README ou guia dentro de `apps/<serviço>/`
  seleciona o serviço correspondente; documentação em `packages/` ou
  `scripts/render/` seleciona os cinco serviços.
- A primeira execução, a execução após falha/cancelamento e a execução manual
  por `workflow_dispatch` atualizam os cinco serviços. Na execução manual,
  selecione `develop` ou `main`; outras branches são ignoradas e o commit
  selecionado precisa ter CI aprovada.

Execuções são serializadas por branch. Se a última execução concluída falhar ou
for cancelada (incluindo a substituição de uma execução pendente), o próximo
deploy atualiza todos os serviços. Isso cobre também a reversão de mudanças que
chegaram ao Render em um deploy parcialmente bem-sucedido.

## Configuração inicial no Render

Crie dez serviços conectados a este repositório Git: cinco com branch `develop`
e cinco com branch `main`. Use nomes distintos, por exemplo `argos-main-api-hm`
e `argos-main-api-prod`, e mantenha todos os serviços de um ambiente na mesma
região. Com o código atual, crie os cinco como **Web Services** e use suas URLs
HTTPS públicas nas chamadas entre aplicações. O cliente MCP do principal
(`apps/main_api/app/infrastructure/mcp/client.py`) e a configuração do A2A no
principal (`apps/main_api/app/settings.py`) rejeitam HTTP remoto fora do modo
local. Private Services com HTTP interno exigiriam uma adaptação explícita ou
uma solução de TLS privado; apenas trocar o hostname não funciona.

Em todos os serviços:

- **Runtime:** Python.
- **Root Directory:** vazio (raiz do repositório). Configurar `apps/<serviço>`
  impede que o Render disponibilize `packages/` no build e no runtime.
- **Auto-Deploy:** Off. O Actions controla os deploys após a CI; habilitar
  também o deploy nativo do Render duplicaria execuções e ignoraria esse gate.
- **Python:** `3.14.3`, fixado em `.python-version`. Remova um `PYTHON_VERSION`
  divergente no painel, pois ele tem precedência sobre o arquivo.
- **Build/Start:** use os comandos da tabela abaixo, a partir da raiz.

| Aplicação        | Build Command                                 | Start Command                                   |
| ---------------- | --------------------------------------------- | ----------------------------------------------- |
| `main_api`       | `bash scripts/render/build.sh main_api`       | `python scripts/render/start.py main_api`       |
| `mcp_data`       | `bash scripts/render/build.sh mcp_data`       | `python scripts/render/start.py mcp_data`       |
| `mcp_email`      | `bash scripts/render/build.sh mcp_email`      | `python scripts/render/start.py mcp_email`      |
| `mcp_prediction` | `bash scripts/render/build.sh mcp_prediction` | `python scripts/render/start.py mcp_prediction` |
| `a2a_agent`      | `bash scripts/render/build.sh a2a_agent`      | `python scripts/render/start.py a2a_agent`      |

O launcher acrescenta a raiz e somente o diretório Python da aplicação ao
`PYTHONPATH`, usa `0.0.0.0` e adapta a porta `PORT` do Render às configurações
de cada serviço. Na ausência de `PORT`, usa `10000`. Nas URLs HTTPS públicas,
use o domínio fornecido pelo painel sem acrescentar a porta interna `10000`.

Configure `/health/ready` como HTTP health check do `main_api`. O A2A permite
`/health/live` sem autenticação; sua readiness exige Bearer e deve ser validada
por uma chamada autenticada. Os três MCPs não expõem uma rota HTTP de saúde
pública: mantenha a verificação de porta do Render e valide as tools em uma
homologação autenticada.

## Secrets do GitHub Actions

Em **Settings → Environments**, crie `hm` e `prod`. Em cada ambiente cadastre os
mesmos nomes, com valores próprios:

| Secret                             | Valor no ambiente selecionado               |
| ---------------------------------- | ------------------------------------------- |
| `RENDER_API_KEY`                   | Chave da API Render com acesso aos serviços |
| `RENDER_SERVICE_ID_MAIN_API`       | ID `srv-...` do `main_api`                  |
| `RENDER_SERVICE_ID_MCP_DATA`       | ID `srv-...` do `mcp_data`                  |
| `RENDER_SERVICE_ID_MCP_EMAIL`      | ID `srv-...` do `mcp_email`                 |
| `RENDER_SERVICE_ID_MCP_PREDICTION` | ID `srv-...` do `mcp_prediction`            |
| `RENDER_SERVICE_ID_A2A_AGENT`      | ID `srv-...` do `a2a_agent`                 |

Os dez IDs devem ser distintos. Restrinja `hm` a `develop` e `prod` a `main`.
Para atualização inteiramente automática, não configure revisores obrigatórios
nesses GitHub Environments. O script verifica a branch configurada no serviço, o
runtime Python, a raiz e Auto-Deploy desabilitado antes de disparar o build.

## Configuração das aplicações

Credenciais de runtime ficam nos serviços Render, separadas dos secrets de
deploy do GitHub. Use os arquivos `apps/<serviço>/.env.example` como inventário,
sem copiar os placeholders ou os valores de localhost:

- No `main_api`, configure `APP_ENV=development` em HM e `APP_ENV=production` em
  produção, além de `AUTH_MODE=jwt`. Configure Firebase, PostgreSQL de
  identidade, Mongo replica set, modelos e chaves próprias de acesso/delegação.
  Os valores locais `APP_ENV=local` e `AUTH_MODE=stub` não servem para cloud.
- Use bancos, collections/índices, Qdrant, SMTP e credenciais adequados a cada
  ambiente. O pipeline não provisiona nem migra essas dependências.
- Aponte os `MAIN_API_MCP_*_URL` para os serviços **do mesmo ambiente**, com
  `/mcp`, e o cliente A2A para `/a2a`. Configure tokens e pares de chaves
  compatíveis entre emissores/consumidores. As URLs anunciadas e audiences
  (`*_RESOURCE_SERVER_URL`, `A2A_ENDPOINT_URL`) devem coincidir com as usadas
  pelos clientes, incluindo esquema, host, porta e caminho.
- No A2A, configure `APP_ENV` também para o ambiente correto de observabilidade.
  O launcher usa `apps/a2a_agent/data/documents` como corpus padrão, que contém
  documentos sintéticos de demonstração; use um corpus aprovado para operação.
  Por padrão o manifesto fica em `/tmp/argos-a2a/manifest.json`, é efêmero e
  pode ser reconstruído após reinícios. Para persistência, configure disco e
  sobrescreva `A2A_MANIFEST_PATH` com caminho absoluto. Se usar corpus externo,
  configure também `A2A_DOCUMENT_SOURCE_ROOT` com caminho absoluto.

O sucesso dos cinco deploys não é uma transação: durante a atualização podem
coexistir versões diferentes. Mudanças em contratos compartilhados devem manter
compatibilidade entre versões consecutivas. Em falha, consulte o link do deploy
nos logs do job; uma execução manual na branch correspondente reimplanta os
cinco serviços após a CI. Timeout no Actions não cancela o deploy que já foi
aceito pelo Render: confira seu estado antes de repetir.

## Referências

- [Render: monorepos e disponibilidade da raiz](https://render.com/docs/monorepo-support)
- [Render: versão do Python](https://render.com/docs/python-version)
- [Render API: deploy de um commit específico](https://api-docs.render.com/reference/create-deploy)
