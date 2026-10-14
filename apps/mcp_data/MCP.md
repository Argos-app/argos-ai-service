# MCP de dados

Guia vigente da interface MCP Streamable HTTP. Schemas das tools e contratos
tipados do código são normativos.

Aplicação independente de consulta para PostgreSQL e MongoDB. Expõe o transporte
MCP Streamable HTTP em `/mcp` com consultas de pecuária para Fazenda, origem
assinada de relatórios e catálogo/detalhes de medicamentos para Doutor.

## Configuração

O processo encerra com erro se qualquer variável obrigatória estiver ausente:

| Variável                               | Finalidade                                                                   |
| -------------------------------------- | ---------------------------------------------------------------------------- |
| `MCP_DATA_SQL_DSN`                     | DSN PostgreSQL de uma conta provisionada para leitura.                       |
| `MCP_DATA_MONGO_URI`                   | URI MongoDB de uma conta provisionada com papel `read` no banco configurado. |
| `MCP_DATA_MONGO_DATABASE`              | Banco MongoDB.                                                               |
| `MCP_DATA_MONGO_CATTLE_COLLECTION`     | Nome confirmado/configurado da coleção de documentos bovinos.                |
| `MCP_DATA_MONGO_RELATIONAL_ID_FORMAT`  | Representação do UUID relacional no Mongo: `string` ou `uuid_standard`.      |
| `MCP_DATA_MONGO_MEDICINE_COLLECTION`   | Nome confirmado/configurado da coleção de detalhes de medicamentos.          |
| `MCP_DATA_MONGO_MEDICINE_ID_FORMAT`    | Representação de `relational_medicine_id`: `string` ou `uuid_standard`.      |
| `MCP_DATA_INTERNAL_SERVICE_TOKEN`      | Token interno de serviço aceito pelo verificador MCP.                        |
| `MCP_DATA_AUTH_ISSUER_URL`             | Emissor OAuth configurado no transporte MCP.                                 |
| `MCP_DATA_RESOURCE_SERVER_URL`         | URL deste resource server, incluindo `/mcp`.                                 |
| `MCP_DATA_DELEGATION_ISSUER`           | Valor `iss` esperado nos tokens de delegação do serviço principal.           |
| `MCP_DATA_TRUSTED_DELEGATION_KEYS`     | JSON `kid` → chave pública Ed25519 em Base64 do serviço principal.           |
| `MCP_DATA_DELEGATION_MAX_TTL`          | Validade máxima do token de delegação, em segundos; padrão `300`.            |
| `MCP_DATA_DELEGATION_CLOCK_SKEW`       | Tolerância de relógio para validar tokens, em segundos; padrão `30`.         |
| `MCP_DATA_REPORT_SIGNING_KEY_ID`       | Identificador da chave Ed25519 usada para assinar o envelope de relatório.   |
| `MCP_DATA_REPORT_SIGNING_PRIVATE_KEY`  | Chave privada Ed25519 PKCS#8 DER em Base64; deve permanecer em secret store. |
| `MCP_DATA_REPORT_ENVELOPE_TTL_SECONDS` | Validade do envelope assinado; padrão `300`, máximo `300`.                   |
| `MCP_DATA_HOST`                        | Host de escuta; padrão `0.0.0.0`.                                            |
| `MCP_DATA_PORT`                        | Porta de escuta; padrão `8000`.                                              |

O pool PostgreSQL define `default_transaction_read_only=on`, valida esse estado
na inicialização e cada leitura abre transação explicitamente `READ ONLY`. O
adapter Mongo só oferece `find_one` e `find_many`, com limite para listas. O
runtime não cria índices nem executa seed ou migração. A conta Mongo deve ser
criada pela infraestrutura com papel somente de leitura; o aplicativo não
consegue transformar uma conta privilegiada em read-only. Credenciais e
conectividade são verificadas antes de iniciar o transporte.

O MCP publica `get_lot_report_source` com `payload.lot_id` e
`payload.property_id`. Exige agente `fazenda` e operação `report.read`; revalida
o usuário e a propriedade ativa antes de buscar lote, aplicações ativas e
animais em exceção. A tool não assina resultado incompleto: acima de 100
aplicações ou animais, retorna erro em vez de truncar. O envelope vincula
solicitante, propriedade, lote, timestamp e validade. O consumidor principal e o
MCP de e-mail precisam confiar na chave pública correspondente ao
`MCP_DATA_REPORT_SIGNING_KEY_ID`.

## Execução local

Instale `apps/mcp_data/requirements.txt`, configure as variáveis acima e rode na
raiz do monorepositório:

```powershell
$env:PYTHONPATH = "apps/mcp_data/src"
python -m argos_mcp_data
```

O transporte exige Bearer token com scope `data:read` e contexto delegado
validado em `X-Argos-Delegation`. As consultas de Fazenda exigem `cattle.read`;
a origem de relatório exige `report.read`. Ambas revalidam o escopo de
propriedades. As ferramentas do Doutor exigem agente `doutor`, operação
`medicine.read` e contexto delegado válido; elas consultam o catálogo global,
sem retornar uso ou estoque por propriedade. `list_medicines` consulta o
cadastro relacional; `list_medicine_documents` lê a coleção Mongo separadamente,
sem comparar ou validar o documento contra o SQL. Coleções e representações dos
UUIDs devem refletir a implantação real.

## Diagnóstico e correlação

O principal inclui correlation ID e ID do span pai em claims opcionais da
delegação Ed25519 validada por este servidor e envia `X-Argos-Correlation-ID`
como header diagnóstico. Isso permite correlacionar a chamada com a observação
de saída do `main_api`.
O MCP de dados não persiste uma observação própria; logs do processo podem ser
associados pelo ID de delegação/request quando disponíveis. Valores ausentes em
telemetria não indicam ausência de consulta nem consumo zero. Os IDs não são
argumentos das tools. Consulte [operação de observabilidade](../../docs/operacao-observabilidade.md)
e o [dicionário MongoDB BI](../../docs/dicionario-mongodb-bi.md).
