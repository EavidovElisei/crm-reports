-- Таблица для хранения токенов авторизации
CREATE TABLE auth_tokens (
    id SERIAL PRIMARY KEY,
    service VARCHAR(50) UNIQUE NOT NULL,
    login VARCHAR(100) NOT NULL,
    password VARCHAR(255) NOT NULL,
    current_token TEXT,
    token_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Таблица для хранения заявок
CREATE TABLE requests (
    id BIGINT PRIMARY KEY,
    created_at TIMESTAMP NOT NULL,
    data JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индексы для оптимизации
CREATE INDEX idx_requests_created_at ON requests(created_at);
CREATE INDEX idx_requests_data_gin ON requests USING gin(data);
CREATE INDEX idx_auth_tokens_service ON auth_tokens(service);

-- Вставка начальных данных для CRM
INSERT INTO auth_tokens (service, login, password) 
VALUES ('crm', 'your_username', 'your_password')
ON CONFLICT (service) DO NOTHING;

-- Функция для автоматического обновления updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Триггер для обновления updated_at в requests
CREATE TRIGGER update_requests_updated_at 
    BEFORE UPDATE ON requests 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();