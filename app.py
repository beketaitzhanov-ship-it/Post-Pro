from flask import Flask, render_template, request, jsonify, session
import os
import re
import json
from datetime import datetime
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from dotenv import load_dotenv
import logging

def load_track_numbers():
    """
    Загружает трек-номера из файла
    """
    try:
        with open('data/test_track_numbers.txt', 'r', encoding='utf-8') as f:
            track_numbers = [line.strip() for line in f.readlines() if line.strip()]
        print(f"✅ Загружено {len(track_numbers)} трек-номеров")
        return track_numbers
    except FileNotFoundError:
        print("❌ Файл с трек-номерами не найден")
        return []

# Загружаем трек-номера при старте приложения
track_numbers = load_track_numbers()

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'postpro-secret-key-2024')
app.config['PERMANENT_SESSION_LIFETIME'] = 1800

# ↓↓↓ ВСТАВИТЬ ЗДЕСЬ - класс SmartIntentManager ↓↓↓
class SmartIntentManager:
    def __init__(self):
        self.load_intent_config()
    
    def load_intent_config(self):
        with open('intent_config.json', 'r', encoding='utf-8') as f:
            self.config = json.load(f)
    
    def should_switch_to_delivery(self, message):
        """
        Строгая проверка - ТОЛЬКО явные признаки доставки
        Возвращает True только если есть четкие параметры доставки
        """
        message_lower = message.lower()
        
        # 1. Проверяем числа с единицами измерения
        has_parameters = self._has_delivery_parameters(message_lower)
        
        # 2. Проверяем явные ключевые слова доставки
        has_delivery_keywords = any(
            keyword in message_lower 
            for keyword in self.config["delivery_triggers"]["explicit_keywords"]
        )
        
        # 3. Проверяем города доставки
        has_city = any(
            city in message_lower 
            for city in self.config["delivery_triggers"]["city_keywords"]
        )
        
        # 4. Проверяем типы товаров
        has_product = any(
            product in message_lower 
            for product in self.config["delivery_triggers"]["product_keywords"]
        )
        
        # АКТИВИРУЕМ РЕЖИМ ДОСТАВКИ ТОЛЬКО ЕСЛИ:
        # - Есть параметры (числа + единицы) ИЛИ
        # - Явный запрос доставки И параметры/город/товар
        if has_parameters or (has_delivery_keywords and (has_parameters or has_city or has_product)):
            return True
        
        # ВСЕ остальные случаи - свободный диалог
        return False
    
    def _has_delivery_parameters(self, message_lower):
        """Проверяет наличие параметров доставки"""
        # Вес: число + кг
        weight_pattern = r'\d+\s*(кг|kg|килограмм)'
        # Габариты: число×число×число или числа с единицами
        size_pattern = r'\d+[×x*]\d+[×x*]\d+|\d+\s*(метр|м|m|см|cm|мм)'
        
        return bool(re.search(weight_pattern, message_lower) or 
                   re.search(size_pattern, message_lower))
    
    def get_intent_type(self, message):
        """Определяет тип интента для шаблонных ответов"""
        message_lower = message.lower()
        
        for category, keywords in self.config["free_chat_priority"].items():
            if any(keyword in message_lower for keyword in keywords):
                return category
        
        return "general_chat"
# ↑↑↑ КОНЕЦ ВСТАВКИ КЛАССА ↑↑↑

# --- ЗАГРУЗКА КОНФИГУРАЦИИ ---

# --- ЗАГРУЗКА КОНФИГУРАЦИИ ---
def load_config():
    """Загружает конфигурацию из файла config.json."""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config_data = json.load(f)
            logger.info(">>> Файл config.json успешно загружен.")
            return config_data
    except FileNotFoundError:
        logger.error("!!! КРИТИЧЕСКАЯ ОШИБКА: Файл config.json не найден!")
        return None
    except json.JSONDecodeError:
        logger.error("!!! КРИТИЧЕСКАЯ ОШИБКА: Неверный формат данных в config.json!")
        return None
    except Exception as e:
        logger.error(f"!!! КРИТИЧЕСКАЯ ОШИБКА при загрузке config.json: {e}")
        return None

config = load_config()

if config:
    EXCHANGE_RATE = config.get("EXCHANGE_RATE", {}).get("rate", 550)
    DESTINATION_ZONES = config.get("DESTINATION_ZONES", {})
    T1_RATES_DENSITY = config.get("T1_RATES_DENSITY", {})
    T2_RATES = config.get("T2_RATES", {})
    T2_RATES_DETAILED = config.get("T2_RATES_DETAILED", {})
    CUSTOMS_RATES = config.get("CUSTOMS_RATES", {})
    CUSTOMS_FEES = config.get("CUSTOMS_FEES", {})
    GREETINGS = config.get("GREETINGS", [])
    PRODUCT_CATEGORIES = config.get("PRODUCT_CATEGORIES", {})
else:
    logger.error("!!! Приложение запускается с значениями по умолчанию из-за ошибки загрузки config.json")
    EXCHANGE_RATE, DESTINATION_ZONES, T1_RATES_DENSITY, T2_RATES, CUSTOMS_RATES, CUSTOMS_FEES, GREETINGS, PRODUCT_CATEGORIES = 550, {}, {}, {}, {}, {}, [], {}

# Максимальные размеры для доставки до двери (в метрах)
MAX_DIMENSIONS = {
    'length': 2.3,   # 230 см
    'width': 1.8,    # 180 см 
    'height': 1.1    # 110 см
}

# --- НОВЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С КОНФИГОМ ---

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

def find_destination_zone(city_name, destination_zones):
    """
    Находит зону назначения по названию города
    """
    city_lower = city_name.lower().strip()
    
    # Прямой поиск
    if city_lower in destination_zones:
        return destination_zones[city_lower]
    
    # Поиск с учетом возможных опечаток
    for city, zone in destination_zones.items():
        if city in city_lower or city_lower in city:
            return zone
    
    return None

def calculate_shipping_cost(category, weight, volume, destination_city):
    """
    Полный расчет стоимости доставки
    """
    # Определяем зону
    zone = find_destination_zone(destination_city, DESTINATION_ZONES)
    if not zone:
        return "Город не найден"
    
    # Определяем тарифы для категории
    category_rates = T1_RATES_DENSITY.get(category)
    if not category_rates:
        return "Категория не найдена"
    
    # Расчет плотности и выбор тарифа
    density = weight / volume if volume > 0 else 0
    
    # Логика выбора тарифа по плотности
    selected_rate = None
    for rate in category_rates:
        if density >= rate["min_density"]:
            selected_rate = rate
            break
    
    if not selected_rate:
        return "Не удалось подобрать тариф"
    
    # Расчет стоимости Т1
    if selected_rate["unit"] == "kg":
        t1_cost_usd = weight * selected_rate["price"]
    else:  # m3
        t1_cost_usd = volume * selected_rate["price"]
    
    t1_cost_kzt = t1_cost_usd * EXCHANGE_RATE
    
    # Расчет стоимости Т2
    city_lower = destination_city.lower()
    if city_lower == "алматы" or city_lower == "алмата":
        t2_rate = T2_RATES.get("алматы", 120)
        zone_name = "алматы"
    else:
        t2_rate = T2_RATES.get(str(zone), 250)
        zone_name = f"зона {zone}"
    
    t2_cost_kzt = weight * t2_rate
    
    # Итоговая стоимость с комиссией 20%
    total_cost = (t1_cost_kzt + t2_cost_kzt) * 1.20
    
    return {
        't1_cost': t1_cost_kzt,
        't2_cost': t2_cost_kzt,
        'total': total_cost,
        'zone': zone_name,
        't2_rate': t2_rate,
        'volume': volume,
        'density': density,
        'rule': selected_rate,
        't1_cost_usd': t1_cost_usd,
        'category': category
    }

# --- ЗАГРУЗКА ПРОМПТА ЛИЧНОСТИ ---
def load_personality_prompt():
    """Загружает промпт личности из файла personality_prompt.txt."""
    try:
        with open('personality_prompt.txt', 'r', encoding='utf-8') as f:
            prompt_text = f.read()
            logger.info(">>> Файл personality_prompt.txt успешно загружен.")
            return prompt_text
    except FileNotFoundError:
        logger.error("!!! Файл personality_prompt.txt не найден! Бот будет отвечать стандартно.")
        return "Ты — дружелюбный и профессиональный ассистент логистической компании Post Pro. Общайся вежливо, с лёгким позитивом и эмодзи, как живой человек."

PERSONALITY_PROMPT = load_personality_prompt()

# --- СИСТЕМНЫЙ ПРОМПТ ---
SYSTEM_INSTRUCTION = """
Ты — умный ассистент компании PostPro. Твоя главная цель — помочь клиенту рассчитать стоимость доставки и оформировать заявку.

***ВАЖНЫЕ ПРАВИЛА:***

1. **СКЛАДЫ В КИТАЕ:** У нас только 2 склада - ИУ и Гуанчжоу. Если клиент спрашивает "откуда заберете?" - отвечай: "Уточните у вашего поставщика, какой склад ему ближе - ИУ или Гуанчжоу"

2. **ТАРИФЫ:**
   - Т1: Доставка из Китая до Алматы (только до склада, самовывоз)
   - Т2: Доставка до двери в ЛЮБОМ городе Казахстана, включая доставку по Алматы

3. **ОПЛАТА:**
   - У нас пост-оплата: вы платите при получении груза
   - Форматы оплата: безналичный расчет, наличные, Kaspi, Halyk, Freedom Bank
   - Если спрашивают про оплату - всегда объясняй эту систему

4. **ЛОГИКА ДИАЛОГА:**
   - Сначала собери все данные для расчета
   - Покажи итоговую стоимость
   - Предложи детальный расчет
   - В конце предлагай заявку

5. **СБОР ЗАЯВКИ:**
   - Когда клиент пишет имя и телефон - сохраняй заявку
   - Формат: [ЗАЯВКА] Имя: [имя], Телефон: [телефон]

6. **ОБЩИЕ ВОПРОСЫ:**
   - Если вопрос не о доставке (погода, имя бота и т.д.) - отвечай нормально
   - Не зацикливайся только на доставке

7. **НЕ УПОМИНАЙ:** другие города Китая кроме ИУ и Гуанчжоу

Всегда будь дружелюбным и профессиональным! 😊
"""

# --- ИНИЦИАЛИЗАЦИЯ МОДЕЛИ ---
model = None
try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name='models/gemini-2.0-flash'
        )
        logger.info(">>> Модель Gemini успешно инициализирована.")
    else:
        logger.error("!!! API ключ не найден")
except Exception as e:
    logger.error(f"!!! Ошибка инициализации Gemini: {e}")

# --- ФУНКЦИИ РАСЧЕТА ---
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

def check_dimensions_exceeded(length, width, height):
    """Проверяет, превышает ли груз максимальные размеры для доставки до двери"""
    if not length or not width or not height:
        return False
    
    return (length > MAX_DIMENSIONS['length'] or 
            width > MAX_DIMENSIONS['width'] or 
            height > MAX_DIMENSIONS['height'])

def get_t1_density_rule(product_type, weight, volume):
    """Находит и возвращает правило тарифа Т1 на основе плотности груза."""
    if not volume or volume <= 0:
        return None, None

    density = weight / volume
    
    # Используем новую функцию определения категории
    category = find_product_category(product_type, PRODUCT_CATEGORIES)
    if not category:
        category = "мебель"  # категория по умолчанию
    
    rules = T1_RATES_DENSITY.get(category.lower())
    if not rules:
        rules = T1_RATES_DENSITY.get("мебель")
    
    # Проверка на наличие правил
    if not rules:
        return None, density

    for rule in sorted(rules, key=lambda x: x['min_density'], reverse=True):
        if density >= rule['min_density']:
            return rule, density
            
    return None, density

def calculate_t2_cost(weight: float, zone: str):
    """Расчет стоимости Т2 по прогрессивным тарифам из Excel"""
    try:
        # Берем тарифы из конфига
        t2_rates = T2_RATES_DETAILED["large_parcel"]
        
        # Находим подходящий весовой диапазон
        base_cost = 0
        remaining_weight = weight
        
        for weight_range in t2_rates["weight_ranges"]:
            if weight <= weight_range["max"]:
                base_cost = weight_range["zones"][zone]
                remaining_weight = 0
                break
            elif weight > 20:  # Если вес больше 20 кг, используем последний диапазон + доп кг
                if weight_range["max"] == 20:
                    base_cost = weight_range["zones"][zone]
                    remaining_weight = weight - 20
                break
        
        # Добавляем стоимость дополнительных кг если вес больше 20 кг
        if remaining_weight > 0:
            extra_rate = t2_rates["extra_kg_rate"][zone]
            base_cost += remaining_weight * extra_rate
        
        return base_cost
    except Exception as e:
        logger.error(f"Ошибка расчета Т2: {e}")
        # Fallback: старый расчет
        if zone == "алматы":
            return weight * 250
        else:
            return weight * T2_RATES.get(zone, 300)

def calculate_quick_cost(weight: float, product_type: str, city: str, volume: float = None, length: float = None, width: float = None, height: float = None):
    """Быстрый расчет стоимости - единый центр всех расчетов"""
    try:
        rule, density = get_t1_density_rule(product_type, weight, volume)
        if not rule:
            return None
        
        price = rule['price']
        unit = rule['unit']
        
        if unit == "kg":
            cost_usd = price * weight
        elif unit == "m3":
            cost_usd = price * volume
        else:
            cost_usd = price * weight 
        
        t1_cost_kzt = cost_usd * EXCHANGE_RATE
        
        # Используем новую функцию определения зоны
        zone = find_destination_zone(city, DESTINATION_ZONES)
        if not zone:
            return None
            
        city_lower = city.lower()
        if city_lower == "алматы" or city_lower == "алмата":
            t2_cost_kzt = calculate_t2_cost(weight, "алматы")
            zone_name = "алматы"
        else:
            t2_cost_kzt = calculate_t2_cost(weight, str(zone))
            zone_name = f"зона {zone}"
        
        total_cost = (t1_cost_kzt + t2_cost_kzt) * 1.20
        
        return {
            't1_cost': t1_cost_kzt,
            't2_cost': t2_cost_kzt,
            'total': total_cost,
            'zone': zone_name,
            't2_rate': f"прогрессивный тариф (зона {zone})",
            'volume': volume,
            'density': density,
            'rule': rule,
            't1_cost_usd': cost_usd,
            'length': length,
            'width': width, 
            'height': height
        }
    except Exception as e:
        logger.error(f"Ошибка расчета: {e}")
        return None

def calculate_detailed_cost(quick_cost, weight: float, product_type: str, city: str):
    """Детальный расчет с разбивкой по плотности"""
    print(f"=== ОТЛАДКА calculate_detailed_cost ===")
    print(f"quick_cost keys: {quick_cost.keys() if quick_cost else 'None'}")
    if quick_cost:
        print(f"length: {quick_cost.get('length')}")
        print(f"width: {quick_cost.get('width')}") 
        print(f"height: {quick_cost.get('height')}")
    
    if not quick_cost:
        return "Ошибка расчета"
    
    t1_cost = quick_cost['t1_cost']
    t2_cost = quick_cost['t2_cost'] 
    zone = quick_cost['zone']
    t2_rate = quick_cost['t2_rate']
    volume = quick_cost['volume']
    density = quick_cost['density']
    rule = quick_cost['rule']
    t1_cost_usd = quick_cost['t1_cost_usd']
    
    price = rule['price']
    unit = rule['unit']
    if unit == "kg":
        calculation_text = f"${price}/кг × {weight} кг = ${t1_cost_usd:.2f} USD"
    elif unit == "m3":
        calculation_text = f"${price}/м³ × {volume:.3f} м³ = ${t1_cost_usd:.2f} USD"
    else:
        calculation_text = f"${price}/кг × {weight} кг = ${t1_cost_usd:.2f} USD"
    
    city_name = city.capitalize()
    
    # Проверяем габариты на превышение
    length = quick_cost.get('length')
    width = quick_cost.get('width') 
    height = quick_cost.get('height')
    
    if check_dimensions_exceeded(length, width, height):
        # Груз превышает размеры - только самовывоз
        t2_explanation = f"❌ **Ваш груз превышает максимальный размер посылки 230×180×110 см**\n• Доставка только до склада Алматы (самовывоз)"
        t2_cost = 0
        zone_text = "только самовывоз"
        comparison_text = f"💡 **Самовывоз со склада в Алматы:** {t1_cost * 1.20:.0f} тенге (включая комиссию 20%)"
    else:
        # Груз в пределах размеров - можно до двери
        if zone == "алматы":
            t2_explanation = f"• Доставка по городу Алматы до вашего адреса"
            zone_text = "город Алматы"
            comparison_text = f"💡 **Если самовывоз со склада в Алматы:** {t1_cost * 1.20:.0f} тенге (включая комиссию 20%)"
        else:
            t2_explanation = f"• Доставка до вашего адреса в {city_name}"
            zone_text = f"Зона {zone}"
            comparison_text = f"💡 **Если самовывоз из Алматы:** {t1_cost * 1.20:.0f} тенге (включая комиссию 20%)"

        # Пересчитываем итоговую стоимость
    if check_dimensions_exceeded(length, width, height):
        total_cost = t1_cost * 1.20  # Только Т1 с комиссией
    else:
        total_cost = (t1_cost + t2_cost) * 1.20
        
    response = (
        f"📊 **Детальный расчет для {weight} кг «{product_type}» в г. {city_name}:**\n\n"
        
        f"**Т1: Доставка из Китая до Алматы**\n"
        f"• Плотность вашего груза: **{density:.1f} кг/м³**\n"
        f"• Применен тариф Т1: **${price} за {unit}**\n"
        f"• Расчет: {calculation_text}\n"
        f"• По курсу {EXCHANGE_RATE} тенge/$ = **{t1_cost:.0f} тенge**\n\n"
        
        f"**Т2: Доставка до двери ({zone_text})**\n"
        f"{t2_explanation}\n"
        f"• Прогрессивный тариф для {weight} кг = **{t2_cost:.0f} тенge**\n\n"
        
        f"**Комиссия компании (20%):**\n"
        f"• ({t1_cost:.0f} + {t2_cost:.0f}) × 20% = **{(t1_cost + t2_cost) * 0.20:.0f} тенge**\n\n"
        
        f"------------------------------------\n"
        f"💰 **ИТОГО с доставкой до двери:** ≈ **{total_cost:,.0f} тенge**\n\n"
        
        f"{comparison_text}\n\n"
        f"💡 **Страхование:** дополнительно 1% от стоимости груза\n"
        f"💳 **Оплата:** пост-оплата при получении\n\n"
        f"✅ **Оставить заявку?** Напишите ваше имя и телефон!\n"
        f"🔄 **Новый расчет?** Напишите **Старт**"
    )
    return response

def explain_tariffs():
    """Объяснение тарифов Т1 и Т2"""
    return """🚚 **Объяснение тарифов:**

**Т1 - Доставка до склада в Алматы:**
• Доставка из Китая до нашего сортировочного склада в Алматы
• Вы забираете груз самовывозом со склада
• ТОЛЬКО склад в Алматы, без доставки по городу
• **НОВОЕ:** Расчет по плотности груза (вес/объем) - чем выше плотность, тем выгоднее тариф!

**Т2 - Доставка до двери:**
• Доставка из Китая + доставка до вашего адреса в ЛЮБОМ городе Казахстана
• Включая доставку по городу Алматы до вашего адреса
• Мы привозим груз прямо к вам

💡 **Важно:** Даже если вы в Алматы, но нужна доставка до адреса - это Т2

💳 **Оплата:** пост-оплата при получении (наличные, Kaspi, Halyk, Freedom Bank, безнал)"""

def get_payment_info():
    """Информация о способах оплаты"""
    return """💳 **Условия оплаты:**

💰 **Пост-оплата:** Вы платите при получении груза в удобном для вас формате:

• **Безналичный расчет** перечислением на счет
• **Наличными** 
• **Kaspi Bank**
• **Halyk Bank** 
• **Freedom Bank**

💡 Оплата производится только после доставки и осмотра груза!"""

def get_delivery_procedure():
    return """📦 **Процедура доставки:**

1. **Прием груза в Китае:** Ваш груз прибудет на наш склад в Китае (ИУ или Гуанчжоу)
2. **Осмотр и обработка:** Взвешиваем, фотографируем, упаковываем
3. **Подтверждение:** Присылаем детали груза
4. **Отправка:** Доставляем до Алматы (Т1) или до двери (Т2)
5. **Получение и оплата:** Забираете груз и оплачиваете удобным способом

💳 **Оплата:** пост-оплата при получении (наличные, Kaspi, Halyk, Freedom Bank, безнал)

✅ **Хотите оформить заявку?** Напишите ваше имя и телефон!"""

def save_application(details):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"Новая заявка: {timestamp}\n{details}\n"
        with open("applications.txt", "a", encoding="utf-8") as f: 
            f.write("="*50 + "\n" + log_entry + "="*50 + "\n\n")
        logger.info(f"Заявка сохранена: {details}")
    except Exception as e: 
        logger.error(f"Ошибка сохранения: {e}")

def get_gemini_response(user_message, context=""):
    """Получает ответ от Gemini для общих вопросов."""
    if not model:
        return "Извините, сейчас я могу отвечать только на вопросы по доставке."
    
    try:
        full_prompt = f"{PERSONALITY_PROMPT}\n\nТекущий контекст диалога:\n{context}\n\nВопрос клиента: {user_message}\n\nТвой ответ:"
        
        response = model.generate_content(
            contents=full_prompt,
            generation_config=GenerationConfig(
                temperature=0.8,
                max_output_tokens=1000,
            )
        )
        return response.text
    except Exception as e:
        logger.error(f"Ошибка Gemini: {e}")
        return "Ой, кажется, у меня что-то пошло не так с креативной частью! Давайте лучше вернемся к расчету доставки, с этим я точно справлюсь. 😊"

def extract_delivery_info(text):
    """Извлечение данных о доставки"""
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
        
        # Используем новую функцию определения города
        text_lower = text.lower()
        for city_name in DESTINATION_ZONES:
            if city_name in text_lower:
                city = city_name
                break
        
        # Используем новую функцию определения категории товара
        product_type = find_product_category(text, PRODUCT_CATEGORIES)
        
        return weight, product_type, city
    except Exception as e:
        logger.error(f"Ошибка извлечения данных: {e}")
        return None, None, None

def extract_contact_info(text):
    """Умное извлечение контактных данных"""
    name = None
    phone = None
    
    clean_text = re.sub(r'\s+', ' ', text.strip()).lower()
    
    # Улучшенный поиск имени - ищем в любом месте текста
    name_patterns = [
        r'(?:имя|меня зовут|зовут)\s*[:\-]?\s*([а-яa-z]{2,})',
        r'^([а-яa-z]{2,})(?:\s|,|$)',
        r'([а-яa-z]{2,})\s*(?:\d|,|$)'
    ]
    
    for pattern in name_patterns:
        name_match = re.search(pattern, clean_text)
        if name_match:
            name = name_match.group(1).capitalize()
            break
    
    phone_patterns = [
        r'(\d{10,11})',
        r'(\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})',
        r'(\d{3}[\s\-]?\d{2}[\s\-]?\d{2}[\s\-]?\d{3})',
    ]
    
    for pattern in phone_patterns:
        phone_match = re.search(pattern, clean_text)
        if phone_match:
            phone = re.sub(r'\D', '', phone_match.group(1))
            if phone.startswith('8'):
                phone = '7' + phone[1:]
            elif len(phone) == 10:
                phone = '7' + phone
            break
    
    if name and phone and len(phone) >= 10:
        return name, phone
    
    if phone and not name:
        name_before_comma = re.search(r'^([а-яa-z]+)\s*[,]', clean_text)
        if name_before_comma:
            name = name_before_comma.group(1).capitalize()
    
    return name, phone

# 🎯 НАЧАЛО_НОВЫХ_ФУНКЦИЙ
def generate_delivery_response(message):
    """
    Обрабатывает сообщение в режиме доставки
    """
    try:
        # Извлекаем данные о доставке
        weight, product_type, city = extract_delivery_info(message)
        length, width, height = extract_dimensions(message)
        volume_direct = extract_volume(message)
        
        # Расчет объема
        volume = volume_direct
        if not volume and length and width and height:
            volume = length * width * height
        
        # Проверяем наличие всех данных
        if not weight:
            return "📊 Укажите вес груза в кг (например: 50 кг)"
        if not product_type:
            return "📦 Укажите тип товара (мебель, техника, косметика и т.д.)"
        if not city:
            return "🏙️ Укажите город доставки (Алматы, Астана и т.д.)"
        if not volume:
            return "📐 Укажите габариты (например: 1.2×0.8×0.5 м) или объем"
        
        # Производим расчет
        quick_cost = calculate_quick_cost(weight, product_type, city, volume)
        
        if quick_cost:
            return calculate_detailed_cost(quick_cost, weight, product_type, city)
        else:
            return "❌ Не удалось рассчитать стоимость. Проверьте данные."
            
    except Exception as e:
        logger.error(f"Ошибка в generate_delivery_response: {e}")
        return "⚠️ Ошибка расчета. Попробуйте еще раз."

def generate_free_response(message, intent_type=None):
    """
    Обрабатывает сообщение в режиме свободного диалога
    """
    try:
        # Используем Gemini для свободного диалога
        bot_response = get_gemini_response(message)
        return bot_response
        
    except Exception as e:
        logger.error(f"Ошибка в generate_free_response: {e}")
        return "💬 Давайте поговорим о чем-то другом! Чем еще могу помочь?"
# 🎯 КОНЕЦ_НОВЫХ_ФУНКЦИЙ
    
# ↓↓↓ ВСТАВИТЬ ЗДЕСЬ - основная функция обработки (версия с обработкой ошибок) ↓↓↓
def handle_message_universal(user_id, message):
    intent_manager = SmartIntentManager()
    
    try:
        if intent_manager.should_switch_to_delivery(message):
            response = generate_delivery_response(message)
            return response
        else:
            intent_type = intent_manager.get_intent_type(message)
            response = generate_free_response(message, intent_type)
            return response
    except NameError as e:
        logger.error(f"Function not found: {e}")
        return "⚠️ Системная ошибка: функции обработки не найдены"
# ↑↑↑ КОНЕЦ ВСТАВКИ ФУНКЦИИ ↑↑↑

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        logger.info(f"=== НОВЫЙ ЗАПРОС: {user_message} ===")
        
        if not user_message:
            return jsonify({"response": "Пожалуйста, введите сообщение."})
        
        delivery_data = session.get('delivery_data', {'weight': None, 'product_type': None, 'city': None, 'volume': None})
        chat_history = session.get('chat_history', [])
        waiting_for_contacts = session.get('waiting_for_contacts', False)
        calculation_shown = session.get('calculation_shown', False)
        
        chat_history.append(f"Клиент: {user_message}")
        
        # Приветствия
        if user_message.lower() in GREETINGS:
            session.update({
                'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'volume': None},
                'chat_history': [f"Клиент: {user_message}"],
                'waiting_for_contacts': False,
                'calculation_shown': False
            })
            logger.info(f"=== ВОЗВРАТ ПРИВЕТСТВИЯ ===")  # ← ДОБАВИТЬ
            return jsonify({"response": "Привет! 👋 Я ассистент Post Pro. Помогу рассчитать доставку из Китая в Казахстан!\n\n📦 **Для расчета укажите 4 параметра:**\n• **Вес груза** (в кг)\n• **Тип товара** (мебель, техника, одежда и т.д.)\n• **Габариты** (Д×Ш×В в метрах или сантиметрах)\n• **Город доставки**\n\n💡 **Пример:** \"50 кг мебель в Астану, габариты 120×80×50\""})

        # Обработка команды "Старт" для нового расчета
        if user_message.lower() in ['старт', 'start', 'новый расчет', 'сначала', 'новая заявка']:
            session.update({
                'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'volume': None},
                'chat_history': [],
                'waiting_for_contacts': False,
                'calculation_shown': False
            })
            logger.info(f"=== ВОЗВРАТ СТАРТ ===")  # ← ДОБАВИТЬ
            return jsonify({"response": "🔄 Начинаем новый расчет!\n\n📦 **Для расчета укажите 4 параметра:**\n• **Вес груза** (в кг)\n• **Тип товара** (мебель, техника, одежда и т.д.)\n• **Габариты** (Д×Ш×В в метрах или сантиметрах)\n• **Город доставки**\n\n💡 **Пример:** \"50 кг мебель в Астану, габариты 120×80×50\""})
        
        # Если ждем контакты (после показа расчета)
        if waiting_for_contacts:
            name, phone = extract_contact_info(user_message)
            
            if name and phone:
                details = f"Имя: {name}, Телефон: {phone}"
                if delivery_data['weight']:
                    details += f", Вес: {delivery_data['weight']} кг"
                if delivery_data['product_type']:
                    details += f", Товар: {delivery_data['product_type']}"
                if delivery_data['city']:
                    details += f", Город: {delivery_data['city']}"
                if delivery_data.get('volume'):
                    details += f", Объем: {delivery_data['volume']:.3f} м³"
                
                save_application(details)
                
                session.update({
                    'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'volume': None},
                    'chat_history': [],
                    'waiting_for_contacts': False,
                    'calculation_shown': False
                })
                
                return jsonify({"response": "🎉 Спасибо, что выбрали Post Pro! Менеджер свяжется с вами в течение часа. 📞⏰ **Рабочее время:** с 9:00 до 19:00 по времени Астаны"})
            else:
                return jsonify({"response": "Не удалось распознать контакты. Пожалуйста, укажите в формате: 'Имя, 87001234567'"})
        
        # ОБЩИЕ ВОПРОСЫ - передаем Gemini ДО логики расчетов
        non_calc_keywords = ['привет', 'как дела', 'что умеешь', 'кто ты', 'погода', 'бот', 'помощь', 'помоги', 'как настроение', 'расскажи о себе', 'что ты']
        if any(word in user_message.lower() for word in non_calc_keywords):
            bot_response = get_gemini_response(user_message)
            chat_history.append(f"Ассистент: {bot_response}")
            session['chat_history'] = chat_history
            return jsonify({"response": bot_response})
        
        # Обработка специальных команд (только если расчет еще не показан)
        if not calculation_shown:
            # Запросы об оплате
            if any(word in user_message.lower() for word in ['оплат', 'платеж', 'заплатит', 'деньги', 'как платит', 'наличн', 'безнал', 'kaspi', 'halyk', 'freedom', 'банк']):
                logger.info(f"=== ВОЗВРАТ ОПЛАТА ===")  # ← ДОБАВИТЬ
                return jsonify({"response": get_payment_info()})
            
            # Запросы о тарифах Т1/Т2
            if any(word in user_message.lower() for word in ['т1', 'т2', 'тариф', 'что такое т', 'объясни тариф']):
                logger.info(f"=== ВОЗВРАТ ТАРИФЫ ===")  # ← ДОБАВИТЬ
                return jsonify({"response": explain_tariffs()})
            
            # Запросы о заявке (до расчета)
            if any(word in user_message.lower() for word in ['заявк', 'оставь', 'свяж', 'контакт', 'позвон', 'менеджер']):
                return jsonify({"response": "Сначала давайте рассчитаем стоимость доставки. Укажите вес, тип товара, габариты и город доставки."})
            
            # Процедура доставки
            if any(word in user_message.lower() for word in ['процедур', 'процесс', 'как достав', 'как получ']):
                return jsonify({"response": get_delivery_procedure()})
        
        # Технология
        if any(word in user_message.lower() for word in ['на каком ии', 'какой ии', 'технология']):
            return jsonify({"response": "Я работаю на базе Post Pro ИИ! 🚀"})
        
        # Извлечение данных о доставке
        weight, product_type, city = extract_delivery_info(user_message)
        length, width, height = extract_dimensions(user_message)
        volume_direct = extract_volume(user_message)

        data_updated = False
        confirmation_parts = []

        if weight and weight != delivery_data['weight']:
            delivery_data['weight'] = weight
            data_updated = True
            confirmation_parts.append(f"📊 **Вес:** {weight} кг")

        if product_type and product_type != delivery_data['product_type']:
            delivery_data['product_type'] = product_type
            data_updated = True
            confirmation_parts.append(f"📦 **Товар:** {product_type}")

        if city and city != delivery_data['city']:
            delivery_data['city'] = city
            data_updated = True
            confirmation_parts.append(f"🏙️ **Город:** {city.capitalize()}")

        # Обработка габаритов и объема (объем имеет приоритет)
        if volume_direct and volume_direct != delivery_data.get('volume'):
            delivery_data['volume'] = volume_direct
            delivery_data['length'] = None
            delivery_data['width'] = None
            delivery_data['height'] = None
            data_updated = True
            confirmation_parts.append(f"📏 **Объем:** {volume_direct:.3f} м³")
        elif length and width and height:
            calculated_volume = length * width * height
            current_volume = delivery_data.get('volume')
            if current_volume is None or abs(calculated_volume - current_volume) > 0.001:
                delivery_data['length'] = length
                delivery_data['width'] = width
                delivery_data['height'] = height
                delivery_data['volume'] = calculated_volume
                data_updated = True
                confirmation_parts.append(f"📐 **Габариты:** {length:.2f}×{width:.2f}×{height:.2f} м")
                confirmation_parts.append(f"📏 **Объем:** {calculated_volume:.3f} м³")
        
        # Если данные обновлены, показываем подтверждение
        if data_updated and not calculation_shown:
            response_message = "✅ **Данные обновлены:**\n" + "\n".join(confirmation_parts) + "\n\n"
            
            # Проверяем наличие всех данных для расчета
            has_all_data = (
                delivery_data['weight'] and 
                delivery_data['product_type'] and 
                delivery_data['city'] and 
                delivery_data.get('volume')
            )
            
            if has_all_data:
                response_message += "📋 **Все данные собраны!** Готовы к расчету стоимости доставки."
            else:
                missing_data = []
                if not delivery_data['weight']:
                    missing_data.append("вес груза")
                if not delivery_data['product_type']:
                    missing_data.append("тип товара")
                if not delivery_data.get('volume'):
                    missing_data.append("габариты или объем")
                if not delivery_data['city']:
                    missing_data.append("город доставки")
                
                response_message += f"📝 **Осталось указать:** {', '.join(missing_data)}"
            
            session['delivery_data'] = delivery_data
            session['chat_history'] = chat_history
            return jsonify({"response": response_message})
        
        # Проверка наличия всех данных для расчета
        has_all_data = (
            delivery_data['weight'] and 
            delivery_data['product_type'] and 
            delivery_data['city'] and 
            delivery_data.get('volume')
        )
        
            
        # ТРИГГЕР РАСЧЕТА - когда все данные собраны и расчет еще не показан
        if has_all_data and not calculation_shown:
            # Производим расчет
            quick_cost = calculate_quick_cost(
                delivery_data['weight'], 
                delivery_data['product_type'], 
                delivery_data['city'],
                delivery_data.get('volume'),
                delivery_data.get('length'),
                delivery_data.get('width'), 
                delivery_data.get('height')
            )
            
            if quick_cost:
                # Сразу показываем детальный расчет вместо вопроса
                detailed_response = calculate_detailed_cost(
                    quick_cost,
                    delivery_data['weight'], 
                    delivery_data['product_type'], 
                    delivery_data['city']
                )
                
                # Сохраняем результат расчета в сессии
                session['quick_cost'] = quick_cost
                session['calculation_shown'] = True
                session['waiting_for_contacts'] = True  # Сразу переходим к сбору контактов
                session['delivery_data'] = delivery_data
                session['chat_history'] = chat_history
                
                return jsonify({"response": detailed_response})
            else:
                return jsonify({"response": "❌ Не удалось рассчитать стоимость. Проверьте правильность введенных данных."})
        
        # Обработка после показа расчета
        if calculation_shown:
            # Запрос детального расчета
            if any(word in user_message.lower() for word in ['детальн', 'подробн', 'разбей', 'тариф', 'да', 'yes', 'конечно']):
                detailed_response = calculate_detailed_cost(
                    session.get('quick_cost'),
                    delivery_data['weight'], 
                    delivery_data['product_type'], 
                    delivery_data['city']
                )
                session['waiting_for_contacts'] = True
                session['chat_history'] = chat_history
                return jsonify({"response": detailed_response})
            
            # Запрос на оформление заявки
            if any(word in user_message.lower() for word in ['заявк', 'оставь', 'свяж', 'контакт', 'позвон', 'менеджер', 'дальше', 'продолж']):
                session['waiting_for_contacts'] = True
                session['chat_history'] = chat_history
                return jsonify({"response": "Отлично! Для связи укажите:\n• Ваше имя\n• Номер телефона\n\nНапример: 'Аслан, 87001234567'"})
        
        # ВО ВСЕХ ОСТАЛЬНЫХ СЛУЧАЯХ - используем Gemini для естественного общения
        context_lines = []
        
        # Добавляем историю диалога
        if len(chat_history) > 0:
            context_lines.append("История диалога:")
            for msg in chat_history[-6:]:  # Берем последние 6 сообщений
                context_lines.append(msg)
        
        # Добавляем текущие данные о доставке
        context_lines.append("\nТекущие данные для доставки:")
        if delivery_data['weight']:
            context_lines.append(f"- Вес: {delivery_data['weight']} кг")
        else:
            context_lines.append(f"- Вес: не указан")
        if delivery_data['product_type']:
            context_lines.append(f"- Товар: {delivery_data['product_type']}")
        else:
            context_lines.append(f"- Товар: не указан")
        if delivery_data['city']:
            context_lines.append(f"- Город: {delivery_data['city']}")
        else:
            context_lines.append(f"- Город: не указан")
        if delivery_data.get('volume'):
            context_lines.append(f"- Объем: {delivery_data['volume']:.3f} м³")
        else:
            context_lines.append(f"- Объем: не указан")
        
        context = "\n".join(context_lines)
        
        # Создаем промпт для Gemini
        gemini_prompt = f"""
        {PERSONALITY_PROMPT}
        
        Ты - умный и дружелюбный ассистент компании Post Pro. Твоя главная цель - помочь клиенту рассчитать стоимость доставки из Китая в Казахстан.
        
        Контекст диалога:
        {context}
        
        Важные правила:
        1. **СКЛАДЫ В КИТАЕ:** У нас только 2 склада - ИУ и Гуанчжоу. 
        2. Если клиент спрашивает про ЛЮБОЙ другой город Китая (Шанхай, Пекин, Гонконг, Шэньчжэнь и т.д.) - отвечай:
           "У нас склады только в ИУ и Гуанчжоу. Уточните у вашего поставщика, какой склад ему ближе - ИУ или Гуанчжоу. Или посмотрите сами адрес складов - я вам отправлю."
        3. Не предлагай и не упоминай другие города Китая кроме ИУ и Гуанчжоу.
        4. Общайся естественно, с юмором и эмодзи
        5. Если данных для расчета не хватает - вежливо напомни какие параметры нужны
        6. Не заставляй клиента строго следовать формату - принимай данные в любом виде
        7. Поддержи любой разговор, но мягко возвращай к теме доставки
        8. Используй информацию из истории диалога
        
        Вопрос клиента: {user_message}
        """
        logger.info(f"=== ВЫЗОВ GEMINI ===")
        logger.info(f"Промпт: {gemini_prompt[:500]}...")  # первые 500 символов
        
        bot_response = get_gemini_response(gemini_prompt)
        logger.info(f"=== ОТВЕТ GEMINI: {bot_response} ===")
        chat_history.append(f"Ассистент: {bot_response}")
        
        # Ограничение истории
        if len(chat_history) > 10:
            chat_history = chat_history[-10:]
        
        session['chat_history'] = chat_history
        session['delivery_data'] = delivery_data
        
        return jsonify({"response": bot_response})
        
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        return jsonify({"response": "Извините, произошла ошибка. Попробуйте еще раз."})

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)





















