#!/usr/bin/env python3
"""Тепловая карта отправок ККТ по субъектам РФ: помесячно и накопительно."""
from __future__ import annotations

import html
import json
import re
from calendar import monthrange
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from lib.region_map_paths import PATHS

EXCLUDED_STATUSES = (
    "CANCELED_BY_CLIENT",
    "CANCELED_BY_BANK",
    "REFUND",
)

MONTH_LABELS = (
    "Янв", "Фев", "Мар", "Апр", "Май", "Июн",
    "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек",
)

# hc-key карты → официальное имя субъекта.
REGION_NAMES = {
    "ru-2510": "Москва",
    "ru-ad": "Республика Адыгея",
    "ru-al": "Алтайский край",
    "ru-am": "Амурская область",
    "ru-ar": "Архангельская область",
    "ru-as": "Астраханская область",
    "ru-bk": "Республика Башкортостан",
    "ru-bl": "Белгородская область",
    "ru-br": "Брянская область",
    "ru-bu": "Республика Бурятия",
    "ru-ck": "Чукотский автономный округ",
    "ru-cl": "Челябинская область",
    "ru-cn": "Чеченская Республика",
    "ru-ct": "Забайкальский край",
    "ru-cv": "Чувашская Республика",
    "ru-da": "Республика Дагестан",
    "ru-ga": "Республика Алтай",
    "ru-in": "Республика Ингушетия",
    "ru-ir": "Иркутская область",
    "ru-iv": "Ивановская область",
    "ru-ka": "Камчатский край",
    "ru-kb": "Кабардино-Балкарская Республика",
    "ru-kc": "Карачаево-Черкесская Республика",
    "ru-kd": "Краснодарский край",
    "ru-ke": "Кемеровская область",
    "ru-kg": "Калужская область",
    "ru-kh": "Хабаровский край",
    "ru-ki": "Республика Карелия",
    "ru-kk": "Республика Хакасия",
    "ru-kl": "Республика Калмыкия",
    "ru-km": "Ханты-Мансийский автономный округ — Югра",
    "ru-kn": "Калининградская область",
    "ru-ko": "Республика Коми",
    "ru-ks": "Курская область",
    "ru-kt": "Костромская область",
    "ru-ku": "Курганская область",
    "ru-kv": "Кировская область",
    "ru-ky": "Красноярский край",
    "ru-ln": "Ленинградская область",
    "ru-lp": "Липецкая область",
    "ru-me": "Республика Марий Эл",
    "ru-mg": "Магаданская область",
    "ru-mm": "Мурманская область",
    "ru-mr": "Республика Мордовия",
    "ru-ms": "Московская область",
    "ru-ng": "Новгородская область",
    "ru-nn": "Ненецкий автономный округ",
    "ru-no": "Республика Северная Осетия — Алания",
    "ru-ns": "Новосибирская область",
    "ru-nz": "Нижегородская область",
    "ru-ob": "Оренбургская область",
    "ru-ol": "Орловская область",
    "ru-om": "Омская область",
    "ru-pe": "Пермский край",
    "ru-pr": "Приморский край",
    "ru-ps": "Псковская область",
    "ru-pz": "Пензенская область",
    "ru-ro": "Ростовская область",
    "ru-rz": "Рязанская область",
    "ru-sa": "Самарская область",
    "ru-sk": "Республика Саха (Якутия)",
    "ru-sl": "Сахалинская область",
    "ru-sm": "Смоленская область",
    "ru-sp": "Санкт-Петербург",
    "ru-sr": "Саратовская область",
    "ru-st": "Ставропольский край",
    "ru-sv": "Свердловская область",
    "ru-tb": "Тамбовская область",
    "ru-tl": "Тульская область",
    "ru-to": "Томская область",
    "ru-tt": "Республика Татарстан",
    "ru-tu": "Республика Тыва",
    "ru-tv": "Тверская область",
    "ru-ty": "Тюменская область",
    "ru-ud": "Удмуртская Республика",
    "ru-ul": "Ульяновская область",
    "ru-vg": "Волгоградская область",
    "ru-vl": "Владимирская область",
    "ru-vo": "Вологодская область",
    "ru-vr": "Воронежская область",
    "ru-yn": "Ямало-Ненецкий автономный округ",
    "ru-ys": "Ярославская область",
    "ru-yv": "Еврейская автономная область",
}

# Длинные формулировки из адреса — раньше коротких, чтобы «москва» не перебивала «московская».
REGION_PHRASES = [
    ("ханты мансийский автономный округ", "ru-km"),
    ("ханты мансийский ао", "ru-km"),
    ("ямало ненецкий автономный округ", "ru-yn"),
    ("ямало ненецкий ао", "ru-yn"),
    ("ненецкий автономный округ", "ru-nn"),
    ("чукотский автономный округ", "ru-ck"),
    ("еврейская автономная область", "ru-yv"),
    ("кабардино балкарская республика", "ru-kb"),
    ("карачаево черкесская республика", "ru-kc"),
    ("северная осетия алания", "ru-no"),
    ("республика северная осетия", "ru-no"),
    ("кемеровская область кузбасс", "ru-ke"),
    ("кемеровская область", "ru-ke"),
    ("московская область", "ru-ms"),
    ("московская обл", "ru-ms"),
    ("ленинградская область", "ru-ln"),
    ("ленинградская обл", "ru-ln"),
    ("свердловская область", "ru-sv"),
    ("свердловская обл", "ru-sv"),
    ("новосибирская область", "ru-ns"),
    ("новосибирская обл", "ru-ns"),
    ("ростовская область", "ru-ro"),
    ("ростовская обл", "ru-ro"),
    ("воронежская область", "ru-vr"),
    ("воронежская обл", "ru-vr"),
    ("волгоградская область", "ru-vg"),
    ("волгоградская обл", "ru-vg"),
    ("нижегородская область", "ru-nz"),
    ("нижегородская обл", "ru-nz"),
    ("челябинская область", "ru-cl"),
    ("челябинская обл", "ru-cl"),
    ("самарская область", "ru-sa"),
    ("самарская обл", "ru-sa"),
    ("саратовская область", "ru-sr"),
    ("саратовская обл", "ru-sr"),
    ("иркутская область", "ru-ir"),
    ("иркутская обл", "ru-ir"),
    ("тюменская область", "ru-ty"),
    ("тюменская обл", "ru-ty"),
    ("омская область", "ru-om"),
    ("омская обл", "ru-om"),
    ("томская область", "ru-to"),
    ("томская обл", "ru-to"),
    ("ульяновская область", "ru-ul"),
    ("ульяновская обл", "ru-ul"),
    ("ярославская область", "ru-ys"),
    ("ярославская обл", "ru-ys"),
    ("калининградская область", "ru-kn"),
    ("калининградская обл", "ru-kn"),
    ("мурманская область", "ru-mm"),
    ("мурманская обл", "ru-mm"),
    ("архангельская область", "ru-ar"),
    ("архангельская обл", "ru-ar"),
    ("вологодская область", "ru-vo"),
    ("вологодская обл", "ru-vo"),
    ("новгородская область", "ru-ng"),
    ("новгородская обл", "ru-ng"),
    ("псковская область", "ru-ps"),
    ("псковская обл", "ru-ps"),
    ("тверская область", "ru-tv"),
    ("тверская обл", "ru-tv"),
    ("смоленская область", "ru-sm"),
    ("смоленская обл", "ru-sm"),
    ("брянская область", "ru-br"),
    ("брянская обл", "ru-br"),
    ("калужская область", "ru-kg"),
    ("калужская обл", "ru-kg"),
    ("тульская область", "ru-tl"),
    ("тульская обл", "ru-tl"),
    ("рязанская область", "ru-rz"),
    ("рязанская обл", "ru-rz"),
    ("владимирская область", "ru-vl"),
    ("владимирская обл", "ru-vl"),
    ("ивановская область", "ru-iv"),
    ("ивановская обл", "ru-iv"),
    ("костромская область", "ru-kt"),
    ("костромская обл", "ru-kt"),
    ("кировская область", "ru-kv"),
    ("кировская обл", "ru-kv"),
    ("пензенская область", "ru-pz"),
    ("пензенская обл", "ru-pz"),
    ("тамбовская область", "ru-tb"),
    ("тамбовская обл", "ru-tb"),
    ("липецкая область", "ru-lp"),
    ("липецкая обл", "ru-lp"),
    ("курская область", "ru-ks"),
    ("курская обл", "ru-ks"),
    ("орловская область", "ru-ol"),
    ("орловская обл", "ru-ol"),
    ("белгородская область", "ru-bl"),
    ("белгородская обл", "ru-bl"),
    ("астраханская область", "ru-as"),
    ("астраханская обл", "ru-as"),
    ("оренбургская область", "ru-ob"),
    ("оренбургская обл", "ru-ob"),
    ("курганская область", "ru-ku"),
    ("курганская обл", "ru-ku"),
    ("сахалинская область", "ru-sl"),
    ("сахалинская обл", "ru-sl"),
    ("магаданская область", "ru-mg"),
    ("магаданская обл", "ru-mg"),
    ("амурская область", "ru-am"),
    ("амурская обл", "ru-am"),
    ("краснодарский край", "ru-kd"),
    ("ставропольский край", "ru-st"),
    ("красноярский край", "ru-ky"),
    ("хабаровский край", "ru-kh"),
    ("приморский край", "ru-pr"),
    ("камчатский край", "ru-ka"),
    ("забайкальский край", "ru-ct"),
    ("алтайский край", "ru-al"),
    ("пермский край", "ru-pe"),
    ("республика башкортостан", "ru-bk"),
    ("башкортостан", "ru-bk"),
    ("республика татарстан", "ru-tt"),
    ("татарстан", "ru-tt"),
    ("республика адыгея", "ru-ad"),
    ("республика алтай", "ru-ga"),
    ("горный алтай", "ru-ga"),
    ("республика хакасия", "ru-kk"),
    ("хакасия", "ru-kk"),
    ("республика тыва", "ru-tu"),
    ("республика тува", "ru-tu"),
    ("республика бурятия", "ru-bu"),
    ("бурятия", "ru-bu"),
    ("республика саха якутия", "ru-sk"),
    ("республика саха", "ru-sk"),
    ("якутия", "ru-sk"),
    ("республика карелия", "ru-ki"),
    ("карелия", "ru-ki"),
    ("республика коми", "ru-ko"),
    ("удмуртская республика", "ru-ud"),
    ("удмуртия", "ru-ud"),
    ("чувашская республика", "ru-cv"),
    ("чувашия", "ru-cv"),
    ("республика мордовия", "ru-mr"),
    ("мордовия", "ru-mr"),
    ("республика марий эл", "ru-me"),
    ("марий эл", "ru-me"),
    ("чеченская республика", "ru-cn"),
    ("чечня", "ru-cn"),
    ("республика дагестан", "ru-da"),
    ("дагестан", "ru-da"),
    ("республика ингушетия", "ru-in"),
    ("ингушетия", "ru-in"),
    ("республика калмыкия", "ru-kl"),
    ("калмыкия", "ru-kl"),
    ("санкт петербург", "ru-sp"),
    ("г санкт петербург", "ru-sp"),
    ("город санкт петербург", "ru-sp"),
    ("г москва", "ru-2510"),
    ("город москва", "ru-2510"),
    ("кузбасс", "ru-ke"),
    ("югра", "ru-km"),
    ("ямал", "ru-yn"),
]

CITY_TO_KEY = {
    "москва": "ru-2510",
    "санкт петербург": "ru-sp",
    "петербург": "ru-sp",
    "пушкин": "ru-sp",
    "колпино": "ru-sp",
    "петергоф": "ru-sp",
    "люберцы": "ru-ms",
    "химки": "ru-ms",
    "мытищи": "ru-ms",
    "балашиха": "ru-ms",
    "подольск": "ru-ms",
    "королев": "ru-ms",
    "красногорск": "ru-ms",
    "одинцово": "ru-ms",
    "серпухов": "ru-ms",
    "солнечногорск": "ru-ms",
    "дмитров": "ru-ms",
    "раменское": "ru-ms",
    "щелково": "ru-ms",
    "коломна": "ru-ms",
    "домодедово": "ru-ms",
    "жуковский": "ru-ms",
    "реутов": "ru-ms",
    "пушкино": "ru-ms",
    "видное": "ru-ms",
    "лобня": "ru-ms",
    "клин": "ru-ms",
    "электросталь": "ru-ms",
    "ногинск": "ru-ms",
    "чехов": "ru-ms",
    "кашира": "ru-ms",
    "воскресенск": "ru-ms",
    "наро фоминск": "ru-ms",
    "казань": "ru-tt",
    "набережные челны": "ru-tt",
    "альметьевск": "ru-tt",
    "уфа": "ru-bk",
    "стерлитамак": "ru-bk",
    "салават": "ru-bk",
    "екатеринбург": "ru-sv",
    "нижний тагил": "ru-sv",
    "каменск уральский": "ru-sv",
    "первоуральск": "ru-sv",
    "новосибирск": "ru-ns",
    "бердск": "ru-ns",
    "краснодар": "ru-kd",
    "сочи": "ru-kd",
    "новороссийск": "ru-kd",
    "анапа": "ru-kd",
    "геленджик": "ru-kd",
    "армавир": "ru-kd",
    "ростов на дону": "ru-ro",
    "таганрог": "ru-ro",
    "шахты": "ru-ro",
    "новочеркасск": "ru-ro",
    "волгодонск": "ru-ro",
    "воронеж": "ru-vr",
    "волгоград": "ru-vg",
    "волжский": "ru-vg",
    "самара": "ru-sa",
    "тольятти": "ru-sa",
    "сызрань": "ru-sa",
    "пермь": "ru-pe",
    "челябинск": "ru-cl",
    "магнитогорск": "ru-cl",
    "омск": "ru-om",
    "ульяновск": "ru-ul",
    "иркутск": "ru-ir",
    "братск": "ru-ir",
    "ангарск": "ru-ir",
    "ярославль": "ru-ys",
    "тюмень": "ru-ty",
    "барнаул": "ru-al",
    "бийск": "ru-al",
    "хабаровск": "ru-kh",
    "владивосток": "ru-pr",
    "находка": "ru-pr",
    "усурийск": "ru-pr",
    "калининград": "ru-kn",
    "мурманск": "ru-mm",
    "тула": "ru-tl",
    "рязань": "ru-rz",
    "пенза": "ru-pz",
    "киров": "ru-kv",
    "томск": "ru-to",
    "кемерово": "ru-ke",
    "новокузнецк": "ru-ke",
    "ставрополь": "ru-st",
    "пятигорск": "ru-st",
    "кисловодск": "ru-st",
    "ессентуки": "ru-st",
    "грозный": "ru-cn",
    "махачкала": "ru-da",
    "нальчик": "ru-kb",
    "владикавказ": "ru-no",
    "черкесск": "ru-kc",
    "майкоп": "ru-ad",
    "элиста": "ru-kl",
    "абакан": "ru-kk",
    "кызыл": "ru-tu",
    "улан удэ": "ru-bu",
    "чита": "ru-ct",
    "благовещенск": "ru-am",
    "якутск": "ru-sk",
    "магадан": "ru-mg",
    "петропавловск камчатский": "ru-ka",
    "южно сахалинск": "ru-sl",
    "нарьян мар": "ru-nn",
    "салехард": "ru-yn",
    "ханты мансийск": "ru-km",
    "сургут": "ru-km",
    "нижневартовск": "ru-km",
    "сыктывкар": "ru-ko",
    "ижевск": "ru-ud",
    "чебоксары": "ru-cv",
    "саранск": "ru-mr",
    "йошкар ола": "ru-me",
    "нижний новгород": "ru-nz",
    "дзержинск": "ru-nz",
    "великий новгород": "ru-ng",
    "петрозаводск": "ru-ki",
    "архангельск": "ru-ar",
    "вологда": "ru-vo",
    "череповец": "ru-vo",
    "псков": "ru-ps",
    "тверь": "ru-tv",
    "смоленск": "ru-sm",
    "брянск": "ru-br",
    "орел": "ru-ol",
    "курск": "ru-ks",
    "белгород": "ru-bl",
    "липецк": "ru-lp",
    "тамбов": "ru-tb",
    "астрахань": "ru-as",
    "оренбург": "ru-ob",
    "курган": "ru-ku",
    "красноярск": "ru-ky",
    "норильск": "ru-ky",
    "ачинск": "ru-ky",
    "горно алтайск": "ru-ga",
    "кострома": "ru-kt",
    "иваново": "ru-iv",
    "владимир": "ru-vl",
    "калуга": "ru-kg",
    "саратов": "ru-sr",
    "энгельс": "ru-sr",
    "тобольск": "ru-ty",
}

_PHRASE_RE = [
    (re.compile(r"(?<![а-я])" + re.escape(phrase) + r"(?![а-я])"), key)
    for phrase, key in sorted(REGION_PHRASES, key=lambda item: len(item[0]), reverse=True)
]
_CITY_RE = [
    (re.compile(r"(?<![а-я])" + re.escape(city) + r"(?![а-я])"), key)
    for city, key in sorted(CITY_TO_KEY.items(), key=lambda item: len(item[0]), reverse=True)
]


def normalize_text(value: Any) -> str:
    if not value or value == "—":
        return ""
    text = str(value).lower().replace("ё", "е")
    text = text.replace("—", " ").replace("–", " ").replace("-", " ")
    text = re.sub(r"[^a-zа-я0-9 ]+", " ", text)
    return " ".join(text.split())


def resolve_region_key(address: Any, city: Any = "") -> Optional[str]:
    text = normalize_text(" ".join(part for part in (address, city) if part))
    if not text:
        return None
    for pattern, key in _PHRASE_RE:
        if pattern.search(text):
            return key
    for pattern, key in _CITY_RE:
        if pattern.search(text):
            return key
    return None


def _parse_ms(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_heatmap_payload(end_dt: datetime) -> Dict[str, Any]:
    """Отправки с января выбранного года по end_dt, разрез по региону адреса доставки."""
    import psycopg2
    from config import DB_CONFIG

    year = end_dt.year
    last_month = end_dt.month
    month_count = last_month
    year_start = datetime(year, 1, 1)
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            data->>'sentToDeliveryDate',
            COALESCE(NULLIF(data#>>'{enrichment,delivery_address}', ''), ''),
            COALESCE(NULLIF(data#>>'{enrichment,delivery_city}', ''), '')
        FROM requests
        WHERE data->>'sentToDeliveryDate' IS NOT NULL
          AND data->>'sentToDeliveryDate' ~ '^[0-9]+$'
          AND to_timestamp((data->>'sentToDeliveryDate')::bigint / 1000.0) >= %s
          AND to_timestamp((data->>'sentToDeliveryDate')::bigint / 1000.0) <= %s
          AND COALESCE(data->>'status', '') <> ALL(%s)
        """,
        (year_start, end_dt, list(EXCLUDED_STATUSES)),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    region_monthly: Dict[str, List[int]] = {
        key: [0] * month_count for key in REGION_NAMES
    }
    unknown_monthly = [0] * month_count
    mapped = 0
    shipped = 0

    for sent_raw, address, city in rows:
        sent_ms = _parse_ms(sent_raw)
        if sent_ms is None:
            continue
        try:
            sent_at = datetime.fromtimestamp(sent_ms / 1000.0)
        except (OSError, OverflowError, ValueError):
            continue
        if sent_at.year != year or sent_at.month > last_month:
            continue
        shipped += 1
        idx = sent_at.month - 1
        key = resolve_region_key(address, city)
        if key and key in region_monthly:
            region_monthly[key][idx] += 1
            mapped += 1
        else:
            unknown_monthly[idx] += 1

    def with_cumulative(monthly: List[int]) -> Tuple[List[int], List[int]]:
        running = 0
        cumulative = []
        for value in monthly:
            running += value
            cumulative.append(running)
        return monthly, cumulative

    regions = []
    for key, name in REGION_NAMES.items():
        monthly, cumulative = with_cumulative(region_monthly[key])
        if cumulative[-1] == 0:
            continue
        regions.append({
            "key": key,
            "name": name,
            "monthly": monthly,
            "cumulative": cumulative,
        })
    regions.sort(key=lambda item: (-item["cumulative"][-1], item["name"]))

    unk_m, unk_c = with_cumulative(unknown_monthly)
    last_is_partial = end_dt.day < monthrange(year, last_month)[1]
    months = []
    for idx in range(month_count):
        month_total = sum(item["monthly"][idx] for item in regions) + unk_m[idx]
        months.append({
            "index": idx,
            "label": MONTH_LABELS[idx],
            "title": f"{MONTH_LABELS[idx]} {year}",
            "total": month_total,
            "partial": idx == month_count - 1 and last_is_partial,
        })

    return {
        "year": year,
        "selected": month_count - 1,
        "months": months,
        "regions": regions,
        "unknown": {"monthly": unk_m, "cumulative": unk_c},
        "shipped": shipped,
        "mapped": mapped,
    }


def build_svg() -> str:
    parts = [
        '<svg class="heat-svg" viewBox="0 0 10850 6610" '
        'role="img" aria-label="Карта отправок по регионам РФ">'
    ]
    for key, path_d in PATHS.items():
        name = html.escape(REGION_NAMES.get(key, key))
        parts.append(
            f'<path data-key="{html.escape(key)}" data-name="{name}" d="{path_d}"></path>'
        )
    parts.append(
        '<circle class="heat-city" data-key="ru-2510" data-name="Москва" '
        'cx="1202" cy="3693" r="70"></circle>'
    )
    parts.append(
        '<circle class="heat-city" data-key="ru-sp" data-name="Санкт-Петербург" '
        'cx="1395" cy="2852" r="55"></circle>'
    )
    parts.append("</svg>")
    return "".join(parts)


_HEAT_CSS = """
<style>
.heat-wrap {
    background: #fff;
    border: 1px solid rgba(226,232,240,0.9);
    border-radius: 16px;
    padding: 18px 18px 12px;
    margin-bottom: 22px;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.05);
}
.heat-head {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
    align-items: flex-start;
    margin-bottom: 12px;
}
.heat-wrap h2 { font-size: 1.1rem; font-weight: 650; }
.heat-sub { color: #6b7c93; font-size: 0.86rem; margin-top: 4px; }
.heat-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.heat-months { display: flex; flex-wrap: wrap; gap: 6px; }
.heat-months button, .heat-mode button {
    border: 1px solid #dbe3ee;
    background: #f8fafc;
    color: #1f2a37;
    border-radius: 8px;
    padding: 6px 10px;
    font-weight: 650;
    cursor: pointer;
    font-size: 0.82rem;
}
.heat-months button.active, .heat-mode button.active {
    background: #1e3a5f;
    border-color: #1e3a5f;
    color: #fff;
}
.heat-mode { display: flex; gap: 6px; }
.heat-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.6fr) minmax(280px, 0.9fr);
    gap: 16px;
    align-items: start;
}
.heat-map-box { position: relative; }
.heat-svg { width: 100%; height: auto; max-height: 560px; display: block; }
.heat-svg path, .heat-svg circle.heat-city {
    fill: #edf1f7;
    stroke: #fff;
    stroke-width: 8;
    cursor: pointer;
}
.heat-svg path:hover, .heat-svg circle.heat-city:hover,
.heat-svg path.is-hover, .heat-svg circle.heat-city.is-hover {
    stroke: #1e3a5f;
    stroke-width: 14;
}
.heat-svg circle.heat-city { stroke-width: 10; }
.heat-tip {
    position: absolute;
    display: none;
    pointer-events: none;
    background: #1f2a37;
    color: #fff;
    border-radius: 10px;
    padding: 8px 10px;
    font-size: 0.82rem;
    line-height: 1.35;
    z-index: 3;
    min-width: 160px;
    box-shadow: 0 8px 20px rgba(15, 23, 42, 0.25);
}
.heat-tip strong { display: block; margin-bottom: 2px; }
.heat-legend {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 8px;
    color: #6b7c93;
    font-size: 0.8rem;
}
.heat-legend-bar {
    height: 10px;
    flex: 1;
    max-width: 220px;
    border-radius: 99px;
    background: linear-gradient(90deg, #fff7ed, #fb923c, #c2410c);
}
.heat-table-wrap { max-height: 560px; overflow: auto; }
.heat-table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
.heat-table th, .heat-table td {
    border-bottom: 1px solid #e6ebf2;
    padding: 7px 6px;
    text-align: left;
}
.heat-table th { color: #6b7c93; font-weight: 650; position: sticky; top: 0; background: #fff; }
.heat-table td.num { text-align: right; font-variant-numeric: tabular-nums; font-weight: 650; }
.heat-table tr.is-hover td { background: #eef4ff; }
.heat-swatch {
    display: inline-block;
    width: 10px; height: 10px;
    border-radius: 3px;
    margin-right: 6px;
    vertical-align: middle;
}
.heat-empty { color: #6b7c93; font-size: 0.86rem; padding: 12px 0; }
.heat-dyn-head {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: baseline;
    flex-wrap: wrap;
    margin: 18px 0 8px;
}
.heat-dyn-head h3 { font-size: 1rem; font-weight: 650; }
.heat-dyn-scroll {
    overflow: auto;
    max-height: 520px;
    border: 1px solid #e6ebf2;
    border-radius: 12px;
}
.heat-dyn { width: max-content; min-width: 100%; border-collapse: separate; border-spacing: 0; font-size: 0.8rem; }
.heat-dyn th, .heat-dyn td {
    border-bottom: 1px solid #e6ebf2;
    border-right: 1px solid #f1f5f9;
    padding: 6px 8px;
    white-space: nowrap;
}
.heat-dyn thead th {
    position: sticky;
    top: 0;
    z-index: 3;
    background: #f8fafc;
    color: #6b7c93;
    font-weight: 650;
    cursor: pointer;
}
.heat-dyn tfoot td {
    position: sticky;
    bottom: 0;
    background: #f8fafc;
    font-weight: 700;
}
.heat-dyn .heat-dyn-sticky {
    position: sticky;
    left: 0;
    z-index: 2;
    background: #fff;
    min-width: 196px;
    max-width: 240px;
    text-align: left;
    white-space: normal;
}
.heat-dyn thead .heat-dyn-sticky { z-index: 4; background: #f8fafc; cursor: default; }
.heat-dyn tfoot .heat-dyn-sticky { z-index: 3; background: #f8fafc; }
.heat-dyn td.num, .heat-dyn th.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    font-weight: 650;
    min-width: 76px;
}
.heat-dyn .heat-share { display: block; }
.heat-dyn .heat-delta {
    display: block;
    font-size: 0.68rem;
    font-weight: 700;
    line-height: 1.2;
}
.heat-dyn .heat-delta.up { color: #15803d; }
.heat-dyn .heat-delta.down { color: #b91c1c; }
.heat-dyn .heat-delta.zero { color: #94a3b8; }
.heat-dyn .heat-delta.up.is-on-dark, .heat-dyn .heat-delta.down.is-on-dark, .heat-dyn .heat-delta.zero.is-on-dark { color: #fff; opacity: 0.92; }
.heat-dyn th.is-selected, .heat-dyn td.is-selected { box-shadow: inset 0 -3px 0 #1e3a5f; }
.heat-dyn tr.is-hover td, .heat-dyn tr.is-hover .heat-dyn-sticky { filter: brightness(0.97); outline: 2px solid #1e3a5f; outline-offset: -2px; }
.heat-dyn .is-partial::after { content: '*'; color: #c2410c; margin-left: 2px; }
.heat-share-legend {
    display: flex;
    align-items: center;
    gap: 8px;
    color: #6b7c93;
    font-size: 0.78rem;
    white-space: nowrap;
}
.heat-share-bar {
    height: 10px;
    width: 140px;
    border-radius: 99px;
    background: linear-gradient(90deg, #dc2626, #f8fafc, #16a34a);
}
@media (max-width: 980px) {
    .heat-grid { grid-template-columns: 1fr; }
}
</style>
"""

_HEAT_JS = """
<script>
(function () {
    const data = window.SLA_HEATMAP;
    if (!data) return;
    const svg = document.querySelector('.heat-svg');
    const tableBody = document.getElementById('heat-table-body');
    const dynHead = document.getElementById('heat-dyn-head');
    const dynBody = document.getElementById('heat-dyn-body');
    const dynFoot = document.getElementById('heat-dyn-foot');
    const dynHint = document.getElementById('heat-dyn-hint');
    const tip = document.getElementById('heat-tip');
    const summary = document.getElementById('heat-summary');
    const monthButtons = document.querySelectorAll('.heat-months button');
    const modeButtons = document.querySelectorAll('.heat-mode button');
    let monthIndex = data.selected;
    let mode = 'cumulative';

    function heatColor(value, max) {
        if (!value || max <= 0) return '#edf1f7';
        const t = Math.sqrt(value / max);
        const stops = [
            [255, 247, 237],
            [253, 186, 116],
            [234, 88, 12],
            [154, 52, 18]
        ];
        const scaled = t * (stops.length - 1);
        const i = Math.min(stops.length - 2, Math.floor(scaled));
        const f = scaled - i;
        const a = stops[i];
        const b = stops[i + 1];
        const r = Math.round(a[0] + (b[0] - a[0]) * f);
        const g = Math.round(a[1] + (b[1] - a[1]) * f);
        const bl = Math.round(a[2] + (b[2] - a[2]) * f);
        return 'rgb(' + r + ',' + g + ',' + bl + ')';
    }

    function valueOf(region) {
        return region[mode][monthIndex] || 0;
    }

    function unknownValue() {
        return (data.unknown[mode][monthIndex] || 0);
    }

    function monthTotal(i) {
        return (data.months[i] && data.months[i].total) || 0;
    }

    function shareOf(monthly, i) {
        const tot = monthTotal(i);
        if (!tot) return 0;
        return 100 * ((monthly[i] || 0) / tot);
    }

    function round1(x) {
        return Math.round(x * 10) / 10;
    }

    function fmtPct(x) {
        return round1(x).toFixed(1).replace('.', ',') + '%';
    }

    function fmtPp(d, onDark) {
        if (d == null || isNaN(d)) return '';
        const rounded = round1(d);
        if (rounded === 0) {
            return '<span class="heat-delta zero' + (onDark ? ' is-on-dark' : '') + '">0 п.п.</span>';
        }
        const cls = rounded > 0 ? 'up' : 'down';
        const abs = Math.abs(rounded).toFixed(1).replace('.', ',');
        const sign = rounded > 0 ? '+' : '−';
        return '<span class="heat-delta ' + cls + (onDark ? ' is-on-dark' : '') + '">' + sign + abs + ' п.п.</span>';
    }

    function mixRgb(a, b, t) {
        return [
            Math.round(a[0] + (b[0] - a[0]) * t),
            Math.round(a[1] + (b[1] - a[1]) * t),
            Math.round(a[2] + (b[2] - a[2]) * t)
        ];
    }

    function deltaColor(d, scale) {
        if (d == null || scale <= 0 || round1(d) === 0) {
            return { bg: '#f8fafc', fg: '#1f2a37', dark: false };
        }
        const t = Math.min(1, Math.abs(d) / scale);
        const light = d > 0 ? [220, 252, 231] : [254, 226, 226];
        const dark = d > 0 ? [21, 128, 61] : [185, 28, 28];
        const rgb = mixRgb(light, dark, Math.sqrt(t));
        const isDark = t > 0.62;
        return { bg: 'rgb(' + rgb.join(',') + ')', fg: isDark ? '#fff' : '#1f2a37', dark: isDark };
    }

    function shareSeries(item) {
        return data.months.map((_, i) => shareOf(item.monthly, i));
    }

    function shareDeltas(shares) {
        return shares.map((s, i) => (i === 0 ? null : s - shares[i - 1]));
    }

    function clearHover() {
        svg.querySelectorAll('.is-hover').forEach((el) => el.classList.remove('is-hover'));
        document.querySelectorAll('#heat-table-body .is-hover, #heat-dyn-body .is-hover').forEach((el) => {
            el.classList.remove('is-hover');
        });
    }

    function setHover(key, on) {
        svg.querySelectorAll('[data-key="' + key + '"]').forEach((el) => {
            el.classList.toggle('is-hover', on);
        });
        document.querySelectorAll('#heat-table-body tr[data-key="' + key + '"], #heat-dyn-body tr[data-key="' + key + '"]').forEach((el) => {
            el.classList.toggle('is-hover', on);
        });
    }

    function selectMonth(index) {
        monthIndex = index;
        monthButtons.forEach((b) => b.classList.toggle('active', Number(b.dataset.index) === index));
        paint();
    }

    function paintRank() {
        const max = data.regions.reduce((m, r) => Math.max(m, valueOf(r)), 0);
        const byKey = {};
        data.regions.forEach((r) => { byKey[r.key] = r; });
        svg.querySelectorAll('[data-key]').forEach((el) => {
            const region = byKey[el.dataset.key];
            const value = region ? valueOf(region) : 0;
            el.style.fill = heatColor(value, max);
        });
        const month = data.months[monthIndex];
        const known = data.regions.reduce((s, r) => s + valueOf(r), 0);
        const unknown = unknownValue();
        const label = mode === 'cumulative' ? 'накопительно до' : 'за';
        summary.textContent = label + ' ' + month.title + ': ' + known + ' с регионом, без адреса ' + unknown + '. Всего отправок с начала года: ' + data.shipped + ', регион определён у ' + data.mapped + '.';
        const rows = data.regions.slice().sort((a, b) => valueOf(b) - valueOf(a) || a.name.localeCompare(b.name, 'ru'));
        tableBody.innerHTML = rows.map((r) => {
            const val = valueOf(r);
            const monthVal = r.monthly[monthIndex] || 0;
            const cumul = r.cumulative[monthIndex] || 0;
            return '<tr data-key="' + r.key + '"><td><span class="heat-swatch" style="background:' + heatColor(val, max) + '"></span>' + r.name + '</td><td class="num">' + monthVal + '</td><td class="num">' + cumul + '</td></tr>';
        }).join('') + '<tr data-key="unknown"><td>Без адреса / не разобран</td><td class="num">' + (data.unknown.monthly[monthIndex] || 0) + '</td><td class="num">' + (data.unknown.cumulative[monthIndex] || 0) + '</td></tr>';
    }

    function paintDyn() {
        const months = data.months;
        const items = data.regions.map((r) => {
            const shares = shareSeries(r);
            return { key: r.key, name: r.name, monthly: r.monthly, shares: shares, deltas: shareDeltas(shares), yearShare: data.shipped ? 100 * (r.cumulative[r.cumulative.length - 1] || 0) / data.shipped : 0 };
        });
        const unkShares = data.months.map((_, i) => shareOf(data.unknown.monthly, i));
        const unkItem = { key: 'unknown', name: 'Без адреса / не разобран', monthly: data.unknown.monthly, shares: unkShares, deltas: shareDeltas(unkShares), yearShare: data.shipped ? 100 * (data.unknown.cumulative[data.unknown.cumulative.length - 1] || 0) / data.shipped : 0 };

        const absDeltas = [];
        items.concat([unkItem]).forEach((item) => {
            item.deltas.forEach((d) => { if (d != null) absDeltas.push(Math.abs(d)); });
        });
        absDeltas.sort((a, b) => a - b);
        const p95 = absDeltas.length ? absDeltas[Math.min(absDeltas.length - 1, Math.floor(absDeltas.length * 0.95))] : 1;
        const scale = Math.max(1.2, Math.min(3, p95 || 1.2));

        function cellFor(item, i) {
            const share = item.shares[i];
            const delta = item.deltas[i];
            const tone = deltaColor(delta, scale);
            const count = item.monthly[i] || 0;
            const tot = monthTotal(i);
            const title = item.name + ', ' + months[i].title + ': ' + count + ' из ' + tot + ' (' + fmtPct(share) + ')' + (delta == null ? '' : ', ' + (round1(delta) > 0 ? '+' : '') + round1(delta).toFixed(1).replace('.', ',') + ' п.п. к предыдущему');
            return '<td class="num' + (i === monthIndex ? ' is-selected' : '') + '" style="background:' + tone.bg + ';color:' + tone.fg + '" title="' + title + '"><span class="heat-share">' + fmtPct(share) + '</span>' + (delta == null ? '' : fmtPp(delta, tone.dark)) + '</td>';
        }

        dynHead.innerHTML = '<tr><th class="heat-dyn-sticky">Регион</th>' + months.map((m, i) => {
            const cls = (i === monthIndex ? 'is-selected' : '') + (m.partial ? ' is-partial' : '');
            return '<th class="num ' + cls + '" data-index="' + i + '" title="' + m.title + (m.partial ? ' (месяц ещё не закрыт)' : '') + '">' + m.label + '</th>';
        }).join('') + '<th class="num">Доля года</th><th class="num">Δ доля</th></tr>';

        const sorted = items.slice().sort((a, b) => {
            const da = monthIndex === 0 ? a.shares[0] : (a.deltas[monthIndex] || 0);
            const db = monthIndex === 0 ? b.shares[0] : (b.deltas[monthIndex] || 0);
            return db - da || b.shares[monthIndex] - a.shares[monthIndex] || a.name.localeCompare(b.name, 'ru');
        });
        dynBody.innerHTML = sorted.map((item) => {
            const selDelta = item.deltas[monthIndex];
            return '<tr data-key="' + item.key + '"><td class="heat-dyn-sticky">' + item.name + '</td>' + months.map((_, i) => cellFor(item, i)).join('') + '<td class="num">' + fmtPct(item.yearShare) + '</td><td class="num">' + (selDelta == null ? '—' : fmtPp(selDelta, false)) + '</td></tr>';
        }).join('') + '<tr data-key="unknown"><td class="heat-dyn-sticky">' + unkItem.name + '</td>' + months.map((_, i) => cellFor(unkItem, i)).join('') + '<td class="num">' + fmtPct(unkItem.yearShare) + '</td><td class="num">' + (unkItem.deltas[monthIndex] == null ? '—' : fmtPp(unkItem.deltas[monthIndex], false)) + '</td></tr>';

        dynFoot.innerHTML = '<tr><td class="heat-dyn-sticky">Отправок за месяц</td>' + months.map((m, i) => {
            return '<td class="num' + (i === monthIndex ? ' is-selected' : '') + '">' + m.total + '</td>';
        }).join('') + '<td class="num">' + data.shipped + '</td><td class="num">100%</td></tr>';

        const partial = months.some((m) => m.partial);
        dynHint.textContent = 'В ячейке доля региона в отправках этого месяца. Цвет и нижняя строка — как доля изменилась к предыдущему месяцу, в процентных пунктах. Зелёный — выросла, красный — упала. Строки отсортированы по изменению доли в выбранном месяце.' + (partial ? ' * месяц ещё не закрыт.' : '');
    }

    function paint() {
        paintRank();
        paintDyn();
    }

    function showTip(event, key) {
        const region = data.regions.find((r) => r.key === key);
        const name = region ? region.name : (event.target.getAttribute('data-name') || 'Регион');
        const monthVal = region ? (region.monthly[monthIndex] || 0) : 0;
        const cumul = region ? (region.cumulative[monthIndex] || 0) : 0;
        tip.innerHTML = '<strong>' + name + '</strong>За месяц: ' + monthVal + '<br>Накопительно: ' + cumul;
        const box = svg.getBoundingClientRect();
        tip.style.display = 'block';
        tip.style.left = Math.min(box.width - 180, Math.max(8, event.clientX - box.left + 12)) + 'px';
        tip.style.top = Math.max(8, event.clientY - box.top - 12) + 'px';
    }

    monthButtons.forEach((btn) => {
        btn.addEventListener('click', () => selectMonth(Number(btn.dataset.index)));
    });
    modeButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
            mode = btn.dataset.mode;
            modeButtons.forEach((b) => b.classList.toggle('active', b === btn));
            paint();
        });
    });
    dynHead.addEventListener('click', (event) => {
        const th = event.target.closest('th[data-index]');
        if (!th) return;
        selectMonth(Number(th.dataset.index));
    });
    svg.addEventListener('mousemove', (event) => {
        const target = event.target.closest('[data-key]');
        if (!target) {
            tip.style.display = 'none';
            clearHover();
            return;
        }
        const key = target.dataset.key;
        clearHover();
        setHover(key, true);
        showTip(event, key);
    });
    svg.addEventListener('mouseleave', () => {
        tip.style.display = 'none';
        clearHover();
    });
    function bindRowHover(node) {
        node.addEventListener('mouseover', (event) => {
            const row = event.target.closest('tr[data-key]');
            if (!row) return;
            clearHover();
            setHover(row.dataset.key, true);
        });
        node.addEventListener('mouseleave', clearHover);
    }
    bindRowHover(tableBody);
    bindRowHover(dynBody);
    paint();
})();
</script>
"""


def render_heatmap_section(payload: Dict[str, Any]) -> str:
    if not payload.get("months"):
        return (
            '<section class="heat-wrap" id="region-heatmap">'
            "<h2>Тепловая карта отправок по регионам</h2>"
            '<div class="heat-empty">Нет отправок за выбранный год.</div>'
            "</section>"
        )
    month_buttons = "".join(
        '<button type="button" data-index="{idx}" class="{cls}">{label}</button>'.format(
            idx=month["index"],
            cls="active" if month["index"] == payload["selected"] else "",
            label=html.escape(month["label"]),
        )
        for month in payload["months"]
    )
    payload_json = json.dumps(payload, ensure_ascii=False)
    return (
        _HEAT_CSS
        + '<section class="heat-wrap" id="region-heatmap">'
        + '<div class="heat-head">'
        + "<div>"
        + "<h2>Тепловая карта отправок по регионам</h2>"
        + '<div class="heat-sub">Субъект РФ из адреса доставки ККТ. Месяц — дата отправки. '
        + "Накопительный итог считается с января "
        + str(payload["year"])
        + " года.</div>"
        + "</div>"
        + '<div class="heat-controls">'
        + '<div class="heat-months">'
        + month_buttons
        + "</div>"
        + '<div class="heat-mode">'
        + '<button type="button" class="active" data-mode="cumulative">Накопительно</button>'
        + '<button type="button" data-mode="monthly">За месяц</button>'
        + "</div>"
        + "</div>"
        + "</div>"
        + '<div class="heat-grid">'
        + '<div class="heat-map-box">'
        + build_svg()
        + '<div class="heat-tip" id="heat-tip"></div>'
        + '<div class="heat-legend"><span>мало</span><div class="heat-legend-bar"></div><span>много</span></div>'
        + '<div class="heat-sub" id="heat-summary"></div>'
        + "</div>"
        + '<div class="heat-table-wrap">'
        + '<table class="heat-table"><thead><tr>'
        + "<th>Регион</th><th>За месяц</th><th>Накопительно</th>"
        + "</tr></thead><tbody id=\"heat-table-body\"></tbody></table>"
        + "</div></div>"
        + '<div class="heat-dyn-head"><div><h3>Доля регионов по месяцам</h3>'
        + '<div class="heat-sub" id="heat-dyn-hint"></div></div>'
        + '<div class="heat-share-legend"><span>доля упала</span><div class="heat-share-bar"></div><span>доля выросла</span></div></div>'
        + '<div class="heat-dyn-scroll"><table class="heat-dyn" id="heat-dyn">'
        + '<thead id="heat-dyn-head"></thead>'
        + '<tbody id="heat-dyn-body"></tbody>'
        + '<tfoot id="heat-dyn-foot"></tfoot>'
        + "</table></div></section>"
        + "<script>window.SLA_HEATMAP = "
        + payload_json
        + ";</script>"
        + _HEAT_JS
    )
