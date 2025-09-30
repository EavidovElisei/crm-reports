-- Команда для выполнения:
-- psql -h localhost -d crm_reports -U crm_reports_user -f database/analytics_table.sql

-- Создание таблицы для данных аналитики
CREATE TABLE analytics_data (
    id SERIAL PRIMARY KEY,
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индексы для оптимизации
CREATE INDEX idx_analytics_data_period ON analytics_data(period_start, period_end);
CREATE INDEX idx_analytics_data_created_at ON analytics_data(created_at);
CREATE INDEX idx_analytics_data_gin ON analytics_data USING gin(data);

-- Функция для автоматического обновления updated_at
CREATE OR REPLACE FUNCTION update_analytics_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Триггер для обновления updated_at
CREATE TRIGGER update_analytics_updated_at 
    BEFORE UPDATE ON analytics_data 
    FOR EACH ROW 
    EXECUTE FUNCTION update_analytics_updated_at_column();

-- Добавление учетных данных для analytics API
INSERT INTO auth_tokens (service, login, password) 
VALUES ('analytics', '79997151332', 'fPp3UC2Zq')
ON CONFLICT (service) DO UPDATE SET
    login = EXCLUDED.login,
    password = EXCLUDED.password,
    token_updated_at = CURRENT_TIMESTAMP; 