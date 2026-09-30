# Guia de contribuição

Este documento descreve a configuração atual e o fluxo básico de contribuição do
monorepositório Python do Argos AI Service.

## Índice

- [Pré-requisitos](#pré-requisitos)
- [Configuração local](#configuração-local)
- [Verificações locais](#verificações-locais)
- [Conventional Commits](#conventional-commits)
- [Fluxo de branches](#fluxo-de-branches)
- [Pull Requests](#pull-requests)
- [Comparação com o backend](#comparação-com-o-backend)

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
npm install
```

## Verificações locais

Execute os mesmos grupos principais usados pelo CI:

```bash
ruff check .
ruff format --check .
python -m compileall apps packages tests
pip-audit -r requirements-dev.txt
npm run format:check
```

Com a `.venv` ativada, execute os testes Python:

```bash
python -m pytest -q -p no:cacheprovider
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
protection no GitHub. O workflow executa em qualquer push e em PRs destinadas a
`main` ou `develop`.
