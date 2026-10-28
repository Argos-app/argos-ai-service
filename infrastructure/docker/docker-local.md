# Execução local integrada com Docker

### Modelos e prazo de execução

No modo `provider`, informe em `.env` as URLs dos providers ou gateways
escolhidos: `OPENAI_BASE_URL` e `ANTHROPIC_BASE_URL` definem as bases dos
modelos de chat; `OPENAI_EMBEDDINGS_URL` e `VOYAGE_EMBEDDINGS_URL` definem os
endpoints completos de embeddings. Não há fallback de URL no código; com
providers reais, a aplicação exige as URLs adequadas ao protocolo de cada
integração. Os nomes de modelos também devem ser preenchidos no ambiente; não há
nomes padrão no runtime. A Main API usa `OPENAI_MODEL_FAST` para roteador,
orquestrador, juiz e formatador, e `OPENAI_MODEL_SPECIALIST` para especialistas.
O A2A usa `OPENAI_MODEL_SPECIALIST` para respostas documentais e
`OPENAI_EMBEDDINGS_MODEL` para o índice principal do RAG. O fallback usa
`VOYAGE_EMBEDDINGS_MODEL` e `VOYAGE_API_KEY`. O Qdrant mantém os vetores OpenAI
e Voyage como vetores nomeados separados na mesma coleção. Saídas estruturadas
são solicitadas por JSON Schema nativo.

Opcionalmente, `ANTHROPIC_API_KEY` habilita fallback de respostas: configure
`ANTHROPIC_MODEL_FAST_FALLBACK` para o papel rápido e
`ANTHROPIC_MODEL_SPECIALIST_FALLBACK` para especialistas da Main API e respostas
do A2A. O fallback ocorre em falhas transitórias da OpenAI; a Anthropic recebe a
mesma entrada da chamada que falhou. Se a vetorização OpenAI falhar, a busca usa
Voyage e o vetor nomeado correspondente. A sincronização prepara os documentos
nos dois espaços; embeddings de provedores diferentes não são misturados.

Na Main API, os agentes usam `ProviderStrategy(strict=True)` para solicitar
saída estruturada nativa. Especialistas também vinculam suas ferramentas de
negócio, portanto o endpoint escolhido deve suportar JSON Schema e a combinação
com tools quando necessário. O bootstrap verifica as interfaces localmente, sem
fazer uma chamada ao provider; valide a compatibilidade real em homologação.

`MAIN_API_EXECUTION_TIMEOUT_SECONDS=300` define o prazo total de uma interação.
O timeout de cada chamada, o orçamento de chamadas e a validade das preparações
continuam sendo limites independentes. Aumentar o prazo não transforma uma falha
em sucesso: o retorno permanece degradado quando a interpretação falha, mas
preserva resultados externos verificados. Nenhuma ação de e-mail é repetida para
reparar a saída do modelo.

O Compose da raiz reúne MongoDB, PostgreSQL, Qdrant, Mailpit e os cinco serviços
Argos. O perfil `apps` ativa os processos de aplicação. O setup usa dados
fictícios, credenciais descartáveis e modelos simulados; não configure chaves de
produção neste ambiente.

## Pré-requisitos e preparação

Tenha Docker Desktop com o engine Linux em execução. Na raiz do repositório,
gere as chaves internas aleatórias usadas somente entre os containers:

```powershell
.\.venv\Scripts\python.exe scripts/docker/create-local-env.py
```

Se não houver ambiente virtual, instale `cryptography` em um Python local e rode
`python scripts/docker/create-local-env.py`. O script cria `.env`, que é
ignorado pelo Git. Ele contém senhas aleatórias para PostgreSQL e chaves Ed25519
para delegações MCP e assinatura de relatórios, além das opções centralizadas
para modelos, SMTP e LangSmith. Configure todo o Compose integrado nesse único
arquivo. As variáveis são encaminhadas apenas aos containers que as utilizam.
Para recriá-lo, especifique `--force`; isso invalida as credenciais de uma pilha
já iniciada. O arquivo `.env.example` da raiz serve como referência; prefira o
script para gerar o `.env` com as credenciais internas.

Para estimar custos quando o provider não retorna preço, configure tarifas em
USD por milhão de tokens para os perfis `MODEL_FAST_*`, `MODEL_FAST_FALLBACK_*`,
`MODEL_SPECIALIST_*`, `MODEL_SPECIALIST_FALLBACK_*`,
`MODEL_EMBEDDING_INPUT_COST_PER_MILLION_TOKENS` e
`MODEL_EMBEDDING_FALLBACK_INPUT_COST_PER_MILLION_TOKENS`. O custo explícito do
provider tem precedência; sem uso completo ou tarifa necessária, o custo fica
ausente. Embeddings usam somente a tarifa de entrada. O campo
`billing.cost_source` distingue `provider` de `configured_rate`; o formato das
observações Mongo e dos metadados LangSmith não muda. A estimativa é indicativa
e não substitui a cobrança do provider. O script cria essas variáveis vazias,
então elas não ativam estimativas até serem preenchidas.

Por padrão, o ambiente local usa `AUTH_MODE=stub`, que representa todos os
requests como um único usuário sintético. Fora desse modo, o serviço aceita
somente o JWT próprio obtido em `POST /v1/auth/firebase`: a rota valida o
Firebase ID token e consulta o cadastro PostgreSQL. Configure o projeto
Firebase, uma credencial Admin SDK (arquivo de service account montado como
secret ou ADC), o DSN PostgreSQL somente leitura e
`MAIN_API_ACCESS_TOKEN_KEY_ID` com `MAIN_API_ACCESS_TOKEN_PRIVATE_KEY` (chave
Ed25519 PKCS#8 DER em Base64, exclusiva e diferente da chave de delegação MCP).
O JWT dura cinco minutos e carrega o UUID interno do usuário no `sub` e seu UID
Firebase; nome, e-mail, permissões e fazendas não são incluídos. As fazendas são
recarregadas do banco em cada chamada protegida. Tokens assinados pelo backend
de identidade não são mais aceitos.

Para montar um JSON de service account no `main_api`, defina
`MAIN_API_FIREBASE_PROJECT_ID` e `MAIN_API_FIREBASE_CREDENTIALS_HOST_PATH` no
`.env`. O arquivo `compose.firebase.yaml` monta a credencial em
`/run/secrets/firebase-service-account.json` como somente leitura e configura
`MAIN_API_FIREBASE_CREDENTIALS_FILE` e `GOOGLE_APPLICATION_CREDENTIALS` com o
caminho dentro do container. Suba a pilha incluindo o override:

```powershell
docker compose --file compose.yaml --file compose.firebase.yaml --profile apps config --quiet
docker compose --file compose.yaml --file compose.firebase.yaml --profile apps up --build -d
```

O caminho do host não é passado ao container. Para usar ADC, não inclua o
override e configure o mecanismo de credenciais do ambiente onde o container
executa.

## Banco PostgreSQL e dados sintéticos

Em um volume PostgreSQL novo, a imagem executa, em ordem, os módulos
`01_configuration.sql` até `09_data_mart.sql`, o `10_dataload_completo.sql` e o
`11_prediction_coverage.sql`, `12_local_stub_access.sql`,
`13_local_prediction_coverage_fixture.sql` e
`14_local_email_recipient_fixtures.sql` localizados em
`infrastructure/docker/postgres/init/`. Os nove primeiros módulos vêm dos SQL
separados de `argos-data-engineering`; o arquivo monolítico histórico
`criacao_banco_completo.sql` não é usado. O módulo 10 carrega registros
fictícios em todas as tabelas do schema. O módulo 11 expõe uma view de cobertura
para testar a predição com essa base sintética. O módulo 13 preenche o histórico
sintético exigido por essa cobertura; o módulo 14 cria os cinco contatos de
teste do fluxo de e-mail, sem criar credenciais ou vínculos de acesso. O script
99 cria o usuário `argos_reader` somente depois de schema, carga e view,
concedendo leitura aos serviços.

A carga destina-se a volume vazio. Ela não é executada de novo em um volume
existente. Para apagar a base local e recriá-la do zero, pare a pilha e remova
os volumes Docker; esta ação apaga os dados locais:

```powershell
docker compose --profile apps down --volumes
```

Os UUIDs do primeiro usuário e das duas propriedades fictícias são fixos para
coincidir com o stub de identidade local; o módulo 12 cria o vínculo vigente
entre esse usuário e a segunda propriedade para consultas integradas. Os demais
identificadores gerados não são estáveis entre cargas. Nenhum dos dados é
apropriado para validação de negócio, previsão real ou produção.

## Documentos sintéticos no MongoDB

Ao iniciar o perfil `apps`, o serviço de carga `mongo_seed` espera MongoDB e
PostgreSQL saudáveis e preenche `cattle_documents` e `medicine_documents` no
banco `argos_ai`. A carga lê do PostgreSQL os dois imóveis fixos do stub local,
seus rebanhos/lotes e o catálogo de medicamentos; assim, os UUIDs relacionais
dos documentos Mongo correspondem à massa SQL da mesma execução. Os campos não
representam histórico real de produção.

O script `scripts/docker/seed-local-mongo.py` pode ser executado novamente: ele
substitui somente documentos marcados por essa carga e preserva os demais dados
das collections. Para recarregar após recriar/repopular o PostgreSQL:

```powershell
docker compose --profile apps run --rm mongo_seed
```

O MCP de dados só inicia depois do sucesso da carga. Os testes de integração do
MCP continuam usando databases Mongo e PostgreSQL descartáveis próprios; essa
carga é para as jornadas locais integradas.

## Subir e validar a pilha

```powershell
docker compose config --quiet
docker compose --profile apps config --quiet
docker compose --profile apps up --build -d
docker compose --profile apps ps
```

Os serviços se conectam por nomes DNS internos. As portas publicadas escutam
somente em `127.0.0.1`: API principal em `8080`, A2A em `8001`, PostgreSQL em
`54327`, Qdrant em `6333`, SMTP de captura em `1025` e Mailpit UI em `8025`.
Mongo replica set `rs0` usa a porta `27027`. A API exige o Mongo replica set
para transações e checkpoints. O perfil integrado inicia dependências saudáveis
antes dos processos que as usam.

Verifique o estado e os health checks:

```powershell
docker compose --profile apps ps
Invoke-WebRequest http://localhost:8080/health/live
Invoke-WebRequest http://localhost:8080/health/ready
```

`live` confirma que o processo da API está ativo. `ready` exige persistência,
configuração das integrações e runtime de agentes. Para consultar contagens do
schema e verificar o dataload:

```powershell
docker compose exec postgres psql -U argos_admin -d argos -c "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name;"
docker compose exec postgres psql -U argos_admin -d argos -c "SELECT COUNT(*) FROM property; SELECT COUNT(*) FROM app_user;"
```

Para acompanhar falhas de inicialização, use
`docker compose --profile apps logs <serviço>`.

## Modelos simulados

O modo dos dois serviços é selecionado por `ARGOS_MODEL_MODE` em `.env`, gerado
pelo script de preparação. O padrão é `simulated`; para usar providers, altere
essa variável para `provider`. O Compose injeta o mesmo modo na API principal e
no A2A. Assim, no modo `simulated`, nenhum serviço precisa de API key de modelo,
endpoint externo ou credencial LangSmith. O modelo principal produz planos e
respostas estruturados de forma determinística e ainda chama as ferramentas MCP
reais, que consultam a carga sintética. As respostas estruturadas do
Orquestrador e do Juiz também são simuladas, para que a resposta final passe
pelos mesmos contratos do fluxo sem depender de um provider. No A2A, embeddings
simulados usam hashing lexical determinístico: termos compartilhados ajudam a
recuperar trechos relacionados, mas não há compreensão semântica. A resposta
mostra os trechos encontrados e suas citações, sem inventar uma síntese; o modo
continua voltado a testar contratos e integração, não a qualidade de modelos.

No modo `provider`, preencha os endpoints, nomes e chaves correspondentes em
`.env`, usando `.env.example` da raiz como referência. Configure
`A2A_QDRANT_VECTOR_SIZE` (1536 para `text-embedding-3-small`) e
`A2A_QDRANT_VOYAGE_VECTOR_SIZE` (1024 para `voyage-4-large`). A primeira
sincronização após habilitar Voyage reindexa o corpus nos dois espaços e publica
uma geração nova. A credencial de escrita Qdrant precisa criar a coleção de
staging e atualizar o alias configurado. Se as credenciais ou os modelos
estiverem ausentes/inválidos, a prontidão ou as chamadas de modelo falharão.
Para execução fora do Compose, o padrão de cada aplicação continua sendo
`provider`.

Depois de mudar `.env`, valide e recrie os serviços:

```powershell
docker compose --profile apps config --quiet
docker compose --profile apps up --build -d
```

O SMTP também pode ser configurado no mesmo `.env`. Se `MCP_EMAIL_SMTP_HOST`
ficar vazio, o Compose usa Mailpit, que captura mensagens localmente e não as
entrega. Para envio real, preencha host, porta, usuário, senha e
`MCP_EMAIL_SMTP_USE_TLS`. LangSmith é opcional e usa as variáveis
`MAIN_API_LANGSMITH_*` e `A2A_LANGSMITH_*` no mesmo arquivo. Para chaves de
escopo organizacional, configure também `MAIN_API_LANGSMITH_WORKSPACE_ID` e
`A2A_LANGSMITH_WORKSPACE_ID` com o ID do workspace; o ID da organização não
substitui o workspace.

Os `.env.example` dentro de cada aplicação continuam disponíveis somente como
referência para execução individual fora do Compose. O Compose da raiz não lê
esses arquivos.

## Execução individual

As imagens independentes usam contexto de build na raiz para incluir os pacotes
compartilhados:

```powershell
docker build -f apps/main_api/Dockerfile -t argos-main-api:local .
docker build -f apps/mcp_data/Dockerfile -t argos-mcp-data:local .
docker build -f apps/mcp_email/Dockerfile -t argos-mcp-email:local .
docker build -f apps/mcp_prediction/Dockerfile -t argos-mcp-prediction:local .
docker build -f apps/a2a_agent/Dockerfile -t argos-a2a-agent:local .
```

Para executar um container isoladamente, prepare as dependências necessárias ou
use os serviços de infraestrutura pelo Compose. A URL dentro de um container
deve usar o DNS do serviço; `localhost` sempre aponta para o próprio container.
O Compose específico em `apps/a2a_agent/compose.yaml` continua disponível para o
ciclo individual do A2A e tem configuração própria.

## Parar e limpar

```powershell
docker compose --profile apps down
```

Este comando mantém os volumes. Para remover definitivamente os dados locais,
use `down --volumes` conforme a seção de PostgreSQL.
