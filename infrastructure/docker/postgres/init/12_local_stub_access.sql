-- Concede ao stub de identidade local a segunda propriedade de demonstração.
-- Este vínculo existe somente para o conjunto sintético do Compose.
-- Mantém o Firebase UID do cadastro de teste alinhado ao Principal local da API.
DO $stub_identity$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM app_user
        WHERE id = '00000000-0000-4000-8000-000000000001'::UUID
    ) THEN
        RAISE EXCEPTION 'O usuário sintético do stub local não existe.';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM app_user
        WHERE firebase_uid = 'local-stub-user'
          AND id <> '00000000-0000-4000-8000-000000000001'::UUID
    ) THEN
        RAISE EXCEPTION 'O Firebase UID do stub local já está associado a outro usuário.';
    END IF;

    UPDATE app_user
    SET firebase_uid = 'local-stub-user'
    WHERE id = '00000000-0000-4000-8000-000000000001'::UUID
      AND firebase_uid IS DISTINCT FROM 'local-stub-user';
END;
$stub_identity$;

INSERT INTO user_property (user_id, property_id, link_date, active)
SELECT
    '00000000-0000-4000-8000-000000000001'::UUID,
    '00000000-0000-4000-8000-000000000102'::UUID,
    CURRENT_DATE,
    TRUE
WHERE NOT EXISTS (
    SELECT 1
    FROM user_property
    WHERE user_id = '00000000-0000-4000-8000-000000000001'::UUID
      AND property_id = '00000000-0000-4000-8000-000000000102'::UUID
      AND active = TRUE
      AND link_date <= CURRENT_DATE
      AND (unlink_date IS NULL OR unlink_date >= CURRENT_DATE)
);
