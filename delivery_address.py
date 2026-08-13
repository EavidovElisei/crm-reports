#!/usr/bin/env python3
"""Извлечение адреса/города доставки ККТ из данных CRM."""
import re


ADDRESS_KEYS = (
    'originalSpotAddress',
    'original_spot_address',
    'spotAddress',
    'spot_address',
    'deliveryAddress',
    'delivery_address',
    'kktDeliveryAddress',
    'delivery_city',
    'deliveryCity',
    'recipientCity',
    'clientCity',
    'city',
    'cityName',
)


def parse_city_from_address(address):
    """Достаёт город из строки вида 'г Москва, Зелёный пр-кт, д 85'."""
    if not isinstance(address, str):
        return None
    text = address.strip()
    if not text:
        return None

    first = text.split(',')[0].strip()
    if not first:
        return None

    # Уже короткое значение без улицы
    lowered = first.lower()
    if lowered.startswith(('ул', 'пр', 'пер', 'ш.', 'шоссе', 'д ', 'д.', 'корп')):
        return None

    m = re.match(
        r'^(?:город\s+|гор\.\s*|г\.\s*|г\s+)?(.+)$',
        first,
        flags=re.IGNORECASE,
    )
    city = (m.group(1) if m else first).strip(' .')
    return city or None


def _value_as_city_or_address(value):
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ('originalSpotAddress', 'spotAddress', 'address', 'fullAddress',
                    'name', 'title', 'value', 'city', 'cityName'):
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def extract_delivery_address(data, install_data=None):
    """Полный адрес доставки ККТ из заявки / install / enrichment."""
    sources = []
    if isinstance(install_data, dict):
        sources.append(install_data)
    if isinstance(data, dict):
        enrichment = data.get('enrichment')
        if isinstance(enrichment, dict):
            sources.append(enrichment)
        sources.append(data)

    for obj in sources:
        if not isinstance(obj, dict):
            continue
        for key in ADDRESS_KEYS:
            raw = _value_as_city_or_address(obj.get(key))
            if raw:
                return raw
        for nest_key in ('delivery', 'deliveryAddress', 'address', 'spot',
                         'recipient', 'client', 'organization'):
            nest = obj.get(nest_key)
            if isinstance(nest, dict):
                for key in ADDRESS_KEYS + ('address', 'fullAddress', 'name'):
                    raw = _value_as_city_or_address(nest.get(key))
                    if raw:
                        return raw
    return None


def extract_delivery_city(data, install_data=None):
    """Город получателя: отдельное поле или парсинг originalSpotAddress."""
    sources = []
    if isinstance(install_data, dict):
        sources.append(install_data)
    if isinstance(data, dict):
        enrichment = data.get('enrichment')
        if isinstance(enrichment, dict):
            sources.append(enrichment)
        sources.append(data)

    # Сначала явный город
    for obj in sources:
        if not isinstance(obj, dict):
            continue
        for key in ('delivery_city', 'deliveryCity', 'recipientCity', 'clientCity',
                    'city', 'cityName'):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    address = extract_delivery_address(data, install_data=install_data)
    if not address:
        return None
    return parse_city_from_address(address) or address
