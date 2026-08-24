-- Комментарии к этапам SLA (внутренняя заметка по заявке + этапу)
-- psql -h localhost -d crm_reports -U crm_reports_user -f database/sla_stage_comments.sql

CREATE TABLE IF NOT EXISTS sla_stage_comments (
    request_id BIGINT NOT NULL,
    stage_key TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id, stage_key)
);

CREATE INDEX IF NOT EXISTS idx_sla_stage_comments_updated_at
    ON sla_stage_comments (updated_at DESC);
