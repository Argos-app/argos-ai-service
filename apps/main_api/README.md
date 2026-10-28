# Main API

A Main API recebe interações autenticadas, mantém sessões e coordena agentes e
serviços externos para responder ao usuário.

## Tecnologias e execução

Usa Python 3.14.3, FastAPI, LangChain, LangGraph, Motor/MongoDB, PostgreSQL,
Redis, os SDKs MCP e A2A e os pacotes compartilhados do monorepositório. As
dependências estão em `requirements.txt` e `pylock.toml`.

A forma integrada, com dependências locais e modelos simulados, está em
[Docker local](../../infrastructure/docker/docker-local.md). Para executar o
processo no host, instale as dependências a partir da raiz, configure no
ambiente as variáveis de [.env.example](.env.example) e disponibilize as
dependências escolhidas. O Python não carrega esse arquivo automaticamente. No
PowerShell:

```powershell
python -m pip install -r apps/main_api/requirements.txt
$env:PYTHONPATH = "apps/main_api"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`/health/ready` depende da configuração e disponibilidade dos componentes
necessários; subir o processo, isoladamente, não garante prontidão. Para testes:

```powershell
python -m pytest apps/main_api/tests/unit -q
```

As instruções para Mongo replica set, PostgreSQL e MCP de e-mail descartáveis
estão em [tests/integration/README.md](tests/integration/README.md).

## Contrato HTTP

| Método e caminho                                            | Operação                                                        |
| ----------------------------------------------------------- | --------------------------------------------------------------- |
| `GET /health/live`, `GET /health/ready`                     | Saúde e prontidão.                                              |
| `POST /v1/auth/firebase`                                    | Troca Firebase ID token por token de acesso do serviço.         |
| `POST /v1/sessions`, `GET /v1/sessions`                     | Cria ou lista sessões do usuário.                               |
| `GET /v1/sessions/{session_id}/messages`                    | Consulta histórico paginado.                                    |
| `POST /v1/sessions/{session_id}/interactions/async`         | Aceita mensagem ou resposta a pergunta; retorna `operation_id`. |
| `GET /v1/sessions/{session_id}/interactions/{operation_id}` | Consulta estado e resultado da interação.                       |

Os schemas, exemplos, erros e autenticação estão no
[contrato da API](docs/api-main-api.md) e em `/docs` quando o processo está
ativo. `X-Argos-Correlation-ID` é retornado nas respostas de negócio.
