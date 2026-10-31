# MCP de predição

Guia vigente da interface MCP Streamable HTTP. Schemas das tools e contratos
tipados do código são normativos.

Servidor MCP read-only que estima o consumo histórico sazonal de medicamentos
para o agente Analista. O cálculo usa aplicações registradas em uma janela de
365 ou 366 dias e a média diária de cada mês do ano para estimar um horizonte de
1 a 28 dias. Não há modelo treinado: o método é uma extrapolação simples do
histórico disponível.

O MCP não prevê produção, doença ou morte de bovinos, não calcula estoque,
frascos ou compras e não recomenda tratamentos. Cabeças-administrações são
contagens acumuladas de aplicações, não animais únicos tratados. Medicamentos e
unidades permanecem separados.

## Contrato MCP

O transporte Streamable HTTP publica `/mcp` e uma única ferramenta:
`estimate_medicine_consumption`, com um argumento `payload`:

```json
{
	"payload": {
		"property_ids": ["<UUID da propriedade autorizada>"],
		"medicine_names": ["Ivermectina"],
		"as_of": "2026-10-08",
		"horizon_days": 28
	}
}
```

`property_ids` é um filtro opcional com até 100 UUIDs distintos.
`medicine_names` é opcional e aceita até 100 nomes distintos (até 80 caracteres
cada), como nome comercial ou princípio ativo. O MCP resolve esses nomes,
ignorando maiúsculas/minúsculas, contra `medicine.brand_name` e
`medicine.active_ingredient` somente nos medicamentos com aplicações no período
e nas propriedades autorizadas. Havendo mais de uma correspondência, cada
medicamento aparece separadamente na resposta com nome comercial, princípio
ativo e UUID interno. `as_of` é obrigatório, deve ser uma data encerrada no
formato `YYYY-MM-DD`, e `horizon_days` aceita de 1 a 28, com padrão 28. Os
filtros só reduzem a seleção: propriedades precisam estar contidas na delegação
assinada da chamada. Sem `property_ids`, usa-se o escopo delegado completo. Eles
nunca concedem acesso. A consulta recusa resultados acima de 1.000 grupos, sem
retornar uma estimativa parcial.

Se algum nome não corresponder a histórico em cada propriedade selecionada, a
resposta será `insufficient_data`; o MCP não presume consumo zero nem tenta
resolver o nome em um catálogo global fora do escopo autorizado.

A resposta inclui status, janelas histórica e estimada, propriedades, cobertura,
quantidades e eventos históricos e estimados por propriedade, medicamento e
unidade, método, origem dos dados e limitações. `insufficient_data` e
`inconsistent_data` não devem ser interpretados como consumo zero. Origem
desconhecida impede uma estimativa numérica.

A fonte consultada é `application` relacionada a lote, rebanho, propriedade,
lote de medicamento e medicamento. O MCP também verifica consistência de
detalhes individuais de aplicação. Quando configurada, uma relação de cobertura
informa a completude temporal por propriedade; sem essa fonte, a cobertura é
desconhecida. Meses sem eventos registrados entram como zero observado, mas não
provam que não houve aplicações fora do banco. Produtos sem histórico não
recebem estimativa zero.

## Autenticação e acesso

Cada chamada em `/mcp` exige ambos os headers:

- `Authorization: Bearer ...`: token interno do serviço chamador, validado pelo
  MCP e limitado ao scope `prediction:read`.
- `X-Argos-Delegation: ...`: delegação Ed25519 do usuário, validada para chave,
  emissor, audience, agente Analista, operação `prediction.read`, propriedades e
  validade.

O acesso às propriedades é revalidado no PostgreSQL antes da consulta. O filtro
de propriedade deve ser um subconjunto não vazio do escopo autorizado; chamada
sem delegação válida ou com propriedade externa não retorna dados. O token e a
delegação são contexto protegido do transporte, nunca argumentos da ferramenta.

Configure `MCP_PREDICTION_SQL_DSN` com uma credencial PostgreSQL somente
leitura. O runtime também define transações read-only, verifica a sessão ao
iniciar e aplica timeout SQL padrão de 5 segundos (configurável de 1 a 60). O
MCP não cria schema, aplica grants, executa cargas ou persiste estimativas.

## Configuração

Use [`.env.example`](.env.example) como referência dos nomes e formatos. O
serviço lê variáveis do ambiente do processo; não carrega arquivos `.env`
automaticamente. Configure os valores fora do repositório:

| Variável                                      | Uso                                                      |
| --------------------------------------------- | -------------------------------------------------------- |
| `MCP_PREDICTION_SQL_DSN`                      | DSN PostgreSQL com credencial somente leitura.           |
| `MCP_PREDICTION_SQL_TIMEOUT_SECONDS`          | Timeout por operação SQL; padrão 5, faixa 1–60.          |
| `MCP_PREDICTION_INTERNAL_SERVICE_TOKEN`       | Bearer interno compartilhado com o chamador autorizado.  |
| `MCP_PREDICTION_AUTH_ISSUER_URL`              | Emissor configurado no provedor de autenticação MCP.     |
| `MCP_PREDICTION_RESOURCE_SERVER_URL`          | URL pública do recurso, normalmente terminada em `/mcp`. |
| `MCP_PREDICTION_DELEGATION_ISSUER`            | Emissor confiável da delegação do usuário.               |
| `MCP_PREDICTION_TRUSTED_DELEGATION_KEYS`      | Objeto JSON `key_id: chave pública Ed25519 Base64`.      |
| `MCP_PREDICTION_DELEGATION_MAX_TTL`           | TTL máximo aceito, padrão 300 segundos.                  |
| `MCP_PREDICTION_DELEGATION_CLOCK_SKEW`        | Tolerância de relógio, padrão 30 segundos.               |
| `MCP_PREDICTION_DATA_ORIGIN`                  | `recorded`, `synthetic` ou `unknown`; padrão `unknown`.  |
| `MCP_PREDICTION_COVERAGE_RELATION`            | View opcional com cobertura temporal por propriedade.    |
| `MCP_PREDICTION_HOST` / `MCP_PREDICTION_PORT` | Interface e porta HTTP; padrão `0.0.0.0:8000`.           |

`unknown` é intencionalmente seguro: sem declarar a origem pelo serviço, a
resposta não contém estimativa numérica. Não use `synthetic` para rotular dados
reais, nem `recorded` para fixtures.

## Diagnóstico e correlação

O principal envia `X-Argos-Correlation-ID` e transmite `correlation_id` e
`parent_observation_id` em claims opcionais protegidos pela delegação Ed25519. O
servidor valida os UUIDs, mas não grava uma collection própria de observações; a
observação de chamada MCP fica no principal. Portanto, ausência de registro não
prova que não houve consulta e desconhecido não é consumo zero. Os IDs não fazem
parte do payload da ferramenta. Veja
[operação de observabilidade](../../packages/observability/docs/operations.md).

## Execução local

Execute a partir da raiz deste monorepo. O ambiente precisa incluir tanto
`apps/mcp_prediction/src` quanto os diretórios compartilhados `packages`:

```powershell
python -m venv .venv/mcp-prediction
.venv/mcp-prediction/Scripts/python.exe -m pip install -r apps/mcp_prediction/requirements.txt
$env:PYTHONPATH = "$(Get-Location);$(Get-Location)\apps\mcp_prediction\src"
```

Defina as variáveis obrigatórias de configuração no processo e inicie:

```powershell
.venv/mcp-prediction/Scripts/python.exe -m argos_mcp_prediction
```

Para executar os testes unitários e de contrato usando as dependências
compartilhadas de desenvolvimento:

```powershell
python -m venv .venv/argos-ai-service
.venv/argos-ai-service/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/argos-ai-service/Scripts/python.exe -m pytest apps/mcp_prediction/tests/unit packages/contracts/tests/test_prediction.py -q
```

Os testes reais de transporte e PostgreSQL descartável estão em
[`tests/integration/README.md`](tests/integration/README.md).

## Estrutura e artefato

O código do serviço está em `src/argos_mcp_prediction`. Os contratos de predição
e segurança estão em `packages/contracts` e `packages/security`.
`requirements.txt` lista dependências externas do runtime; os pacotes
compartilhados são código deste monorepo, não serviços Python instaláveis do
PyPI. Portanto, qualquer imagem ou artefato executável precisa incluir essas
três árvores de código e configurar o caminho de importação para a raiz do
monorepo e `apps/mcp_prediction/src`. O processo não importa código de
`mcp_email`, `mcp_data` ou `main_api`.

O agente Analista da Main API pode chamar este MCP com delegação assinada quando
os serviços estiverem configurados. O MCP também pode ser executado e testado
isoladamente com credenciais e dados de teste.

Para montar a imagem isolada a partir da raiz do repositório:

```powershell
docker build -f apps/mcp_prediction/Dockerfile -t argos-mcp-prediction .
```

O Dockerfile copia o serviço e `packages/contracts`/`packages/security` para a
imagem, instala as dependências externas e executa com usuário sem privilégios.
Configure os segredos e a conexão no ambiente do container; a imagem não inclui
chave de delegação, token ou DSN.
