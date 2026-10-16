# Argos AI Service

Sistema de IA do Argos, plataforma de gerenciamento de aplicações de
medicamentos em bovinos. O backend chama este serviço em nome do usuário; o
aplicativo Kotlin apresenta respostas e perguntas de esclarecimento.

Este README distingue o código atual das integrações planejadas. Backend e
mobile ficam em outros repositórios.

O principal tem contratos HTTP, grafo de agentes, Factory e Registry. Ele usa
Mongo para sessões, mensagens, eventos de execução, checkpoints, resumos e
preferências limitadas do usuário. A API valida identidade JWT EdDSA do backend
e vincula ferramentas LangChain aos MCPs configurados, com delegação por
usuário, propriedade, agente e operação. O backend ainda não inclui `farm_ids`,
então a criação de sessão permanece bloqueada até delegar o escopo autorizado. O
runtime principal configura um modelo por agente (`MAIN_API_MODEL_ROUTER`,
`MAIN_API_MODEL_FARM`, `MAIN_API_MODEL_DOCTOR`, `MAIN_API_MODEL_ANALYST`,
`MAIN_API_MODEL_SECRETARY`, `MAIN_API_MODEL_INFORMATION`,
`MAIN_API_MODEL_ORCHESTRATOR`, `MAIN_API_MODEL_JUDGE` e
`MAIN_API_MODEL_QUESTION_FORMATTER`). URL e chave são compartilhadas entre os
agentes; cada variável pode apontar para o mesmo modelo ou para um modelo
diferente. A sumarização de sessão e a extração de memória reutilizam o modelo
do Router. Sem runtime válido, `/health/ready` retorna indisponível. Os testes
injetam modelos simulados explicitamente por `create_app(models=...)`.

## Agentes

Os agentes são acionados conforme a solicitação; não executam todos a cada
mensagem. O sistema principal usa FastAPI, LangGraph e LangChain.

| Agente                  | Função                                                                                                                                     |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Fazenda                 | Consulta aplicações, lotes, bovinos em exceção e carências. Sempre consulta o MCP de dados para responder.                                 |
| Doutor                  | Consulta informações documentadas sobre medicamentos no MCP de dados. Apresenta fontes e limites; não substitui avaliação veterinária.     |
| Analista                | Solicita cálculos e previsões ao MCP de predição e explica resultados, premissas e limitações.                                             |
| Secretário              | Recebe referências de Fazenda e pode preparar relatórios ou consultar estado no MCP de e-mail; o envio aguarda autorização determinística. |
| Informações             | Responde questões gerais e encaminha perguntas documentais ou legais especializadas ao A2A.                                                |
| Orquestrador            | Consolida os resultados, identifica lacunas e solicita complementações dentro dos limites do fluxo.                                        |
| Juiz                    | Verifica se as afirmações têm apoio nas fontes e sinaliza inconsistências ou necessidade de revisão.                                       |
| Formatador de perguntas | Transforma informações faltantes em perguntas de esclarecimento, em texto ou com opções para o aplicativo.                                 |
| Documental — A2A        | Consulta termos, condições e FAQs nos PDFs atuais indexados no Qdrant.                                                                     |
| Legal — A2A             | Responde com base nas fontes legais disponíveis, indicando jurisdição, datas e limitações.                                                 |

Autenticação, anonimização por regex, validações, cálculos, renderização e envio
não são agentes com LLM. O Roteador seleciona especialistas; o **agente
Formatador de perguntas** produz perguntas em texto ou com opções para o
aplicativo e permite retomar a sessão após a resposta do usuário.

## MCPs

MCP é o protocolo usado para disponibilizar ferramentas aos agentes. Cada MCP é
uma aplicação separada e valida o acesso aos dados solicitados.

| MCP                         | Função                                                                                                                                                                                                                                                       |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Dados — implementado        | Expõe consultas de propriedades, rebanhos, lotes, aplicações, exceções, ocorrências, documentos, catálogo e `get_lot_report_source`. A origem `LotReportEnvelope` é filtrada pelo escopo vigente e assinada por Ed25519.                                     |
| E-mail — integração parcial | O Secretário prepara relatórios com a origem assinada, pode solicitar envio após autorização determinística explícita e consulta o estado. A entrega ainda depende de SMTP e chaves internas confiáveis.                                                     |
| Predição — integrado        | Estima consumo de medicamentos por propriedade e unidade, repetindo a média diária mensal de um histórico anual por até 28 dias. A tool Analista envia delegação curta com `prediction.read`; não usa modelo treinado nem prevê produção, doenças ou mortes. |

## Sessões e memória do principal

O MCP de predição só confirma cobertura quando a variável
MCP_PREDICTION_COVERAGE_RELATION aponta para uma view PostgreSQL administrada
pelo pipeline de dados e com as colunas property_id, coverage_start,
coverage_end e evidence_source. As janelas precisam formar uma sequência sem
lacunas. Sem view configurada ou evidência completa, a tool informa cobertura
desconhecida/incompleta e não apresenta estimativa; primeiro e último evento não
são usados como prova.

O contexto enviado aos agentes é limitado a 12.000 caracteres, até 20 mensagens
recentes e uma leitura de no máximo 200 mensagens, mesmo se o sumarizador
falhar. Por interação, o principal limita a 12 chamadas de modelo, 12 chamadas
de ferramenta, 48.000 caracteres totais de saídas de ferramentas, 1.200 tokens
de saída por chamada e 90 segundos de execução. Isso restringe o consumo, mas
não define um teto monetário exato, pois preço e uso reportado variam por
provider e modelo. A telemetria de uso existente permanece de melhor esforço;
não foi criado um ledger durável de custo. Fazenda, Doutor, Analista e
Informações não podem retornar respostas de sucesso sem ao menos uma chamada
externa bem-sucedida; referências geradas pelo modelo são substituídas por
referências opacas das chamadas observadas.

O `main_api` grava sessões e interações no Mongo, sem armazenar as mensagens
originais em paralelo. O conteúdo persistido é sanitizado. A cada 15 entradas
únicas do usuário, o serviço publica um resumo progressivo com teto de 2.000
tokens; o perfil entre sessões é restrito ao tom de comunicação e ao objetivo
geral declarado. Sessões e preferências não expiram automaticamente.

O contexto enviado aos agentes é limitado a 12.000 caracteres, no máximo 20
mensagens recentes e uma leitura de até 200 mensagens para montar a janela;
essas restrições também valem quando o sumarizador está indisponível. Por
interação, o principal limita a 12 chamadas de modelo, 12 chamadas de
ferramenta, 48.000 caracteres totais de respostas de ferramentas, 1.200 tokens
de saída por chamada e 90 segundos de execução. Esses limites restringem
consumo, mas não definem valor monetário exato: preços e uso reportado variam
por provider/modelo. A telemetria de uso existente continua de melhor esforço;
não foi criado um ledger durável de custo.

Respostas de Fazenda, Doutor, Analista e Informações ficam indisponíveis se não
houver chamada externa bem-sucedida. Citações geradas pelo modelo são
substituídas por referências opacas da chamada, sem persistir seu conteúdo.

O tracing LangSmith do `main_api` é opcional e só é ativado quando
`MAIN_API_LANGSMITH_API_KEY` está configurada. Por padrão, traces de agentes,
chamadas a modelos e ferramentas ocultam entradas e saídas; habilite
`MAIN_API_LANGSMITH_CAPTURE_CONTENT=true` somente após aprovar o envio de
prompts, respostas e argumentos de ferramentas ao projeto LangSmith configurado.
A chave deve ficar no gerenciador de secrets do ambiente.
`MAIN_API_LANGSMITH_PROJECT` seleciona o projeto e `MAIN_API_LANGSMITH_ENDPOINT`
permite apontar para uma instância aprovada. O Mongo continua armazenando apenas
os eventos brutos sanitizados existentes; conteúdo de trace é armazenado
separadamente pelo LangSmith. A coleta local no Mongo independe do LangSmith e
inclui `X-Argos-Correlation-ID` na resposta, `correlation_id` nos spans e
`parent_observation_id` para relacionar chamadas aninhadas. O correlation ID é
enviado às chamadas MCP e segue assinado para Data, Prediction e A2A; os MCPs
não persistem observações próprias no momento. Filas limitadas podem perder
observações, então ausência não significa zero uso. Operação, ranking de
chamadas e tokens por usuário e limites estão em
[`docs/operacao-observabilidade.md`](docs/operacao-observabilidade.md); a
extração e o dicionário BI estão em
[`docs/observabilidade-extracao-mongo.md`](docs/observabilidade-extracao-mongo.md)
e [`docs/dicionario-mongodb-bi.md`](docs/dicionario-mongodb-bi.md). Avaliações e
feedback podem ser feitos sobre traces na plataforma; esta integração não define
critérios automáticos de qualidade.

As rotas disponíveis são `POST /v1/sessions`, `GET /v1/sessions`,
`POST /v1/sessions/{session_id}/interactions` e
`GET /v1/sessions/{session_id}/messages`. A consulta de mensagens não chama o
LLM, valida o titular, usa cursor estável e aceita páginas de 1 a 100 itens
(padrão 50). Eventos internos de agentes e tools não são incluídos no histórico
público.

O contrato OpenAPI e exemplos de autenticação, erros e correlação estão em
[`docs/api-main-api.md`](docs/api-main-api.md) e
[`docs/openapi-main-api.json`](docs/openapi-main-api.json).

O serviço precisa de Mongo em replica set para transações e checkpointer. Os
serviços com persistência própria compartilham o banco `argos_ai` e separam os
dados por collections. A configuração por sistema, variáveis, URIs para host e
Compose, precedência e migração das collections antigas de e-mail estão em
[`docs/configuracao-mongodb.md`](docs/configuracao-mongodb.md). O Compose local
usa um único `.env` gerado por `scripts/docker/create-local-env.py`; os
`.env.example` de cada aplicação serve para execução individual. Crie o arquivo
local de um serviço copiando o exemplo correspondente, por exemplo
`Copy-Item apps/main_api/.env.example apps/main_api/.env`; esses `.env` locais
são ignorados pelo Git. O Compose individual do A2A carrega seu arquivo. Nas
execuções diretas dos outros serviços, exporte as variáveis para o processo,
pois o Python não lê `.env` automaticamente:
[`apps/main_api/.env.example`](apps/main_api/.env.example),
[`apps/mcp_data/.env.example`](apps/mcp_data/.env.example) e
[`apps/mcp_email/.env.example`](apps/mcp_email/.env.example),
[`apps/mcp_prediction/.env.example`](apps/mcp_prediction/.env.example) e
[`apps/a2a_agent/.env.example`](apps/a2a_agent/.env.example). Em execução local
no host contra o Compose de testes, o URI usa `directConnection=true` porque o
replica set anuncia um nome de host interno. O stub de identidade continua
disponível apenas em ambiente local com `APP_ENV=local` e `AUTH_MODE=stub`. Fora
desse modo, configure a chave pública Ed25519 do backend em
`MAIN_API_AUTH_TRUSTED_PUBLIC_KEYS`; a API valida issuer, audience, validade,
agente e operações delegadas. O backend deve incluir `farm_ids` no token para
que a API possa abrir sessões com o escopo autorizado.

O tracing LangSmith opcional acompanha uma execução do grafo e suas chamadas
aninhadas a agentes, modelos e ferramentas; os passos de resumo e memória são
registrados separadamente. Sem `MAIN_API_LANGSMITH_API_KEY`, nada é enviado.
Conteúdo fica oculto por padrão; `MAIN_API_LANGSMITH_CAPTURE_CONTENT=true`
habilita prompts, respostas e argumentos de ferramentas após aprovação. Com a
captura ativa, e-mails, UUIDs, CPFs, CNPJs, telefones e tokens Bearer são
anonimizados. O projeto é selecionado por `MAIN_API_LANGSMITH_PROJECT` e o
endpoint HTTPS opcional por `MAIN_API_LANGSMITH_ENDPOINT`. O serviço A2A possui
configuração própria; IDs assinados de correlação e pai permitem relacionar a
observação de chamada do principal às observações A2A quando ambas as
persistências estiverem habilitadas.

Testes unitários: `python -m pytest apps/main_api/tests/unit -q`. Testes de
persistência e retomada com Mongo replica set descartável: siga
[`apps/main_api/tests/integration/README.md`](apps/main_api/tests/integration/README.md).
O job `Main API Mongo integration tests` da CI executa esses testes e publica o
relatório JUnit.

### Fluxo planejado de relatório de lote

O Roteador cria uma tarefa Fazenda e outra Secretário dependente. Fazenda obtém
`LotReportEnvelope` pelo MCP de dados; o executor preserva o envelope assinado e
passa sua referência ao Secretário. O principal monta as chamadas ao MCP de
e-mail usando os contratos de `packages/contracts`.

O MCP de dados resolve o lote somente nas propriedades autorizadas e obtém sua
projeção Mongo. Código de lote é único por rebanho, e nome de rebanho por
propriedade; correspondências ambíguas exigem esclarecimento. O contrato atual
aceita um lote. O MCP de e-mail resolve nomes/e-mails no cadastro e recebe o
envelope sem consultar novamente o documento de negócio.

O principal troca nomes e e-mails de destinatários explícitos por referências
`[DESTINATARIO_n]` antes de persistir a mensagem ou enviá-la ao LLM. O seletor
original e a origem da intenção de envio ficam no checkpoint da interação e
continuam disponíveis durante esclarecimentos; não são reutilizados em outro
pedido. Pedido explícito inequívoco pode seguir sem segunda confirmação;
preparar não autoriza enviar.

O MCP de e-mail expõe `prepare_report`, `send_report` e `get_report_status` por
Streamable HTTP em `/mcp`. As tools recebem `payload`; o Bearer interno fica no
transporte. Recuperações reusam o mesmo `report_id` e chave de idempotência.
`accepted` indica aceite pelo provedor, sem comprovar entrega ou leitura.
`partial` indica que o provedor recusou um ou mais destinatários. A operação não
deve ser reenviada automaticamente, pois outros destinatários podem já ter
recebido o e-mail.

O transporte SMTP usa STARTTLS com validação de cadeia de certificados e nome do
servidor pelo trust store padrão do sistema. `MCP_EMAIL_SMTP_USE_TLS=false` é
reservado a serviços locais de captura, como Mailpit; não use em ambientes com
entrega real. Uma conexão com certificado inválido falha antes do envio.

Se o processo cair depois de reservar uma operação, ela permanece `pending`; se
a conexão cair após o provedor possivelmente aceitar a mensagem, o MCP marca
`unknown`. Repetir a mesma chave de idempotência devolve o estado existente e
nunca chama o SMTP de novo. A operação deve ser reconciliada por uma pessoa
operadora usando o `operation_id`/`provider_message_id` e os registros do
provedor. Não libere nem repita automaticamente uma operação ambígua: só após
confirmar no provedor que nenhum destinatário recebeu a mensagem, uma nova
solicitação explícita pode gerar outra operação. `accepted` significa aceite do
provedor, não entrega ou leitura.

## A2A

A2A é o protocolo de comunicação com o serviço dos especialistas Documental e
Legal. O agente Informações do principal encaminha a pergunta e recebe uma
resposta com fontes e limitações.

A cada acionamento, o serviço verifica alterações nos PDFs e atualiza somente os
documentos afetados no Qdrant. Os agentes acessam apenas arquivos atuais e
informam a data do documento e sua última atualização conhecida. PDFs de teste
ficam no repositório.

O serviço tem build independente em `apps/a2a_agent/Dockerfile` e Compose
individual documentado em
[`apps/a2a_agent/README.md`](apps/a2a_agent/README.md). O runtime implementa
adapters HTTP OpenAI-compatible para embeddings e geração, além de uma consulta
PostgreSQL somente leitura para autorização documental. Endpoints, modelos,
papéis autorizados, credenciais e o Qdrant precisam ser configurados no
deployment. A origem cloud e a chamada real pelo principal seguem pendentes.

## Estrutura do projeto

```text
argos-ai-service/
├── apps/
│   ├── main_api/          # API, sessões, grafo e agentes principais
│   ├── mcp_data/          # Consultas aos dados de negócio
│   ├── mcp_email/         # Relatórios e envio de e-mail
│   ├── mcp_prediction/    # Cálculos, inferência e comandos de treino
│   └── a2a_agent/         # Serviço isolado: especialistas, PDFs, Qdrant e imagem
├── packages/
│   ├── contracts/        # Modelos Pydantic de comunicação
│   ├── security/         # Validação de identidade e escopo
│   └── observability/    # Logs, traces, métricas e custos
├── tests/                # Contratos, integração, E2E, avaliações e fixtures
├── resources/            # PDFs de teste, templates e manifestos
├── experiments/          # Dataloads e experimentos de predição
├── config/               # Modelos e fallbacks fixos por agente
├── infrastructure/       # Configuração dos serviços locais
├── docs/                 # Planejamento e documentação
├── compose.yaml          # Dependências locais e perfil integrado de serviços
└── .github/workflows/    # CI no GitHub Actions
```

Cada aplicação terá configuração, dependências e execução próprias, com deploy
independente. Os contratos compartilhados precisam acompanhar o artefato de cada
serviço que os consome; o processo não depende do código de outro serviço
montado em runtime. O perfil Docker integrado, o schema e os dados sintéticos
PostgreSQL, e os providers de modelo simulados estão documentados em
[`docs/docker-local.md`](docs/docker-local.md). O modo simulado permite
exercitar o fluxo sem chaves externas e identifica suas respostas como
ilustrativas.

No principal, a organização separa API, domínio, orquestração, agentes e
infraestrutura. **Protocol + Factory + Registry** padroniza a criação dos
agentes: o Protocol define a interface, o Registry lista os agentes permitidos e
a Factory os instancia com suas dependências.

## Grafo de agentes

O desenho abaixo representa o fluxo previsto para as integrações. O executor
atual ordena e executa tarefas sequencialmente; a passagem protegida de
artefatos de relatório ainda será implementada.

```mermaid
flowchart TD
    Input[Mensagem do backend] --> Auth[Validar identidade, fazendas e sessão]
    Auth --> Privacy[Preservar parâmetros protegidos e minimizar dados]
    Privacy --> Guard{Guardrail aprova?}
    Guard -->|Não ou falha| Safe[Finalização segura sem ferramentas de negócio]
    Guard -->|Sim| Route[Roteador]
    Route --> Missing{Faltam informações?}
    Missing -->|Sim| Question[Agente Formatador de perguntas]
    Question --> Wait[Aguardar resposta do usuário]
    Wait --> Auth
    Missing -->|Não| Specialists[Executar especialistas necessários]
    Specialists --> Farm[Fazenda / Doutor]
    Specialists --> Secretary[Secretário]
    Specialists --> Analyst[Analista]
    Specialists --> Info[Informações]
    Farm --> Data[MCP de dados]
    Secretary --> Email[MCP de e-mail]
    Analyst --> Prediction[MCP de predição]
    Info --> A2A[A2A: Documental / Legal quando necessário]
    Data -->|Origem do relatório de lote| Artifact[Executor guarda origem assinada do relatório]
    Artifact -->|Referência da tarefa Fazenda| Secretary
    Data --> Merge[Orquestrador consolida resultados]
    Email --> Merge
    Prediction --> Merge
    Info --> Merge
    A2A --> Merge
    Merge --> Judge[Juiz e validações]
    Judge --> Decision{Resultado}
    Decision -->|Atendido| Answer[Resposta com fontes e limitações]
    Decision -->|Falta esclarecimento| Question
    Decision -->|Revisar texto dentro dos limites| Merge
    Decision -->|Limite ou evidência insuficiente| Partial[Resposta parcial ou abstenção]
```

Paralelismo de tarefas independentes e limites gerais de tempo/custo são
evoluções planejadas. O grafo atual limita a cinco tarefas e uma revisão do
Juiz. O Juiz revisa a resposta ao chat; a autorização determinística de envio
deve preceder a chamada ao MCP. A resposta a uma pergunta retoma a sessão após
nova validação de acesso; falhas não são apresentadas como dados ou operações
bem-sucedidas. Ao atingir um limite, o controle determinístico encerra a
execução com conteúdo já validado ou uma mensagem segura.

Para subir a integração em um cluster Kubernetes local, consulte
[docs/kubernetes-local.md](docs/kubernetes-local.md). O estado e os resultados
de homologação integrada estão em
[docs/resultado-homologacao-local.md](docs/resultado-homologacao-local.md).
