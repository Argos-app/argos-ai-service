# Execução local integrada com Docker

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
`python scripts/docker/create-local-env.py`. O script cria `.env.local`, que é
ignorado pelo Git. Ele contém senhas aleatórias para PostgreSQL e chaves Ed25519
para delegações MCP e assinatura de relatórios. Para recriá-lo, especifique
`--force`; isso invalida as credenciais de uma pilha já iniciada.

## Banco PostgreSQL e dados sintéticos

Em um volume PostgreSQL novo, a imagem executa, em ordem, os módulos
`01_configuracao.sql` até `09_data_mart.sql`, o `10_dataload_completo.sql` e o
`11_prediction_coverage.sql` e `12_local_stub_access.sql` localizados em
`infrastructure/docker/postgres/init/`. Os nove primeiros módulos vêm dos SQL
separados de `argos-data-engineering`; o arquivo monolítico histórico
`criacao_banco_completo.sql` não é usado. O módulo 10 carrega registros
fictícios em todas as tabelas do schema. O módulo 11 expõe uma view de cobertura
para testar a predição com essa base sintética. O script 99 cria o usuário
`argos_reader` somente depois de schema, carga e view, concedendo leitura aos
serviços.

A carga destina-se a volume vazio. Ela não é executada de novo em um volume
existente. Para apagar a base local e recriá-la do zero, pare a pilha e remova
os volumes Docker; esta ação apaga os dados locais:

```powershell
docker compose --env-file .env.local --profile apps down --volumes
```

Os UUIDs do primeiro usuário e das duas propriedades fictícias são fixos para
coincidir com o stub de identidade local; o módulo 12 cria o vínculo vigente
entre esse usuário e a segunda propriedade para consultas integradas. Os demais
identificadores gerados não são estáveis entre cargas. Nenhum dos dados é
apropriado para validação de negócio, previsão real ou produção.

## Subir e validar a pilha

```powershell
docker compose --env-file .env.local config --quiet
docker compose --env-file .env.local --profile apps config --quiet
docker compose --env-file .env.local --profile apps up --build -d
docker compose --env-file .env.local --profile apps ps
```

Os serviços se conectam por nomes DNS internos. As portas publicadas escutam
somente em `127.0.0.1`: API principal em `8080`, A2A em `8001`, PostgreSQL em
`54327`, Qdrant em `6333`, SMTP de captura em `1025` e Mailpit UI em `8025`.
Mongo replica set `rs0` usa a porta `27027`. A API exige o Mongo replica set
para transações e checkpoints. O perfil integrado inicia dependências saudáveis
antes dos processos que as usam.

Verifique o estado e os health checks:

```powershell
docker compose --env-file .env.local --profile apps ps
Invoke-WebRequest http://localhost:8080/health/live
Invoke-WebRequest http://localhost:8080/health/ready
```

`live` confirma que o processo da API está ativo. `ready` exige persistência,
configuração das integrações e runtime de agentes. Para consultar contagens do
schema e verificar o dataload:

```powershell
docker compose --env-file .env.local exec postgres psql -U argos_admin -d argos -c "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name;"
docker compose --env-file .env.local exec postgres psql -U argos_admin -d argos -c "SELECT COUNT(*) FROM property; SELECT COUNT(*) FROM app_user;"
```

Para acompanhar falhas de inicialização, use
`docker compose --env-file .env.local --profile apps logs <serviço>`.

## Modelos simulados

O Compose configura `MAIN_API_MODEL_MODE=simulated` e
`A2A_MODEL_MODE=simulated`. Assim, nenhum serviço precisa de API key de modelo,
endpoint externo ou credencial LangSmith. O modelo principal produz planos e
respostas estruturados de forma determinística e ainda chama as ferramentas MCP
reais, que consultam a carga sintética. O A2A cria embeddings determinísticos
para indexar os PDFs do repositório e responde com texto fixo citando os trechos
recuperados. Esses vetores não têm qualidade semântica e as respostas são
marcadas como ilustrativas; esse modo testa contratos e integração, não a
qualidade dos modelos.

Para execução fora do Compose, o modo padrão é `provider`. Configure os nomes,
endpoints e credenciais de cada serviço conforme os `.env.example`. Não altere o
modo para `simulated` fora de ambientes locais de teste. Mailpit é o provider
SMTP local de captura e não entrega mensagens a destinatários reais.

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
docker compose --env-file .env.local --profile apps down
```

Este comando mantém os volumes. Para remover definitivamente os dados locais,
use `down --volumes` conforme a seção de PostgreSQL.
