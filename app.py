from flask import Flask, render_template, request, jsonify, session
import os
import re
from datetime import datetime
import google.generativeai as genai
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

# --- КОНСТАНТЫ ---
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

# --- ТАРИФЫ T1 ПО ПЛОТНОСТИ ---
def calculate_t1_rate_by_density(product_type, density):
    if product_type in ['мебель', 'стройматериалы', 'оборудование', 'посуда', 'лампы']:
        if density >= 400: return (0.80, 'kg')
        elif 350 <= density < 400: return (0.90, 'kg')
        elif 300 <= density < 350: return (1.00, 'kg')
        elif 250 <= density < 300: return (1.10, 'kg')
        elif 200 <= density < 250: return (1.20, 'kg')
        elif 190 <= density < 200: return (1.30, 'kg')
        elif 180 <= density < 190: return (1.40, 'kg')
        elif 170 <= density < 180: return (1.50, 'kg')
        elif 160 <= density < 170: return (1.60, 'kg')
        elif 150 <= density < 160: return (1.70, 'kg')
        elif 140 <= density < 150: return (1.80, 'kg')
        elif 130 <= density < 140: return (1.90, 'kg')
        elif 120 <= density < 130: return (2.00, 'kg')
        elif 110 <= density < 120: return (2.10, 'kg')
        elif 100 <= density < 110: return (2.20, 'kg')
        else: return (230, 'm3')
    elif product_type in ['автозапчасти']:
        if density >= 400: return (1.00, 'kg')
        elif 350 <= density < 400: return (1.20, 'kg')
        elif 300 <= density < 350: return (1.25, 'kg')
        elif 250 <= density < 300: return (1.35, 'kg')
        elif 200 <= density < 250: return (1.40, 'kg')
        elif 190 <= density < 200: return (1.50, 'kg')
        elif 180 <= density < 190: return (1.60, 'kg')
        elif 170 <= density < 180: return (1.70, 'kg')
        elif 160 <= density < 170: return (1.80, 'kg')
        elif 150 <= density < 160: return (1.90, 'kg')
        elif 140 <= density < 150: return (2.10, 'kg')
        elif 130 <= density < 140: return (2.10, 'kg')
        elif 120 <= density < 130: return (2.20, 'kg')
        elif 110 <= density < 120: return (2.30, 'kg')
        elif 100 <= density < 110: return (2.40, 'kg')
        else: return (240, 'm3')
    elif product_type in ['аксессуары для телефонов', 'косметика', 'головные уборы', 'сумки']:
        if density >= 400: return (0.90, 'kg')
        elif 350 <= density < 400: return (1.00, 'kg')
        elif 300 <= density < 350: return (1.10, 'kg')
        elif 250 <= density < 300: return (1.20, 'kg')
        elif 200 <= density < 250: return (1.30, 'kg')
        elif 190 <= density < 200: return (1.40, 'kg')
        elif 180 <= density < 190: return (1.50, 'kg')
        elif 170 <= density < 180: return (1.60, 'kg')
        elif 160 <= density < 170: return (1.70, 'kg')
        elif 150 <= density < 160: return (1.80, 'kg')
        elif 140 <= density < 150: return (1.90, 'kg')
        elif 130 <= density < 140: return (2.00, 'kg')
        elif 120 <= density < 130: return (2.10, 'kg')
        elif 110 <= density < 120: return (2.20, 'kg')
        elif 100 <= density < 110: return (2.30, 'kg')
        else: return (230, 'm3')
    elif product_type in ['малая техника', 'электроника', 'техника']:
        if density >= 400: return (1.40, 'kg')
        elif 300 <= density < 400: return (1.50, 'kg')
        elif 200 <= density < 300: return (1.60, 'kg')
        elif 190 <= density < 200: return (1.70, 'kg')
        elif 180 <= density < 190: return (1.80, 'kg')
        elif 170 <= density < 180: return (1.90, 'kg')
        elif 160 <= density < 170: return (2.00, 'kg')
        elif 150 <= density < 160: return (2.10, 'kg')
        elif 140 <= density < 150: return (2.20, 'kg')
        elif 130 <= density < 140: return (2.30, 'kg')
        elif 120 <= density < 130: return (2.40, 'kg')
        elif 110 <= density < 120: return (2.50, 'kg')
        elif 100 <= density < 110: return (2.60, 'kg')
        else: return (270, 'm3')
    elif product_type in ['продукты', 'чай']:
        if density >= 300: return (1.50, 'kg')
        elif 250 <= density < 300: return (1.60, 'kg')
        elif 200 <= density < 250: return (1.70, 'kg')
        elif 190 <= density < 200: return (1.80, 'kg')
        elif 180 <= density < 190: return (1.90, 'kg')
        elif 170 <= density < 180: return (2.00, 'kg')
        elif 160 <= density < 170: return (2.10, 'kg')
        elif 150 <= density < 160: return (2.20, 'kg')
        elif 140 <= density < 150: return (2.30, 'kg')
        elif 130 <= density < 140: return (2.40, 'kg')
        elif 120 <= density < 130: return (2.50, 'kg')
        elif 110 <= density < 120: return (2.60, 'kg')
        elif 100 <= density < 110: return (2.70, 'kg')
        else: return (280, 'm3')
    elif product_type in ['ткани', 'текстиль', 'одежда']:
        if density >= 300: return (0.80, 'kg')
        elif 250 <= density < 300: return (0.90, 'kg')
        elif 200 <= density < 250: return (1.00, 'kg')
        elif 180 <= density < 200: return (1.10, 'kg')
        elif 170 <= density < 180: return (1.20, 'kg')
        elif 160 <= density < 170: return (1.30, 'kg')
        elif 150 <= density < 160: return (1.40, 'kg')
        elif 130 <= density < 150: return (1.50, 'kg')
        elif 120 <= density < 130: return (1.60, 'kg')
        elif 110 <= density < 120: return (1.70, 'kg')
        elif 100 <= density < 110: return (1.80, 'kg')
        else: return None
    elif product_type in ['инструменты']:
        if density >= 400: return (0.75, 'kg')
        elif 350 <= density < 400: return (0.80, 'kg')
        elif 300 <= density < 350: return (0.90, 'kg')
        elif 250 <= density < 300: return (1.00, 'kg')
        elif 200 <= density < 250: return (1.10, 'kg')
        elif 190 <= density < 200: return (1.20, 'kg')
        elif 180 <= density < 190: return (1.30, 'kg')
        elif 170 <= density < 180: return (1.40, 'kg')
        elif 160 <= density < 170: return (1.50, 'kg')
        elif 150 <= density < 160: return (1.60, 'kg')
        elif 140 <= density < 150: return (1.70, 'kg')
        elif 130 <= density < 140: return (1.80, 'kg')
        elif 120 <= density < 130: return (1.90, 'kg')
        elif 110 <= density < 120: return (2.00, 'kg')
        elif 100 <= density < 110: return (2.10, 'kg')
        else: return (220, 'm3')
    elif product_type in ['белье', 'постельное белье', 'полотенца', 'одеяла']:
        if density >= 180: return (1.30, 'kg')
        else: return None
    elif product_type in ['игрушки']:
        if density >= 200: return (1.50, 'kg')
        elif 190 <= density < 200: return (310, 'm3')
        elif 180 <= density < 190: return (300, 'm3')
        elif 170 <= density < 180: return (290, 'm3')
        elif 160 <= density < 170: return (280, 'm3')
        elif 150 <= density < 160: return (270, 'm3')
        elif 140 <= density < 150: return (260, 'm3')
        elif 130 <= density < 140: return (250, 'm3')
        elif 120 <= density < 130: return (240, 'm3')
        else: return (230, 'm3')
    elif product_type in ['лекарства', 'медикаменты']:
        if density >= 300: return (2.90, 'kg')
        elif 200 <= density < 300: return (3.00, 'kg')
        elif 100 <= density < 200: return (3.10, 'kg')
        else: return (320, 'm3')
    elif product_type in ['общие товары', 'вещи']:
        if density >= 400: return (2.20, 'kg')
        elif 300 <= density < 400: return (2.30, 'kg')
        elif 200 <= density < 300: return (2.40, 'kg')
        elif 100 <= density < 200: return (2.50, 'kg')
        else: return (260, 'm3')
    else:
        if density >= 200: return (2.40, 'kg')
        else: return (250, 'm3')

# --- ТАРИФЫ Т2 (Казпочта) ---
def calculate_t2_cost(weight, zone, is_fragile=False, is_village=False):
    base_rates = {1: 4200, 2: 4400, 3: 4600, 4: 4800, 5: 5000}
    per_kg_rates = {1: 210, 2: 220, 3: 230, 4: 240, 5: 250}
    
    if weight <= 20:
        base_rate = base_rates.get(zone, 4600)
        cost = (base_rate / 20) * weight
    else:
        base_rate = base_rates.get(zone, 4600)
        per_kg = per_kg_rates.get(zone, 230)
        cost = base_rate + (weight - 20) * per_kg
    
    if is_fragile: cost *= 1.5
    if is_village: cost *= 2.0
    
    return cost

# --- КОНСТАНТЫ ДЛЯ РАСТАМОЖКИ ---
CUSTOMS_RATES = {
    "одежда": 10, "электроника": 5, "косметика": 15, "техника": 5,
    "мебель": 10, "автозапчасти": 5, "общие товары": 10, "инструменты": 8,
    "ткани": 12, "посуда": 10, "продукты": 15, "лекарства": 0, "белье": 12,
    "игрушки": 5, "вещи": 10
}

CUSTOMS_FEES = {
    "оформление": 15000, "сертификат": 120000, "происхождения": 500, 
    "брокер": 60000, "декларация": 15000
}

GREETINGS = ["привет", "здравствуй", "здравствуйте", "салем", "сәлем", "добрый день", "добрый вечер", "доброе утро"]

# --- СИСТЕМНЫЕ ПРОМПТЫ ---
MAIN_SYSTEM_INSTRUCTION = """
Ты — умный ассистент компании PostPro. Твоя главная цель — помочь клиенту рассчитать стоимость доставки и оформить заявку.
"""

# --- ИНИЦИАЛИЗАЦИЯ МОДЕЛЕЙ ---
main_model = None
customs_model = None

def initialize_models():
    global main_model, customs_model
    try:
        if not GEMINI_API_KEY:
            logger.error("!!! API ключ не найден")
            return False
            
        genai.configure(api_key=GEMINI_API_KEY)
        main_model = genai.GenerativeModel(
            model_name='gemini-1.5-flash',
            system_instruction=MAIN_SYSTEM_INSTRUCTION
        )
        customs_model = genai.GenerativeModel(
            model_name='gemini-1.5-flash',
            system_instruction="Ты — специалист по таможенному оформлению. Определяй код ТН ВЭД ЕАЭС для товаров. Возвращай ТОЛЬКО код в формате XXXXX XXX X"
        )
        
        logger.info(">>> Модели Gemini успешно инициализированы")
        return True
    except Exception as e:
        logger.error(f"!!! Ошибка инициализации Gemini: {e}")
        return False

# --- ФУНКЦИИ ИЗВЛЕЧЕНИЯ ДАННЫХ ---
def extract_delivery_info(text):
    weight, product_type, city, volume = None, None, None, None
    
    try:
        # Поиск веса
        weight_patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:кг|kg|килограмм|кило)',
        ]
        for pattern in weight_patterns:
            match = re.search(pattern, text.lower())
            if match:
                weight = float(match.group(1))
                break
        
        # Поиск объема
        volume_patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:м³|m³|м3|m3|куб|куб\.?м)',
        ]
        for pattern in volume_patterns:
            match = re.search(pattern, text.lower())
            if match:
                volume = float(match.group(1))
                break
        
        # Поиск габаритов
        dimensions_pattern = r'(\d+)\s*[хx×]\s*(\d+)\s*[хx×]\s*(\d+)\s*(?:см|cm)'
        dimensions_match = re.search(dimensions_pattern, text.lower())
        if dimensions_match:
            length = int(dimensions_match.group(1))
            width = int(dimensions_match.group(2))
            height = int(dimensions_match.group(3))
            volume = (length * width * height) / 1000000
        
        # Поиск города
        text_lower = text.lower()
        for city_name in DESTINATION_ZONES:
            if city_name in text_lower:
                city = city_name
                break
        
        # Поиск типа товара
        product_keywords = {
            'одежда': ['одежда', 'адежда', 'одежд', 'штаны', 'футболки', 'куртки'],
            'лекарства': ['лекарства', 'лекарсива', 'медикаменты', 'таблетки'],
            'косметика': ['косметика', 'крем', 'шампунь', 'макияж'],
            'техника': ['техника', 'телефон', 'ноутбук', 'гаджет'],
            'мебель': ['мебель', 'стол', 'стул', 'кровать'],
            'посуда': ['посуда', 'тарелки', 'чашки'],
            'общие товары': ['товары', 'товар', 'разное'],
            'игрушки': ['игрушки', 'игрушка', 'куклы'],
            'электроника': ['электроника', 'телефон', 'ноутбук'],
            'вещи': ['вещи', 'вещь', 'личные вещи']
        }
        
        found_type = None
        for prod_type, keywords in product_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                found_type = prod_type
                break
        
        product_type = found_type if found_type else "общие товары"
            
        return weight, product_type, city, volume
        
    except Exception as e:
        logger.error(f"Ошибка извлечения данных: {e}")
        return None, None, None, None

def extract_customs_info(text):
    try:
        invoice_value = None
        cost_patterns = [
            r'стоимос\w*\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(?:usd|\$|доллар)',
            r'(\d+(?:\.\d+)?)\s*(?:usd|\$|доллар)',
        ]
        for pattern in cost_patterns:
            match = re.search(pattern, text.lower())
            if match:
                value = float(match.group(1))
                if value < 100000:
                    invoice_value = value
                    break
        
        tnved_code = None
        tnved_match = re.search(r'\bкод\s*[:\-]?\s*(\d{4,10}(?:\s?\d{2,4}){0,3})', text.lower())
        if tnved_match:
            tnved_code = re.sub(r'\s+', '', tnved_match.group(1))
        
        return invoice_value, tnved_code
        
    except Exception as e:
        logger.error(f"Ошибка извлечения данных растаможки: {e}")
        return None, None

def extract_contact_info(text):
    name, phone = None, None
    clean_text = re.sub(r'\s+', ' ', text.strip()).lower()
    
    name_match = re.search(r'^([а-яa-z]{2,})', clean_text)
    if name_match:
        name = name_match.group(1).capitalize()
    
    phone_patterns = [
        r'(\d{10,11})',
        r'(\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})',
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
    
    return name, phone

# --- ОСНОВНЫЕ ФУНКЦИИ РАСЧЕТА ---
def calculate_quick_cost(weight, volume, product_type, city):
    try:
        if volume is None or volume <= 0:
            return None

        density = weight / volume
        t1_result = calculate_t1_rate_by_density(product_type, density)
        if t1_result is None:
            return None
        
        t1_rate, unit = t1_result
        
        if unit == 'kg':
            t1_cost_usd = weight * t1_rate
        else:
            t1_cost_usd = volume * t1_rate
        
        t1_cost_kzt = t1_cost_usd * EXCHANGE_RATE
        
        city_lower = city.lower()
        if city_lower == "алматы" or city_lower == "алмата":
            t2_cost_kzt = 120 * weight
        else:
            zone = DESTINATION_ZONES.get(city_lower, 3)
            t2_cost_kzt = calculate_t2_cost(weight, zone)
        
        return {
            't1_cost': t1_cost_kzt,
            't2_cost': t2_cost_kzt,
            'density': density
        }
        
    except Exception as e:
        logger.error(f"Ошибка расчета: {e}")
        return None

def calculate_customs_cost(invoice_value, product_type, weight):
    try:
        product_type_lower = product_type.lower()
        customs_rate = CUSTOMS_RATES.get(product_type_lower, 10)
        
        duty_usd = invoice_value * (customs_rate / 100)
        vat_base = invoice_value + duty_usd
        vat_usd = vat_base * 0.12
        
        duty_kzt = duty_usd * EXCHANGE_RATE
        vat_kzt = vat_usd * EXCHANGE_RATE
        
        total_kzt = duty_kzt + vat_kzt + CUSTOMS_FEES['брокер'] + CUSTOMS_FEES['декларация']
        
        return {
            'duty_usd': duty_usd,
            'vat_usd': vat_usd,
            'duty_kzt': duty_kzt,
            'vat_kzt': vat_kzt,
            'total_kzt': total_kzt,
            'customs_rate': customs_rate
        }
    except Exception as e:
        logger.error(f"Ошибка расчета растаможки: {e}")
        return None

def get_customs_detailed_calculation(invoice_value, product_type, weight, tnved_code):
    """Детальный расчет таможенных платежей для ИНВОЙС"""
    try:
        customs_cost = calculate_customs_cost(invoice_value, product_type, weight)
        if not customs_cost:
            return "Ошибка расчета таможенных платежей"
        
        response = (
            f"📋 **ДЕТАЛЬНЫЙ РАСЧЕТ ТАМОЖЕННЫХ ПЛАТЕЖЕЙ:**\n\n"
            f"✅ Таможенная стоимость: {invoice_value} USD\n"
            f"✅ Код ТН ВЭД: {tnved_code}\n"
            f"✅ Ставка пошлины: {customs_cost['customs_rate']}%\n\n"
            f"💸 **Таможенные платежи:**\n"
            f"• Пошлина: {customs_cost['duty_usd']:.2f} USD ({customs_cost['duty_kzt']:,.0f} ₸)\n"
            f"• НДС: {customs_cost['vat_usd']:.2f} USD ({customs_cost['vat_kzt']:,.0f} ₸)\n"
            f"• Услуги брокера: {CUSTOMS_FEES['брокер']:,} ₸\n"
            f"• Подача декларации: {CUSTOMS_FEES['декларация']:,} ₸\n"
            f"• Сертификат соответствия: {CUSTOMS_FEES['сертификат']:,} ₸\n\n"
            f"💰 **ИТОГО таможенные платежи: {customs_cost['total_kzt'] + CUSTOMS_FEES['сертификат']:,.0f} ₸**\n"
        )
            
        return response
        
    except Exception as e:
        logger.error(f"Ошибка детального расчета: {e}")
        return "Ошибка расчета таможенных платежей"

def get_tnved_code(product_name):
    if not customs_model:
        return "6307 90 980 0"
    try:
        product_name = product_name if product_name else "общие товары"
        prompt = f"Определи код ТН ВЭД ЕАЭС для товара: '{product_name}'. Верни ТОЛЬКО код"
        response = customs_model.generate_content(prompt)
        code = response.text.strip()
        if re.match(r'^\d{4,10}[\s\d]*$', code):
            return code
        else:
            return "6307 90 980 0"
    except Exception as e:
        logger.error(f"Ошибка получения кода ТН ВЭД: {e}")
        return "6307 90 980 0"

def get_gemini_response(user_message, context=""):
    if not main_model:
        return "Сервис временно недоступен"
    try:
        prompt = f"Контекст: {context}\n\nСообщение пользователя: {user_message}"
        response = main_model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Ошибка Gemini: {e}")
        return "Извините, произошла ошибка при обработке запроса"

# --- МАРШРУТЫ FLASK ---
@app.route('/', methods=['GET'])
def index():
    if 'delivery_data' not in session:
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None, 'volume': None, 'delivery_type': None, 'delivery_option': None}
    if 'customs_data' not in session:
        session['customs_data'] = {'invoice_value': None, 'tnved_code': None}
    if 'chat_history' not in session:
        session['chat_history'] = []
    
    if main_model is None:
        initialize_models()
    
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message:
            return jsonify({"response": "Пожалуйста, введите сообщение."})
        
        delivery_data = session.get('delivery_data', {})
        customs_data = session.get('customs_data', {})
        chat_history = session.get('chat_history', [])
        
        chat_history.append(f"Клиент: {user_message}")
        
        if main_model is None:
            if not initialize_models():
                return jsonify({"response": "🚚 Добро пожаловать в PostPro! Сервис временно недоступен, попробуйте позже."})
        
        response = None
        
        # Сброс по команде
        if user_message.lower() in ['/start', 'старт']:
            session.clear()
            session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None, 'volume': None, 'delivery_type': None, 'delivery_option': None}
            session['customs_data'] = {'invoice_value': None, 'tnved_code': None}
            session['chat_history'] = []
            response = "🚚 Добро пожаловать в PostPro!\n\nЯ помогу вам рассчитать стоимость доставки из Китая в Казахстан."
        
        # Приветствия
        elif user_message.lower() in GREETINGS:
            response = "🚚 Здравствуйте! Я PostPro бот - ваш помощник в доставке из Китая! 😊"
        
        # Основная логика обработки запросов на расчет
        if not response:
            weight, product_type, city, volume = extract_delivery_info(user_message)
            invoice_value, tnved_code = extract_customs_info(user_message)
            
            # Обновление данных
            if weight: delivery_data['weight'] = weight
            if product_type: delivery_data['product_type'] = product_type
            if city: delivery_data['city'] = city
            if volume: delivery_data['volume'] = volume
            if invoice_value: customs_data['invoice_value'] = invoice_value
            if tnved_code: customs_data['tnved_code'] = tnved_code
            
            # Определение типа доставки
            if not delivery_data['delivery_type']:
                if customs_data['invoice_value'] or 'инвойс' in user_message.lower():
                    delivery_data['delivery_type'] = 'INVOICE'
                else:
                    delivery_data['delivery_type'] = 'CARGO'
            
            session['delivery_data'] = delivery_data
            session['customs_data'] = customs_data
            
            # Проверка наличия данных
            has_basic_data = all([delivery_data['weight'], delivery_data['product_type'], delivery_data['city'], delivery_data['volume']])
            
            if has_basic_data:
                if delivery_data['delivery_type'] == 'CARGO':
                    delivery_cost = calculate_quick_cost(
                        delivery_data['weight'], 
                        delivery_data['volume'],
                        delivery_data['product_type'], 
                        delivery_data['city']
                    )
                    
                    if delivery_cost:
                        response = (
                            f"📊 **Расчет стоимости доставки КАРГО:**\n\n"
                            f"✅ {delivery_data['weight']} кг {delivery_data['product_type']} в {delivery_data['city'].capitalize()}\n"
                            f"✅ Объем: {delivery_data['volume']} м³ (плотность: {delivery_cost['density']:.1f} кг/м³)\n\n"
                            f"**Стоимость доставки:**\n"
                            f"• Т1 (Китай-Алматы): {delivery_cost['t1_cost']:,.0f} ₸\n"
                            f"• Т2 (Алматы-{delivery_data['city'].capitalize()}): {delivery_cost['t2_cost']:,.0f} ₸\n"
                            f"• Сервисный сбор (20%): {(delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 0.20:,.0f} ₸\n\n"
                            f"💰 **ИТОГО: {(delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20:,.0f} ₸**\n\n"
                            f"💎 Хотите оставить заявку?"
                        )
                    else:
                        response = "❌ Не удалось рассчитать стоимость. Проверьте введенные данные."
                
                else:  # INVOICE
                    if not customs_data['invoice_value']:
                        response = "Для расчета ИНВОЙСА укажите стоимость товаров в USD (например: 1500 USD)"
                    elif not customs_data.get('tnved_code'):
                        if 'не знаю' in user_message.lower():
                            tnved_code = get_tnved_code(delivery_data['product_type'])
                            customs_data['tnved_code'] = tnved_code
                            session['customs_data'] = customs_data
                            response = f"🔍 Определяю код ТНВЭД...\n✅ Найден код: {tnved_code}\n\n📊 Продолжаем расчет..."
                        else:
                            response = "📋 Укажите код ТНВЭД или напишите \"не знаю\""
                    else:
                        # Расчет инвойса с ДЕТАЛЬНОЙ РАСПИСКОЙ
                        delivery_cost = calculate_quick_cost(
                            delivery_data['weight'], 
                            delivery_data['volume'],
                            delivery_data['product_type'], 
                            delivery_data['city']
                        )
                        customs_cost = calculate_customs_cost(
                            customs_data['invoice_value'],
                            delivery_data['product_type'],
                            delivery_data['weight']
                        )
                        
                        if delivery_cost and customs_cost:
                            # Детальный расчет таможенных платежей
                            customs_details = get_customs_detailed_calculation(
                                customs_data['invoice_value'],
                                delivery_data['product_type'],
                                delivery_data['weight'],
                                customs_data['tnved_code']
                            )
                            
                            total_customs = customs_cost['total_kzt'] + CUSTOMS_FEES['сертификат']
                            total_delivery = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20
                            total_invoice = total_delivery + total_customs
                            
                            response = (
                                f"{customs_details}\n\n"
                                f"📊 **РАСЧЕТ ДОСТАВКИ ИНВОЙС:**\n\n"
                                f"✅ {delivery_data['weight']} кг {delivery_data['product_type']} в {delivery_data['city'].capitalize()}\n"
                                f"✅ Объем: {delivery_data['volume']} м³\n\n"
                                f"**Стоимость доставки:**\n"
                                f"• Т1 (Китай-Алматы): {delivery_cost['t1_cost']:,.0f} ₸\n"
                                f"• Т2 (Алматы-{delivery_data['city'].capitalize()}): {delivery_cost['t2_cost']:,.0f} ₸\n"
                                f"• Сервисный сбор (20%): {(delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 0.20:,.0f} ₸\n"
                                f"• ИТОГО доставка: {total_delivery:,.0f} ₸\n\n"
                                f"💰 **ОБЩАЯ СТОИМОСТЬ ИНВОЙС: {total_invoice:,.0f} ₸**\n\n"
                                f"💎 Хотите оставить заявку?"
                            )
                        else:
                            response = "❌ Ошибка расчета. Проверьте данные."
            else:
                # Если не хватает данных для расчета - запрашиваем их
                missing = []
                if not delivery_data['weight']: missing.append("вес")
                if not delivery_data['product_type']: missing.append("тип товара")
                if not delivery_data['city']: missing.append("город")
                if not delivery_data['volume']: missing.append("объем")
                
                if missing:
                    response = f"📋 Для расчета укажите: {', '.join(missing)}\n\n💡 Пример: \"50 кг одежды в Астану, объем 0.5 м³\""
        
        # Если не нашли ответ в бизнес-логике - обращаемся к Gemini
        if not response:
            context = f"История: {chat_history[-3:] if len(chat_history) > 3 else chat_history}"
            response = get_gemini_response(user_message, context)
        
        chat_history.append(f"Ассистент: {response}")
        session['chat_history'] = chat_history
        
        return jsonify({"response": response})
        
    except Exception as e:
        logger.error(f"Ошибка в /chat: {e}")
        return jsonify({"response": "Произошла ошибка. Пожалуйста, попробуйте еще раз."})

if __name__ == '__main__':
    if initialize_models():
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        logger.info(f"=== PostPro Chat Bot запущен ===")
        logger.info(f"Локальный доступ: http://localhost:5000")
        logger.info(f"Сетевой доступ: http://{local_ip}:5000")
        app.run(host='0.0.0.0', port=5000, debug=True)
    else:
        logger.error("!!! Не удалось инициализировать модели Gemini")
