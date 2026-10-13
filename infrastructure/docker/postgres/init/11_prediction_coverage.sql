-- Cobertura explicitamente sintética para o ambiente local de homologação.
-- Não representa a cobertura do backend nem deve ser publicada em produção.
CREATE OR REPLACE VIEW local_prediction_coverage AS
SELECT p.id AS property_id,
       current_date - 365 AS coverage_start,
       current_date AS coverage_end,
       'dataload_sintetico_local'::text AS evidence_source
FROM property AS p
WHERE p.source_id IS NOT NULL;
