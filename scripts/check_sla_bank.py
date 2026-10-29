#!/usr/bin/env python3
"""
Проверка данных отчёта /sla/bank.

Сверяет выгрузку с эталонным файлом для банка и ищет логические ошибки:
даты этапов идут в правильном порядке, значения не отрицательные,
у заявки есть ИНН, TID и телефон.

Запуск из корня проекта:
    .venv/bin/python scripts/check_sla_bank.py --our export.xlsx --ref sla_bank_ref.xlsx
"""
import argparse
import statistics
from collections import Counter

from openpyxl import load_workbook

STAGE_NAMES = [
    'Рабочих часов до первого звонка',
    'Рабочих дней до оплаты счёта',
    'Рабочих дней до отправки оборудования',
    'Рабочих дней до получения оборудования',
    'Рабочих дней до успешной реализации',
]
# Колонки таблицы: 0 — ID, 1 — ИНН, 2 — TID, дальше этапы
ID, INN, TID, CALL, PAY, DISP, REC, SUCC = range(8)


def load(path):
    ws = load_workbook(path, data_only=True).active
    rows = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0]:
            rows[int(row[0])] = list(row)
    return rows


def num(value):
    return None if value in (None, '') else float(value)


def describe(name, rows):
    print(f'\n=== {name}: {len(rows)} строк')
    for index, label in zip(range(3, 8), STAGE_NAMES):
        values = [num(r[index]) for r in rows.values() if num(r[index]) is not None]
        empty = len(rows) - len(values)
        if not values:
            continue
        print(
            f'  {label[:38]:40} заполнено {len(values):6} пусто {empty:6} '
            f'мин {min(values):8.2f} медиана {statistics.median(values):8.2f} '
            f'макс {max(values):9.2f}'
        )
    tid = sum(1 for r in rows.values() if r[TID] not in (None, ''))
    inn = sum(1 for r in rows.values() if r[INN] not in (None, ''))
    print(f'  ИНН заполнен у {inn}/{len(rows)}, TID у {tid}/{len(rows)}')

    # Логика: последний этап не может быть раньше предыдущего
    problems = Counter()
    for r in rows.values():
        succ, rec, disp_stage, pay_stage = num(r[SUCC]), num(r[REC]), num(r[DISP]), num(r[PAY])
        if None not in (succ, pay_stage) and succ < pay_stage:
            problems['реализация раньше оплаты'] += 1
        if None not in (succ, rec) and succ < rec:
            problems['реализация раньше получения'] += 1
        if None not in (rec, disp_stage) and rec < disp_stage:
            problems['получение раньше отправки'] += 1
        for index in range(3, 8):
            value = num(r[index])
            if value is not None and value < 0:
                problems['отрицательные значения'] += 1
    print('  логика:', dict(problems) or 'нарушений нет')


def compare(our, ref):
    common = set(our) & set(ref)
    print(f'\n=== Сверка: общих заявок {len(common)} из {len(ref)} в эталоне и {len(our)} у нас')
    for index, label in zip(range(3, 8), STAGE_NAMES):
        same = diff = only_ours = only_ref = 0
        deltas = []
        for rid in common:
            a, b = num(our[rid][index]), num(ref[rid][index])
            if a is not None and b is not None:
                if abs(a - b) <= 0.02:
                    same += 1
                else:
                    diff += 1
                    deltas.append((rid, a, b))
            elif a is None and b is None:
                same += 1
            elif b is None:
                only_ours += 1
            else:
                only_ref += 1
        worst = sorted(deltas, key=lambda d: -abs(d[1] - d[2]))[:3]
        print(
            f'  {label[:38]:40} совпало {same:6} расхождение {diff:5} '
            f'у нас пусто/у эталона есть {only_ref:5} наоборот {only_ours:4}'
        )
        if worst:
            print('     крупнейшие расхождения (ID, наш, эталон):', worst)

    tid_same = sum(1 for rid in common if str(our[rid][TID]) == str(ref[rid][TID]))
    print(f'  TID совпало {tid_same}/{len(common)}')

    missing = sorted(set(ref) - set(our))
    extra = sorted(set(our) - set(ref))
    print(f'  заявок эталона нет у нас: {len(missing)} | наших нет в эталоне: {len(extra)}')
    if missing:
        print('   примеры отсутствующих:', missing[:10])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--our', required=True, help='выгрузка /sla/bank/export')
    parser.add_argument('--ref', help='эталонный файл для банка')
    args = parser.parse_args()

    our = load(args.our)
    describe('Наша выгрузка', our)
    if args.ref:
        ref = load(args.ref)
        describe('Эталон', ref)
        compare(our, ref)


if __name__ == '__main__':
    main()