# Autenticação Firebase e autorização

A Main API autentica a identidade pelo Firebase e autoriza o usuário e suas
propriedades pelo PostgreSQL. O contrato HTTP e os exemplos estão em
[api-contract.md](api-contract.md).

## Troca e uso do token

1. O cliente envia um Firebase ID token no Bearer de `POST /v1/auth/firebase`. O
   Firebase Admin verifica assinatura, validade, projeto e revogação.
2. O serviço busca o cadastro por `app_user.firebase_uid`, valida situação e
   propriedades e emite um JWT próprio Ed25519. O UUID interno Argos fica no
   `sub` e o UID Firebase é incluído como claim; nome, e-mail, papel e fazendas
   não são autorizações carregadas pelo token.
3. Nas rotas protegidas seguintes, o cliente envia o JWT próprio. A API valida o
   token e recarrega o cadastro e as fazendas pelo UID, conferindo também o UUID
   interno. Alterações de acesso passam a valer nas próximas consultas.

O UID é a chave de associação: e-mail ausente, não verificado ou coincidente não
vincula automaticamente um cadastro. Um usuário sem UID associado precisa ser
conciliado por um processo administrativo externo. A revogação do Firebase é
verificada na troca; um JWT próprio já emitido pode continuar válido até
expirar, com TTL padrão de cinco minutos e limite definido nas configurações.

## Regras de acesso

O usuário e sua permissão cadastrada devem estar ativos, sem bloqueio vigente; a
Main API exige ao menos uma propriedade autorizada. As propriedades ativas são
obtidas por titularidade ou vínculo ativo dentro das datas de vigência. O código
do papel, por si só, não concede acesso global às propriedades. Sessões
continuam limitadas ao seu titular.

Token ausente ou inválido retorna 401; cadastro sem autorização retorna 403;
falha da verificação Firebase ou do PostgreSQL retorna 503. Não aceite usuário,
permissões ou fazendas fornecidos pela mensagem como autoridade.

O PostgreSQL é consultado por uma credencial read-only configurada por
`MAIN_API_AUTH_DATABASE_URL`. O Firebase usa `MAIN_API_FIREBASE_PROJECT_ID` e
Application Default Credentials ou `MAIN_API_FIREBASE_CREDENTIALS_FILE`. O JWT
próprio requer chave privada Ed25519 e ID de chave do serviço; suas variáveis
estão no [.env.example](../.env.example). `AUTH_MODE=stub` só é permitido com
`APP_ENV=local`.

## Chamadas entre serviços

A Main API usa tokens internos e delegações assinadas para os MCPs de dados e
predição e para o A2A documental. As verificações e reconsultas exigidas por
cada operação estão em `packages/security` e nos serviços correspondentes. O MCP
de e-mail usa Bearer de serviço e valida o envelope assinado do relatório; a
autorização de envio é controlada no fluxo da Main API. Mantenha as chaves de
token da API, delegação e assinatura do relatório separadas.

## Testes

```powershell
python -m pytest apps/main_api/tests/unit/test_auth.py apps/main_api/tests/unit/test_access_tokens.py apps/main_api/tests/unit/test_firebase_auth.py apps/main_api/tests/unit/test_firebase_auth_route.py apps/main_api/tests/unit/test_postgres_user_access.py apps/main_api/tests/unit/test_routes.py -q
```

Esses testes usam doubles para Firebase e banco. A consulta PostgreSQL real é
coberta com infraestrutura descartável conforme o
[guia de integração da Main API](../tests/integration/README.md). Nenhum desses
testes comprova credenciais ou schema de um ambiente implantado.
