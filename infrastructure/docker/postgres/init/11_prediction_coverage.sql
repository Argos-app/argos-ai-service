-- Cobertura sintética local: só declara uma janela quando existe ao menos uma
-- aplicação registrada em cada dia do histórico anual exigido pela predição.
-- Não representa a cobertura do backend nem deve ser publicada em produção.
CREATE OR REPLACE VIEW local_prediction_coverage AS
SELECT p.id AS property_id,
       current_date - 365 AS coverage_start,
       current_date - 1 AS coverage_end,
       'dataload_sintetico_local_diario'::text AS evidence_source
FROM property AS p
JOIN herd AS h ON h.property_id = p.id
JOIN lot AS l ON l.herd_id = h.id
JOIN application AS a ON a.lot_id = l.id AND a.active = TRUE
WHERE p.source_id IS NOT NULL
GROUP BY p.id
HAVING count(DISTINCT a.application_date) FILTER (
    WHERE a.application_date BETWEEN current_date - 365 AND current_date - 1
) = 365;
