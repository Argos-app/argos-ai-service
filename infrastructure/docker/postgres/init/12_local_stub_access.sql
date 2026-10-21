-- Mantém o Firebase UID do cadastro sintético alinhado ao Principal local.
-- A chave única do banco rejeita associação a outra identidade.
UPDATE app_user
SET firebase_uid = 'local-stub-user'
WHERE id = '00000000-0000-4000-8000-000000000001'::UUID
  AND firebase_uid IS DISTINCT FROM 'local-stub-user';

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
