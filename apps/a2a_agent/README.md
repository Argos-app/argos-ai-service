# Argos A2A de Informações

Serviço isolado que expõe as skills Documental e Legal por A2A e consulta o
corpus global de leis e termos/condições. O artefato contém somente
`apps/a2a_agent` e os contratos e primitivas de segurança compartilhados; não
instala nem importa o `main_api` ou outros MCPs.

## Build e execução

O build é feito na raiz do monorepo para copiar apenas as dependências
compartilhadas explicitamente:

```powershell
docker build -f apps/a2a_agent/Dockerfile -t argos-a2a-agent:local .
Copy-Item apps/a2a_agent/.env.example apps/a2a_agent/.env
# Edite .env com URLs, credenciais e diretórios fornecidos pelo deployment.
docker compose --env-file apps/a2a_agent/.env -f apps/a2a_agent/compose.yaml up --build -d
```

O Compose publica a porta somente em `127.0.0.1`, usa filesystem raiz somente
leitura, executa sem root, limita CPU/memória, monta PDFs e código de adapters
como somente leitura e preserva o manifesto em volume separado. O Qdrant é
externo ao Compose de produção e precisa expor chaves distintas de leitura e de
escrita do índice.

## Adapters de runtime

O runtime requer adapters de embeddings FAIIS, geração de respostas e consulta
vigente da permissão `documents.read`. Eles ainda não são implementados neste
repositório. O deployment fornece um módulo Python confiável no diretório
montado em `/opt/argos/a2a-adapters`, configura `A2A_RUNTIME_FACTORY` no formato
`modulo:factory` e a factory retorna `RuntimeDependencies`. A factory pode ser
síncrona ou assíncrona. O exemplo de ambiente usa placeholders e não inicia o
serviço até esses adapters e todos os segredos/configurações obrigatórios serem
substituídos. O serviço falha fechado quando a factory está ausente ou inválida.

Não monte código de adapter não revisado: ele é código executável dentro do
processo. Não versione `apps/a2a_agent/.env`; injete Bearer, chaves públicas de
delegação e credenciais Qdrant por um gerenciador de segredos. Configure
credenciais Qdrant diferentes e não compartilhe a chave do writer com clientes.

## Operação e recuperação

- `GET /health/live` verifica o processo; `GET /health/ready` também depende da
  disponibilidade do Qdrant e exige Bearer de serviço.
- Preserve o volume `a2a-state` em reinícios. O manifesto é estado técnico da
  sincronização; não edite seu conteúdo manualmente.
- Fonte local é montada somente para leitura. A atualização do Qdrant acontece
  pelo sincronizador, que mantém a credencial de escrita atrás da porta de
  sincronização; consultas usam a credencial de leitura.
- O serviço rejeita configuração incompleta, dimensões incompatíveis e falhas de
  leitura do manifesto antes de aceitar tráfego. Em falha de Qdrant, deixe a
  prontidão indisponível, preserve o manifesto e recupere Qdrant conforme o
  procedimento operacional desse ambiente antes de reabrir tráfego.
- Os limites funcionais vigentes incluem 20 resultados por busca, 50 documentos
  por página, agregações limitadas a 10.000 documentos/100 grupos, trechos de
  até três páginas e timeout Qdrant de até 30 segundos. Os limites de container
  são 1 CPU e 1 GiB e devem ser revistos com medições do workload.
- A etapa de cloud e a integração com Informações no principal ainda são
  pendências separadas; este container não declara esses adapters como prontos.

## Verificações

```powershell
python -m ruff check apps/a2a_agent
python -m pytest apps/a2a_agent/tests/unit -q
docker compose --env-file apps/a2a_agent/.env -f apps/a2a_agent/compose.yaml config --quiet
```

A homologação com Qdrant real está documentada em
[`tests/integration/README.md`](tests/integration/README.md). Ela usa outro
Compose e credenciais descartáveis; não aponta para o Qdrant de produção.
