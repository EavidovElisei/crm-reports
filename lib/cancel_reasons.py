#!/usr/bin/env python3
"""
Причины отмен заявок — разбор комментария из CRM по категориям.

Комментарий к отмене приходит в двух видах:
  «Не нужна касса, попросил отменить заявку»
  «Отказ клиента, "текст причины"»
Поэтому сначала достаём текст в кавычках, а если его нет — берём строку целиком.

Категории общие для страницы /managers/cancel и скриптов проверки,
чтобы цифры в отчёте и в ручных сверках совпадали.
"""
import re

NO_REASON = 'Причина не указана'
EMPTY = 'Без содержания'

# Порядок = порядок строк в таблице отчёта
REASON_ORDER = [
    'Касса не нужна / передумал',
    'Не дозвонились',
    NO_REASON,
    'Нужен терминал/эквайринг',
    'Дубль заявки',
    'Ушёл к партнёру',
    'Нестандартная интеграция',
    'Расторжение договора (НИБ)',
    'Некорректное информирование',
    'Бизнес закрылся / сезонность',
    'Сроки доставки',
    'Прочее',
    EMPTY,
]

# Служебные ответы: причина по факту не названа
SERVICE_TEXTS = {
    'комментарий',
    'гбр',
    'отказ',
    'отказ клиента',
    'отказ по инициативе клиента.',
    'клиент передумал',
}


def inner_comment(text):
    """Текст в кавычках из «Отказ клиента, "…"», иначе строка целиком."""
    text = str(text or '').strip()
    match = re.search(r'"(.+)"', text, re.S)
    return (match.group(1) if match else text).strip()


def classify(text):
    """Категория причины отмены по тексту комментария."""
    raw = inner_comment(text)
    low = raw.lower()

    if not low:
        return NO_REASON
    if re.fullmatch(r'[.\-+_ ]*', low):
        return EMPTY
    if low.startswith('#') or 'za-' in low or 'ac-' in low or 'awap' in low:
        return NO_REASON
    if low in SERVICE_TEXTS:
        return NO_REASON

    if 'дозвон' in low:
        return 'Не дозвонились'
    if 'дубл' in low or 'две заявки' in low or 'одна касса' in low:
        return 'Дубль заявки'
    if (
        'пейкипер' in low or 'paykeeper' in low or 'life pay' in low
        or 'другой банк' in low or 'партнер' in low or 'партнёр' in low
    ):
        return 'Ушёл к партнёру'
    if 'терминал' in low or 'эквайринг' in low:
        return 'Нужен терминал/эквайринг'
    if 'интеграц' in low or 'iiko' in low or 'битрикс' in low:
        return 'Нестандартная интеграция'
    if 'расторж' in low:
        return 'Расторжение договора (НИБ)'
    if 'информир' in low:
        return 'Некорректное информирование'
    if 'не нужна' in low or 'не актуаль' in low or 'передумал' in low or 'нет потре' in low:
        return 'Касса не нужна / передумал'
    if 'закрытие' in low or 'сезон' in low:
        return 'Бизнес закрылся / сезонность'
    if 'доставк' in low or 'срок' in low:
        return 'Сроки доставки'
    return 'Прочее'


def reason_rows(counter):
    """Строки таблицы: только непустые категории, в фиксированном порядке."""
    return [(name, counter.get(name, 0)) for name in REASON_ORDER if counter.get(name)]


def percent(part, total):
    return 0.0 if not total else round(100.0 * part / total, 1)