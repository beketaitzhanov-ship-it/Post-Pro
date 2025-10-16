# calculation.py - модуль для расчетов стоимости доставки
import re
import logging

logger = logging.getLogger(__name__)

# Константы тарифов Т2 для крупногабаритных посылок
T2_RATES_DETAILED = {
    "large_parcel": {
        "weight_ranges": [
            {"max": 1, "zones": {"1": 2205, "2": 2310, "3": 2415, "4": 2520, "5": 2625}},
            {"max": 2, "zones": {"1": 2310, "2": 2420, "3": 2530, "4": 2640, "5": 2750}},
            {"max": 3, "zones": {"1": 2415, "2": 2530, "3": 2645, "4": 2760, "5": 2875}},
            {"max": 4, "zones": {"1": 2520, "2": 2640, "3": 2760, "4": 2880, "5": 3000}},
            {"max": 5, "zones": {"1": 2625, "2": 2750, "3": 2875, "4": 3000, "5": 3125}},
            {"max": 6, "zones": {"1": 2730, "2": 2860, "3": 2990, "4": 3120, "5": 3250}},
            {"max": 7, "zones": {"1": 2835, "2": 2970, "3": 3105, "4": 3240, "5": 3375}},
            {"max": 8, "zones": {"1": 2940, "2": 3080, "3": 3220, "4": 3360, "5": 3500}},
            {"max": 9, "zones": {"1": 3045, "2": 3190, "3": 3335, "4": 3480, "5": 3625}},
            {"max": 10, "zones": {"1": 3150, "2": 3300, "3": 3450, "4": 3600, "5": 3750}},
            {"max": 11, "zones": {"1": 3255, "2": 3410, "3": 3565, "4": 3720, "5": 3875}},
            {"max": 12, "zones": {"1": 3360, "2": 3520, "3": 3680, "4": 3840, "5": 4000}},
            {"max": 13, "zones": {"1": 3465, "2": 3630, "3": 3795, "4": 3960, "5": 4125}},
            {"max": 14, "zones": {"1": 3570, "2": 3740, "3": 3910, "4": 4080, "5": 4250}},
            {"max": 15, "zones": {"1": 3675, "2": 3850, "3": 4025, "4": 4200, "5": 4375}},
            {"max": 16, "zones": {"1": 3780, "2": 3960, "3": 4140, "4": 4320, "5": 4500}},
            {"max": 17, "zones": {"1": 3885, "2": 4070, "3": 4255, "4": 4440, "5": 4625}},
            {"max": 18, "zones": {"1": 3990, "2": 4180, "3": 4370, "4": 4560, "5": 4750}},
            {"max": 19, "zones": {"1": 4095, "2": 4290, "3": 4485, "4": 4680, "5": 4875}},
            {"max": 20, "zones": {"1": 4200, "2": 4400, "3": 4600, "4": 4800, "5": 5000}}
        ],
        "extra_kg_rate": {"1": 220, "2": 230, "3": 240, "4": 250, "5": 260}
    },
    "coefficients": {
        "careful_handling": 1.5,
        "rural_delivery": 2.0
    },
    "additional_services": {
        "declared_value": 0.01,
        "storage_per_day": 500,
        "loading_unloading_per_kg": 50
    }
}

def calculate_t2_cost(weight, zone):
    """
    Расчет стоимости Т2 для крупногабаритных посылок
    по официальным тарифам Казпочты
    """
    try:
        weight_ranges = T2_RATES_DETAILED["large_parcel"]["weight_ranges"]
        extra_kg_rate = T2_RATES_DETAILED["large_parcel"]["extra_kg_rate"]
        
        # Находим базовую стоимость для веса до 20 кг
        base_cost = None
        for weight_range in weight_ranges:
            if weight <= weight_range["max"]:
                base_cost = weight_range["zones"].get(str(zone))
                break
        
        # Если вес больше 20 кг, применяем дополнительную ставку
        if weight > 20:
            if base_cost is None:
                # Берем стоимость за 20 кг как базовую
                base_cost = weight_ranges[-1]["zones"].get(str(zone), 0)
            
            extra_weight = weight - 20
            extra_cost = extra_weight * extra_kg_rate.get(str(zone), 0)
            total_cost = base_cost + extra_cost
        else:
            total_cost = base_cost if base_cost is not None else 0
        
        return total_cost
        
    except Exception as e:
        logger.error(f"Ошибка расчета Т2: {e}")
        return None

def calculate_large_parcel_cost(weight, zone, is_door_to_door=True):
    """
    Расчет стоимости для крупногабаритной посылки
    """
    try:
        # Для услуги "До двери" используем специальные тарифы
        if is_door_to_door:
            return calculate_t2_cost(weight, zone)
        else:
            # Для других услуг можно добавить соответствующую логику
            return calculate_t2_cost(weight, zone) * 0.8  # Пример: скидка 20% для самовывоза
            
    except Exception as e:
        logger.error(f"Ошибка расчета крупногабаритной посылки: {e}")
        return None

def extract_dimensions(text):
    """Извлекает габариты (длина, ширина, высота) из текста в любом формате."""
    patterns = [
        r'(?:габарит\w*|размер\w*|дшв|длш|разм)?\s*'
        r'(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m|сантиметр\w*|метр\w*)?\s*'
        r'[xх*×на\s\-]+\s*'
        r'(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m|сантиметр\w*|метр\w*)?\s*'
        r'[xх*×на\s\-]+\s*'
        r'(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m|сантиметр\w*|метр\w*)?'
    ]
    
    text_lower = text.lower()
    
    for pattern in patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            try:
                l = float(match.group(1).replace(',', '.'))
                w = float(match.group(2).replace(',', '.'))
                h = float(match.group(3).replace(',', '.'))
                
                match_text = match.group(0).lower()
                has_explicit_cm = any(word in match_text for word in ['см', 'cm', 'сантим'])
                has_explicit_m = any(word in match_text for word in ['м', 'm', 'метр'])
                
                is_cm = (
                    has_explicit_cm or
                    (l > 5 or w > 5 or h > 5) and not has_explicit_m
                )
                
                if is_cm:
                    l = l / 100
                    w = w / 100
                    h = h / 100
                
                logger.info(f"Извлечены габариты: {l:.3f}x{w:.3f}x{h:.3f} м")
                return l, w, h
                
            except (ValueError, IndexError) as e:
                logger.warning(f"Ошибка преобразования габаритов: {e}")
                continue
    
    pattern_dl_sh_v = r'(?:длин[аы]?|length)\s*(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m)?\s*(?:ширин[аы]?|width)\s*(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m)?\s*(?:высот[аы]?|height)\s*(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m)?'
    
    match = re.search(pattern_dl_sh_v, text_lower)
    if match:
        try:
            l = float(match.group(1).replace(',', '.'))
            w = float(match.group(2).replace(',', '.'))
            h = float(match.group(3).replace(',', '.'))
            
            match_text = match.group(0).lower()
            has_explicit_cm = any(word in match_text for word in ['см', 'cm', 'сантим'])
            has_explicit_m = any(word in match_text for word in ['м', 'm', 'метр'])
            
            is_cm = (
                has_explicit_cm or
                (l > 5 or w > 5 or h > 5) and not has_explicit_m
            )
            
            if is_cm:
                l = l / 100
                w = w / 100
                h = h / 100
            
            logger.info(f"Извлечены габариты (формат дшв): {l:.3f}x{w:.3f}x{h:.3f} м")
            return l, w, h
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Ошибка преобразования габаритов дшв: {e}")
    
    pattern_three_numbers = r'(?<!\d)(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)(?!\d)'
    
    match = re.search(pattern_three_numbers, text_lower)
    if match:
        try:
            l = float(match.group(1).replace(',', '.'))
            w = float(match.group(2).replace(',', '.'))
            h = float(match.group(3).replace(',', '.'))
            
            if l > 5 and w > 5 and h > 5:
                l = l / 100
                w = w / 100
                h = h / 100
            
            logger.info(f"Извлечены габариты (три числа): {l:.3f}x{w:.3f}x{h:.3f} м")
            return l, w, h
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Ошибка преобразования трех чисел: {e}")
    
    return None, None, None

def extract_volume(text):
    """Извлекает готовый объем из текста в любом формате."""
    patterns = [
        r'(\d+(?:[.,]\d+)?)\s*(?:куб\.?\s*м|м³|м3|куб\.?|кубическ\w+\s*метр\w*|кубометр\w*)',
        r'(?:объем|volume)\w*\s*(\d+(?:[.,]\d+)?)\s*(?:куб\.?\s*м|м³|м3|куб\.?)?',
        r'(\d+(?:[.,]\d+)?)\s*(?:cubic|cub)',
        r'(\d+(?:[.,]\d+)?)\s*(?=куб|м³|м3|объем)'
    ]
    
    text_lower = text.lower()
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                volume = float(match.group(1).replace(',', '.'))
                logger.info(f"Извлечен объем: {volume} м³")
                return volume
            except (ValueError, IndexError) as e:
                logger.warning(f"Ошибка преобразования объема: {e}")
                continue
    
    return None

def find_product_category(text, product_categories):
    """
    Находит категорию товара по тексту
    """
    if not text:
        return None
        
    text_lower = text.lower().strip()
    
    for category, data in product_categories.items():
        for keyword in data["keywords"]:
            if keyword in text_lower:
                return category
    
    return None

def get_t1_density_rule(product_type, weight, volume, T1_RATES_DENSITY):
    """Находит и возвращает правило тарифа Т1 на основе плотности груза."""
    if not volume or volume <= 0:
        return None, None

    density = weight / volume
    
    # Используем новую функцию определения категории
    category = find_product_category(product_type, T1_RATES_DENSITY)
    
    # 🔥 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Если категория не найдена, используем "общие"
    if not category:
        category = "общие"
        logger.warning(f"Категория для '{product_type}' не найдена, используется 'общие'")
    
    # 🔥 ИСПРАВЛЕНИЕ: Убедимся что category - это строка и приведем к нижнему регистру
    category_key = str(category).lower() if category else "общие"
    
    rules = T1_RATES_DENSITY.get(category_key)
    
    # 🔥 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Если правил для категории нет, используем "общие"
    if not rules:
        rules = T1_RATES_DENSITY.get("общие")
        logger.warning(f"Правила для категории '{category_key}' не найдены, используются 'общие'")
    
    # 🔥 КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Проверяем что rules - это список
    if not rules or not isinstance(rules, list):
        logger.error(f"Правила для категории '{category_key}' не найдены или неверного типа: {type(rules)}")
        return None, density

    try:
        # 🔥 ИСПРАВЛЕНИЕ: Проверяем что правила можно отсортировать
        if not rules:
            return None, density
            
        for rule in sorted(rules, key=lambda x: x.get('min_density', 0), reverse=True):
            if density >= rule.get('min_density', 0):
                return rule, density
    except (KeyError, TypeError, AttributeError) as e:
        logger.error(f"Ошибка сортировки правил: {e}, rules: {rules}")
        return None, density
            
    return None, density

calculate_quick_cost    
    for pattern in patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            try:
                quantity = int(match.group(1))
                product_type = match.group(2).strip()
                length = float(match.group(3).replace(',', '.'))
                width = float(match.group(4).replace(',', '.'))
                height = float(match.group(5).replace(',', '.'))
                weight_per_unit = float(match.group(6).replace(',', '.'))
                
                # Автоматически определяем единицы измерения (см или м)
                if length > 10 or width > 10 or height > 10:
                    # Если числа большие, предполагаем что это см
                    length = length / 100
                    width = width / 100
                    height = height / 100
                
                volume_per_unit = length * width * height
                total_weight = quantity * weight_per_unit
                total_volume = quantity * volume_per_unit
                
                item = {
                    'quantity': quantity,
                    'product_type': product_type,
                    'dimensions': {
                        'length': length,
                        'width': width, 
                        'height': height,
                        'string': f"{length*100:.0f}×{width*100:.0f}×{height*100:.0f} см"
                    },
                    'weight_per_unit': weight_per_unit,
                    'total_weight': total_weight,
                    'volume_per_unit': volume_per_unit,
                    'total_volume': total_volume,
                    'density': weight_per_unit / volume_per_unit if volume_per_unit > 0 else 0
                }
                
                items.append(item)
                logger.info(f"Найден товар: {quantity} шт {product_type}, {weight_per_unit}кг, {volume_per_unit:.3f}м³")
                
            except (ValueError, IndexError) as e:
                logger.warning(f"Ошибка разбора товара: {e}")
                continue
    
    return items

def calculate_multiple_items(items, city, EXCHANGE_RATE, DESTINATION_ZONES, T1_RATES_DENSITY, T2_RATES):
    """
    Расчет стоимости для нескольких товаров
    """
    try:
        results = []
        total_weight = 0
        total_volume = 0
        total_t1_cost = 0
        total_t2_cost = 0
        
        for i, item in enumerate(items, 1):
            # Расчет для каждого товара
            quick_cost = calculate_quick_cost(
                item['total_weight'],
                item['product_type'],
                city,
                item['total_volume'],
                EXCHANGE_RATE,
                DESTINATION_ZONES,
                T1_RATES_DENSITY,
                T2_RATES
            )
            
            if quick_cost:
                item_result = {
                    'index': i,
                    'product_type': item['product_type'],
                    'quantity': item['quantity'],
                    'total_weight': item['total_weight'],
                    'total_volume': item['total_volume'],
                    'density': item['density'],
                    't1_cost': quick_cost['t1_cost'],
                    't2_cost': quick_cost['t2_cost'],
                    'rule': quick_cost['rule']
                }
                
                results.append(item_result)
                
                # Суммируем общие показатели
                total_weight += item['total_weight']
                total_volume += item['total_volume']
                total_t1_cost += quick_cost['t1_cost']
                total_t2_cost += quick_cost['t2_cost']
        
        if not results:
            return None
        
        # Общий расчет
        total_without_commission = total_t1_cost + total_t2_cost
        commission = total_without_commission * 0.20
        total_cost = total_without_commission + commission
        
        return {
            'items': results,
            'totals': {
                'total_weight': total_weight,
                'total_volume': total_volume,
                'total_t1_cost': total_t1_cost,
                'total_t2_cost': total_t2_cost,
                'commission': commission,
                'total_cost': total_cost
            },
            'city': city
        }
        
    except Exception as e:
        logger.error(f"Ошибка расчета нескольких товаров: {e}")
        return None

def format_multiple_items_response(calculation_result, city):
    """
    Форматирует детальный ответ для нескольких товаров
    """
    if not calculation_result:
        return "❌ Не удалось рассчитать стоимость для вашего заказа."
    
    items = calculation_result['items']
    totals = calculation_result['totals']
    
    response = "📦 **Детальный расчет для вашего заказа:**\n\n"
    
    # Детали по каждому товару
    for item in items:
        response += (
            f"**{item['index']}. {item['product_type'].title()} ({item['quantity']} шт):**\n"
            f"• Вес: {item['total_weight']} кг ({item['quantity']} × {item['total_weight']/item['quantity']:.0f} кг)\n"
            f"• Объем: {item['total_volume']:.3f} м³\n"
            f"• Плотность: {item['density']:.1f} кг/м³\n"
            f"• Стоимость Т1: {item['t1_cost']:,.0f} тенге\n"
            f"• Стоимость Т2: {item['t2_cost']:,.0f} тенге\n\n"
        )
    
    # Итоговая сумма
    response += (
        f"💰 **ИТОГО по заказу:**\n"
        f"• Общий вес: {totals['total_weight']} кг\n"
        f"• Общий объем: {totals['total_volume']:.3f} м³\n"
        f"• Стоимость Т1: {totals['total_t1_cost']:,.0f} тенге\n"
        f"• Стоимость Т2: {totals['total_t2_cost']:,.0f} тенге\n"
        f"• Комиссия 20%: {totals['commission']:,.0f} тенге\n\n"
        f"💵 **ОБЩАЯ СТОИМОСТЬ ДОСТАВКИ ДО ДВЕРИ:**\n"
        f"**{totals['total_cost']:,.0f} тенге**\n\n"
        f"🏙️ **Город доставки:** {city.capitalize()}\n\n"
        f"💡 **Страхование:** дополнительно 1% от стоимости груза\n"
        f"💳 **Оплата:** пост-оплата при получении\n\n"
        f"✅ **Оставить заявку?** Напишите ваше имя и телефон!\n"
        f"🔄 **Новый расчет?** Напишите **Старт**"
    )
    
    return response

def extract_city_from_multiple_items(text, DESTINATION_ZONES):
    """
    Извлекает город из текста с несколькими товарами
    """
    text_lower = text.lower()
    
    # Ищем города в тексте
    for city_name in DESTINATION_ZONES:
        if city_name in text_lower:
            return city_name
    
    return None

def has_multiple_items(text):
    """
    Проверяет, содержит ли текст описание нескольких товаров
    """
    # Паттерны, указывающие на несколько товаров
    multiple_indicators = [
        r'\d+\s*(?:коробк\w+|шт|штук\w+).*?\d+[xх×]\d+[xх×]\d+.*?\d+\s*кг',
        r'\d+\s*(?:паллет\w+|мешк\w+).*?\d+[xх×]\d+[xх×]\d+.*?\d+\s*кг',
        r'.*?коробк\w+.*?коробк\w+',  # упоминание коробок во множественном числе
        r'.*?паллет\w+.*?паллет\w+',  # упоминание паллетов во множественном числе
    ]
    
    text_lower = text.lower()
    
    for pattern in multiple_indicators:
        if re.search(pattern, text_lower):
            return True
    
    # Проверяем наличие нескольких числовых паттернов
    item_count = len(re.findall(r'\d+\s*(?:кг|коробк|паллет|шт|штук)', text_lower))
    return item_count >= 2

def extract_delivery_info(text, DESTINATION_ZONES=None, PRODUCT_CATEGORIES=None):
    """Извлечение данных о доставке (обновленная версия)"""
    if DESTINATION_ZONES is None:
        DESTINATION_ZONES = {}
    if PRODUCT_CATEGORIES is None:
        PRODUCT_CATEGORIES = {}
    
    # Сначала проверяем на множественные товары
    if has_multiple_items(text):
        items = parse_multiple_items(text)
        city = extract_city_from_multiple_items(text, DESTINATION_ZONES)
        
        if items and city:
            return {
                'multiple_items': True,
                'items': items,
                'city': city,
                'weight': sum(item['total_weight'] for item in items),
                'product_type': "разные товары",
                'volume': sum(item['total_volume'] for item in items)
            }
    
    # Старая логика для одиночных товаров
    weight = None
    product_type = None
    city = None
    
    try:
        weight_patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:кг|kg|килограмм|кило)',
            r'вес\s*[:\-]?\s*(\d+(?:\.\d+)?)',
        ]
        
        for pattern in weight_patterns:
            match = re.search(pattern, text.lower())
            if match:
                weight = float(match.group(1))
                break
        
        text_lower = text.lower()
        for city_name in DESTINATION_ZONES:
            if city_name in text_lower:
                city = city_name
                break
        
        product_type = find_product_category(text, PRODUCT_CATEGORIES)
        
        return {
            'multiple_items': False
            'weight': weight,
            'product_type': product_type,
            'city': city
        }
        
    except Exception as e:
        logger.error(f"Ошибка извлечения данных: {e}")
        return {
            'multiple_items': False,
            'weight': None,
            'product_type': None,
            'city': None
        }
        }

# Добавить в начало calculation.py после других функций
__all__ = [
    'calculate_t2_cost', 'calculate_large_parcel_cost', 'extract_dimensions',
    'extract_volume', 'find_product_category', 'find_destination_zone',
    'calculate_quick_cost', 'calculate_detailed_cost', 'extract_delivery_info',
    'parse_multiple_items', 'calculate_multiple_items', 'format_multiple_items_response',
    'has_multiple_items'
]
