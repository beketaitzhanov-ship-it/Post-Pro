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

# ТАРИФЫ
T1_RATES = {  # Китай → Алматы (USD/кг)
    "ткани": 1.70, "одежда": 1.70, "инструменты": 2.10, "общие товары": 2.40, "мебель": 2.10, 
    "косметика": 2.30, "автозапчасти": 2.40, "малая техника": 2.50, "продукты": 2.70, 
    "белье": 2.80, "лекарства": 2.90, "лекарсива": 2.90, "медикаменты": 2.90, "посуда": 2.20,
    "игрушки": 2.30, "электроника": 2.60, "техника": 2.60
}

T2_RATES = {  # Алматы → город назначения (тенге/кг)
    "алматы": 120,     # Доставка по городу Алматы
    1: 150,            # Зона 1 (Алматинская область)
    2: 200,            # Зона 2 (Южный Казахстан)
    3: 250,            # Зона 3 (Центральный и Северный Казахстан)
    4: 350,            # Зона 4 (Западный Казахстан)
    5: 450             # Зона 5 (Прикаспийский регион)
}

# --- КОНСТАНТЫ ДЛЯ РАСТАМОЖКИ ---
CUSTOMS_RATES = {
    "одежда": 10, "электроника": 5, "косметика": 15, "техника": 5,
    "мебель": 10, "автозапчасти": 5, "общие товары": 10, "инструменты": 8,
    "ткани": 12, "посуда": 10, "продукты": 15, "лекарства": 0, "белье": 12,
    "игрушки": 5
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

1. **СКЛАДЫ В КИТАЕ:** У нас только 2 склада - ИУ и Гуанчжоу.

2. **ТАРИФЫ:**
   - Т1: Доставка из Китая до Алматы (только до склада, самовывоз)
   - Т2: Доставка до двери в ЛЮБОМ городе Казахстана, включая доставку по Алматы

3. **ОПЛАТА:**
   - Пост-оплата: клиент платит при получении груза
   - Форматы оплаты: безналичный расчет, наличные, Kaspi, Halyk, Freedom Bank

4. **ЛОГИКА ДИАЛОГА:**
   - Если клиент выбирает "1" или "2" - это выбор варианта доставки
   - Не переспрашивай данные которые уже получены
   - При выборе варианта доставки сразу переходи к оформлению заявки

5. **ОБЩИЕ ВОПРОСЫ:**
   - Если вопрос не о доставке - отвечай как умный ИИ-помощник
   - Поддержи любой диалог, не отказывай в ответах
   - Мягко возвращай к теме доставки

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
- "косметика" → "3304 99 000 0"
"""

# --- DEBUG ФУНКЦИЯ ---
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
                model_name='models/gemini-2.0-flash',
                system_instruction=MAIN_SYSTEM_INSTRUCTION
            )
            customs_model = genai.GenerativeModel(
                model_name='models/gemini-2.0-flash', 
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

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def is_delivery_choice(message):
    """Определяет, является ли сообщение выбором доставки"""
    message_lower = message.lower().strip()
    
    choices = [
        # Цифры
        '1', '2',
        # Кириллица (маленькие/большие)
        'т1', 'т2', 't1', 't2',
        # Слова
        'первый', 'второй', 'один', 'два',
        # Варианты с пробелами/дефисами
        'т 1', 'т 2', 't 1', 't 2', 'т-1', 'т-2', 't-1', 't-2'
    ]
    
    return message_lower in choices

def parse_delivery_choice(message):
    """Преобразует любой вариант выбора в стандартный формат"""
    message_lower = message.lower().strip()
    
    # Самовывоз (Т1)
    if message_lower in ['1', 'т1', 't1', 'первый', 'один', 'т 1', 't 1', 'т-1', 't-1']:
        return "самовывоз"
    
    # До двери (Т2)  
    elif message_lower in ['2', 'т2', 't2', 'второй', 'два', 'т 2', 't 2', 'т-2', 't-2']:
        return "до двери"
    
    else:
        return None

def doesnt_know_tnved(message):
    """Определяет, что клиент не знает код ТНВЭД"""
    patterns = [
        'не знаю', 'не знаю код', 'нет кода', 'не помню', 
        'подскажите', 'подскажи', 'что это код', 'какой код',
        'помогите', 'помоги', 'не уверен', 'затрудняюсь',
        'нет', 'не знаем', 'не помним'
    ]
    return any(pattern in message.lower() for pattern in patterns)

def get_missing_data(delivery_data, customs_data, delivery_type):
    """Определяет какие данные отсутствуют"""
    missing = []
    if not delivery_data.get('weight'): 
        missing.append("вес груза")
    if not delivery_data.get('product_type'): 
        missing.append("тип товара")
    if not delivery_data.get('city'): 
        missing.append("город доставки")
    if delivery_type == 'INVOICE' and not customs_data.get('invoice_value'): 
        missing.append("стоимость в USD")
    if delivery_type == 'INVOICE' and not customs_data.get('tnved_code'): 
        missing.append("код ТНВЭД")
    return missing

# --- ФУНКЦИИ РАСЧЕТА ДОСТАВКИ ---
def calculate_quick_cost(weight: float, product_type: str, city: str):
    """Быстрый расчет стоимости"""
    try:
        # Т1: Китай → Алматы
        product_type_lower = product_type.lower()
        t1_rate = T1_RATES.get(product_type_lower, 2.40)
        t1_cost_usd = weight * t1_rate
        t1_cost_kzt = t1_cost_usd * EXCHANGE_RATE
        
        # Т2: Определяем тариф для города
        city_lower = city.lower()
        if city_lower == "алматы" or city_lower == "алмата":
            t2_rate = T2_RATES["алматы"]
            zone = "алматы"
        else:
            zone = DESTINATION_ZONES.get(city_lower, 3)
            t2_rate = T2_RATES.get(zone, 250)
        
        t2_cost_kzt = weight * t2_rate
        
        # Итоговая стоимость (Т1 + Т2 + 20% комиссия)
        total_cost = (t1_cost_kzt + t2_cost_kzt) * 1.20
        
        return {
            't1_cost': t1_cost_kzt,
            't2_cost': t2_cost_kzt, 
            'total': total_cost,
            'zone': zone,
            't2_rate': t2_rate,
            't1_rate': t1_rate
        }
    except Exception as e:
        logger.error(f"Ошибка расчета: {e}")
        return None

def calculate_detailed_cost(weight: float, product_type: str, city: str, delivery_type: str = "до двери"):
    """Детальный расчет с разбивкой"""
    quick_cost = calculate_quick_cost(weight, product_type, city)
    if not quick_cost:
        return "Ошибка расчета"
    
    t1_cost = quick_cost['t1_cost']
    t2_cost = quick_cost['t2_cost'] 
    t1_rate = quick_cost['t1_rate']
    t2_rate = quick_cost['t2_rate']
    zone = quick_cost['zone']
    
    # Определяем стоимость в зависимости от типа доставки
    if delivery_type == "самовывоз":
        total = t1_cost * 1.20
        delivery_text = f"💰 ИТОГО с самовывозом из Алматы: ≈ {total:.0f} тенге"
        comparison_text = f"💡 Если доставка до двери: {(t1_cost + t2_cost) * 1.20:.0f} тенге"
    else:
        total = (t1_cost + t2_cost) * 1.20
        delivery_text = f"💰 ИТОГО с доставкой до двери: ≈ {total:.0f} тенге"
        comparison_text = f"💡 Если самовывоз из Алматы: {t1_cost * 1.20:.0f} тенге"
    
    city_name = city.capitalize()
    
    response = (
        f"📊 Детальный расчет для {weight} кг «{product_type}» в г. {city_name}:\n\n"
        f"**Т1: Доставка из Китая до Алматы**\n"
        f"• До склада в Алматы (самовывоз)\n"
        f"• ${t1_rate}/кг × {weight} кг = ${weight * t1_rate:.2f} USD\n"
        f"• По курсу {EXCHANGE_RATE} тенге/$ = {t1_cost:.0f} тенге\n\n"
    )
    
    if delivery_type == "до двери":
        if zone == "алматы":
            t2_explanation = f"• Доставка по городу Алматы до вашего адреса"
            zone_text = "город Алматы"
        else:
            t2_explanation = f"• Доставка до вашего адреса в {city_name}"
            zone_text = f"Зона {zone}"
        
        response += (
            f"**Т2: Доставка до двери ({zone_text})**\n"
            f"{t2_explanation}\n"
            f"• {t2_rate} тенге/кг × {weight} кг = {t2_cost:.0f} тенге\n\n"
        )
    
    response += (
        f"**Комиссия компании (20%):**\n"
        f"• {t1_cost:.0f} тенге × 20% = {t1_cost * 0.20:.0f} тенге\n"
    )
    
    if delivery_type == "до двери":
        response += f"• {t2_cost:.0f} тенге × 20% = {t2_cost * 0.20:.0f} тенге\n\n"
    else:
        response += "\n"
    
    response += (
        f"------------------------------------\n"
        f"{delivery_text}\n\n"
        f"{comparison_text}\n\n"
        f"💡 Страхование: дополнительно 1% от стоимости груза\n"
        f"💳 Оплата: пост-оплата при получении\n\n"
        f"🏷️ Выберите вариант доставки:\n"
        f"🚚 1 - Самовывоз из Алматы (только Т1)\n"
        f"🏠 2 - Доставка до двери (Т1 + Т2)\n\n"
        f"Напишите '1' или '2' чтобы продолжить!"
    )
    
    return response

# --- ФУНКЦИИ РАСЧЕТА РАСТАМОЖКИ ---
def calculate_customs_cost(invoice_value: float, product_type: str, weight: float = None, has_certificate: bool = False, needs_certificate: bool = False):
    """Расчет таможенных платежей"""
    try:
        # Пошлина
        customs_rate = CUSTOMS_RATES.get(product_type.lower(), 10) / 100
        duty_usd = invoice_value * customs_rate
        duty_kzt = duty_usd * EXCHANGE_RATE
        
        # НДС (12% от: стоимость + пошлина)
        vat_base_usd = invoice_value + duty_usd
        vat_kzt = vat_base_usd * 0.12 * EXCHANGE_RATE
        
        # Сборы
        customs_fee = CUSTOMS_FEES["оформление"]
        certificate_fee = CUSTOMS_FEES["сертификат"] if needs_certificate else 0
        origin_cert_fee = CUSTOMS_FEES["происхождения"] * EXCHANGE_RATE if has_certificate else 0
        broker_fee = CUSTOMS_FEES["брокер"]
        declaration_fee = CUSTOMS_FEES["декларация"]
        
        total_customs_kzt = duty_kzt + vat_kzt + customs_fee + certificate_fee + origin_cert_fee + broker_fee + declaration_fee
        
        return {
            'duty_usd': duty_usd,
            'duty_kzt': duty_kzt,
            'vat_kzt': vat_kzt,
            'customs_fee': customs_fee,
            'certificate_fee': certificate_fee,
            'origin_cert_fee': origin_cert_fee,
            'broker_fee': broker_fee,
            'declaration_fee': declaration_fee,
            'total_kzt': total_customs_kzt,
            'total_usd': total_customs_kzt / EXCHANGE_RATE
        }
    except Exception as e:
        logger.error(f"Ошибка расчета растаможки: {e}")
        return None

def get_tnved_code(product_name):
    """Получение кода ТН ВЭД через Gemini"""
    if not customs_model:
        return "Требуется уточнение"
    
    try:
        prompt = f"Определи код ТН ВЭД ЕАЭС для товара: '{product_name}'. Верни ТОЛЬКО код в формате XXXXX XXX X без каких-либо пояснений, текста или точек. Только цифры и пробелы."
        response = customs_model.generate_content(prompt)
        code = response.text.strip()
        
        # Проверяем, что ответ похож на код ТН ВЭД
        if re.match(r'^\d{4,10}[\s\d]*$', code):
            return code
        else:
            return "9503 00 700 0"  # Код по умолчанию для игрушек
    except Exception as e:
        logger.error(f"Ошибка получения кода ТН ВЭД: {e}")
        return "9503 00 700 0"  # Код по умолчанию для игрушек

def check_certification_requirements(product_name):
    """Проверка требований к сертификации через Gemini"""
    if not customs_model:
        return False
    
    try:
        prompt = f"Нужен ли сертификат соответствия ТР ТС для товара: '{product_name}'? Ответь только 'ДА' или 'НЕТ' без пояснений."
        response = customs_model.generate_content(prompt)
        return "ДА" in response.text.upper()
    except Exception as e:
        logger.error(f"Ошибка проверки сертификации: {e}")
        return False

def get_customs_full_calculation(weight: float, product_type: str, city: str, invoice_value: float, tnved_code: str = None):
    """Полный расчет с доставкой и растаможкой"""
    try:
        # Расчет доставки
        delivery_cost = calculate_quick_cost(weight, product_type, city)
        if not delivery_cost:
            return "Ошибка расчета доставки"
        
        # Расчет растаможки
        needs_certification = check_certification_requirements(product_type)
        customs_cost = calculate_customs_cost(invoice_value, product_type, weight, False, needs_certification)
        if not customs_cost:
            return "Ошибка расчета растаможки"
        
        if not tnved_code:
            tnved_code = get_tnved_code(product_type)
        
        # Расчет вариантов доставки
        t1_total = delivery_cost['t1_cost'] * 1.20 + customs_cost['total_kzt']
        t2_total = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20 + customs_cost['total_kzt']
        
        response = (
            f"📊 Расчет для ИНВОЙС:\n\n"
            f"✅ Товар: {weight} кг {product_type} в {city.capitalize()}\n"
            f"✅ Таможенная стоимость: {invoice_value} USD\n"
            f"✅ Код ТНВЭД: {tnved_code}\n\n"
            
            f"🏷️ Выберите вариант доставки:\n\n"
            
            f"🚚 ВАРИАНТ 1: ДОСТАВКА ДО АЛМАТЫ (Т1)\n"
            f"• Доставка до склада в Алматы (самовывоз)\n"
            f"• Таможенное оформление включено\n"
            f"• Услуги брокера: {CUSTOMS_FEES['брокер']:,} ₸\n"
            f"• Подача декларации: {CUSTOMS_FEES['декларация']:,} ₸\n"
            f"📦 Стоимость доставки: {delivery_cost['t1_cost'] * 1.20:.0f} ₸\n"
            f"💰 ОБЩАЯ СТОИМОСТЬ: {t1_total:,.0f} ₸\n\n"
            
            f"🏠 ВАРИАНТ 2: ДОСТАВКА ДО ДВЕРИ (Т1+Т2)\n"
            f"• Доставка до вашего адреса в {city.capitalize()}\n"
            f"• Таможенное оформление включено\n"
            f"• Услуги брокера: {CUSTOMS_FEES['брокер']:,} ₸\n"
            f"• Подача декларации: {CUSTOMS_FEES['декларация']:,} ₸\n"
            f"📦 Стоимость доставки: {(delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20:.0f} ₸\n"
            f"💰 ОБЩАЯ СТОИМОСТЬ: {t2_total:,.0f} ₸\n\n"
            
            f"📄 Сертификация: {'требуется' if needs_certification else 'не требуется'}\n\n"
            
            f"💡 Напишите '1' или '2' чтобы выбрать вариант доставки!"
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка полного расчета: {e}")
        return "Ошибка расчета"

def show_final_calculation(delivery_data, customs_data, delivery_option):
    """Показывает итоговый расчет после выбора доставки"""
    try:
        if delivery_data['delivery_type'] == 'CARGO':
            # Расчет для КАРГО
            delivery_cost = calculate_quick_cost(delivery_data['weight'], delivery_data['product_type'], delivery_data['city'])
            if delivery_option == "самовывоз":
                total_cost = delivery_cost['t1_cost'] * 1.20
            else:
                total_cost = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20
            
            response = (
                f"✅ Выбрана ДОСТАВКА ДО {'ДВЕРИ' if delivery_option == 'до двери' else 'АЛМАТЫ (самовывоз)'}\n\n"
                f"💰 Итоговая стоимость: {total_cost:,.0f} ₸\n"
                f"📦 {'Груз будет доставлен по адресу в ' + delivery_data['city'].capitalize() if delivery_option == 'до двери' else 'Самовывоз со склада в Алматы'}\n"
                f"⏱️ Срок доставки: 12-15 дней\n\n"
                f"✅ Хотите оформить заявку? Напишите имя и телефон!"
            )
            
        else:  # INVOICE
            # Расчет для ИНВОЙС
            customs_cost = calculate_customs_cost(
                customs_data['invoice_value'],
                customs_data['product_type'],
                delivery_data['weight'],
                customs_data['has_certificate'],
                customs_data['needs_certificate']
            )
            
            delivery_cost = calculate_quick_cost(delivery_data['weight'], delivery_data['product_type'], delivery_data['city'])
            
            if delivery_option == "самовывоз":
                total_delivery = delivery_cost['t1_cost'] * 1.20
            else:
                total_delivery = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20
            
            total_cost = total_delivery + customs_cost['total_kzt']
            
            response = (
                f"✅ Выбрана ДОСТАВКА ДО {'ДВЕРИ' if delivery_option == 'до двери' else 'АЛМАТЫ (самовывоз)'}\n\n"
                f"💰 Итоговая стоимость: {total_cost:,.0f} ₸\n"
                f"📦 {'Груз будет доставлен по адресу в ' + delivery_data['city'].capitalize() if delivery_option == 'до двери' else 'Самовывоз со склада в Алматы'}\n"
                f"⏱️ Срок доставки: 12-15 дней\n\n"
                f"📋 Код ТН ВЭД: {customs_data.get('tnved_code', 'Требуется уточнение')}\n"
                f"📄 Сертификация: {'требуется' if check_certification_requirements(delivery_data['product_type']) else 'не требуется'}\n\n"
                f"✅ Хотите оформить заявку? Напишите имя и телефон!"
            )
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка итогового расчета: {e}")
        return "Ошибка расчета. Пожалуйста, попробуйте еще раз."

def get_gemini_response(user_message, context="", use_customs_model=False):
    """Получение ответа от Gemini"""
    if not main_model:
        return "Сервис временно недоступен"
    
    try:
        model_to_use = customs_model if use_customs_model else main_model
        full_prompt = f"Контекст: {context}\n\nСообщение: {user_message}\n\nОтвет:"
        response = model_to_use.generate_content(
            full_prompt,
            generation_config=GenerationConfig(
                temperature=0.7,
                max_output_tokens=1000,
            )
        )
        return response.text
    except Exception as e:
        logger.error(f"Ошибка Gemini: {e}")
        return "Извините, произошла ошибка. Попробуйте позже."

def extract_delivery_info(text):
    """Извлечение данных о доставке"""
    weight = None
    product_type = None
    city = None
    
    try:
        # Поиск веса
        weight_patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:кг|kg|килограмм|кило)',
            r'вес\s*[:\-]?\s*(\d+(?:\.\d+)?)',
        ]
        
        for pattern in weight_patterns:
            match = re.search(pattern, text.lower())
            if match:
                weight = float(match.group(1))
                break
        
        # Поиск города
        text_lower = text.lower()
        for city_name in DESTINATION_ZONES:
            if city_name in text_lower:
                city = city_name
                break
        
        # Поиск типа товара
        product_keywords = {
            'одежда': ['одежда', 'адежда', 'одежд'],
            'лекарства': ['лекарства', 'лекарсива', 'медикаменты'],
            'косметика': ['косметика', 'крем', 'шампунь', 'макияж'],
            'техника': ['техника', 'телефон', 'ноутбук', 'гаджет'],
            'мебель': ['мебель', 'стол', 'стул', 'кровать'],
            'посуда': ['посуда', 'тарелки', 'чашки'],
            'общие товары': ['товары', 'товар', 'разное', 'прочее'],
            'игрушки': ['игрушки', 'игрушка', 'куклы', 'машинки', 'конструктор'],
            'электроника': ['электроника', 'телефон', 'ноутбук', 'планшет', 'компьютер']
        }
        
        for prod_type, keywords in product_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                product_type = prod_type
                break
        
        if not product_type:
            product_type = "общие товары"
            
        return weight, product_type, city
    except Exception as e:
        logger.error(f"Ошибка извлечения данных: {e}")
        return None, None, None

def extract_customs_info(text):
    """Извлечение данных для растаможки"""
    try:
        # Поиск стоимости
        cost_patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:usd|\$|доллар)',
            r'стоимос\w*\s*[:\-]?\s*(\d+(?:\.\d+)?)',
            r'на\s*(\d+(?:\.\d+)?)\s*(?:usd|\$)',
        ]
        
        invoice_value = None
        for pattern in cost_patterns:
            match = re.search(pattern, text.lower())
            if match:
                invoice_value = float(match.group(1))
                break
        
        # Поиск кода ТНВЭД
        tnved_match = re.search(r'(\d{4,10}[\s\d]*)', text)
        tnved_code = tnved_match.group(1) if tnved_match else None
        
        return invoice_value, tnved_code
    except Exception as e:
        logger.error(f"Ошибка извлечения данных растаможки: {e}")
        return None, None

def extract_contact_info(text):
    """Умное извлечение контактных данных"""
    name = None
    phone = None
    
    # Удаляем лишние пробелы и приводим к нижнему регистру
    clean_text = re.sub(r'\s+', ' ', text.strip()).lower()
    
    # Поиск имени (первое слово из 2+ русских/английских букв)
    name_match = re.search(r'^([а-яa-z]{2,})', clean_text)
    if name_match:
        name = name_match.group(1).capitalize()
    
    # Поиск телефона (разные форматы)
    phone_patterns = [
        r'(\d{10,11})',  # 87057600909
        r'(\d{3}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2})',  # 870 576 00 909
        r'(\d{3}[\s\-]?\d{2}[\s\-]?\d{2}[\s\-]?\d{3})',  # 870 57 600 909
    ]
    
    for pattern in phone_patterns:
        phone_match = re.search(pattern, clean_text)
        if phone_match:
            phone = re.sub(r'\D', '', phone_match.group(1))
            # Нормализация номера
            if phone.startswith('8'):
                phone = '7' + phone[1:]
            elif len(phone) == 10:
                phone = '7' + phone
            break
    
    # Если нашли и имя и телефон - возвращаем
    if name and phone and len(phone) >= 10:
        return name, phone
    
    # Если есть только телефон, пробуем найти имя в тексте
    if phone and not name:
        # Ищем имя перед запятой или в начале текста
        name_before_comma = re.search(r'^([а-яa-z]+)\s*[,]', clean_text)
        if name_before_comma:
            name = name_before_comma.group(1).capitalize()
    
    return name, phone

def save_application(details):
    """Сохранение заявки"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"Новая заявка: {timestamp}\n{details}\n"
        with open("applications.txt", "a", encoding="utf-8") as f: 
            f.write("="*50 + "\n" + log_entry + "="*50 + "\n\n")
        logger.info(f"Заявка сохранена: {details}")
    except Exception as e: 
        logger.error(f"Ошибка сохранения: {e}")

# --- ROUTES ---
@app.route('/')
def index(): 
    if 'delivery_data' not in session:
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None, 'delivery_option': None}
    if 'customs_data' not in session:
        session['customs_data'] = {'invoice_value': None, 'product_type': None, 'has_certificate': False, 'needs_certificate': False, 'tnved_code': None}
    if 'chat_history' not in session:
        session['chat_history'] = []
    if 'waiting_for_contacts' not in session:
        session['waiting_for_contacts'] = False
    if 'waiting_for_customs' not in session:
        session['waiting_for_customs'] = False
    if 'waiting_for_delivery_choice' not in session:
        session['waiting_for_delivery_choice'] = False
    if 'waiting_for_tnved' not in session:
        session['waiting_for_tnved'] = False
    
    # Инициализация моделей при первом обращении
    if main_model is None:
        initialize_models()
    
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message:
            return jsonify({"response": "Пожалуйста, введите сообщение."})
        
        # Отладка
        debug_session(session, f"Получено сообщение: '{user_message}'")
        
        # Инициализация сессий
        delivery_data = session.get('delivery_data', {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None, 'delivery_option': None})
        customs_data = session.get('customs_data', {'invoice_value': None, 'product_type': None, 'has_certificate': False, 'needs_certificate': False, 'tnved_code': None})
        chat_history = session.get('chat_history', [])
        waiting_for_contacts = session.get('waiting_for_contacts', False)
        waiting_for_customs = session.get('waiting_for_customs', False)
        waiting_for_delivery_choice = session.get('waiting_for_delivery_choice', False)
        waiting_for_tnved = session.get('waiting_for_tnved', False)
        
        chat_history.append(f"Клиент: {user_message}")
        
        # Приветствия
        if user_message.lower() in GREETINGS:
            session.update({
                'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None, 'delivery_option': None},
                'customs_data': {'invoice_value': None, 'product_type': None, 'has_certificate': False, 'needs_certificate': False, 'tnved_code': None},
                'chat_history': [f"Клиент: {user_message}"],
                'waiting_for_contacts': False,
                'waiting_for_customs': False,
                'waiting_for_delivery_choice': False,
                'waiting_for_tnved': False
            })
            return jsonify({"response": "Привет! \n👋 Я ваш ИИ-помощник Post Pro.\n\n🚚 Рассчитаю доставку из Китая в Казахстан:\n\n• Доставка до Алматы \n• Доставка до двери по Казахстану\n\nНаши склады в Китае: ИУ/Гуанчжоу\n\nВыберите доставку:\n\n📦 КАРГО - упрощенная доставка для личных вещей, пробных партий\n\n📄 ИНВОЙС - полное таможенное оформление для коммерческих партий\n\nПросто напишите:\n- Вес груза\n- Тип товара  \n- Город доставки\n- Карго или Инвойс\n\nИ я сразу покажу расчет! ✨"})
        
        # ПЕРВЫЙ ПРИОРИТЕТ: Обработка выбора доставки (1 или 2)
        if waiting_for_delivery_choice and is_delivery_choice(user_message):
            delivery_option = parse_delivery_choice(user_message)
            delivery_data['delivery_option'] = delivery_option
            session['delivery_data'] = delivery_data
            session['waiting_for_delivery_choice'] = False
            session['waiting_for_contacts'] = True
            
            # Показываем итоговый расчет
            response = show_final_calculation(delivery_data, customs_data, delivery_option)
            
            chat_history.append(f"Бот: {response}")
            session['chat_history'] = chat_history
            return jsonify({"response": response})
        
        # ВТОРОЙ ПРИОРИТЕТ: Обработка контактов
        if waiting_for_contacts:
            name, phone = extract_contact_info(user_message)
            
            if name and phone:
                # Формируем детали заявки
                details = f"Имя: {name}, Телефон: {phone}"
                if delivery_data['weight']:
                    details += f", Вес: {delivery_data['weight']} кг"
                if delivery_data['product_type']:
                    details += f", Товар: {delivery_data['product_type']}"
                if delivery_data['city']:
                    details += f", Город: {delivery_data['city']}"
                if delivery_data['delivery_option']:
                    details += f", Доставка: {delivery_data['delivery_option']}"
                if customs_data['invoice_value']:
                    details += f", Стоимость: {customs_data['invoice_value']} USD"
                if customs_data['tnved_code']:
                    details += f", ТНВЭД: {customs_data['tnved_code']}"
                if delivery_data['delivery_type']:
                    details += f", Тип: {delivery_data['delivery_type']}"
                
                save_application(details)
                
                # Очищаем сессию
                session.update({
                    'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None, 'delivery_option': None},
                    'customs_data': {'invoice_value': None, 'product_type': None, 'has_certificate': False, 'needs_certificate': False, 'tnved_code': None},
                    'chat_history': [],
                    'waiting_for_contacts': False,
                    'waiting_for_customs': False,
                    'waiting_for_delivery_choice': False,
                    'waiting_for_tnved': False
                })
                
                response = "🎉 Заявка оформлена!\n\n⏰ Менеджер свяжется с вами в течение 30 минут для подтверждения деталей! \n🕙 Рабочее время: с 10:00 до 20:00 по времени Астаны 📞"
                return jsonify({"response": response})
            else:
                # Если не распознали - уточняем
                return jsonify({"response": "Не удалось распознать контакты. Пожалуйста, укажите в формате: 'Имя, 87001234567'"})
        
        # ТРЕТИЙ ПРИОРИТЕТ: Обработка кода ТНВЭД
        if waiting_for_tnved:
            if doesnt_know_tnved(user_message):
                # Автоматически определяем код через Gemini
                tnved_code = get_tnved_code(delivery_data['product_type'])
                customs_data['tnved_code'] = tnved_code
                session['customs_data'] = customs_data
                session['waiting_for_tnved'] = False
                
                response = f"🔍 Определяю код ТНВЭД для '{delivery_data['product_type']}'...\n✅ Найден код: {tnved_code}\n\n📊 Продолжаем расчет..."
                
                # Показываем расчет с выбором доставки
                full_calculation = get_customs_full_calculation(
                    delivery_data['weight'], 
                    delivery_data['product_type'], 
                    delivery_data['city'], 
                    customs_data['invoice_value'],
                    tnved_code
                )
                session['waiting_for_delivery_choice'] = True
                
                chat_history.append(f"Бот: {response}")
                session['chat_history'] = chat_history
                return jsonify({"response": response + "\n\n" + full_calculation})
            
            elif re.match(r'^\d{4,10}', user_message):
                # Пользователь ввел код вручную
                customs_data['tnved_code'] = user_message
                session['customs_data'] = customs_data
                session['waiting_for_tnved'] = False
                
                # Показываем расчет с выбором доставки
                response = get_customs_full_calculation(
                    delivery_data['weight'], 
                    delivery_data['product_type'], 
                    delivery_data['city'], 
                    customs_data['invoice_value'],
                    user_message
                )
                session['waiting_for_delivery_choice'] = True
                
                chat_history.append(f"Бот: {response}")
                session['chat_history'] = chat_history
                return jsonify({"response": f"✅ Код ТНВЭД сохранен!\n\n{response}"})
            else:
                return jsonify({"response": "Пожалуйста, укажите код ТНВЭД или напишите 'не знаю' чтобы я определил код автоматически."})
        
        # Выбор типа доставки (только если еще не выбран)
        if not delivery_data['delivery_type']:
            if any(word in user_message.lower() for word in ['карго', 'cargo', 'личные вещи', 'пробная партия', 'упрощен']):
                delivery_data['delivery_type'] = 'CARGO'
                session['delivery_data'] = delivery_data
                session['waiting_for_customs'] = False
                return jsonify({"response": "📦 ВЫБРАН КАРГО (упрощенная доставка)\n\nРасчет по тарифам Т1 и Т2\n\n💡 Просто напишите:\n• Вес груза\n• Тип товара  \n• Город доставки\n\nПример: '50 кг одежды в Астану'"})
            
            elif any(word in user_message.lower() for word in ['инвойс', 'invoice', 'коммерческий', 'растаможка', 'таможен', 'полный']):
                delivery_data['delivery_type'] = 'INVOICE'
                session['delivery_data'] = delivery_data
                session['waiting_for_customs'] = True
                session['waiting_for_tnved'] = True
                return jsonify({"response": "📄 ВЫБРАН ИНВОЙС (полное таможенное оформление)\n\n• Полный расчет таможенных платежей\n• Работа с кодами ТН ВЭД\n• Сертификация и документы\n\n💡 Для расчета укажите:\n• Вес груза и тип товара\n• Город доставки в Казахстане  \n• Стоимость товара по инвойсу (USD)\n• Код ТНВЭД\n\nПример: '100 кг электроники в Алматы, стоимость 5000 USD, код 9503007000'"})
        
        # Если выбран тип доставки, извлекаем данные
        if delivery_data['delivery_type']:
            # Извлекаем данные о доставке
            weight, product_type, city = extract_delivery_info(user_message)
            
            # Извлекаем данные для растаможки (только для ИНВОЙС)
            invoice_value, tnved_code = extract_customs_info(user_message)
            
            # Сохраняем полученные данные (если они есть)
            updated = False
            if weight:
                delivery_data['weight'] = weight
                updated = True
            if product_type:
                delivery_data['product_type'] = product_type
                customs_data['product_type'] = product_type
                updated = True
            if city:
                delivery_data['city'] = city
                updated = True
            if invoice_value and delivery_data['delivery_type'] == 'INVOICE':
                customs_data['invoice_value'] = invoice_value
                updated = True
            if tnved_code and delivery_data['delivery_type'] == 'INVOICE':
                customs_data['tnved_code'] = tnved_code
                session['waiting_for_tnved'] = False
                updated = True
            
            if updated:
                session['delivery_data'] = delivery_data
                session['customs_data'] = customs_data
            
            # Проверяем какие данные отсутствуют
            missing_data = get_missing_data(delivery_data, customs_data, delivery_data['delivery_type'])
            
            if missing_data:
                # Если есть недостающие данные - просим их
                if delivery_data['delivery_type'] == 'INVOICE' and 'код ТНВЭД' in missing_data and customs_data['invoice_value']:
                    session['waiting_for_tnved'] = True
                    response = f"✅ Получены данные: {delivery_data['weight']} кг {delivery_data['product_type']} в {delivery_data['city']}, стоимость {customs_data['invoice_value']} USD\n\nУкажите код ТНВЭД или напишите 'не знаю' чтобы я определил код автоматически."
                else:
                    response = f"Для расчета не хватает: {', '.join(missing_data)}\n\nПожалуйста, укажите недостающие данные."
                
                chat_history.append(f"Бот: {response}")
                session['chat_history'] = chat_history
                return jsonify({"response": response})
            else:
                # Все данные есть - показываем расчет
                if delivery_data['delivery_type'] == 'CARGO':
                    response = calculate_detailed_cost(
                        delivery_data['weight'], 
                        delivery_data['product_type'], 
                        delivery_data['city'], 
                        "до двери"
                    )
                    session['waiting_for_delivery_choice'] = True
                else:  # INVOICE
                    response = get_customs_full_calculation(
                        delivery_data['weight'], 
                        delivery_data['product_type'], 
                        delivery_data['city'], 
                        customs_data['invoice_value'],
                        customs_data['tnved_code']
                    )
                    session['waiting_for_delivery_choice'] = True
                
                chat_history.append(f"Бот: {response}")
                session['chat_history'] = chat_history
                return jsonify({"response": response})
        
        # Если все проверки пройдены и не распознана команда - используем Gemini
        ai_response = get_gemini_response(user_message, " ".join(chat_history[-3:]))
        chat_history.append(f"Бот: {ai_response}")
        session['chat_history'] = chat_history
        
        return jsonify({"response": ai_response})
        
    except Exception as e:
        logger.error(f"Ошибка в чате: {e}")
        debug_session(session, f"Ошибка: {e}")
        return jsonify({"response": "Извините, произошла ошибка. Попробуйте еще раз."})

if __name__ == '__main__':
    # Инициализация моделей при запуске
    initialize_models()
    app.run(debug=True, host='0.0.0.0', port=5000)
