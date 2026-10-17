-- Fixture exclusivamente local para validar a predição com um ano observável.
-- Preenche apenas dias sem aplicação nas duas propriedades do usuário stub;
-- todos os registros usam Vacina Teste A e são explicitamente sintéticos.
BEGIN;
SET LOCAL app.carga = 'true';

DO $validate_fixture_prerequisites$
BEGIN
    IF (SELECT count(*) FROM property
        WHERE id IN (
            '00000000-0000-4000-8000-000000000101'::UUID,
            '00000000-0000-4000-8000-000000000102'::UUID
        ) AND active AND source_id IS NOT NULL) <> 2 THEN
        RAISE EXCEPTION 'As duas propriedades sintéticas do stub local são obrigatórias.';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM medicine
        WHERE brand_name = 'Vacina Teste A'
          AND active
          AND meat_withdrawal_days = 0
    ) THEN
        RAISE EXCEPTION 'Vacina Teste A ativa e sem carência é obrigatória para o fixture.';
    END IF;
END;
$validate_fixture_prerequisites$;

WITH selected_medicine AS (
    SELECT m.id,
           m.brand_name,
           m.meat_withdrawal_days
    FROM medicine AS m
    WHERE m.brand_name = 'Vacina Teste A'
      AND m.active
      AND m.meat_withdrawal_days = 0
    ORDER BY (
        SELECT count(*)
        FROM application AS a
        JOIN medicine_batch AS existing_batch ON existing_batch.id = a.medicine_batch_id
        WHERE existing_batch.medicine_id = m.id
    ) DESC, m.id
    LIMIT 1
)
INSERT INTO medicine_batch (
    medicine_id,
    batch_number,
    manufacture_date,
    expiry_date,
    delivery_date,
    initial_quantity,
    min_stock,
    max_stock,
    storage_place,
    status,
    source_id
)
SELECT m.id,
       'LOCAL-PREDICTION-COVERAGE',
       current_date - 600,
       current_date + 365,
       current_date - 400,
       1000000,
       0,
       1000000,
       'Fixture sintético local',
       'DISPONIVEL',
       gen_random_uuid()
FROM selected_medicine AS m
ON CONFLICT (medicine_id, batch_number) DO NOTHING;

INSERT INTO stock_movement (
    medicine_batch_id,
    user_id,
    type,
    quantity,
    movement_date,
    reason
)
SELECT b.id,
       u.id,
       'E',
       b.initial_quantity,
       b.delivery_date + time '08:00',
       'CARGA_INICIAL'
FROM medicine_batch AS b
JOIN medicine AS m ON m.id = b.medicine_id
JOIN LATERAL (
    SELECT account_user.id
    FROM app_user AS account_user
    JOIN permission AS permission_role ON permission_role.id = account_user.permission_id
    WHERE account_user.active
      AND permission_role.code = 'SISTEMA'
    ORDER BY account_user.created_at
    LIMIT 1
) AS u ON TRUE
WHERE m.brand_name = 'Vacina Teste A'
  AND b.batch_number = 'LOCAL-PREDICTION-COVERAGE'
  AND NOT EXISTS (
      SELECT 1
      FROM stock_movement AS existing
      WHERE existing.medicine_batch_id = b.id
        AND existing.application_id IS NULL
        AND existing.type = 'E'
        AND existing.reason = 'CARGA_INICIAL'
  );

WITH test_property AS (
    SELECT p.id AS property_id,
           p.user_id,
           (
               SELECT l.id
               FROM herd AS h
               JOIN lot AS l ON l.herd_id = h.id
               WHERE h.property_id = p.id
                 AND h.active
                 AND l.active
                 AND l.head_count > 0
               ORDER BY l.lot_code, l.id
               LIMIT 1
           ) AS lot_id
    FROM property AS p
    WHERE p.id IN (
        '00000000-0000-4000-8000-000000000101'::UUID,
        '00000000-0000-4000-8000-000000000102'::UUID
    )
      AND p.active
      AND p.source_id IS NOT NULL
),
test_day AS (
    SELECT (current_date - day_offset)::DATE AS application_date
    FROM generate_series(1, 365) AS day_offset
),
test_medicine AS (
    SELECT m.id AS medicine_id,
           m.dose,
           m.unit,
           m.route,
           m.meat_withdrawal_days,
           b.id AS medicine_batch_id
    FROM medicine AS m
    JOIN medicine_batch AS b ON b.medicine_id = m.id
    WHERE m.brand_name = 'Vacina Teste A'
      AND m.active
      AND b.batch_number = 'LOCAL-PREDICTION-COVERAGE'
    ORDER BY m.id
    LIMIT 1
)
INSERT INTO application (
    lot_id,
    medicine_batch_id,
    user_id,
    application_date,
    application_time,
    dose,
    unit,
    route,
    meat_withdrawal_end,
    head_count_applied,
    goal,
    notes,
    source_id
)
SELECT tp.lot_id,
       tm.medicine_batch_id,
       tp.user_id,
       td.application_date,
       time '08:00',
       tm.dose,
       tm.unit,
       tm.route,
       td.application_date + tm.meat_withdrawal_days,
       1,
       'Homologação de predição local',
       'LOCAL_PREDICTION_COVERAGE_FIXTURE',
       gen_random_uuid()
FROM test_property AS tp
CROSS JOIN test_day AS td
CROSS JOIN test_medicine AS tm
WHERE tp.lot_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM application AS existing
      JOIN lot AS existing_lot ON existing_lot.id = existing.lot_id
      JOIN herd AS existing_herd ON existing_herd.id = existing_lot.herd_id
      WHERE existing_herd.property_id = tp.property_id
        AND existing.active
        AND existing.application_date = td.application_date
  );

DO $validate_fixture_coverage$
BEGIN
    IF EXISTS (
        SELECT p.id
        FROM property AS p
        JOIN herd AS h ON h.property_id = p.id
        JOIN lot AS l ON l.herd_id = h.id
        JOIN application AS a ON a.lot_id = l.id AND a.active
        WHERE p.id IN (
            '00000000-0000-4000-8000-000000000101'::UUID,
            '00000000-0000-4000-8000-000000000102'::UUID
        )
        GROUP BY p.id
        HAVING count(DISTINCT a.application_date) FILTER (
            WHERE a.application_date BETWEEN current_date - 365 AND current_date - 1
        ) <> 365
    ) OR (SELECT count(*) FROM local_prediction_coverage WHERE property_id IN (
        '00000000-0000-4000-8000-000000000101'::UUID,
        '00000000-0000-4000-8000-000000000102'::UUID
    )) <> 2 THEN
        RAISE EXCEPTION 'A cobertura sintética anual local não foi completa.';
    END IF;
END;
$validate_fixture_coverage$;

COMMIT;
