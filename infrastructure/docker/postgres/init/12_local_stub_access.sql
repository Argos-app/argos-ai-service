-- Concede ao stub de identidade local a segunda propriedade de demonstração.
-- Este vínculo existe somente para o conjunto sintético do Compose.
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
