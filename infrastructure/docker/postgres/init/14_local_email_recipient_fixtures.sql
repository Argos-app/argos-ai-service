-- Contatos sintéticos para validar localmente a busca de destinatários do MCP de e-mail.
-- Não criam credenciais de autenticação nem vínculos de acesso a propriedades.
BEGIN;
SET LOCAL app.carga = 'true';

DO $validate_prerequisites$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM permission WHERE code = 'APLICADOR' AND active) THEN
        RAISE EXCEPTION 'A permissão APLICADOR é necessária para o fixture local de e-mail.';
    END IF;
END;
$validate_prerequisites$;

WITH recipients(id, source_id, name, cpf, email, firebase_uid) AS (
    VALUES
        ('00000000-0000-4000-8000-000000000201'::UUID,
         '00000000-0000-4000-8000-000000000301'::UUID,
         'Luis Medeiros', '90000000001', 'luis.medeiros@institutojef.org.br',
         'local-email-test-luis-medeiros'),
        ('00000000-0000-4000-8000-000000000202'::UUID,
         '00000000-0000-4000-8000-000000000302'::UUID,
         'Leticia Camargo', '90000000002', 'leticia.camargo@institutojef.org.br',
         'local-email-test-leticia-camargo'),
        ('00000000-0000-4000-8000-000000000203'::UUID,
         '00000000-0000-4000-8000-000000000303'::UUID,
         'Beatriz Inacio', '90000000003', 'beatriz.inacio@institutojef.org.br',
         'local-email-test-beatriz-inacio'),
        ('00000000-0000-4000-8000-000000000204'::UUID,
         '00000000-0000-4000-8000-000000000304'::UUID,
         'Enzo Alves', '90000000004', 'enzo.alves@institutojef.org.br',
         'local-email-test-enzo-alves'),
        ('00000000-0000-4000-8000-000000000205'::UUID,
         '00000000-0000-4000-8000-000000000305'::UUID,
         'Vinicius D''Oliveira', '90000000005', 'vinicius.doliveira@institutojef.org.br',
         'local-email-test-vinicius-doliveira')
)
INSERT INTO app_user (
    id, permission_id, name, cpf, email, firebase_uid, first_login, active, source_id
)
SELECT recipients.id,
       permission.id,
       recipients.name,
       recipients.cpf,
       recipients.email,
       recipients.firebase_uid,
       TRUE,
       TRUE,
       recipients.source_id
FROM recipients
JOIN permission ON permission.code = 'APLICADOR'
ON CONFLICT (email) DO NOTHING;

DO $validate_recipients$
BEGIN
    IF EXISTS (
        SELECT expected.email
        FROM (VALUES
            ('luis.medeiros@institutojef.org.br', 'Luis Medeiros'),
            ('leticia.camargo@institutojef.org.br', 'Leticia Camargo'),
            ('beatriz.inacio@institutojef.org.br', 'Beatriz Inacio'),
            ('enzo.alves@institutojef.org.br', 'Enzo Alves'),
            ('vinicius.doliveira@institutojef.org.br', 'Vinicius D''Oliveira')
        ) AS expected(email, name)
        LEFT JOIN app_user AS actual
          ON lower(BTRIM(actual.email)) = lower(expected.email)
         AND actual.active
        GROUP BY expected.email, expected.name
        HAVING count(actual.id) <> 1
            OR count(*) FILTER (
                WHERE actual.id IS NOT NULL AND BTRIM(actual.name) = expected.name
            ) <> 1
    ) THEN
        RAISE EXCEPTION 'Os destinatários do fixture local estão ambíguos ou incompletos.';
    END IF;
    IF (
        SELECT count(*)
        FROM app_user
        WHERE active
          AND email IN (
              'luis.medeiros@institutojef.org.br',
              'leticia.camargo@institutojef.org.br',
              'beatriz.inacio@institutojef.org.br',
              'enzo.alves@institutojef.org.br',
              'vinicius.doliveira@institutojef.org.br'
          )
    ) <> 5 THEN
        RAISE EXCEPTION 'Todos os cinco destinatários locais devem estar ativos.';
    END IF;
END;
$validate_recipients$;

COMMIT;
