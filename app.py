from flask import Flask, render_template, request, jsonify, session
import os
import re
from datetime import datetime
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from dotenv import load_dotenv
import socket
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")

app = Flask(__name__)
app.secret_key = 'postpro-secret-key-2024'
app.config['PERMANENT_SESSION_LIFETIME'] = 1800

# --- КОНСТАНТЫ И БАЗА ДАННЫХ ---
DESTINATION_ZONES = {
    "талдыкорган": 1, "конаев": 1, "текели": 1, "капчагай": 1, "есик": 1, "талгар": 1, "каскелен": 1,
    "жаркент": 1, "сарканд": 1, "аксу": 1, "алматы": 1, "алмата": 1,
    "тараз": 2, "шымкент": 2, "туркестан": 2, "аулиеата": 2, "кордай": 2, "мерке": 2, "мойынкум": 2,
    "жанатас": 2, "каратау": 2, "шу": 2, "кент": 2,
    "астана": 3, "кокшетау": 3, "степногорск": 3, "атбасар": 3, "ерементау": 3, "макинск": 3,
    "караганда": 3, "балхаш": 3, "темиртау": 3, "шахтинск": 3, "жезказган": 3, "сатпаев": 3,
    "кызылорда": 3, "казалынск": 3, "жанакорган": 3, "петропавловск": 3, "павлодар": 3, "экибастуз": 3,
    "костанай": 3, "рудный": 3, "семей": 3, "курчатов": 3, "аягоз": 3,
    "актобе": 4, "хромтау": 4, "шалкар": 4, "уральск": 4, "аксай": 4, "чингирлау": 4,
    "атырау": 5, "кульсары": 5, "актау": 5, "жанаозен": 5, "бейнеу": 5
}

EXCHANGE_RATE = 550

# --- КОНСТАНТЫ ДЛЯ РАСТАМОЖКИ ---
CUSTOMS_RATES = {
    "одежда": 10, "электроника": 5, "косметика": 15, "техника": 5,
    "мебель": 10, "автозапчасти": 5, "общие товары": 10, "инструменты": 8,
    "ткани": 12, "посуда": 10, "продукты": 15, "лекарства": 0, "белье": 12,
    "игрушки": 5, "вещи": 10, 'стройматериалы': 10, 'оборудование': 5, 'лампы': 8,
    'аксессуары для телефонов': 5, 'головные уборы': 10, 'сумки': 10, 'малая техника': 5,
    'чай': 15, 'текстиль': 12, 'постельное белье': 12, 'полотенца': 12, 'одеяла': 12,
    'медикаменты': 0
}

CUSTOMS_FEES = {
    "оформление": 15000,  # тенге
    "сертификат": 120000,  # тенге
    "происхождения": 500,  # USD
    "брокер": 60000,      # тенге
    "декларация": 15000   # тенге
}

GREETINGS = ["привет", "здравствуй", "здравствуйте", "салем", "сәлем", "добрый день", "добрый вечер", "доброе утро"]

# --- СИСТЕМНЫЕ ПРОМПТЫ ---
MAIN_SYSTEM_INSTRUCTION = """
Ты — умный ассистент компании PostPro. Твоя главная цель — помочь клиенту рассчитать стоимость доставки и оформить заявку.
***ВАЖНЫЕ ПРАВИЛА:***

1. **РАСЧЕТ ПО ПЛОТНОСТИ:** Всегда запрашивай вес и ОБЪЕМ (или габариты) для точного расчета. Это самое важное.
2. **ТАРИФЫ:**
   - Т1: Доставка из Китая до Алматы (самовывоз). Стоимость зависит от плотности (вес/объем).
   - Т2: Доставка до двери в любом городе Казахстана.

3. **ЛОГИКА ДИАЛОГА:**
   - Если клиент выбирает "1" или "2" - это выбор варианта доставки.
   - Не переспрашивай данные, которые уже получены.
   - При выборе варианта доставки сразу переходи к оформлению заявки.

4. **ОБЩИЕ ВОПРОСЫ:**
   - Если вопрос не о доставке - отвечай как умный ИИ-помощник.
   - Поддержи любой диалог, мягко возвращая к теме доставки.

Всегда будь дружелюбным и профессиональным! 😊
"""

CUSTOMS_SYSTEM_INSTRUCTION = """
Ты — специалист по таможенному оформлению. Определяй код ТН ВЭД ЕАЭС для товаров.
ПРАВИЛА:
- Возвращай ТОЛЬКО код в формате XXXXX XXX X
- Без пояснений, текста, точек
- Только цифры и пробелы
- Если не уверен - верни наиболее вероятный код

Примеры:
- "игрушки" → "9503 00 700 0"
- "одежда" → "6109 10 000 0"
- "телефон" → "8517 12 000 0"
"""

# --- ОТЛАДКА ---
def debug_session(session_data, message):
    """Функция для отладки сессии"""
    print(f"=== DEBUG: {message} ===")
    print(f"delivery_data: {session_data.get('delivery_data')}")
    print(f"customs_data: {session_data.get('customs_data')}")
    print(f"waiting_for_contacts: {session_data.get('waiting_for_contacts')}")
    print(f"waiting_for_customs: {session_data.get('waiting_for_customs')}")
    print(f"waiting_for_delivery_choice: {session_data.get('waiting_for_delivery_choice')}")
    print(f"waiting_for_tnved: {session_data.get('waiting_for_tnved')}")
    print("=== DEBUG END ===")

# --- ИНИЦИАЛИЗАЦИЯ МОДЕЛЕЙ ---
main_model = None
customs_model = None

def initialize_models():
    """Инициализация моделей Gemini"""
    global main_model, customs_model
    try:
        if GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            main_model = genai.GenerativeModel(
                model_name='models/gemini-1.5-flash',
                system_instruction=MAIN_SYSTEM_INSTRUCTION
            )
            customs_model = genai.GenerativeModel(
                model_name='models/gemini-1.5-flash',
                system_instruction=CUSTOMS_SYSTEM_INSTRUCTION
            )
            logger.info(">>> Модели Gemini успешно инициализированы.")
            return True
        else:
            logger.error("!!! API ключ не найден")
            return False
    except Exception as e:
        logger.error(f"!!! Ошибка инициализации Gemini: {e}")
        return False

# --- УМНЫЕ ФУНКЦИИ ДЛЯ ОБРАБОТКИ ВВОДА ---
def is_delivery_choice(message):
    """Определяет, является ли сообщение выбором доставки"""
    message_lower = message.lower().strip()
    choices = ['1', '2', 'т1', 'т2', 't1', 't2', 'первый', 'второй', 'один', 'два']
    return message_lower in choices

def parse_delivery_choice(message):
    """Преобразует любой вариант выбора в стандартный формат"""
    message_lower = message.lower().strip()
    if message_lower in ['1', 'т1', 't1', 'первый', 'один']:
        return "самовывоз"
    elif message_lower in ['2', 'т2', 't2', 'второй', 'два']:
        return "до двери"
    else:
        return None

def doesnt_know_tnved(message):
    """Определяет, что клиент не знает код ТНВЭД - РАСШИРЕННАЯ ВЕРСИЯ"""
    patterns = [
        'не знаю', 'нет кода', 'не помню', 'подскажите', 'помогите', 'какой код',
        'что указывать', 'где взять', 'как узнать', 'определи код', 'автоматически',
        'сам определи', 'нет', 'не имею', 'отсутствует', 'забыл', 'без кода',
        'что такое тнвед', 'пропусти', 'дальше', 'не важно', 'сомневаюсь', 'хз'
    ]
    message_lower = message.lower().strip()
    return any(pattern in message_lower for pattern in patterns)

def get_missing_data(delivery_data, customs_data, delivery_type):
    """Определяет какие данные отсутствуют"""
    missing = []
    if not delivery_data.get('weight'):
        missing.append("вес груза")
    if not delivery_data.get('volume'):
        missing.append("объем груза (м³) или габариты (Д×Ш×В в см)")
    if not delivery_data.get('product_type'):
        missing.append("тип товара")
    if not delivery_data.get('city'):
        missing.append("город доставки")
    if delivery_type == 'INVOICE':
        if not customs_data.get('invoice_value'):
            missing.append("стоимость в USD")
        if not customs_data.get('tnved_code'):
            missing.append("код ТНВЭД")
    return missing

# --- НОВЫЕ ФУНКЦИИ ИЗВЛЕЧЕНИЯ ДАННЫХ ---
def extract_delivery_info(text):
    """Извлечение данных о доставке, включая объем и габариты"""
    data = {}
    text_lower = text.lower()
    
    # Поиск веса
    weight_match = re.search(r'(\d+[,.]?\d*)\s*(кг|kg|килограмм)', text_lower)
    if weight_match:
        data['weight'] = float(weight_match.group(1).replace(',', '.'))

    # Поиск объема в м³
    volume_match = re.search(r'(\d+[,.]?\d*)\s*(м³|m³|м3|m3|куб)', text_lower)
    if volume_match:
        data['volume'] = float(volume_match.group(1).replace(',', '.'))
    
    # Поиск габаритов (ДхШхВ в см) и конвертация в м³
    dims_match = re.search(r'(\d+)\s*[хx×*]\s*(\d+)\s*[хx×*]\s*(\d+)\s*(см|cm)?', text_lower)
    if dims_match and 'volume' not in data:
        l, w, h = map(int, dims_match.groups()[:3])
        data['volume'] = (l * w * h) / 1_000_000
        data['dimensions_str'] = f"{l}x{w}x{h} см"

    # Поиск города
    for city_name in DESTINATION_ZONES:
        if city_name in text_lower:
            data['city'] = city_name
            break
            
    # Поиск типа товара
    product_keywords = {
        'мебель': ['мебель', 'стол', 'стул', 'кровать', 'диван'],
        'стройматериалы': ['стройматериалы', 'плитка', 'ламинат', 'обои'],
        'оборудование': ['оборудование', 'станок', 'аппарат'],
        'посуда': ['посуда', 'тарелки', 'чашки', 'кастрюли'],
        'лампы': ['лампы', 'люстры', 'светильники'],
        'автозапчасти': ['автозапчасти', 'запчасти', 'детали авто'],
        'аксессуары для телефонов': ['аксессуары для теле', 'чехлы', 'зарядки'],
        'косметика': ['косметика', 'крем', 'шампунь', 'парфюм'],
        'головные уборы': ['головные уборы', 'шапки', 'кепки'],
        'сумки': ['сумки', 'рюкзаки', 'чемоданы'],
        'малая техника': ['малая техника', 'миксер', 'блендер', 'чайник'],
        'продукты': ['продукты', 'еда', 'консервы'],
        'чай': ['чай'],
        'ткани': ['ткани', 'текстиль', 'рулоны'],
        'одежда': ['одежда', 'одежд', 'штаны', 'футболки', 'куртки'],
        'инструменты': ['инструменты', 'дрель', 'шуруповерт'],
        'белье': ['белье', 'нижнее белье'],
        'постельное белье': ['постельное белье', 'простыни', 'наволочки'],
        'игрушки': ['игрушки', 'куклы', 'машинки'],
        'электроника': ['электроника', 'телефон', 'ноутбук', 'планшет'],
        'лекарства': ['лекарства', 'медикаменты', 'таблетки'],
        'вещи': ['вещи', 'личные вещи', 'груз']
    }
    
    found_type = None
    for prod_type, keywords in product_keywords.items():
        if any(keyword in text_lower for keyword in keywords):
            found_type = prod_type
            break
    
    if found_type:
        data['product_type'] = found_type
    elif not data.get('product_type'): # Если тип еще не установлен
        data['product_type'] = 'общие товары'

    return data


def extract_customs_info(text):
    """Извлечение данных для растаможки"""
    invoice_value, tnved_code = None, None
    cost_match = re.search(r'(\d+[,.]?\d*)\s*(usd|\$|доллар)', text.lower())
    if cost_match:
        invoice_value = float(cost_match.group(1).replace(',', '.'))
    
    tnved_match = re.search(r'\b(\d{4}[\s-]?\d{2}[\s-]?\d{4})\b|\b(\d{10})\b', text)
    if tnved_match:
        tnved_code = tnved_match.group(1) or tnved_match.group(2)
        tnved_code = re.sub(r'[\s-]', '', tnved_code)

    return invoice_value, tnved_code

def extract_contact_info(text):
    """Умное извлечение контактных данных"""
    name, phone = None, None
    name_match = re.search(r'([а-яА-Яa-zA-Z]{2,})', text)
    if name_match:
        name = name_match.group(1).capitalize()
    
    phone_match = re.search(r'\+?[78]?[\s-]?\(?(\d{3})\)?[\s-]?(\d{3})[\s-]?(\d{2})[\s-]?(\d{2})', text)
    if phone_match:
        phone = f"7{phone_match.group(1)}{phone_match.group(2)}{phone_match.group(3)}{phone_match.group(4)}"
        
    return name, phone

# --- НОВЫЕ ФУНКЦИИ РАСЧЕТА СТОИМОСТИ ---

def calculate_t1_rate_by_density(product_type, density):
    """Расчет тарифа T1 на основе плотности груза - возвращает (ставка, единица_измерения)"""
    # Категории товаров сгруппированы для упрощения
    category_map = {
        'мебель': 'мебель', 'стройматериалы': 'мебель', 'оборудование': 'мебель', 'посуда': 'мебель', 'лампы': 'мебель',
        'автозапчасти': 'автозапчасти',
        'аксессуары для телефонов': 'аксессуары', 'косметика': 'аксессуары', 'головные уборы': 'аксессуары', 'сумки': 'аксессуары',
        'малая техника': 'техника', 'электроника': 'техника',
        'продукты': 'продукты', 'чай': 'продукты',
        'ткани': 'ткани', 'одежда': 'ткани', 'текстиль': 'ткани',
        'инструменты': 'инструменты',
        'белье': 'белье', 'постельное белье': 'белье', 'полотенца': 'белье', 'одеяла': 'белье',
        'игрушки': 'игрушки',
        'лекарства': 'лекарства', 'медикаменты': 'лекарства',
        'общие товары': 'общие', 'вещи': 'общие'
    }
    category = category_map.get(product_type, 'общие')

    # Тарифные сетки
    rates = {
        'мебель': [(100, 2.20, 'kg'), (110, 2.10, 'kg'), (120, 2.00, 'kg'), (130, 1.90, 'kg'), (140, 1.80, 'kg'), (150, 1.70, 'kg'), (160, 1.60, 'kg'), (170, 1.50, 'kg'), (180, 1.40, 'kg'), (190, 1.30, 'kg'), (200, 1.20, 'kg'), (250, 1.10, 'kg'), (300, 1.00, 'kg'), (350, 0.90, 'kg'), (400, 0.80, 'kg'), (float('inf'), 0.80, 'kg')],
        'автозапчасти': [(100, 2.40, 'kg'), (110, 2.30, 'kg'), (120, 2.20, 'kg'), (130, 2.10, 'kg'), (140, 2.10, 'kg'), (150, 1.90, 'kg'), (160, 1.80, 'kg'), (170, 1.70, 'kg'), (180, 1.60, 'kg'), (190, 1.50, 'kg'), (200, 1.40, 'kg'), (250, 1.35, 'kg'), (300, 1.25, 'kg'), (350, 1.20, 'kg'), (400, 1.00, 'kg'), (float('inf'), 1.00, 'kg')],
        'аксессуары': [(100, 2.30, 'kg'), (110, 2.20, 'kg'), (120, 2.10, 'kg'), (130, 2.00, 'kg'), (140, 1.90, 'kg'), (150, 1.80, 'kg'), (160, 1.70, 'kg'), (170, 1.60, 'kg'), (180, 1.50, 'kg'), (190, 1.40, 'kg'), (200, 1.30, 'kg'), (250, 1.20, 'kg'), (300, 1.10, 'kg'), (350, 1.00, 'kg'), (400, 0.90, 'kg'), (float('inf'), 0.90, 'kg')],
        'техника': [(100, 2.60, 'kg'), (110, 2.50, 'kg'), (120, 2.40, 'kg'), (130, 2.30, 'kg'), (140, 2.20, 'kg'), (150, 2.10, 'kg'), (160, 2.00, 'kg'), (170, 1.90, 'kg'), (180, 1.80, 'kg'), (190, 1.70, 'kg'), (200, 1.60, 'kg'), (300, 1.50, 'kg'), (400, 1.40, 'kg'), (float('inf'), 1.40, 'kg')],
        'продукты': [(100, 2.70, 'kg'), (110, 2.60, 'kg'), (120, 2.50, 'kg'), (130, 2.40, 'kg'), (140, 2.30, 'kg'), (150, 2.20, 'kg'), (160, 2.10, 'kg'), (170, 2.00, 'kg'), (180, 1.90, 'kg'), (190, 1.80, 'kg'), (200, 1.70, 'kg'), (250, 1.60, 'kg'), (300, 1.50, 'kg'), (float('inf'), 1.50, 'kg')],
        'ткани': [(100, 1.80, 'kg'), (110, 1.70, 'kg'), (120, 1.60, 'kg'), (130, 1.50, 'kg'), (150, 1.40, 'kg'), (160, 1.30, 'kg'), (170, 1.20, 'kg'), (180, 1.10, 'kg'), (200, 1.00, 'kg'), (250, 0.90, 'kg'), (300, 0.80, 'kg'), (float('inf'), 0.80, 'kg')],
        'инструменты': [(100, 2.10, 'kg'), (110, 2.00, 'kg'), (120, 1.90, 'kg'), (130, 1.80, 'kg'), (140, 1.70, 'kg'), (150, 1.60, 'kg'), (160, 1.50, 'kg'), (170, 1.40, 'kg'), (180, 1.30, 'kg'), (190, 1.20, 'kg'), (200, 1.10, 'kg'), (250, 1.00, 'kg'), (300, 0.90, 'kg'), (350, 0.80, 'kg'), (400, 0.75, 'kg'), (float('inf'), 0.75, 'kg')],
        'белье': [(180, 1.30, 'kg'), (float('inf'), 1.30, 'kg')],
        'игрушки': [(120, 230, 'm3'), (130, 240, 'm3'), (140, 250, 'm3'), (150, 260, 'm3'), (160, 270, 'm3'), (170, 280, 'm3'), (180, 290, 'm3'), (190, 300, 'm3'), (200, 310, 'm3'), (float('inf'), 1.50, 'kg')],
        'лекарства': [(100, 3.10, 'kg'),(200, 3.00, 'kg'),(300, 2.90, 'kg'), (float('inf'), 2.90, 'kg')],
        'общие': [(100, 2.50, 'kg'), (200, 2.40, 'kg'), (300, 2.30, 'kg'), (400, 2.20, 'kg'), (float('inf'), 2.20, 'kg')]
    }
    
    # Тарифы за м³ для низкой плотности
    m3_rates = {
        'мебель': 230, 'автозапчасти': 240, 'аксессуары': 230, 'техника': 270,
        'продукты': 280, 'ткани': None, 'инструменты': 220, 'белье': None,
        'лекарства': 320, 'общие': 260
    }

    tariff_grid = rates.get(category, rates['общие'])
    
    for max_density, rate, unit in tariff_grid:
        if density < max_density:
            if unit == 'kg':
                return (rate, 'kg')
            else: # m3
                return (rate, 'm3')

    # Обработка низкой плотности
    m3_rate = m3_rates.get(category)
    if m3_rate:
        return (m3_rate, 'm3')

    return None # Требуется индивидуальный расчет

def calculate_t2_cost(weight, zone, is_fragile=False, is_village=False):
    """Расчет стоимости доставки Т2 по Казахстану"""
    if weight <= 0: return 0
    
    base_rates = {1: 2205, 2: 2310, 3: 2415, 4: 2520, 5: 2625}
    per_kg_rates = {1: 210, 2: 220, 3: 230, 4: 240, 5: 250}

    # Находим тариф для веса до 20 кг
    t2_under_20kg = {
        1: [(1,1680),(2,1760),(3,1840),(4,1920),(5,2000),(6,2080),(7,2160),(8,2240),(9,2320),(10,2400),(11,2480),(12,2560),(13,2640),(14,2720),(15,2800),(16,2880),(17,2960),(18,3040),(19,3120),(20,3200)],
        2: [(1,1885),(2,1970),(3,2055),(4,2140),(5,2225),(6,2310),(7,2395),(8,2480),(9,2565),(10,2650),(11,2735),(12,2820),(13,2905),(14,2990),(15,3075),(16,3160),(17,3245),(18,3330),(19,3415),(20,3500)],
        3: [(1,1995),(2,2090),(3,2185),(4,2280),(5,2375),(6,2470),(7,2565),(8,2660),(9,2755),(10,2850),(11,2945),(12,3040),(13,3135),(14,3230),(15,3325),(16,3420),(17,3515),(18,3610),(19,3705),(20,3800)],
        4: [(1,2100),(2,2200),(3,2300),(4,2400),(5,2500),(6,2600),(7,2700),(8,2800),(9,2900),(10,3000),(11,3100),(12,3200),(13,3300),(14,3400),(15,3500),(16,3600),(17,3700),(18,3800),(19,3900),(20,4000)],
        5: [(1,2205),(2,2310),(3,2415),(4,2520),(5,2625),(6,2730),(7,2835),(8,2940),(9,3045),(10,3150),(11,3255),(12,3360),(13,3465),(14,3570),(15,3675),(16,3780),(17,3885),(18,3990),(19,4095),(20,4200)]
    }
    
    cost = 0
    if weight <= 20:
        for w, c in t2_under_20kg.get(zone, t2_under_20kg[3]):
            if weight <= w:
                cost = c
                break
    else:
        base_rate = t2_under_20kg.get(zone, t2_under_20kg[3])[-1][1] # стоимость за 20 кг
        per_kg_rate = per_kg_rates.get(zone, 230)
        cost = base_rate + (weight - 20) * per_kg_rate

    if is_fragile: cost *= 1.5
    if is_village: cost *= 2.0
        
    return cost

def calculate_quick_cost(weight: float, volume: float, product_type: str, city: str):
    """Быстрый расчет стоимости с учетом плотности, БЕЗ СЕРВИСНОГО СБОРА"""
    try:
        if volume is None or volume <= 0 or weight is None or weight <=0:
            return None
        density = weight / volume
        t1_result = calculate_t1_rate_by_density(product_type, density)
        if t1_result is None:
            return {'error': 'Требуется индивидуальный расчет для данного типа товара и плотности.'}
            
        t1_rate, unit = t1_result
        if unit == 'kg':
            t1_cost_usd = weight * t1_rate
            t1_description = f"{weight:.1f} кг × {t1_rate:.2f} $/кг"
        else: # m3
            t1_cost_usd = volume * t1_rate
            t1_description = f"{volume:.2f} м³ × {t1_rate:.0f} $/м³"
        
        t1_cost_kzt = t1_cost_usd * EXCHANGE_RATE
        
        city_lower = city.lower()
        if city_lower == "алматы" or city_lower == "алмата":
            t2_cost_kzt = 120 * weight
            zone = "алматы"
        else:
            zone = DESTINATION_ZONES.get(city_lower, 3)
            t2_cost_kzt = calculate_t2_cost(weight, zone)
        
        return {
            't1_cost': t1_cost_kzt,
            't2_cost': t2_cost_kzt,
            'total': t1_cost_kzt + t2_cost_kzt, # Важно: total без сервисного сбора
            'zone': zone,
            'density': density,
            't1_rate': t1_rate,
            't1_unit': unit,
            't1_description': t1_description
        }
    except Exception as e:
        logger.error(f"Ошибка в calculate_quick_cost: {e}")
        return None

def calculate_customs_cost(invoice_value, product_type, weight, has_certificate=False, needs_certificate=False):
    """Расчет таможенных платежей"""
    try:
        customs_rate = CUSTOMS_RATES.get(product_type.lower(), 10) / 100
        duty_usd = invoice_value * customs_rate
        vat_usd = (invoice_value + duty_usd) * 0.12
        duty_kzt = duty_usd * EXCHANGE_RATE
        vat_kzt = vat_usd * EXCHANGE_RATE
        total_kzt = duty_kzt + vat_kzt + CUSTOMS_FEES['брокер'] + CUSTOMS_FEES['декларация']
        if needs_certificate and not has_certificate:
            total_kzt += CUSTOMS_FEES['сертификат']
        return {
            'duty_kzt': duty_kzt, 'vat_kzt': vat_kzt, 'total_kzt': total_kzt,
            'customs_rate': customs_rate * 100
        }
    except Exception as e:
        logger.error(f"Ошибка расчета растаможки: {e}")
        return None

# --- ОСТАЛЬНЫЕ УТИЛИТЫ ---
def get_tnved_code(product_name):
    """Получение кода ТН ВЭД через Gemini"""
    if not customs_model: return "6307 90 980 0"
    try:
        product_name = product_name if product_name else "общие товары"
        response = customs_model.generate_content(f"Код ТНВЭД для: '{product_name}'")
        code = response.text.strip()
        if re.match(r'^\d{4,10}[\s\d]*$', code): return code
        return "6307 90 980 0"
    except Exception as e:
        logger.error(f"Ошибка получения кода ТН ВЭД: {e}")
        return "6307 90 980 0"

def check_certification_requirements(product_name):
    """Проверка требований к сертификации через Gemini"""
    if not customs_model: return False
    try:
        response = customs_model.generate_content(f"Нужен ли сертификат ТР ТС для: '{product_name}'? Ответь ДА или НЕТ")
        return "ДА" in response.text.upper()
    except Exception as e:
        logger.error(f"Ошибка проверки сертификации: {e}")
        return False

def get_gemini_response(user_message, context=""):
    """Получение ответа от Gemini"""
    if not main_model: return "Сервис временно недоступен"
    try:
        prompt = f"Контекст: {context}\n\nСообщение пользователя: {user_message}"
        response = main_model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Ошибка Gemini: {e}")
        return "Извините, произошла ошибка."

def save_application(details):
    """Сохранение заявки в файл"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"Новая заявка: {timestamp}\n{details}\n"
        with open("applications.txt", "a", encoding="utf-8") as f:
            f.write("="*50 + "\n" + log_entry + "="*50 + "\n\n")
        logger.info(f"Заявка сохранена: {details}")
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

# --- ФУНКЦИИ ФОРМАТИРОВАНИЯ ОТВЕТОВ ---

def get_cargo_calculation_response(delivery_data, delivery_cost):
    """Формирует ответ с расчетом для КАРГО"""
    density_info = f"Плотность груза: {delivery_cost['density']:.1f} кг/м³"
    t1_basis_info = f"Тариф Т1 ({delivery_cost['t1_description']}) рассчитывается за {'**объем (м³)**, так как это выгоднее для легкого груза' if delivery_cost['t1_unit'] == 'm3' else '**вес (кг)**'}."
    
    cost_t1_with_service = delivery_cost['t1_cost'] * 1.20
    cost_t1_t2_with_service = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20

    return (
        f"✅ **Расчет для КАРГО доставки:**\n"
        f"• **Товар:** {delivery_data['weight']} кг {delivery_data['product_type']}\n"
        f"• **Объем:** {delivery_data.get('volume', 'N/A'):.2f} м³ {f'({delivery_data.get(\"dimensions_str\", \"\")})' if delivery_data.get('dimensions_str') else ''}\n"
        f"• **Город:** {delivery_data['city'].capitalize()}\n"
        f"• **{density_info}**\n\n"
        f"*{t1_basis_info}*\n\n"
        f"--- \n"
        f"🏷️ **Выберите вариант доставки:**\n\n"
        f"**🚚 ВАРИАНТ 1: ДОСТАВКА ДО АЛМАТЫ (самовывоз)**\n"
        f"• Стоимость доставки: {delivery_cost['t1_cost']:.0f} ₸\n"
        f"• Сервисный сбор (20%): {delivery_cost['t1_cost'] * 0.20:.0f} ₸\n"
        f"💰 **ИТОГО: {cost_t1_with_service:,.0f} ₸**\n\n"
        f"**🏠 ВАРИАНТ 2: ДОСТАВКА ДО ДВЕРИ в г. {delivery_data['city'].capitalize()}**\n"
        f"• Стоимость доставки (Т1+Т2): {delivery_cost['t1_cost'] + delivery_cost['t2_cost']:.0f} ₸\n"
        f"• Сервисный сбор (20%): {(delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 0.20:.0f} ₸\n"
        f"💰 **ИТОГО: {cost_t1_t2_with_service:,.0f} ₸**\n\n"
        f"--- \n"
        f"💡 **Напишите `1` или `2`, чтобы выбрать подходящий вариант.**"
    )

def get_customs_full_calculation(delivery_data, customs_data, tnved_code):
    """Полный расчет с доставкой и растаможкой"""
    delivery_cost = calculate_quick_cost(delivery_data['weight'], delivery_data['volume'], delivery_data['product_type'], delivery_data['city'])
    if not delivery_cost or delivery_cost.get('error'): return "Ошибка расчета доставки. " + (delivery_cost.get('error') or "")
    
    needs_certification = check_certification_requirements(delivery_data['product_type'])
    customs_cost = calculate_customs_cost(customs_data['invoice_value'], delivery_data['product_type'], delivery_data['weight'], False, needs_certification)
    if not customs_cost: return "Ошибка расчета растаможки"
    
    t1_total = delivery_cost['t1_cost'] * 1.20 + customs_cost['total_kzt']
    t2_total = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20 + customs_cost['total_kzt']
    
    return (
        f"📊 **Расчет для ИНВОЙС:**\n\n"
        f"✅ **Товар:** {delivery_data['weight']} кг {delivery_data['product_type']} в г. {delivery_data['city'].capitalize()}\n"
        f"✅ **Таможенная стоимость:** {customs_data['invoice_value']} USD\n"
        f"✅ **Код ТНВЭД:** {tnved_code}\n\n"
        f"--- \n"
        f"🏷️ **Выберите вариант доставки:**\n\n"
        f"**🚚 ВАРИАНТ 1: ДОСТАВКА ДО АЛМАТЫ (самовывоз)**\n"
        f"• Доставка + услуги + сборы: {t1_total:,.0f} ₸\n\n"
        f"**🏠 ВАРИАНТ 2: ДОСТАВКА ДО ДВЕРИ**\n"
        f"• Доставка + услуги + сборы: {t2_total:,.0f} ₸\n\n"
        f"--- \n"
        f"📄 **Сертификация:** {'требуется' if needs_certification else 'не требуется'}\n\n"
        f"💡 **Напишите `1` или `2` чтобы выбрать вариант.**"
    )

def show_final_calculation(delivery_data, customs_data, delivery_option):
    """Показывает итоговый расчет после выбора доставки"""
    delivery_cost = calculate_quick_cost(delivery_data['weight'], delivery_data['volume'], delivery_data['product_type'], delivery_data['city'])
    if not delivery_cost or delivery_cost.get('error'): return "Ошибка итогового расчета."

    if delivery_data['delivery_type'] == 'CARGO':
        total_cost = delivery_cost['t1_cost'] * 1.20 if delivery_option == "самовывоз" else (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20
    else: # INVOICE
        customs_cost_data = calculate_customs_cost(customs_data['invoice_value'], delivery_data['product_type'], delivery_data['weight'], False, False)
        total_delivery = delivery_cost['t1_cost'] * 1.20 if delivery_option == "самовывоз" else (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20
        total_cost = total_delivery + customs_cost_data['total_kzt']

    return (
        f"✅ Выбрана ДОСТАВКА ДО {'ДВЕРИ' if delivery_option == 'до двери' else 'АЛМАТЫ (самовывоз)'}\n\n"
        f"💰 **Итоговая стоимость: {total_cost:,.0f} ₸**\n"
        f"⏱️ Срок доставки: 12-15 дней\n\n"
        f"💎 Если вас устраивает наш тариф, оставьте заявку!\n"
        f"📝 Для этого напишите ваше **имя и номер телефона**."
    )

# --- МАРШРУТЫ FLASK ---
@app.route('/', methods=['GET'])
def index():
    """Главная страница с чатом"""
    if 'delivery_data' not in session:
        session['delivery_data'] = {}
        session['customs_data'] = {}
        session['chat_history'] = []
        session['waiting_for_contacts'] = False
        session['waiting_for_customs'] = False
        session['waiting_for_delivery_choice'] = False
        session['waiting_for_tnved'] = False
    if main_model is None:
        initialize_models()
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message: return jsonify({"response": "Пожалуйста, введите сообщение."})

        # Инициализация и загрузка данных из сессии
        delivery_data = session.get('delivery_data', {})
        customs_data = session.get('customs_data', {})
        chat_history = session.get('chat_history', [])
        
        chat_history.append(f"Клиент: {user_message}")

        # Сброс по команде
        if user_message.lower() in ['/start', 'сброс', 'старт', 'начать заново', 'новый расчет']:
            session.clear()
            return jsonify({"response": "🚚 Добро пожаловать в PostPro!\n\nЯ помогу вам рассчитать стоимость доставки из Китая в Казахстан.\n\n📦 **КАРГО** - для личных вещей и пробных партий\n📄 **ИНВОЙС** - для коммерческих партий с растаможкой\n\n💡 **Для расчета укажите:**\n• Вес груза (например: 50 кг)\n• **Объем груза (м³) или габариты (Д×Ш×В в см)**\n• Тип товара (одежда, электроника и т.д.)\n• Город доставки в Казахстане\n\n✨ **Примеры запросов:**\n\"50 кг одежды в Астану, объем 0.5 м³\"\n\"Карго 100 кг электроники в Алматы, габариты 120x80x60 см\""})

        # --- ЛОГИКА ДИАЛОГА ПО СОСТОЯНИЯМ ---

        # 1. Ожидание выбора варианта доставки
        if session.get('waiting_for_delivery_choice'):
            if is_delivery_choice(user_message):
                delivery_option = parse_delivery_choice(user_message)
                delivery_data['delivery_option'] = delivery_option
                session['delivery_data'] = delivery_data
                session['waiting_for_delivery_choice'] = False
                session['waiting_for_contacts'] = True # Сразу переходим к ожиданию контактов
                response = show_final_calculation(delivery_data, customs_data, delivery_option)
            else:
                response = get_gemini_response(user_message, "Клиент задает отвлеченный вопрос. Ответь кратко и напомни выбрать вариант доставки: 1 или 2.")
            
            chat_history.append(f"Ассистент: {response}")
            session['chat_history'] = chat_history
            return jsonify({"response": response})

        # 2. Ожидание контактов
        if session.get('waiting_for_contacts'):
            name, phone = extract_contact_info(user_message)
            if name and phone:
                session['waiting_for_contacts'] = False
                app_details = (
                    f"Тип: {delivery_data.get('delivery_type', 'N/A')}\n"
                    f"Вес: {delivery_data.get('weight')} кг\n"
                    f"Объем: {delivery_data.get('volume'):.2f} м³\n"
                    f"Товар: {delivery_data.get('product_type')}\n"
                    f"Город: {delivery_data.get('city')}\n"
                    f"Доставка: {delivery_data.get('delivery_option')}\n"
                    f"Имя: {name}\n"
                    f"Телефон: {phone}\n"
                )
                if delivery_data.get('delivery_type') == 'INVOICE':
                    app_details += (f"Стоимость инвойса: {customs_data.get('invoice_value')} USD\n"
                                    f"Код ТНВЭД: {customs_data.get('tnved_code', 'не указан')}\n")
                save_application(app_details)
                response = f"🤖 ✅ **Заявка оформлена!**\n\n{name}, мы свяжемся с вами по телефону {phone} в ближайшее время.\n\n🔄 Для нового расчета напишите «старт»"
            else:
                response = "Не удалось распознать контакты. Пожалуйста, введите **имя и телефон**."
            
            chat_history.append(f"Ассистент: {response}")
            session['chat_history'] = chat_history
            return jsonify({"response": response})

        # --- ОСНОВНАЯ ЛОГИКА СБОРА ДАННЫХ ---
        
        # Обновляем данные из нового сообщения
        extracted_delivery = extract_delivery_info(user_message)
        delivery_data.update(extracted_delivery)
        
        extracted_customs_val, extracted_tnved = extract_customs_info(user_message)
        if extracted_customs_val: customs_data['invoice_value'] = extracted_customs_val
        if extracted_tnved: customs_data['tnved_code'] = extracted_tnved
        
        # Определяем тип доставки, если еще не определен
        if not delivery_data.get('delivery_type'):
            delivery_data['delivery_type'] = 'INVOICE' if customs_data.get('invoice_value') or 'инвойс' in user_message.lower() else 'CARGO'
        
        # Сохраняем все в сессию
        session['delivery_data'] = delivery_data
        session['customs_data'] = customs_data
        
        # Проверяем, все ли данные собраны
        missing_data = get_missing_data(delivery_data, customs_data, delivery_data['delivery_type'])
        
        if missing_data:
            response = f"Для расчета укажите: **{', '.join(missing_data)}**"
        else:
            # Все данные есть, делаем расчет
            if delivery_data['delivery_type'] == 'CARGO':
                delivery_cost = calculate_quick_cost(delivery_data['weight'], delivery_data['volume'], delivery_data['product_type'], delivery_data['city'])
                if delivery_cost and not delivery_cost.get('error'):
                    response = get_cargo_calculation_response(delivery_data, delivery_cost)
                    session['waiting_for_delivery_choice'] = True
                else:
                    response = "Ошибка расчета. " + (delivery_cost.get('error') or "Проверьте данные.")
            
            else: # INVOICE
                tnved_code = customs_data.get('tnved_code') or get_tnved_code(delivery_data['product_type'])
                customs_data['tnved_code'] = tnved_code
                session['customs_data'] = customs_data
                response = get_customs_full_calculation(delivery_data, customs_data, tnved_code)
                session['waiting_for_delivery_choice'] = True
        
        chat_history.append(f"Ассистент: {response}")
        session['chat_history'] = chat_history
        return jsonify({"response": response})

    except Exception as e:
        logger.error(f"Критическая ошибка в /chat: {e}", exc_info=True)
        return jsonify({"response": "Произошла внутренняя ошибка. Пожалуйста, попробуйте начать заново, написав «старт»."})

@app.route('/clear', methods=['POST'])
def clear_chat():
    session.clear()
    return jsonify({"status": "success"})

if __name__ == '__main__':
    if initialize_models():
        try:
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            logger.info(f"=== PostPro Chat Bot запущен ===")
            logger.info(f"Локальный доступ: http://localhost:5000")
            logger.info(f"Сетевой доступ: http://{local_ip}:5000")
            logger.info(f"=================================")
            app.run(host='0.0.0.0', port=5000, debug=False)
        except Exception as e:
            logger.error(f"Не удалось запустить Flask сервер: {e}")
    else:
        logger.error("!!! Не удалось инициализировать модели Gemini. Запуск отменен.")
