# Guia de contribuição

Este documento descreve a configuração atual e o fluxo básico de contribuição do
monorepositório Python do Argos AI Service.

## Índice

- [Pré-requisitos](#pré-requisitos)
- [Configuração local](#configuração-local)
- [Verificações locais](#verificações-locais)
- [Locks de dependências](#locks-de-dependências)
- [Conventional Commits](#conventional-commits)
- [Fluxo de branches](#fluxo-de-branches)
- [Pull Requests](#pull-requests)

## Pré-requisitos

Instale:

- [Git](https://git-scm.com/);
- [Python 3.14.3](https://www.python.org/downloads/);
- [Node.js 22.22.1 ou superior](https://nodejs.org/) para Prettier, Husky,
  Commitlint e lint-staged.

## Configuração local

Clone o repositório e entre na pasta:

```bash
git clone https://github.com/Argos-app/argos-ai-service.git
cd argos-ai-service
```

Crie a `.venv` na raiz do repositório usando Python 3.14 e ative-a no
PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

O ambiente precisa ser ativado novamente ao abrir um terminal novo. Para sair
dele, execute:

```powershell
deactivate
```

Instale as ferramentas Python usadas pelo CI:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
```

Instale as dependências Node e ative os hooks locais:

```bash
npm ci
```

## Verificações locais

Execute os mesmos grupos principais usados pelo CI:

```bash
ruff check .
ruff format --check .
python -m compileall apps packages tests
pip-audit
npm audit --audit-level=high
npm run format:check
```

Com a `.venv` ativada, execute os testes Python:

```bash
python -m pytest tests apps packages -m "not integration" --cov=apps --cov=packages --cov-fail-under=75
```

As integrações de cada serviço ficam em `apps/<serviço>/tests/integration`.
Consulte o README dessa pasta para subir as dependências descartáveis e ativar
os testes de integração.

Para reproduzir os grupos da CI, siga esta ordem a partir da raiz:

1. Execute as verificações de qualidade e a suíte unitária acima. A CI instala o
   lock da raiz em Linux/Python 3.14.3 e também audita separadamente os cinco
   locks `apps/<serviço>/pylock.toml`; a auditoria de locks Linux deve ser
   executada em Linux, pois algumas dependências não são instaláveis no Windows.
2. Valide e construa as cinco imagens, suba a pilha e execute os health checks e
   a interação em [Docker local](infrastructure/docker/README.md). Use um
   projeto e portas separados se já houver uma pilha ativa. O modo simulado é o
   usado pela CI; providers reais são uma validação adicional.
3. Execute as integrações descartáveis pelos guias da
   [Main API](apps/main_api/tests/integration/README.md),
   [e-mail](apps/mcp_email/tests/integration/README.md),
   [A2A](apps/a2a_agent/tests/integration/README.md),
   [dados](apps/mcp_data/tests/integration/README.md) e
   [predição](apps/mcp_prediction/tests/integration/README.md). Ative as
   variáveis de cada guia: sem elas, partes das suítes são ignoradas.
4. Remova somente a infraestrutura descartável criada para cada teste. A opção
   `down --volumes` remove os volumes do projeto Compose indicado.

Para executar a auditoria dos locks como no job Linux da CI:

```bash
audit_file=$(mktemp)
for lock in apps/{main_api,mcp_data,mcp_email,mcp_prediction,a2a_agent}/pylock.toml; do
  python -c 'import sys,tomllib; p=tomllib.load(open(sys.argv[1],"rb"))["packages"]; print("\n".join("{}=={}".format(x["name"], x["version"]) for x in p))' "$lock" > "$audit_file"
  pip-audit --no-deps -r "$audit_file" || { rm -f "$audit_file"; exit 1; }
done
rm -f "$audit_file"
```

Para corrigir formatação Python:

```bash
ruff check --fix .
ruff format .
```

Para corrigir arquivos suportados pelo Prettier:

```bash
npm run format:write
```

## Locks de dependências

O CI e as imagens Docker usam arquivos `pylock.toml` gerados para Linux e Python
3.14.3. Para regenerá-los de forma reproduzível, use Docker com pip 26.2.1:

```powershell
docker run --rm --mount "type=bind,source=$($PWD.Path),target=/workspace" --workdir /workspace python:3.14.3-slim sh -c "python -m pip install --disable-pip-version-check pip==26.2.1 && python scripts/docker/refresh-python-locks.py"
```

O comando atualiza o lock de CI na raiz e um lock de runtime para cada uma das
cinco aplicações. Mantenha os `requirements.txt` como declarações de faixa e
inclua no mesmo commit os locks regenerados quando alterar dependências. O
`requirements-dev.txt` da raiz reúne as dependências de testes, integração e
ferramentas de todos os serviços; os requisitos de runtime ficam em cada
serviço.

## Conventional Commits

As mensagens de commit são validadas pelo Commitlint através do Husky.

Formato:

```text
tipo(escopo): descrição curta
```

Exemplos:

```text
feat(main-api): adiciona endpoint de saúde
fix(mcp): corrige conexão com PostgreSQL
test(a2a): adiciona testes do agente
chore(ci): ajusta workflow de qualidade
docs: atualiza guia de contribuição
```

Tipos usuais:

| Tipo       | Uso                                      |
| ---------- | ---------------------------------------- |
| `feat`     | Nova funcionalidade                      |
| `fix`      | Correção de bug                          |
| `refactor` | Refatoração sem mudança de comportamento |
| `test`     | Alteração ou inclusão de testes          |
| `chore`    | Manutenção e dependências                |
| `docs`     | Documentação                             |
| `ci`       | Pipeline e automação                     |

## Fluxo de branches

O fluxo recomendado é:

```text
feature/* ou fix/* → develop → main
```

| Branch      | Propósito                                |
| ----------- | ---------------------------------------- |
| `main`      | Código aprovado para produção            |
| `develop`   | Integração e validação antes de produção |
| `feature/*` | Novas funcionalidades                    |
| `fix/*`     | Correções de bugs                        |
| `chore/*`   | Manutenção e configuração                |

Exemplo:

```bash
git checkout develop
git pull
git checkout -b feature/nome-da-feature
```

## Pull Requests

1. Faça push da branch de trabalho.
2. Abra o Pull Request para `develop`.
3. Preencha o template da PR.
4. Aguarde a aprovação exigida pelas regras do repositório.
5. Aguarde os checks `Quality checks` e `Unit tests` do CI.
6. Use Squash Merge quando essa for a política adotada para a branch.

As branches `develop` e `main` devem exigir os checks do CI por meio de branch
protection no GitHub. O workflow executa em pushes para `main` ou `develop`, em
PRs destinadas a essas branches e por acionamento manual.
