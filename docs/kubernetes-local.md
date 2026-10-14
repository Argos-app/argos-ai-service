# Integração local em Kubernetes

Este guia sobe as cinco aplicações do `argos-ai-service` em um cluster local de
um nó, com MongoDB replica set, PostgreSQL inicializado pelos módulos SQL do
repositório, Redis, Qdrant e Mailpit. É possível usar Kind ou o Kubernetes
integrado ao Docker Desktop com provisionador Kubeadm. `MAIN_API_MODEL_MODE` e
`A2A_MODEL_MODE` ficam em `simulated`; não são necessárias chaves de modelos,
e-mail ou LangSmith. Os resultados de IA são simulados e não representam uma
avaliação de qualidade do modelo.

O alvo reproduz a pilha local do Compose, não um deployment de produção. Mongo,
PostgreSQL e Qdrant não têm autenticação habilitada neste cluster local. Não
reutilize credenciais ou dados de produção. Os PVCs preservam Mongo, PostgreSQL,
Qdrant e o manifesto de estado do A2A durante recriações de Pods. Redis e caixa
de entrada do Mailpit são efêmeros; tarefas A2A em andamento continuam em
memória e não são recuperadas após reinício, conforme o contrato atual. Remover
o cluster remove os dados hospedados dentro do nó.

O Compose atual não inclui Collector OpenTelemetry, Prometheus, Grafana ou
Tempo, apesar de esses componentes aparecerem na descrição histórica do plano
geral. Esta configuração Kubernetes também não os inclui. A telemetria da
aplicação mantém as configurações locais atuais e não depende do LangSmith.

## Pré-requisitos

- Windows com Docker Desktop iniciado e backend Linux disponível. Para usar o
  Kubernetes integrado, veja a
  [documentação do Docker Desktop](https://docs.docker.com/desktop/use-desktop/kubernetes/)
  e selecione o provisionador **Kubeadm**. Alternativamente, instale
  [Kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation).
- `kubectl`; veja a [instalação oficial](https://kubernetes.io/docs/tasks/tools/).
- Python 3.14 com `cryptography` e acesso para baixar as imagens base na
  primeira construção.
- Recursos locais suficientes para seis imagens de aplicação/infraestrutura,
  PostgreSQL, MongoDB e Qdrant. Ajuste os requests/limits dos manifests se a
  máquina tiver menos memória.

Escolha uma das opções de cluster. Para o Kubernetes integrado ao Docker
Desktop, crie o cluster pelo dashboard em **Kubernetes → Create cluster** e
escolha **Kubeadm**. No PowerShell, selecione o contexto e confirme o nó:

```powershell
kubectl config use-context docker-desktop
kubectl get nodes
```

Para Kind, execute a partir da raiz do repositório:

```powershell
kind create cluster --config k8s/local/kind-config.yaml
kubectl cluster-info --context kind-argos-local
```

Os PVs `hostPath` desta configuração são estáticos e esperam um cluster de nó
único. O nome `argos-local` identifica o cluster Kind do exemplo. Não aplique
estes PVs em um cluster compartilhado.

## Secrets locais e imagens

Gere credenciais locais descartáveis e prepare o arquivo dotenv consumido pelo
Secret do Kustomize. Ambos os arquivos são ignorados pelo Git:

```powershell
.\.venv\Scripts\python.exe scripts/docker/create-local-env.py --output .env.local
.\.venv\Scripts\python.exe scripts/kubernetes/prepare-local-secrets.py --source .env.local
```

O gerador cria chaves de delegação/assinatura e tokens internos aleatórios; o
script de preparação valida as chaves requeridas e adapta a sintaxe das chaves
JSON ao formato de env file do Kustomize. Não copie para esses arquivos
credenciais reais. Para descartar os arquivos locais, remova `.env.local` e
`k8s/local/.env.local` manualmente depois de encerrar o ambiente.

Construa as cinco imagens do serviço e a imagem PostgreSQL que incorpora os
scripts SQL versionados. Com Kind, use o auxiliar, que também carrega as
imagens:

```powershell
./scripts/kubernetes/build-load-images.ps1
```

O script termina com erro se qualquer build ou o carregamento no Kind falhar. As
imagens são locais e usam `imagePullPolicy: IfNotPresent`.

Com o Kubernetes do Docker Desktop e provisionador Kubeadm, não use esse
auxiliar porque ele chama `kind load`. Construa as mesmas imagens diretamente
no Docker Desktop, cujo image store está disponível ao cluster Kubeadm:

```powershell
$images = @(
    @{ Name = 'argos-main-api:local'; Dockerfile = 'apps/main_api/Dockerfile' },
    @{ Name = 'argos-mcp-data:local'; Dockerfile = 'apps/mcp_data/Dockerfile' },
    @{ Name = 'argos-mcp-prediction:local'; Dockerfile = 'apps/mcp_prediction/Dockerfile' },
    @{ Name = 'argos-mcp-email:local'; Dockerfile = 'apps/mcp_email/Dockerfile' },
    @{ Name = 'argos-a2a-agent:local'; Dockerfile = 'apps/a2a_agent/Dockerfile' },
    @{ Name = 'argos-postgres-init:local'; Dockerfile = 'infrastructure/docker/postgres/Dockerfile.local' }
)
foreach ($image in $images) {
    docker build --tag $image.Name --file $image.Dockerfile .
    if ($LASTEXITCODE -ne 0) { throw "Falha ao construir $($image.Name)" }
}
```

## Subida e validação

Renderize antes de aplicar e então crie os recursos:

```powershell
kubectl kustomize k8s/local
kubectl apply -k k8s/local
kubectl -n argos-local get pods,pvc
kubectl -n argos-local rollout status statefulset/mongo --timeout=5m
kubectl -n argos-local rollout status statefulset/postgres --timeout=5m
kubectl -n argos-local rollout status deployment/qdrant --timeout=5m
kubectl -n argos-local rollout status deployment/main-api --timeout=5m
kubectl -n argos-local rollout status deployment/mcp-data --timeout=5m
kubectl -n argos-local rollout status deployment/mcp-prediction --timeout=5m
kubectl -n argos-local rollout status deployment/mcp-email --timeout=5m
kubectl -n argos-local rollout status deployment/a2a-agent --timeout=5m
```

Os probes de prontidão dos serviços verificam conectividade TCP com suas
dependências e a porta do próprio processo; API principal também consulta
`/health/ready` e A2A consulta essa rota com o token interno. Isso torna uma
dependência indisponível visível como Pod não pronto, mas não substitui um teste
de transação de negócio completo. Startup/liveness verificam que o processo está
respondendo, para que uma falha de dependência não provoque reinícios em ciclo.

Confira o replica set e os dados inicializados em PostgreSQL:

```powershell
kubectl -n argos-local exec mongo-0 -- mongosh --quiet --eval 'rs.status().ok'
kubectl -n argos-local exec postgres-0 -- sh -ec 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" && psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atqc "SELECT ready FROM public.local_environment_ready"'
```

Encaminhe as portas para acessar a API e a caixa de entrada SMTP/UI do Mailpit:

```powershell
kubectl -n argos-local port-forward service/main-api 8080:8000
kubectl -n argos-local port-forward service/mailpit 8025:8025 1025:1025
```

Use o fluxo autenticado de sessão e interação descrito em
[docs/api-main-api.md](api-main-api.md). No modo local `AUTH_MODE=stub`, envie
`Authorization: Bearer local-dev`; a identidade é fornecida pelo stub e não deve
ser usada fora do ambiente descartável. Use a UI do Mailpit em
`http://127.0.0.1:8025` para conferir o e-mail simulado. A aplicação só envia se
o fluxo e os dados de destinatário autorizados permitirem; subir Mailpit não
cria documentos de negócio.

A configuração não carrega automaticamente documentos empresariais para as
collections `local_cattle_documents` e `local_medicine_documents`; o Compose
também não as popula. Um fluxo que depende desses documentos exige fixtures
locais ou dados preparados separadamente, conforme o contrato da ferramenta.
PostgreSQL recebe os scripts `infrastructure/docker/postgres/init/01_*.sql` a
`12_*.sql` e `99-read-only-role.sh` em volume vazio; modificar os scripts não
aplica migrações a um volume existente. Para reconstruir apenas o banco de
teste, pare os consumidores, remova o PVC/PV PostgreSQL ou recrie o cluster e
aplique tudo novamente. Não use remoção de PVC para preservar dados que queira
reter.

## Diagnóstico e persistência

```powershell
kubectl -n argos-local get pods -o wide
kubectl -n argos-local describe pod <pod>
kubectl -n argos-local logs <pod> --all-containers
kubectl -n argos-local get events --sort-by=.lastTimestamp
kubectl -n argos-local get pv,pvc
```

Se o Pod ficar `Pending`, confira capacidade e PVCs. `ImagePullBackOff` em uma
imagem `argos-*:local` normalmente significa que ela não foi construída e
carregada no cluster selecionado. `CrashLoopBackOff` deve ser analisado nos
logs; PostgreSQL executa os scripts de inicialização somente no primeiro boot de
um volume vazio. Se o Secret foi alterado após a criação dos Pods, reaplique o
Kustomize e reinicie os Deployments, pois as variáveis de ambiente são lidas no
início do processo:

```powershell
kubectl apply -k k8s/local
kubectl -n argos-local rollout restart deployment
```

Para verificar persistência de um Pod sem apagar dados:

```powershell
kubectl -n argos-local delete pod mongo-0 postgres-0
kubectl -n argos-local rollout status statefulset/mongo --timeout=5m
kubectl -n argos-local rollout status statefulset/postgres --timeout=5m
```

Validar também reinício de Qdrant e A2A e confirmar que o corpus/manifests
continuam disponíveis. Os fluxos A2A em andamento não são persistidos. Para
encerrar e descartar todos os dados locais, use a opção correspondente ao
provisionador:

```powershell
kind delete cluster --name argos-local
```

O comando acima remove somente o cluster Kind de exemplo. Para o Kubernetes
integrado ao Docker Desktop, encerre ou remova o cluster pela interface do
Docker Desktop em **Kubernetes**. A remoção do cluster descarta os dados locais
armazenados nos volumes do nó.

Este passo não executa a suíte integrada automaticamente. Depois de todos os
Pods estarem prontos, execute os testes E2E documentados pelo API principal e os
testes de integração das aplicações a partir do host. Um cluster com Pods Ready
confirma disponibilidade e conectividade básica, não o aceite de todos os fluxos
de negócio.
