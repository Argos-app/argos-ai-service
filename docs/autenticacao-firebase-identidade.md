# Autenticação Firebase: identidade e regra de acesso

Este documento registra a primeira etapa da integração de autenticação do
serviço de IA: a chave de identidade do usuário, a política para cadastros
legados e a divisão entre autenticação e autorização.

## Evidência disponível

O esquema versionado em `argos-data-engineering/modulos/02_tabelas.sql` e nas
migrations `V2__identity_and_access.sql` e `V3__property_and_device.sql` do
backend contém:

- `app_user.id`, UUID que identifica o cadastro Argos;
- `app_user.firebase_uid`, nullable e destinado ao UID do Firebase;
- `app_user.email`, obrigatório e único por comparação textual;
- `app_user.active` e `app_user.blocked_until`, para a situação do cadastro;
- `app_user.permission_id`, relacionado a `permission.id`;
- `permission.active`, que indica se a permissão cadastrada permanece ativa;
- `property.user_id`, que associa a fazenda proprietária a `app_user.id`;
- `user_property`, vínculo adicional entre usuário e fazenda, com estado e
  período de vigência.

O arquivo `argos-data-engineering/modulos/04_indices.sql` declara o índice único
`uq_app_user_firebase` em `app_user.firebase_uid`. A coluna ser nullable permite
registros sem UID; o índice impede que um mesmo UID não nulo seja associado a
mais de um cadastro na instalação que aplicou essa definição.

Esses arquivos confirmam o esquema **versionado localmente**. Não foi possível
consultar o banco implantado nesta etapa: `main_api` não possui DSN PostgreSQL
nem leitor SQL configurado. A presença da definição no repositório não confirma
que todas as instalações aplicaram a migration ou o índice.

## Decisões de identidade

1. O UID extraído de um Firebase ID token validado é a chave de associação com
   `app_user.firebase_uid`.
2. `app_user.id` permanece a identidade interna usada por sessões e pelos
   registros do serviço de IA. Não usar e-mail como `sub` nem substituir esse
   UUID pelo UID Firebase.
3. E-mail, nome e permissões recebidos do cliente ou contidos no ID token não
   substituem os valores cadastrais. E-mail ausente ou não verificado não altera
   a busca de um UID já vinculado.
4. Um cadastro sem `firebase_uid` não é associado automaticamente a partir de
   uma coincidência de e-mail. Ele requer conciliação administrativa confiável
   que confirme a identidade Firebase e grave o UID no cadastro. Até essa
   conciliação, o serviço não emite JWT da IA para esse usuário.
5. A conciliação deve interromper o processo quando o e-mail verificado não
   encontra cadastro, encontra múltiplos candidatos sem distinção segura ou
   conflita com um UID já vinculado. Não atualizar cadastros nem criar vínculos
   durante a autenticação da IA.

Consulta de diagnóstico para executar **no banco implantado**, antes da
ativação. Ela não foi executada como parte desta etapa:

```sql
SELECT
    COUNT(*) AS total_users,
    COUNT(*) FILTER (WHERE firebase_uid IS NULL) AS users_without_firebase_uid,
    COUNT(*) FILTER (WHERE firebase_uid IS NOT NULL) AS users_with_firebase_uid
FROM app_user;

SELECT lower(email) AS normalized_email, COUNT(*) AS user_count
FROM app_user
GROUP BY lower(email)
HAVING COUNT(*) > 1;

SELECT firebase_uid, COUNT(*) AS user_count
FROM app_user
WHERE firebase_uid IS NOT NULL
GROUP BY firebase_uid
HAVING COUNT(*) > 1;
```

O último diagnóstico deve retornar zero linhas se o índice único correspondente
estiver instalado e íntegro. Verificar também a definição do índice no catálogo
do PostgreSQL. Antes de ativar o novo fluxo, inventariar os cadastros sem UID e
concluir a conciliação; não preencher a coluna com base apenas no e-mail.

## Decisões de autorização

Todos os usuários cujo cadastro esteja ativo, não bloqueado e com permissão
cadastral ativa compartilham as operações HTTP da IA. Operações não são
permissões individuais nem serão devolvidas ao frontend ou incluídas no JWT
próprio.

O acesso variável é o conjunto de fazendas vigente, obtido do banco para o
usuário autenticado. São elegíveis as propriedades ativas em que
`property.user_id` identifica o usuário, ou em que há um registro
`user_property` ativo com `link_date <= CURRENT_DATE` e `unlink_date` nulo ou
maior/igual à data corrente. O próprio cadastro do usuário e sua permissão
também precisam estar ativos, e `blocked_until` não pode estar no futuro.

O serviço obtém essa autorização por `app_user.firebase_uid` e confirma que o
UUID interno retornado corresponde ao `sub` do JWT em cada chamada protegida. O
JWT identifica o usuário; não torna autoridade uma cópia desatualizada das
fazendas. Um usuário habilitado sem fazendas vigentes não pode iniciar uma
sessão da IA, que exige ao menos uma fazenda.

O catálogo versionado contém os códigos `ADMIN`, `GERENTE`, `PECUARISTA`,
`APLICADOR` e `SISTEMA`. O serviço de IA não restringe a autenticação pelo
código do papel: inclusive `SISTEMA` pode autenticar quando possui identidade
Firebase vinculada, cadastro ativo, ausência de bloqueio, permissão cadastral
ativa e fazendas autorizadas. Todos os papéis seguem essas mesmas verificações.
O rótulo de `ADMIN` “acesso total” não cria uma exceção implícita para fazendas:
o acesso da IA continua seguindo a regra de proprietário ou vínculo acima, até
que exista uma regra de negócio explícita e validada para acesso global.

## Compatibilidade da implementação

O `main_api` agora possui um repositório PostgreSQL somente leitura para
carregar esses campos e as fazendas vigentes. O pool é configurado por
`MAIN_API_AUTH_DATABASE_URL`, usa timeout de conexão e consulta e verifica no
startup o modo read-only, o acesso `SELECT` às quatro tabelas e a ausência de
permissões de escrita efetivas da credencial. O Compose local conecta o serviço
com o usuário `argos_reader`, já provisionado pelos scripts locais.

O Firebase Admin SDK é inicializado quando `MAIN_API_FIREBASE_PROJECT_ID` está
configurado. As credenciais vêm de `MAIN_API_FIREBASE_CREDENTIALS_FILE` (arquivo
de service account montado como secret) ou de Application Default Credentials no
ambiente. Com Firebase habilitado, `MAIN_API_AUTH_DATABASE_URL` também é
obrigatório. `FirebaseAdminIdTokenVerifier` valida assinatura, validade e
projeto do Firebase ID token e solicita ao Admin SDK a verificação de revogação
e conta desativada na troca. `FirebaseIdentityAuthenticator` extrai UID, e-mail
opcional e `email_verified`, e busca `app_user.firebase_uid` exclusivamente pelo
UID. E-mail ausente ou não verificado nunca é usado para localizar ou associar
um cadastro.

`POST /v1/auth/firebase` recebe o Firebase ID token no header Bearer, sem corpo,
e devolve somente `{ "access_token": "..." }`. O Firebase autentica a
identidade; o serviço busca o cadastro pelo UID e autoriza somente usuários
ativos, não bloqueados, com permissão cadastral ativa e ao menos uma fazenda
vigente.

O JWT próprio é assinado com Ed25519, identificado por `kid` e tem validade
inicial de cinco minutos. Contém o UUID interno Argos no `sub`, o UID Firebase e
claims técnicos de emissor, audiência, versão, identificador único e datas de
emissão/expiração. Não inclui nome, e-mail, permissão ou fazendas. Sua chave é
exclusiva e distinta da chave usada para assinar delegações MCP. Cada chamada
protegida valida essa assinatura e recarrega cadastro e fazendas por
`firebase_uid` no PostgreSQL, confirmando também que o UUID interno do cadastro
corresponde ao `sub` do JWT. As três operações atuais da IA são comuns e
atribuídas pelo serviço; permissões de fazenda vêm do banco, nunca de claims ou
campos enviados pelo cliente. Mudanças de vínculo ou bloqueios cadastrais têm
efeito nas chamadas seguintes.

As delegações assinadas enviadas aos MCPs usam o UID Firebase como `sub` e
incluem somente as propriedades autorizadas necessárias à chamada. Cada MCP
consulta o acesso atual por `app_user.firebase_uid`, revalida situação e
vínculos, e resolve o UUID interno do cadastro na mesma consulta. O UUID
resolvido permanece no escopo interno do MCP de dados para assinar a origem de
relatórios; ele não é transportado na delegação.

A revogação de um Firebase ID token é conferida com `check_revoked=True` na
próxima chamada a `POST /v1/auth/firebase`. JWTs próprios já emitidos não fazem
consulta ao Firebase em cada chamada e podem continuar válidos até seu `exp`,
com janela máxima de cinco minutos pela configuração limitada de TTL. O cliente
encerra sua sessão local descartando o JWT; tokens já emitidos permanecem
válidos até expirar. O cadastro, bloqueio e acesso a fazendas continuam sendo
revalidados no PostgreSQL em cada chamada protegida.

Tokens próprios inválidos ou expirados retornam 401. Usuário ausente ou com
Firebase UID divergente retorna 401. Cadastro inativo, bloqueado, permissão
inativa ou ausência de fazendas retorna 403. Falha de Firebase durante a troca
ou do PostgreSQL retorna 503 sem liberar acesso.

As rotas `/v1/` não aceitam JWTs do backend de identidade. O `AUTH_MODE=stub`
continua disponível somente para desenvolvimento local; fora dele, Firebase,
PostgreSQL read-only e a chave privada Ed25519 do emissor são obrigatórios. A
validade padrão do JWT próprio é cinco minutos; o cliente renova obtendo um
Firebase ID token atualizado e chamando novamente a rota de troca.

## Testes automatizados de autenticação

A suíte unitária cobre a validação Firebase na fronteira do Admin SDK com
revogação habilitada, associação exclusiva por UID, e-mail ausente ou não
verificado, troca HTTP por JWT, expiração e nova troca, situação cadastral,
revalidação de fazendas e falhas das dependências. Os cenários cadastrais usam o
mesmo UUID e UID do JWT para distinguir corretamente rejeições de identidade
(401) de rejeições de autorização (403). O papel `SISTEMA` também é testado como
habilitado quando atende às mesmas regras dos demais papéis.

O SDK Firebase e o banco são simulados nesses testes; eles não comprovam as
credenciais nem a disponibilidade de uma instalação real. Execute:

```powershell
.venv\Scripts\pytest.exe apps/main_api/tests/unit/test_auth.py apps/main_api/tests/unit/test_access_tokens.py apps/main_api/tests/unit/test_firebase_auth.py apps/main_api/tests/unit/test_firebase_auth_route.py apps/main_api/tests/unit/test_postgres_user_access.py apps/main_api/tests/unit/test_routes.py -q
```

`tests/integration/test_auth_postgres.py`, dentro de `apps/main_api`, executa o
SELECT real em um schema temporário de um PostgreSQL de testes. A fixture cria e
remove apenas esse schema, com uma versão mínima das quatro tabelas e suas
colunas usadas na consulta. Ela valida isolamento por UID, fazendas próprias,
vínculos, limites inclusivos de vigência, inatividade, bloqueio e remoção de
acesso entre consultas. Não substitui a conferência das migrations implantadas.
Configure uma conexão com permissão de criar schemas em um banco **descartável
de testes**, instale `asyncpg` e execute:

```powershell
$env:MAIN_API_TEST_AUTH_DATABASE_URL = "postgresql://test_user:test_password@localhost:54329/test_database"
.venv\Scripts\pytest.exe apps/main_api/tests/integration/test_auth_postgres.py -q
```

Sem essa variável, os testes de integração são explicitamente ignorados; isso
não deve ser interpretado como validação do PostgreSQL real.

## Critérios para concluir a confirmação do ambiente

- Identificar a versão implantada das migrations de identidade e fazendas.
- Confirmar a existência e validade do índice único de `firebase_uid`.
- Medir cadastros sem UID e resolver sua conciliação antes da ativação.
- Confirmar que não há UIDs duplicados ou conflitos de identidade.
- Validar com o responsável de produto que os papéis humanos compartilham as
  operações de IA e que nenhum papel recebe fazendas fora dos vínculos ativos.
