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

O serviço deve obter essa autorização a partir do UUID interno do usuário e
revalidá-la nas chamadas protegidas. O JWT identifica o usuário; não torna
autoridade uma cópia desatualizada das fazendas. Um usuário habilitado sem
fazendas vigentes não pode iniciar uma sessão da IA, que exige ao menos uma
fazenda.

O catálogo versionado contém os códigos `ADMIN`, `GERENTE`, `PECUARISTA`,
`APLICADOR` e `SISTEMA`. `SISTEMA` é descrito como usuário técnico que não faz
login e não deve ser tratado como usuário humano habilitado. O rótulo de `ADMIN`
“acesso total” não cria uma exceção implícita para fazendas: o acesso da IA
continua seguindo a regra de proprietário ou vínculo acima, até que exista uma
regra de negócio explícita e validada para acesso global.

## Compatibilidade da implementação

No runtime atual, `apps/main_api/app/api/v1/dependencies/auth.py` valida um JWT
EdDSA delegado pelo backend e exige uma claim `operations`; as rotas aplicam uma
operação individual. A autorização comum descrita acima é a regra alvo do fluxo
Firebase, após existir validação do cadastro ativo e das fazendas no serviço.
Remover agora as verificações da claim antiga ampliaria o acesso de tokens
delegados sem a substituição equivalente e não faz parte desta etapa.

A etapa seguinte precisa adicionar ao `main_api` o acesso PostgreSQL somente
leitura e um adaptador para carregar o cadastro e as fazendas. Só então a rota
Firebase poderá emitir um JWT mínimo, e as dependências poderão migrar do
contexto delegado antigo para a identidade autenticada e revalidada no banco.

## Critérios para concluir a confirmação do ambiente

- Identificar a versão implantada das migrations de identidade e fazendas.
- Confirmar a existência e validade do índice único de `firebase_uid`.
- Medir cadastros sem UID e resolver sua conciliação antes da ativação.
- Confirmar que não há UIDs duplicados ou conflitos de identidade.
- Validar com o responsável de produto que os papéis humanos compartilham as
  operações de IA e que nenhum papel recebe fazendas fora dos vínculos ativos.
