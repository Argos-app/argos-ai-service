# Configuração e deploy no Render

Este roteiro prepara um ambiente completo. Repita-o com recursos e credenciais
separados para homologação (`develop`, GitHub Environment `hm`) e produção
(`main`, Environment `prod`). O [guia de deploy](README.md) descreve a seleção
automática de serviços e o comportamento do workflow. Os arquivos
`apps/<serviço>/.env.example` são o inventário de variáveis por serviço: não
copie seus valores `localhost`, placeholders ou `APP_ENV=local` para a nuvem.

## 1. Prepare as dependências

1. **PostgreSQL:** obtenha host, porta, database e TLS do provedor do banco
   cadastral. Crie usuários individuais com acesso `SELECT` às tabelas usadas
   por cada serviço, conforme
   [acesso PostgreSQL](../../packages/security/README.md) e as queries
   efetivamente executadas pelos adapters. A senha do DSN é a do usuário
   PostgreSQL. Faça URL encoding de caracteres especiais na senha:
   `postgresql://usuario:senha_codificada@host:5432/banco?sslmode=require`.
2. **MongoDB:** provisione um replica set acessível pelas aplicações, obtenha a
   URI com TLS e crie usuários com privilégios adequados. Main API e MCP de
   e-mail precisam criar índices e gravar; MCP de dados precisa ler as fontes de
   negócio; A2A precisa gravar quando a observabilidade Mongo for ativada.
   Verifique database, collections e permissões em
   [configuração Mongo](../../infrastructure/mongodb/configuration.md). As
   collections próprias são criadas conforme uso; `cattle_documents` e
   `medicine_documents` precisam ser fornecidas pelo produtor de negócio.
3. **Qdrant:** crie um cluster acessível ao A2A, obtenha a URL HTTPS e as
   credenciais de leitura e escrita. Configure dimensões de acordo com cada
   modelo; não compartilhe o mesmo espaço vetorial entre embeddings diferentes.
4. **Firebase:** crie ou escolha o projeto de autenticação, habilite o método
   usado pelo aplicativo e obtenha o ID do projeto. Gere uma chave de service
   account no console do Google Cloud/Firebase e entregue o JSON ao serviço como
   Secret File; informe o caminho montado em
   `MAIN_API_FIREBASE_CREDENTIALS_FILE`. Use uma identidade apropriada para
   verificar tokens e revogação.
5. **Modelos:** obtenha uma chave da OpenAI e uma do OpenRouter nos painéis dos
   respectivos provedores. Confirme acesso aos modelos e endpoints configurados
   e saldo disponível. Os nomes e URLs dos exemplos são configurações, não
   garantias de disponibilidade. Para o fallback Anthropic, o código usa o
   protocolo Messages através da base configurada; para embeddings Voyage usa o
   endpoint de embeddings configurado. Valide ambos no ambiente real.
6. **SMTP:** obtenha host, porta, usuário, senha e remetente autorizado no
   provedor de e-mail. Preparar relatório não comprova que o envio funciona.
7. **Opcional:** obtenha endpoint e chave do LangSmith para traces; configure
   Redis se quiser o ranking de uso. Para BI do A2A, configure também sua URI
   Mongo. Custos, coleta best effort e limites de métricas estão no
   [dicionário de BI](../../infrastructure/mongodb/analytics-dictionary.md).

## 2. Gere os segredos internos

Para cada ambiente, gere quatro tokens independentes de serviço e três pares
Ed25519 independentes: acesso da Main API, delegação e assinatura de relatórios.
Execute localmente em um ambiente Python com `cryptography` instalado. A saída
contém segredos: não a publique, não a salve no repositório e não a compartilhe
em logs ou capturas de tela.

```python
import base64
import secrets
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

for purpose in ("access", "delegation", "report"):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    print(purpose, "private", base64.b64encode(private).decode())
    print(purpose, "public", base64.b64encode(public).decode())
for purpose in ("data", "email", "prediction", "a2a"):
    print(purpose, "token", secrets.token_urlsafe(32))
```

Guarde os resultados em um gerenciador de segredos. A chave privada de acesso é
usada somente pela Main API. Para delegação, a Main API usa a privada e os
serviços Data, Prediction e A2A recebem a pública. Para relatórios, Data usa a
privada e Main API e Email recebem a pública. Os IDs escolhidos nos mapas JSON
devem ser exatamente os mesmos IDs configurados no emissor.

## 3. Crie os Web Services

No Render, conecte o repositório e crie cinco Web Services por ambiente. Use
Python, raiz do repositório como Root Directory, Auto-Deploy desligado e a
branch correspondente. Python é fixado pelo `.python-version`. Na tabela do
[guia de deploy](README.md#configuração-inicial-no-render), copie o Build
Command e o Start Command de cada serviço. A Main API usa `/health/ready` como
health check; o A2A oferece `/health/live`. Guarde as URLs HTTPS geradas e os
IDs `srv-...` dos cinco serviços de cada ambiente.

## 4. Cadastre as variáveis de ambiente

No painel de cada serviço, abra **Environment** e preencha as variáveis do seu
`.env.example`. Cadastre os valores no próprio serviço do ambiente correto; o
GitHub Actions não envia os arquivos `.env` locais ao Render.

### Main API

- `APP_ENV=development` para HM ou `production` para produção; `AUTH_MODE=jwt`.
- `MAIN_API_MONGO_URI` e `MAIN_API_MONGO_DATABASE`: URI do replica set e nome do
  banco. `MAIN_API_AUTH_DATABASE_URL`: DSN do usuário cadastral da Main API.
- `MAIN_API_FIREBASE_PROJECT_ID` e `MAIN_API_FIREBASE_CREDENTIALS_FILE`: ID do
  Firebase e caminho do Secret File. `MAIN_API_ACCESS_TOKEN_ISSUER`,
  `MAIN_API_ACCESS_TOKEN_AUDIENCE`, `MAIN_API_ACCESS_TOKEN_KEY_ID` e
  `MAIN_API_ACCESS_TOKEN_PRIVATE_KEY`: emissor, audience e identificador
  escolhidos e chave privada de acesso gerada na etapa 2.
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL_FAST`,
  `OPENAI_MODEL_SPECIALIST`, `OPENROUTER_API_KEY`, `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_MODEL_FAST_FALLBACK`, `ANTHROPIC_MODEL_SPECIALIST_FALLBACK` e
  `MAIN_API_MODEL_MODE=provider`: chaves, endpoints e modelos selecionados.
- `JEV_URL`, `JEV_MODEL`, `DECISIONS_URL`, `DECISIONS_MODEL` e
  `INPUT_RISK_THRESHOLD`: endpoints e modelos de avaliação de entrada e limiar
  desejado. O JEV usa a chave OpenRouter; Decisions usa a chave OpenAI. O
  exemplo usa limiar `0.8`. Se ambos falharem, a entrada é bloqueada.
- `MAIN_API_MCP_DATA_URL`, `MAIN_API_MCP_EMAIL_URL` e
  `MAIN_API_MCP_PREDICTION_URL`: URLs HTTPS dos serviços do mesmo ambiente com
  `/mcp`. Seus respectivos campos terminados em `_TOKEN` recebem os tokens
  individuais da etapa 2. `MAIN_API_DOCUMENT_A2A_URL` aponta para a URL A2A com
  `/a2a`, e `MAIN_API_DOCUMENT_A2A_SERVICE_TOKEN` recebe o token A2A.
- `MAIN_API_MCP_DELEGATION_ISSUER`, `MAIN_API_MCP_DELEGATION_KEY_ID` e
  `MAIN_API_MCP_DELEGATION_PRIVATE_KEY`: emissor e ID escolhidos e chave privada
  de delegação. `MAIN_API_MCP_DATA_REPORT_TRUSTED_SIGNATURE_KEYS` é um JSON como
  `{"report-v1":"<chave-pública-report-base64>"}`.
- `MODEL_*_COST_PER_MILLION_TOKENS`, `JEV_*_COST_PER_MILLION_TOKENS` e
  `DECISIONS_*_COST_PER_MILLION_TOKENS`: tarifas em USD por milhão de tokens,
  obtidas da tabela do provedor contratado; valores vazios impedem estimativas
  quando o provedor não retorna custo. Não trate os números de exemplo como
  preço confirmado. As chamadas de geração OpenAI solicitam
  `service_tier=priority` (Fast): use as tarifas Fast contratadas para
  estimativas. Se o provedor atender em outro tier, uma tarifa fixa pode
  produzir uma estimativa imprecisa; confira o tier retornado e a fatura.
- `MAIN_API_LANGSMITH_*`, `MAIN_API_OBSERVABILITY_REDIS_URL` e
  `USER_USAGE_REDIS_KEY_PREFIX` são opcionais conforme os recursos desejados.
  Ativar captura de conteúdo envia conteúdo de traces ao LangSmith.

### MCP de dados

- `MCP_DATA_SQL_DSN`: DSN do usuário PostgreSQL próprio do serviço.
- `MCP_DATA_MONGO_URI` e `MCP_DATA_MONGO_DATABASE`: credencial e banco que
  contêm `cattle_documents` e `medicine_documents`.
- `MCP_DATA_MONGO_RELATIONAL_ID_FORMAT` e `MCP_DATA_MONGO_MEDICINE_ID_FORMAT`:
  `string` ou `uuid_standard`, conforme o BSON real das respectivas fontes.
- `MCP_DATA_INTERNAL_SERVICE_TOKEN`: mesmo valor de `MAIN_API_MCP_DATA_TOKEN`.
  `MCP_DATA_RESOURCE_SERVER_URL`: URL HTTPS pública do Data com `/mcp`.
  `MCP_DATA_AUTH_ISSUER_URL`: URL HTTPS do emissor anunciado na configuração
  MCP; use o endereço coerente com a implantação.
- `MCP_DATA_DELEGATION_ISSUER` deve coincidir com o emissor da Main API;
  `MCP_DATA_TRUSTED_DELEGATION_KEYS` recebe JSON com ID e chave pública de
  delegação. `MCP_DATA_REPORT_SIGNING_KEY_ID` e
  `MCP_DATA_REPORT_SIGNING_PRIVATE_KEY` recebem o ID e a privada de relatórios.

### MCP de e-mail

- `MCP_EMAIL_SQL_DSN`, `MCP_EMAIL_MONGO_URI` e `MCP_EMAIL_MONGO_DATABASE`:
  credenciais e bancos acessíveis ao serviço.
- `MCP_EMAIL_INTERNAL_SERVICE_TOKEN` deve coincidir com
  `MAIN_API_MCP_EMAIL_TOKEN`. Configure `MCP_EMAIL_AUTH_ISSUER_URL` e
  `MCP_EMAIL_RESOURCE_SERVER_URL` com as URLs HTTPS adequadas; a segunda é a URL
  do serviço com `/mcp`.
- `MCP_EMAIL_TRUSTED_SIGNATURE_KEYS`: JSON com o ID e a pública de assinatura
  dos relatórios do Data.
- `MCP_EMAIL_NO_REPLY_ADDRESS`, `MCP_EMAIL_SMTP_HOST`, `MCP_EMAIL_SMTP_PORT`,
  `MCP_EMAIL_SMTP_USERNAME`, `MCP_EMAIL_SMTP_PASSWORD` e
  `MCP_EMAIL_SMTP_USE_TLS`: valores fornecidos pelo serviço SMTP. Use remetente
  autorizado e teste com destinatário controlado.

### MCP de predição

- `MCP_PREDICTION_SQL_DSN`: DSN do usuário de leitura de predição.
- `MCP_PREDICTION_INTERNAL_SERVICE_TOKEN` deve coincidir com
  `MAIN_API_MCP_PREDICTION_TOKEN`. Configure `MCP_PREDICTION_AUTH_ISSUER_URL` e
  `MCP_PREDICTION_RESOURCE_SERVER_URL` com URLs HTTPS corretas; esta última
  aponta ao serviço com `/mcp`.
- `MCP_PREDICTION_DELEGATION_ISSUER` e `MCP_PREDICTION_TRUSTED_DELEGATION_KEYS`:
  emissor da Main API e JSON com ID e pública de delegação.
  `MCP_PREDICTION_DATA_ORIGIN=recorded` quando a fonte tiver dados reais
  registrados; `unknown` não fornece estimativa numérica.

### A2A

- `APP_ENV`: mesmo ambiente da Main API; `A2A_MODEL_MODE=provider`.
- `A2A_ENDPOINT_URL` e `A2A_RESOURCE_SERVER_URL`: URL HTTPS pública do A2A com
  `/a2a`, igual a `MAIN_API_DOCUMENT_A2A_URL`. `A2A_INTERNAL_SERVICE_TOKEN` deve
  coincidir com `MAIN_API_DOCUMENT_A2A_SERVICE_TOKEN`.
- `A2A_DELEGATION_ISSUER` deve coincidir com o emissor de delegação da Main API;
  `A2A_TRUSTED_DELEGATION_KEYS` recebe o mesmo ID e pública de delegação.
  `A2A_PERMISSION_SQL_DSN` é o DSN do usuário PostgreSQL do A2A.
- `A2A_QDRANT_URL`, `A2A_QDRANT_READ_API_KEY`, `A2A_QDRANT_WRITE_API_KEY`,
  `A2A_QDRANT_COLLECTION`, `A2A_QDRANT_VECTOR_SIZE`,
  `A2A_QDRANT_VOYAGE_VECTOR_SIZE` e `A2A_QDRANT_DISTANCE`: dados do cluster e
  dimensões dos modelos escolhidos.
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL_SPECIALIST`,
  `OPENAI_EMBEDDINGS_URL`, `OPENAI_EMBEDDINGS_MODEL`, `OPENROUTER_API_KEY`,
  `ANTHROPIC_BASE_URL`, `ANTHROPIC_MODEL_SPECIALIST_FALLBACK`,
  `VOYAGE_EMBEDDINGS_URL` e `VOYAGE_EMBEDDINGS_MODEL`: chaves, endpoints e
  modelos de geração e embedding. Configure também as tarifas `MODEL_*` do
  exemplo se desejar estimativa de custo.
- `A2A_DOCUMENT_SOURCE_ROOT`: diretório absoluto do corpus disponível ao
  processo; `A2A_DOCUMENT_CATALOG_NAME`: nome do catálogo nesse diretório.
  Providencie PDFs e catálogo aprovados. `A2A_MANIFEST_PATH` é o arquivo de
  estado gravável; o padrão do launcher em `/tmp` é efêmero. O diretório dos
  exemplos incluídos no repositório contém documentos sintéticos.
- `A2A_OBSERVABILITY_MONGO_URI`, `A2A_OBSERVABILITY_MONGO_DATABASE`,
  `A2A_OBSERVABILITY_REDIS_URL` e `A2A_LANGSMITH_*` habilitam observabilidade
  adicional. `A2A_LANGSMITH_CAPTURE_CONTENT=true` envia conteúdo de traces.

Os campos `*_HOST` e `*_PORT` são definidos pelo launcher a partir de `PORT` do
Render. Limites e outras opções com valores padrão constam nos exemplos e no
código; ajuste-os somente quando forem necessários ao ambiente.

## 5. Configure o GitHub Actions

1. No GitHub, abra **Settings → Environments** e crie `hm` e `prod`.
2. No painel do Render, gere uma API key com acesso aos serviços do workspace.
3. Em cada Environment, crie `RENDER_API_KEY` e os cinco secrets
   `RENDER_SERVICE_ID_MAIN_API`, `RENDER_SERVICE_ID_MCP_DATA`,
   `RENDER_SERVICE_ID_MCP_EMAIL`, `RENDER_SERVICE_ID_MCP_PREDICTION` e
   `RENDER_SERVICE_ID_A2A_AGENT`, com os IDs daquele ambiente.
4. Restrinja `hm` à branch `develop` e `prod` à `main`. O workflow verifica a
   branch, o runtime e o Auto-Deploy dos serviços antes de publicar.

## 6. Execute o deploy manual pelo GitHub

1. Confirme que o commit desejado está em `develop` ou `main` no GitHub.
2. Abra **Actions → CI → Run workflow**, escolha a mesma branch e aguarde o
   sucesso da CI para o mesmo commit. Uma execução `push` bem-sucedida do mesmo
   SHA também serve.
3. Abra **Actions → Deploy Render → Run workflow**, escolha a mesma branch e
   execute. O modo manual seleciona os cinco serviços do ambiente.
4. Acompanhe os jobs e os links de deploy do Render. O workflow espera o estado
   `live` e o SHA solicitado. Se houver timeout, confira no Render antes de
   repetir: o deploy aceito pode continuar executando.
5. Confira o health da Main API, a readiness autenticada do A2A, autenticação
   Firebase, uma sessão e interação completas, uma consulta documental e as
   tools MCP. Confirme observações no Mongo/LangSmith e teste o envio com
   destinatário autorizado. Health check não confirma chamadas reais aos
   providers nem todos os fluxos.

O deploy automático também ocorre após push elegível em `develop` ou `main`,
depois da CI do mesmo SHA. Um deploy diretamente pelo painel Render não passa
pelo gate e pela seleção do workflow; se for usado, confira manualmente o commit
e a versão de todos os serviços.
