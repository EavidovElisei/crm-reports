#!/usr/bin/env python3
"""
Воркер авторизации - получает токен и обновляет его в БД
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import requests
import hashlib
import base64
import logging
from datetime import datetime
from config import DB_CONFIG, CRM_CONFIG

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - AUTH - %(message)s')


def get_credentials():
    """Получение логина и пароля из БД"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("SELECT login, password FROM auth_tokens WHERE service = 'crm'")
    result = cur.fetchone()

    cur.close()
    conn.close()

    if not result:
        raise ValueError("Не найдены учетные данные для CRM")

    return result[0], result[1]


def get_token(username, password):
    """Получение токена из CRM"""
    # Кодируем пароль
    sha256_password = hashlib.sha256(password.encode()).digest()
    encoded_password = base64.b64encode(sha256_password).decode()

    data = {
        'grant_type': 'password',
        'client_id': CRM_CONFIG['client_id'],
        'client_secret': CRM_CONFIG['client_secret'],
        'username': username,
        'password': encoded_password
    }

    headers = {'Content-Type': 'application/x-www-form-urlencoded'}

    response = requests.post(CRM_CONFIG['auth_url'], data=data, headers=headers)
    response.raise_for_status()

    token_data = response.json()
    return f"Bearer {token_data['access_token']}"


def update_token(token):
    """Обновление токена в БД"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute("""
        UPDATE auth_tokens 
        SET current_token = %s, token_updated_at = %s 
        WHERE service = 'crm'
    """, (token, datetime.now()))

    conn.commit()
    cur.close()
    conn.close()


def main():
    """Основная функция воркера"""
    try:
        logging.info("🔐 Запуск воркера авторизации")

        # Получаем учетные данные
        username, password = get_credentials()
        logging.info(f"Получены учетные данные для: {username}")

        # Получаем токен
        token = get_token(username, password)
        logging.info("✅ Токен успешно получен")

        # Обновляем в БД
        update_token(token)
        logging.info("✅ Токен обновлен в БД")

        return True

    except Exception as e:
        logging.error(f"❌ Ошибка в воркере авторизации: {e}")
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)