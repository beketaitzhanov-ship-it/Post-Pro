from flask import Flask, render_template, request, jsonify, session
import os
import re
from datetime import datetime
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from dotenv import load_dotenv
import socket
import logging
from difflib import get_close_matches

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

EXCHANGE_RATE = 550 # Для демонстрации. В проде будет заменен на динамический курс

# --- ТАРИФЫ ---
def calculate_t1_rate_by_density(product_type, density):
    if product_type in ['мебель', 'стройматериалы', 'оборудование', 'посуда', 'лампы']:
        if density >= 400: return (0.80, 'kg')
        if 350 <= density < 400: return (0.90, 'kg')
        if 300 <= density < 350: return (1.00, 'kg')
        if 250 <= density < 300: return (1.10, 'kg')
        if 200 <= density < 250: return (1.20, 'kg')
        if 190 <= density < 200: return (1.30, 'kg')
        if 180 <= density < 190: return (1.40, 'kg')
        if 170 <= density < 180: return (1.50, 'kg')
        if 160 <= density < 170: return (1.60, 'kg')
        if 150 <= density < 160: return (1.70, 'kg')
        if 140 <= density < 150: return (1.80, 'kg')
        if 130 <= density < 140: return (1.90, 'kg')
        if 120 <= density < 130: return (2.00, 'kg')
        if 110 <= density < 120: return (2.10, 'kg')
        if 100 <= density < 110: return (2.20, 'kg')
        return (230, 'm3')
    if product_type == 'автозапчасти':
        if density >= 400: return (1.00, 'kg')
        if 350 <= density < 400: return (1.20, 'kg')
        if 300 <= density < 350: return (1.25, 'kg')
        if 250 <= density < 300: return (1.35, 'kg')
        if 200 <= density < 250: return (1.40, 'kg')
        if 190 <= density < 200: return (1.50, 'kg')
        if 180 <= density < 190: return (1.60, 'kg')
        if 170 <= density < 180: return (1.70, 'kg')
        if 160 <= density < 170: return (1.80, 'kg')
        if 150 <= density < 160: return (1.90, 'kg')
        if 140 <= density < 150: return (2.10, 'kg')
        if 130 <= density < 140: return (2.10, 'kg')
        if 120 <= density < 130: return (2.20, 'kg')
        if 110 <= density < 120: return (2.30, 'kg')
        if 100 <= density < 110: return (2.40, 'kg')
        return (240, 'm3')
    if product_type in ['аксессуары для телефонов', 'косметика', 'головные уборы', 'сумки']:
        if density >= 400: return (0.90, 'kg')
        if 350 <= density < 400: return (1.00, 'kg')
        if 300 <= density < 350: return (1.10, 'kg')
        if 250 <= density < 300: return (1.20, 'kg')
        if 200 <= density < 250: return (1.30, 'kg')
        if 190 <= density < 200: return (1.40, 'kg')
        if 180 <= density < 190: return (1.50, 'kg')
        if 170 <= density < 180: return (1.60, 'kg')
        if 160 <= density < 170: return (1.70, 'kg')
        if 150 <= density < 160: return (1.80, 'kg')
        if 140 <= density < 150: return (1.90, 'kg')
        if 130 <= density < 140: return (2.00, 'kg')
        if 120 <= density < 130: return (2.10, 'kg')
        if 110 <= density < 120: return (2.20, 'kg')
        if 100 <= density < 110: return (2.30, 'kg')
        return (230, 'm3')
    if product_type in ['малая техника', 'электроника', 'техника']:
        if density >= 400: return (1.40, 'kg')
        if 300 <= density < 400: return (1.50, 'kg')
        if 200 <= density < 300: return (1.60, 'kg')
        if 190 <= density < 200: return (1.70, 'kg')
        if 180 <= density < 190: return (1.80, 'kg')
        if 170 <= density < 180: return (1.90, 'kg')
        if 160 <= density < 170: return (2.00, 'kg')
        if 150 <= density < 160: return (2.10, 'kg')
        if 140 <= density < 150: return (2.20, 'kg')
        if 130 <= density < 140: return (2.30, 'kg')
        if 120 <= density < 130: return (2.40, 'kg')
        if 110 <= density < 120: return (2.50, 'kg')
        if 100 <= density < 110: return (2.60, 'kg')
        return (270, 'm3')
    if product_type in ['продукты', 'чай']:
        if density >= 300: return (1.50, 'kg')
        if 250 <= density < 300: return (1.60, 'kg')
        if 200 <= density < 250: return (1.70, 'kg')
        if 190 <= density < 200: return (1.80, 'kg')
        if 180 <= density < 190: return (1.90, 'kg')
        if 170 <= density < 180: return (2.00, 'kg')
        if 160 <= density < 170: return (2.10, 'kg')
        if 150 <= density < 160: return (2.20, 'kg')
        if 140 <= density < 150: return (2.30, 'kg')
        if 130 <= density < 140: return (2.40, 'kg')
        if 120 <= density < 130: return (2.50, 'kg')
        if 110 <= density < 120: return (2.60, 'kg')
        if 100 <= density < 110: return (2.70, 'kg')
        return (280, 'm3')
    if product_type in ['ткани', 'текстиль', 'одежда']:
        if density >= 300: return (0.80, 'kg')
        if 250 <= density < 300: return (0.90, 'kg')
        if 200 <= density < 250: return (1.00, 'kg')
        if 180 <= density < 200: return (1.10, 'kg')
        if 170 <= density < 180: return (1.20, 'kg')
        if 160 <= density < 170: return (1.30, 'kg')
        if 150 <= density < 160: return (1.40, 'kg')
        if 130 <= density < 150: return (1.50, 'kg')
        if 120 <= density < 130: return (1.60, 'kg')
        if 110 <= density < 120: return (1.70, 'kg')
        if 100 <= density < 110: return (1.80, 'kg')
        return None  # Требуется уточнение
    if product_type == 'инструменты':
        if density >= 400: return (0.75, 'kg')
        if 350 <= density < 400: return (0.80, 'kg')
        if 300 <= density < 350: return (0.90, 'kg')
        if 250 <= density < 300: return (1.00, 'kg')
        if 200 <= density < 250: return (1.10, 'kg')
        if 190 <= density < 200: return (1.20, 'kg')
        if 180 <= density < 190: return (1.30, 'kg')
        if 170 <= density < 180: return (1.40, 'kg')
        if 160 <= density < 170: return (1.50, 'kg')
        if 150 <= density < 160: return (1.60, 'kg')
        if 140 <= density < 150: return (1.70, 'kg')
        if 130 <= density < 140: return (1.80, 'kg')
        if 120 <= density < 130: return (1.90, 'kg')
        if 110 <= density < 120: return (2.00, 'kg')
        if 100 <= density < 110: return (2.10, 'kg')
        return (220, 'm3')
    if product_type in ['белье', 'постельное белье', 'полотенца', 'одеяла']:
        if density >= 180: return (1.30, 'kg')
        return None
    if product_type == 'игрушки':
        if density >= 200: return (1.50, 'kg')
        if 190 <= density < 200: return (310, 'm3')
        if 180 <= density < 190: return (300, 'm3')
        if 170 <= density < 180: return (290, 'm3')
        if 160 <= density < 170: return (280, 'm3')
        if 150 <= density < 160: return (270, 'm3')
        if 140 <= density < 150: return (260, 'm3')
        if 130 <= density < 140: return (250, 'm3')
        if 120 <= density < 130: return (240, 'm3')
        return (230, 'm3')
    if product_type in ['лекарства', 'медикаменты']:
        if density >= 300: return (2.90, 'kg')
        if 200 <= density < 300: return (3.00, 'kg')
        if 100 <= density < 200: return (3.10, 'kg')
        return (320, 'm3')
    # Тариф по умолчанию для "общие товары" и "вещи"
    if density >= 400: return (2.20, 'kg')
    if 300 <= density < 400: return (2.30, 'kg')
    if 200 <= density < 300: return (2.40, 'kg')
    if 100 <= density < 200: return (2.50, 'kg')
    return (260, 'm3')


def calculate_t2_cost(weight, zone, is_fragile=False, is_village=False):
    # Тарифы из документа Казпочты
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

CUSTOMS_RATES = {
    "одежда": 10, "электроника": 5, "косметика": 15, "техника": 5,
    "мебель": 10, "автозапчасти": 5, "общие товары": 10, "инструменты": 8,
    "ткани": 12, "посуда": 10, "продукты": 15, "лекарства": 0, "белье": 12,
    "игрушки": 5, "вещи": 10
}

CUSTOMS_FEES = {
    "оформление": 15000, "сертификат": 120000,
    "брокер": 60000, "декларация": 15000
}

GREETINGS = ["привет", "здравствуй", "здравствуйте", "салем", "сәлем", "добрый день", "добрый вечер", "доброе утро"]

# --- СИСТЕМНЫЕ ПРОМПТЫ ---
MAIN_SYSTEM_INSTRUCTION = """
Ты — умный ассистент компании PostPro. Твоя главная цель — помочь клиенту рассчитать стоимость доставки и оформить заявку.
***ВАЖНЫЕ ПРАВИЛА:***

1. **РАСЧЕТ ПО ПЛОТНОСТИ:** Всегда запрашивай вес и ОБЪЕМ (или габариты) для точного расчета.
2. **ТИПЫ ДОСТАВКИ:** КАРГО (для личных вещей) и ИНВОЙС (для коммерческих партий с растаможкой).
3. **ЛОГИКА ДИАЛОГА:** Не переспрашивай данные, которые уже получены. При выборе варианта доставки (1 или 2) сразу переходи к оформлению заявки.
4. **ОБЩИЕ ВОПРОСЫ:** Если вопрос не о доставке - отвечай как умный ИИ-помощник и мягко возвращай к теме.

Всегда будь дружелюбным и профессиональным! 😊
"""

CUSTOMS_SYSTEM_INSTRUCTION = "Ты — специалист по таможенному оформлению. Определяй код ТН ВЭД ЕАЭС для товаров. Возвращай ТОЛЬКО 10 цифр кода без пробелов и текста. Например: 9503007000"

# --- ИНИЦИАЛИЗАЦИЯ МОДЕЛЕЙ ---
main_model = None
customs_model = None

def initialize_models():
    global main_model, customs_model
    try:
        if not GEMINI_API_KEY:
            logger.error("!!! API ключ GOOGLE_API_KEY не найден")
            return False
        genai.configure(api_key=GEMINI_API_KEY)
        main_model = genai.GenerativeModel(
            model_name='gemini-1.5-flash',
            system_instruction=MAIN_SYSTEM_INSTRUCTION
        )
        customs_model = genai.GenerativeModel(
            model_name='gemini-1.5-flash',
            system_instruction=CUSTOMS_SYSTEM_INSTRUCTION
        )
        main_model.generate_content("Тест") # Проверка соединения
        logger.info(">>> Модели Gemini успешно инициализированы")
        return True
    except Exception as e:
        logger.error(f"!!! Ошибка инициализации Gemini: {e}")
        return False

# --- УМНЫЕ ФУНКЦИИ (HELPER FUNCTIONS) ---
def is_delivery_choice(message):
    return message.lower().strip() in ['1', '2', 'т1', 'т2', 't1', 't2', 'первый', 'второй', 'один', 'два']

def parse_delivery_choice(message):
    if message.lower().strip() in ['1', 'т1', 't1', 'первый', 'один']:
        return "самовывоз"
    if message.lower().strip() in ['2', 'т2', 't2', 'второй', 'два']:
        return "до двери"
    return None

def doesnt_know_tnved(message):
    return any(word in message.lower() for word in ['не знаю', 'нет кода', 'помоги', 'определи'])

def find_closest_city(city_name):
    if not city_name: return None
    matches = get_close_matches(city_name.lower(), DESTINATION_ZONES.keys(), n=1, cutoff=0.7)
    return matches[0] if matches else None

# --- ФУНКЦИИ ИЗВЛЕЧЕНИЯ ДАННЫХ (PARSERS) ---
def extract_delivery_info(text):
    weight, product_type, city, volume = None, None, None, None
    text_lower = text.lower()
    try:
        # Вес
        weight_match = re.search(r'(\d+[,.]?\d*)\s*(кг|kg|килограмм)', text_lower)
        if weight_match: weight = float(weight_match.group(1).replace(',', '.'))
        # Объем
        volume_match = re.search(r'(\d+[,.]?\d*)\s*(м³|m³|м3|m3|куб)', text_lower)
        if volume_match: volume = float(volume_match.group(1).replace(',', '.'))
        # Габариты
        dims_match = re.search(r'(\d+)\s*[хx×*]\s*(\d+)\s*[хx×*]\s*(\d+)\s*(см|cm)?', text_lower)
        if dims_match and not volume:
            l, w, h = map(int, dims_match.groups()[:3])
            volume = (l * w * h) / 1000000
        # Город
        for city_name in DESTINATION_ZONES.keys():
            if city_name in text_lower:
                city = city_name
                break
        # Тип товара
        product_keywords = {
            'одежда': ['одежда', 'штаны', 'футболки', 'куртки'], 'лекарства': ['лекарства', 'медикаменты', 'таблетки'],
            'косметика': ['косметика', 'крем', 'шампунь'], 'техника': ['техника', 'гаджет'],
            'мебель': ['мебель', 'стол', 'стул'], 'посуда': ['посуда', 'тарелки'],
            'игрушки': ['игрушки', 'куклы'], 'электроника': ['электроника', 'телефон', 'ноутбук'],
            'вещи': ['вещи', 'личные вещи'], 'общие товары': ['товар', 'груз']
        }
        for prod_type, keywords in product_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                product_type = prod_type
                break
        return weight, product_type, city, volume
    except Exception as e:
        logger.error(f"Ошибка извлечения данных о доставке: {e}")
        return None, None, None, None

def extract_customs_info(text):
    invoice_value, tnved_code = None, None
    try:
        cost_match = re.search(r'(\d+[,.]?\d*)\s*(usd|\$|доллар)', text.lower())
        if cost_match: invoice_value = float(cost_match.group(1).replace(',', '.'))
        tnved_match = re.search(r'\b(\d{10})\b', text.replace(" ", ""))
        if tnved_match: tnved_code = tnved_match.group(1)
        return invoice_value, tnved_code
    except Exception as e:
        logger.error(f"Ошибка извлечения данных для таможни: {e}")
        return None, None

def extract_contact_info(text):
    name, phone = None, None
    try:
        phone_match = re.search(r'\+?[78]?[\s-]?\(?(\d{3})\)?[\s-]?(\d{3})[\s-]?(\d{2})[\s-]?(\d{2})', text)
        if phone_match:
            phone = f"7{phone_match.group(1)}{phone_match.group(2)}{phone_match.group(3)}{phone_match.group(4)}"
        name_match = re.search(r'([а-яА-Яa-zA-Z]{3,})', text)
        if name_match:
            candidate = name_match.group(1).lower()
            if candidate not in ['привет', 'здравствуйте', 'инвойс', 'карго']:
                name = candidate.capitalize()
        return name, phone
    except Exception as e:
        logger.error(f"Ошибка извлечения контактов: {e}")
        return None, None

# --- ОСНОВНЫЕ ФУНКЦИИ РАСЧЕТА ---
def calculate_quick_cost(weight, volume, product_type, city):
    try:
        if not all([weight, volume, product_type, city]) or volume <= 0: return None
        density = weight / volume
        t1_result = calculate_t1_rate_by_density(product_type, density)
        if t1_result is None: return {'error': 'Требуется индивидуальный расчет'}
        t1_rate, unit = t1_result
        t1_cost_usd = weight * t1_rate if unit == 'kg' else volume * t1_rate
        t1_cost_kzt = t1_cost_usd * EXCHANGE_RATE
        t1_description = f"{weight:.1f} кг × {t1_rate:.2f} $/кг" if unit == 'kg' else f"{volume:.2f} м³ × {t1_rate:.0f} $/м³"
        city_lower = city.lower()
        if city_lower in ["алматы", "алмата"]:
            zone = "Алматы"
            t2_cost_kzt = 120 * weight
        else:
            zone = DESTINATION_ZONES.get(city_lower, 3)
            t2_cost_kzt = calculate_t2_cost(weight, zone)
        return {
            't1_cost': t1_cost_kzt, 't2_cost': t2_cost_kzt, 'density': density,
            't1_description': t1_description, 'zone': zone
        }
    except Exception as e:
        logger.error(f"Ошибка в calculate_quick_cost: {e}")
        return None

def calculate_customs_cost(invoice_value, product_type, weight):
    try:
        rate_percent = CUSTOMS_RATES.get(product_type.lower(), 10)
        rate = rate_percent / 100
        duty_usd = invoice_value * rate
        vat_usd = (invoice_value + duty_usd) * 0.12
        duty_kzt = duty_usd * EXCHANGE_RATE
        vat_kzt = vat_usd * EXCHANGE_RATE
        total_kzt = duty_kzt + vat_kzt + CUSTOMS_FEES['брокер'] + CUSTOMS_FEES['декларация']
        return {'total_kzt': total_kzt, 'rate_percent': rate_percent}
    except Exception as e:
        logger.error(f"Ошибка в calculate_customs_cost: {e}")
        return None

# --- ФУНКЦИИ ВЗАИМОДЕЙСТВИЯ С GEMINI ---
def get_tnved_code(product_name):
    if not customs_model: return "3926909709"
    try:
        response = customs_model.generate_content(product_name if product_name else "общие товары")
        code = response.text.strip()
        return code if re.match(r'^\d{10}$', code) else "3926909709"
    except Exception as e:
        logger.error(f"Ошибка получения кода ТН ВЭД: {e}")
        return "3926909709"

def get_gemini_response(user_message, context=""):
    if not main_model: return "Извините, сервис временно недоступен."
    try:
        prompt = f"Контекст диалога: {context}\n\nСообщение клиента: {user_message}"
        response = main_model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Ошибка генерации ответа Gemini: {e}")
        return "К сожалению, произошла ошибка. Давайте попробуем еще раз."

# --- ФУНКЦИИ ФОРМАТИРОВАНИЯ ОТВЕТОВ ---
def get_missing_data_response(delivery_data, customs_data, delivery_type):
    missing = []
    if not delivery_data.get('weight'): missing.append("вес груза (в кг)")
    if not delivery_data.get('volume'): missing.append("объем (в м³) или габариты (Д×Ш×В в см)")
    if not delivery_data.get('product_type'): missing.append("тип товара")
    if not delivery_data.get('city'): missing.append("город доставки")
    if delivery_type == 'INVOICE':
        if not customs_data.get('invoice_value'): missing.append("стоимость в USD")
    return f"📋 Для расчета, пожалуйста, укажите: **{', '.join(missing)}**"

def get_calculation_response(delivery_data, customs_data, delivery_cost, customs_cost=None):
    if delivery_data['delivery_type'] == 'CARGO':
        t1_total = delivery_cost['t1_cost'] * 1.20
        t2_total = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20
        response = (
            f"📊 **Расчет для КАРГО:**\n\n"
            f"✅ {delivery_data['weight']} кг «{delivery_data['product_type']}» в {delivery_data['city'].capitalize()}\n"
            f"✅ Объем: {delivery_data['volume']:.2f} м³ (Плотность: {delivery_cost['density']:.1f} кг/м³)\n"
            f"*{delivery_cost['t1_description']}*\n\n"
            f"--- \n"
            f"🏷️ **Выберите вариант доставки:**\n\n"
            f"**🚚 1. ДО АЛМАТЫ (самовывоз):** {t1_total:,.0f} ₸\n\n"
            f"**🏠 2. ДО ДВЕРИ (в г. {delivery_data['city'].capitalize()}):** {t2_total:,.0f} ₸\n\n"
            f"--- \n"
            f"💡 *Напишите `1` или `2`, чтобы выбрать подходящий вариант.*"
        )
    else: # INVOICE
        t1_total = delivery_cost['t1_cost'] * 1.20 + customs_cost['total_kzt']
        t2_total = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20 + customs_cost['total_kzt']
        response = (
            f"📊 **Расчет для ИНВОЙС:**\n\n"
            f"✅ {delivery_data['weight']} кг «{delivery_data['product_type']}» в {delivery_data['city'].capitalize()}\n"
            f"✅ Стоимость инвойса: {customs_data['invoice_value']:,.0f} USD\n"
            f"✅ Код ТНВЭД: {customs_data['tnved_code']}\n\n"
            f"--- \n"
            f"🏷️ **Выберите вариант доставки:**\n\n"
            f"**🚚 1. ДО АЛМАТЫ (под ключ):** {t1_total:,.0f} ₸\n\n"
            f"**🏠 2. ДО ДВЕРИ (под ключ в г. {delivery_data['city'].capitalize()}):** {t2_total:,.0f} ₸\n\n"
            f"--- \n"
            f"💡 *Напишите `1` или `2`, чтобы выбрать подходящий вариант.*"
        )
    return response

def get_final_choice_response(delivery_data, customs_data, delivery_option):
    delivery_cost = calculate_quick_cost(delivery_data['weight'], delivery_data['volume'], delivery_data['product_type'], delivery_data['city'])
    if not delivery_cost: return "❌ Ошибка итогового расчета."
    if delivery_data['delivery_type'] == 'CARGO':
        total_cost = delivery_cost['t1_cost'] * 1.20 if delivery_option == 'самовывоз' else (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20
    else: # INVOICE
        customs_cost = calculate_customs_cost(customs_data['invoice_value'], delivery_data['product_type'], delivery_data['weight'])
        if not customs_cost: return "❌ Ошибка расчета таможни."
        total_delivery = delivery_cost['t1_cost'] * 1.20 if delivery_option == 'самовывоз' else (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20
        total_cost = total_delivery + customs_cost['total_kzt']
    return (
        f"✅ **Отлично! Выбран вариант: ДОСТАВКА ДО {'ДВЕРИ' if delivery_option == 'до двери' else 'АЛМАТЫ'}**\n\n"
        f"💰 **Итоговая стоимость: {total_cost:,.0f} ₸**\n"
        f"⏱️ Срок доставки: 12-15 дней\n\n"
        f"💎 **Если вас все устраивает, давайте оформим заявку!**\n"
        f"📝 Для этого, пожалуйста, напишите ваше **имя и номер телефона**."
    )

def save_application(details):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"Новая заявка: {timestamp}\n{details}\n"
        with open("applications.txt", "a", encoding="utf-8") as f:
            f.write("="*50 + "\n" + log_entry + "="*50 + "\n\n")
        logger.info(f"Заявка сохранена: {details.splitlines()[0]}")
    except Exception as e:
        logger.error(f"Ошибка сохранения заявки: {e}")

# --- МАРШРУТЫ FLASK ---
@app.route('/', methods=['GET'])
def index():
    session.clear() # Начинаем с чистой сессии
    session.update({
        'delivery_data': {}, 'customs_data': {}, 'chat_history': [], 'state': 'initial'
    })
    if main_model is None: initialize_models()
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message: return jsonify({"response": "Пожалуйста, введите сообщение."})

        # Загрузка данных из сессии
        delivery_data = session.get('delivery_data', {})
        customs_data = session.get('customs_data', {})
        chat_history = session.get('chat_history', [])
        state = session.get('state', 'initial')

        chat_history.append(f"Клиент: {user_message}")
        logger.info(f"Сессия {session.sid} | Состояние: {state} | Сообщение: '{user_message}'")

        if user_message.lower() in ['/start', 'сброс', 'старт', 'новый расчет']:
            session.clear()
            session.update({
                'delivery_data': {}, 'customs_data': {}, 'chat_history': [], 'state': 'initial'
            })
            response = ("🚚 **Диалог сброшен. Начнем заново!**\n\n"
                        "💡 **Для расчета укажите:**\n"
                        "• Вес груза (например: `50 кг`)\n"
                        "• Объем (м³) или габариты (`120х80х60 см`)\n"
                        "• Тип товара (например: `одежда`)\n"
                        "• Город доставки в Казахстане")
            return jsonify({"response": response})

        # --- УПРАВЛЕНИЕ СОСТОЯНИЯМИ ДИАЛОГА ---

        # 1. СОСТОЯНИЕ: ОЖИДАНИЕ ВЫБОРА ВАРИАНТА ДОСТАВКИ
        if state == 'calculated':
            delivery_option = parse_delivery_choice(user_message)
            if delivery_option:
                delivery_data['delivery_option'] = delivery_option
                response = get_final_choice_response(delivery_data, customs_data, delivery_option)
                state = 'ordering'
            else:
                context = f"Бот ждет выбора варианта (1 или 2). Клиент пишет: '{user_message}'."
                response = get_gemini_response(user_message, context) + "\n\n💡 *Пожалуйста, выберите вариант, написав `1` или `2`.*"
        
        # 2. СОСТОЯНИЕ: ОЖИДАНИЕ КОНТАКТОВ ДЛЯ ОФОРМЛЕНИЯ
        elif state == 'ordering':
            name, phone = extract_contact_info(user_message)
            if name and phone:
                details = (f"Тип: {delivery_data.get('delivery_type')}\n"
                           f"Данные: {delivery_data.get('weight')} кг, {delivery_data.get('volume'):.2f} м³, {delivery_data.get('product_type')}, г. {delivery_data.get('city')}\n"
                           f"Выбор: {delivery_data.get('delivery_option')}\n"
                           f"Имя: {name}\nТелефон: {phone}")
                if delivery_data.get('delivery_type') == 'INVOICE':
                    details += f"\nИнвойс: {customs_data.get('invoice_value')} USD, ТНВЭД: {customs_data.get('tnved_code')}"
                save_application(details)
                response = f"✅ **Заявка оформлена!**\n\n{name}, наш менеджер свяжется с вами по номеру `{phone}`.\n\n🔄 *Для нового расчета напишите «старт»*"
                state = 'finished'
            else:
                context = f"Бот ждет имя и телефон. Клиент пишет: '{user_message}'."
                response = get_gemini_response(user_message, context) + "\n\n❌ **Контакты не распознаны.** Пожалуйста, введите **имя и телефон**."
        
        # 3. СОСТОЯНИЕ: СБОР ДАННЫХ И ПЕРВИЧНЫЙ РАСЧЕТ
        else:
            if user_message.lower() in GREETINGS and not any(delivery_data.values()):
                 response = ("🚚 Добро пожаловать в PostPro!\n\n"
                            "💡 **Для расчета укажите:**\n"
                            "• Вес груза\n• Объем или габариты\n• Тип товара\n• Город доставки")
                 state = 'initial'
            else:
                # Обновляем данные из сообщения
                delivery_data.update(extract_delivery_info(user_message) or {})
                customs_data.update(extract_customs_info(user_message) or {})
                # Определяем тип доставки
                if not delivery_data.get('delivery_type'):
                    delivery_data['delivery_type'] = 'INVOICE' if customs_data.get('invoice_value') or 'инвойс' in user_message.lower() else 'CARGO'
                
                # Проверяем, все ли данные собраны
                missing_data = get_missing_data(delivery_data, customs_data, delivery_data['delivery_type'])
                if missing_data:
                    response = f"📋 Для расчета, пожалуйста, укажите: **{', '.join(missing_data)}**"
                    state = 'gathering'
                else:
                    # Все данные есть - выполняем расчет
                    delivery_cost = calculate_quick_cost(delivery_data['weight'], delivery_data['volume'], delivery_data['product_type'], delivery_data['city'])
                    if not delivery_cost or delivery_cost.get('error'):
                        response = "❌ Ошибка расчета: " + (delivery_cost.get('error') or "Проверьте данные.")
                    else:
                        if delivery_data['delivery_type'] == 'INVOICE' and not customs_data.get('tnved_code'):
                            if doesnt_know_tnved(user_message):
                                customs_data['tnved_code'] = get_tnved_code(delivery_data['product_type'])
                            else:
                                response = "📋 **Укажите код ТНВЭД** (10 цифр) или напишите `не знаю`."
                                state = 'gathering' # Остаемся в состоянии сбора
                        
                        if state != 'gathering':
                            customs_cost_data = None
                            if delivery_data['delivery_type'] == 'INVOICE':
                                customs_cost_data = calculate_customs_cost(customs_data['invoice_value'], delivery_data['product_type'], delivery_data['weight'])
                                if not customs_cost_data: response = "❌ Ошибка расчета таможни."
                            
                            if not response:
                                response = get_calculation_response(delivery_data, customs_data, delivery_cost, customs_cost_data)
                                state = 'calculated'
        
        session.update({
            'delivery_data': delivery_data, 'customs_data': customs_data,
            'chat_history': chat_history, 'state': state
        })
        return jsonify({"response": response})
    except Exception as e:
        logger.error(f"Критическая ошибка в /chat: {e}", exc_info=True)
        return jsonify({"response": "Произошла внутренняя ошибка. Пожалуйста, попробуйте начать заново."})

@app.route('/clear', methods=['POST'])
def clear_chat():
    session.clear()
    return jsonify({"status": "success"})

if __name__ == '__main__':
    if initialize_models():
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        logger.info(f"=== PostPro Chat Bot (v4.0 Final) запущен ===")
        logger.info(f"Локальный доступ: http://localhost:5000")
        logger.info(f"Сетевой доступ: http://{local_ip}:5000")
        app.run(host='0.0.0.0', port=5000, debug=True)
    else:
        logger.error("!!! ЗАПУСК НЕВОЗМОЖЕН: Не удалось инициализировать модели Gemini.")
