#!/usr/bin/env python3
"""
Отладка комментариев
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import DB_CONFIG
import psycopg2
from datetime import datetime

def get_last_comment(order):
    """Получение последнего комментария"""
    if not order or not isinstance(order, dict):
        return "—"
    comment = order.get('lastBankComment', '')
    return comment if comment else "—"

def main():
    # Подключаемся к БД
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    
    # Получаем данные за октябрь
    cur.execute("""
        SELECT data FROM requests 
        WHERE created_at BETWEEN '2025-10-01' AND '2025-10-29 23:59:59'
        ORDER BY created_at DESC
        LIMIT 10
    """)
    
    results = cur.fetchall()
    print(f"Найдено {len(results)} заявок")
    
    comment_stats = {}
    sample_comments = []
    
    for i, (data,) in enumerate(results):
        if not data or not isinstance(data, dict):
            continue
            
        comment = get_last_comment(data)
        print(f"Заявка {i+1}: комментарий = '{comment}'")
        
        if comment and comment != "—" and comment.strip():
            if len(sample_comments) < 5:
                sample_comments.append(comment)
            
            comment_lower = comment.lower().strip()
            
            # Простая группировка
            if 'счет' in comment_lower:
                key = 'Счет'
            elif 'касса' in comment_lower:
                key = 'Касса'
            elif 'установк' in comment_lower:
                key = 'Установка'
            else:
                key = 'Другие'
            
            comment_stats[key] = comment_stats.get(key, 0) + 1
        else:
            comment_stats['Без комментариев'] = comment_stats.get('Без комментариев', 0) + 1
    
    print(f"\nСтатистика комментариев: {comment_stats}")
    print(f"Примеры комментариев: {sample_comments}")
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
