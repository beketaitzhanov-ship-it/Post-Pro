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
    "белье": 2.80, "лекарства": 2.90, "лекарсива": 2.90, "медикаменты": 2.90, "посуда": 2.20
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
    "ткани": 12, "посуда": 10, "продукты": 15, "лекарства": 0, "белье": 12
}

CUSTOMS_FEES = {
    "оформление": 15000,  # тенге
    "сертификат": 120000,  # тенге
    "происхождения": 500  # USD
}

GREETINGS = ["привет", "здравствуй", "здравствуйте", "салем", "сәлем", "добрый день", "добрый вечер", "доброе утро"]

# --- СИСТЕМНЫЙ ПРОМПТ ---
SYSTEM_INSTRUCTION = """
Ты — умный ассистент компании PostPro. Твоя главная цель — помочь клиенту рассчитать стоимость доставки и оформить заявку.

***ВАЖНЫЕ ПРАВИЛА:***

1. **СКЛАДЫ В КИТАЕ:** У нас только 2 склада - ИУ и Гуанчжоу. Если клиент спрашивает "откуда заберете?" - отвечай: "Уточните у вашего поставщика, какой склад ему ближе - ИУ или Гуанчжоу"

2. **ТАРИФЫ:**
   - Т1: Доставка из Китая до Алматы (только до склада, самовывоз)
   - Т2: Доставка до двери в ЛЮБОМ городе Казахстана, включая доставку по Алматы

3. **ОПЛАТА:**
   - У нас пост-оплата: вы платите при получении груза
   - Форматы оплаты: безналичный расчет, наличные, Kaspi, Halyk, Freedom Bank
   - Если спрашивают про оплату - всегда объясняй эту систему

4. **РАСТАМОЖКА:**
   - Если клиент спрашивает про растаможку - предлагай расчет таможенных платежей
   - Объясняй процедуру растаможки и необходимые документы

5. **ЛОГИКА ДИАЛОГА:**
   - Сначала быстрый расчет
   - Потом предлагай детальный расчет
   - В конце предлагай заявку

6. **СБОР ЗАЯВКИ:**
   - Когда клиент пишет имя и телефон - сохраняй заявку
   - Формат: [ЗАЯВКА] Имя: [имя], Телефон: [телефон]

7. **ОБЩИЕ ВОПРОСЫ:**
   - Если вопрос не о доставке (погода, имя бота и т.д.) - отвечай нормально
   - Не зацикливайся только на доставке

8. **НЕ УПОМИНАЙ:** другие города Китая кроме ИУ и Гуанчжоу

Всегда будь дружелюбным и профессиональным! 😊
"""

# --- ИНИЦИАЛИЗАЦИЯ МОДЕЛИ ---
model = None
try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name='models/gemini-2.0-flash',
            system_instruction=SYSTEM_INSTRUCTION
        )
        logger.info(">>> Модель Gemini успешно инициализирована.")
    else:
        logger.error("!!! API ключ не найден")
except Exception as e:
    logger.error(f"!!! Ошибка инициализации Gemini: {e}")

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
            t2_rate = T2_RATES["алматы"]  # Городской тариф
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
            't2_rate': t2_rate
        }
    except Exception as e:
        logger.error(f"Ошибка расчета: {e}")
        return None

def calculate_detailed_cost(weight: float, product_type: str, city: str):
    """Детальный расчет с разбивкой"""
    quick_cost = calculate_quick_cost(weight, product_type, city)
    if not quick_cost:
        return "Ошибка расчета"
    
    t1_cost = quick_cost['t1_cost']
    t2_cost = quick_cost['t2_cost'] 
    total = quick_cost['total']
    zone = quick_cost['zone']
    t2_rate = quick_cost['t2_rate']
    
    # Находим актуальную ставку Т1
    product_type_lower = product_type.lower()
    t1_rate = T1_RATES.get(product_type_lower, 2.40)
    
    # Определяем текст для Т2 в зависимости от города
    city_name = city.capitalize()
    if zone == "алматы":
        t2_explanation = f"• Доставка по городу Алматы до вашего адреса"
        zone_text = "город Алматы"
        comparison_text = f"💡 **Если самовывоз со склада в Алматы:** {t1_cost * 1.20:.0f} тенге"
    else:
        t2_explanation = f"• Доставка до вашего адреса в {city_name}"
        zone_text = f"Зона {zone}"
        comparison_text = f"💡 **Если самовывоз из Алматы:** {t1_cost * 1.20:.0f} тенге"
    
    response = (
        f"📊 **Детальный расчет для {weight} кг «{product_type}» в г. {city_name}:**\n\n"
        f"**Т1: Доставка из Китая до Алматы**\n"
        f"• До склада в Алматы (самовывоз)\n"
        f"• ${t1_rate}/кг × {weight} кг = ${weight * t1_rate:.2f} USD\n"
        f"• По курсу {EXCHANGE_RATE} тенге/$ = {t1_cost:.0f} тенге\n\n"
        f"**Т2: Доставка до двери ({zone_text})**\n"
        f"{t2_explanation}\n"
        f"• {t2_rate} тенге/кг × {weight} кг = {t2_cost:.0f} тенге\n\n"
        f"**Комиссия компании (20%):**\n"
        f"• ({t1_cost:.0f} + {t2_cost:.0f}) × 20% = {(t1_cost + t2_cost) * 0.20:.0f} тенге\n\n"
        f"------------------------------------\n"
        f"💰 **ИТОГО с доставкой до двери:** ≈ **{total:.0f} тенге**\n\n"
        f"{comparison_text}\n\n"
        f"💡 **Страхование:** дополнительно 1% от стоимости груза\n"
        f"💳 **Оплата:** пост-оплата при получении\n\n"
        f"❓ **Не понятны тарифы?** Напишите 'объясни тарифы'\n\n"
        f"✅ **Хотите оставить заявку?** Напишите ваше имя и телефон!"
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
        
        total_customs_kzt = duty_kzt + vat_kzt + customs_fee + certificate_fee + origin_cert_fee
        
        return {
            'duty_usd': duty_usd,
            'duty_kzt': duty_kzt,
            'vat_kzt': vat_kzt,
            'customs_fee': customs_fee,
            'certificate_fee': certificate_fee,
            'origin_cert_fee': origin_cert_fee,
            'total_kzt': total_customs_kzt,
            'total_usd': total_customs_kzt / EXCHANGE_RATE
        }
    except Exception as e:
        logger.error(f"Ошибка расчета растаможки: {e}")
        return None

def get_tnved_code(product_name):
    """Получение кода ТН ВЭД через Gemini"""
    if not model:
        return "Не удалось определить"
    
    try:
        prompt = f"Определи код ТН ВЭД ЕАЭС для товара: '{product_name}'. Верни ТОЛЬКО код в формате XXXXX XXX X без каких-либо пояснений, текста или точек. Только цифры и пробелы."
        response = model.generate_content(prompt)
        code = response.text.strip()
        
        # Проверяем, что ответ похож на код ТН ВЭД
        if re.match(r'^\d{4,10}[\s\d]*$', code):
            return code
        else:
            return "Требуется уточнение"
    except Exception as e:
        logger.error(f"Ошибка получения кода ТН ВЭД: {e}")
        return "Ошибка определения"

def check_certification_requirements(product_name):
    """Проверка требований к сертификации через Gemini"""
    if not model:
        return False
    
    try:
        prompt = f"Нужен ли сертификат соответствия ТР ТС для товара: '{product_name}'? Ответь только 'ДА' или 'НЕТ' без пояснений."
        response = model.generate_content(prompt)
        return "ДА" in response.text.upper()
    except Exception as e:
        logger.error(f"Ошибка проверки сертификации: {e}")
        return True  # На всякий случай предполагаем что нужен

def get_customs_procedure():
    """Процедура растаможки"""
    return """📋 **Процедура растаможки:**

1. **Подготовка документов:**
   • Коммерческий инвойс
   • Упаковочный лист  
   • Внешнеэкономический контракт
   • Товаротранспортная накладная

2. **Таможенные платежи:**
   • Таможенная пошлина (зависит от кода ТН ВЭД)
   • НДС 12% (от стоимости + пошлина)
   • Таможенный сбор

3. **Сертификация:**
   • Сертификат соответствия (при необходимости)
   • Сертификат происхождения (для преференций)

💡 **Наш таможенный брокер поможет с оформлением!**"""

def explain_tariffs():
    """Объяснение тарифов Т1 и Т2"""
    return """🚚 **Объяснение тарифов:**

**Т1 - Доставка до склада в Алматы:**
• Доставка из Китая до нашего сортировочного склада в Алматы
• Вы забираете груз самовывозом со склада
• ТОЛЬКО склад в Алматы, без доставки по городу

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
    if not model:
        return "Сервис временно недоступен"
    
    try:
        full_prompt = f"Контекст: {context}\n\nСообщение: {user_message}\n\nОтвет:"
        response = model.generate_content(
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
            'общие товары': ['товары', 'товар', 'разное', 'прочее']
        }
        
        for prod_type, keywords in product_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                product_type = prod_type
                break
        
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
        
        # Поиск сертификата происхождения
        has_certificate = any(word in text.lower() for word in ['сертификат есть', 'есть сертификат', 'да, есть', 'имеется сертификат'])
        needs_certificate = any(word in text.lower() for word in ['нужен сертификат', 'требуется сертификат', 'без сертификата'])
        
        return invoice_value, has_certificate, needs_certificate
    except Exception as e:
        logger.error(f"Ошибка извлечения данных растаможки: {e}")
        return None, False, False

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

# --- ROUTES ---
@app.route('/')
def index(): 
    if 'delivery_data' not in session:
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None}
    if 'customs_data' not in session:
        session['customs_data'] = {'invoice_value': None, 'product_type': None, 'has_certificate': False, 'needs_certificate': False}
    if 'chat_history' not in session:
        session['chat_history'] = []
    if 'waiting_for_contacts' not in session:
        session['waiting_for_contacts'] = False
    if 'waiting_for_customs' not in session:
        session['waiting_for_customs'] = False
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message:
            return jsonify({"response": "Пожалуйста, введите сообщение."})
        
        # Инициализация сессий
        delivery_data = session.get('delivery_data', {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None})
        customs_data = session.get('customs_data', {'invoice_value': None, 'product_type': None, 'has_certificate': False, 'needs_certificate': False})
        chat_history = session.get('chat_history', [])
        waiting_for_contacts = session.get('waiting_for_contacts', False)
        waiting_for_customs = session.get('waiting_for_customs', False)
        
        chat_history.append(f"Клиент: {user_message}")
        
        # Приветствия
        if user_message.lower() in GREETINGS:
            session.update({
                'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None},
                'customs_data': {'invoice_value': None, 'product_type': None, 'has_certificate': False, 'needs_certificate': False},
                'chat_history': [f"Клиент: {user_message}"],
                'waiting_for_contacts': False,
                'waiting_for_customs': False
            })
            return jsonify({"response": "Привет! 👋 Я ваш ИИ-помощник Post Pro.\n\n🚚 **Рассчитаю доставку из Китая в Казахстан:**\n\nВыберите тип доставки:\n\n🟢 **КАРГО** (упрощенная доставка)\n• Для личных вещей, пробных партий\n• Расчет по тарифам Т1 и Т2\n• Быстрый предварительный расчет\n\n🔵 **ИНВОЙС** (полное таможенное оформление)\n• Для коммерческих партий с инвойсом\n• Полный расчет таможенных платежей\n• Растаможка, сертификация, документы\n\n**Напишите 'Карго' или 'Инвойс'**"})
        
        # Выбор типа доставки (только если еще не выбран)
        if not delivery_data['delivery_type']:
            if any(word in user_message.lower() for word in ['карго', 'cargo', 'личные вещи', 'пробная партия', 'упрощен']):
                delivery_data['delivery_type'] = 'CARGO'
                session['delivery_data'] = delivery_data
                return jsonify({"response": "🟢 **ВЫБРАН КАРГО** (упрощенная доставка)\n\nРасчет по тарифам Т1 и Т2\n\n💡 **Просто напишите:**\n• Вес груза\n• Тип товара  \n• Город доставки\n\n**Пример:** '50 кг одежды в Астану'"})
            
            elif any(word in user_message.lower() for word in ['инвойс', 'invoice', 'коммерческий', 'растаможка', 'таможен', 'полный']):
                delivery_data['delivery_type'] = 'INVOICE'
                session['delivery_data'] = delivery_data
                return jsonify({"response": "🔵 **ВЫБРАН ИНВОЙС** (полное таможенное оформление)\n\n• Полный расчет таможенных платежей\n• Работа с кодами ТН ВЭД\n• Сертификация и документы\n\n💡 **Для расчета укажите:**\n• Вес груза и тип товара\n• Город доставки в Казахстане  \n• Стоимость товара по инвойсу (USD)\n\n**Пример:** '100 кг электроники в Алматы, стоимость 5000 USD'"})
        
        # Если ждем контакты
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
                if customs_data['invoice_value']:
                    details += f", Стоимость: {customs_data['invoice_value']} USD"
                if delivery_data['delivery_type']:
                    details += f", Тип: {delivery_data['delivery_type']}"
                
                save_application(details)
                
                # Очищаем сессию
                session.update({
                    'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None},
                    'customs_data': {'invoice_value': None, 'product_type': None, 'has_certificate': False, 'needs_certificate': False},
                    'chat_history': [],
                    'waiting_for_contacts': False,
                    'waiting_for_customs': False
                })
                
                return jsonify({"response": "🎉 Спасибо, что выбрали Post Pro! Менеджер свяжется с вами в течение 15 минут. 📞"})
            else:
                # Если не распознали - уточняем
                return jsonify({"response": "Не удалось распознать контакты. Пожалуйста, укажите в формате: 'Имя, 87001234567'"})
        
        # Если ждем данные для растаможки (режим ИНВОЙС)
        if waiting_for_customs or delivery_data['delivery_type'] == 'INVOICE':
            invoice_value, has_certificate, needs_certificate = extract_customs_info(user_message)
            
            if invoice_value:
                customs_data['invoice_value'] = invoice_value
                customs_data['has_certificate'] = has_certificate
                customs_data['needs_certificate'] = needs_certificate
                
                # Если есть данные о товаре из доставки - используем их
                if delivery_data['product_type']:
                    customs_data['product_type'] = delivery_data['product_type']
                else:
                    # Пытаемся определить тип товара из сообщения
                    product_types = ['одежда', 'электроника', 'косметика', 'техника', 'мебель', 'автозапчасти', 'посуда']
                    for p_type in product_types:
                        if p_type in user_message.lower():
                            customs_data['product_type'] = p_type
                            break
                
                # Если тип товара не определен - используем общий
                if not customs_data['product_type']:
                    customs_data['product_type'] = "общие товары"
                
                # Получаем код ТН ВЭД
                tnved_code = get_tnved_code(customs_data['product_type'])
                
                # Проверяем нужны ли сертификаты
                needs_certification = check_certification_requirements(customs_data['product_type'])
                
                # Расчет таможенных платежей
                customs_cost = calculate_customs_cost(
                    customs_data['invoice_value'],
                    customs_data['product_type'],
                    delivery_data['weight'] if delivery_data['weight'] else 100,
                    customs_data['has_certificate'],
                    needs_certification or customs_data['needs_certificate']
                )
                
               
