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
    "электроника": 2.60, "смартфоны": 2.80, "игрушки": 2.20, "запчасти": 2.40
}

T2_RATES = {  # Алматы → город назначения (тенге/кг)
    "алматы": 120,     # Доставка по городу Алматы
    1: 150,            # Зона 1 (Алматинская область)
    2: 200,            # Зона 2 (Южный Казахстан)
    3: 250,            # Зона 3 (Центральный и Северный Казахстан)
    4: 350,            # Зона 4 (Западный Казахстан)
    5: 450             # Зона 5 (Прикаспийский регион)
}

# Таможенные ставки (примерные)
CUSTOMS_CLEARANCE_FEE = 15000  # тенге
CUSTOMS_DUTY_RATES = {
    "default": 0.10,  # 10% по умолчанию
    "electronics": 0.05,  # 5% для электроники
    "clothes": 0.15,  # 15% для одежды
}
VAT_RATE = 0.12  # 12% НДС
CERTIFICATE_OF_ORIGIN_COST = 500  # USD
CERTIFICATE_OF_CONFORMITY_COST = 120000  # тенге

GREETINGS = ["привет", "здравствуй", "здравствуйте", "салем", "сәлем", "добрый день", "добрый вечер", "доброе утро"]

# --- СИСТЕМНЫЙ ПРОМПТ ---
SYSTEM_INSTRUCTION = """
Ты — умный ассистент компании PostPro. Твоя главная цель — помочь клиенту рассчитать стоимость доставки и таможенного оформления.

***ВАЖНЫЕ ПРАВИЛА:***

1. **ТИПЫ ДОСТАВКИ:**
   - КАРГО: упрощенная доставка для личных вещей, пробных партий
   - ИНВОЙС: полное таможенное оформление для коммерческих партий

2. **СКЛАДЫ В КИТАЕ:** У нас только 2 склада - ИУ и Гуанчжоу.

3. **ТАРИФЫ:**
   - Т1: Доставка из Китая до Алматы (только до склада, самовывоз)
   - Т2: Доставка до двери в ЛЮБОМ городе Казахстана

4. **ТАМОЖЕННОЕ ОФОРМЛЕНИЕ:**
   - Помогай определять коды ТН ВЭД
   - Объясняй требования к сертификации
   - Рассчитывай таможенные платежи

5. **ОПЛАТА:**
   - У нас пост-оплата: вы платите при получении груза
   - Форматы оплаты: безналичный расчет, наличные, Kaspi, Halyk, Freedom Bank

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

# --- НОВЫЕ ФУНКЦИИ ДЛЯ TNVED И ТАМОЖНИ ---
def get_tnved_code_info(product_name, tnved_code=None):
    """Получение информации о коде ТН ВЭД через Gemini"""
    if not model:
        return "Сервис временно недоступен"
    
    try:
        if tnved_code:
            prompt = f"""Товар: {product_name}
Код ТН ВЭД: {tnved_code}

Проверь правильность кода ТН ВЭД для этого товара и предоставь:
1. Ставку таможенной пошлины (%)
2. Требования к сертификации
3. Особенности оформления
4. Необходимые документы

Ответь структурированно."""
        else:
            prompt = f"""Товар: {product_name}

Найди 2-3 наиболее вероятных кода ТН ВЭД для этого товара. Для каждого кода укажи:
1. Полное описание кода
2. Примерную ставку пошлины
3. Основные требования

Ответь в формате списка вариантов."""
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Ошибка Gemini при проверке ТН ВЭД: {e}")
        return "Не удалось получить информацию о коде ТН ВЭД"

def calculate_customs_costs(product_value_usd, weight, tnved_code, has_origin_certificate, delivery_cost_usd):
    """Расчет таможенных платежей"""
    try:
        # Таможенная стоимость (стоимость товара + доставка Т1)
        customs_value_usd = product_value_usd + delivery_cost_usd
        customs_value_kzt = customs_value_usd * EXCHANGE_RATE
        
        # Определяем ставку пошлины (упрощенная логика)
        duty_rate = CUSTOMS_DUTY_RATES.get("default", 0.10)
        if "электро" in tnved_code.lower() or "85" in tnved_code:
            duty_rate = CUSTOMS_DUTY_RATES.get("electronics", 0.05)
        
        # Применяем скидку при наличии сертификата происхождения
        if has_origin_certificate == "Да":
            duty_rate *= 0.75  # 25% скидка
        
        # Расчет платежей
        customs_duty = customs_value_kzt * duty_rate
        customs_fee = CUSTOMS_CLEARANCE_FEE
        vat_base = customs_value_kzt + customs_duty
        vat = vat_base * VAT_RATE
        
        total_customs = customs_duty + customs_fee + vat
        
        return {
            'customs_value_usd': customs_value_usd,
            'customs_value_kzt': customs_value_kzt,
            'duty_rate': duty_rate,
            'customs_duty': customs_duty,
            'customs_fee': customs_fee,
            'vat': vat,
            'total_customs': total_customs
        }
    except Exception as e:
        logger.error(f"Ошибка расчета таможенных платежей: {e}")
        return None

# --- СУЩЕСТВУЮЩИЕ ФУНКЦИИ РАСЧЕТА (обновленные) ---
def calculate_quick_cost(weight: float, product_type: str, city: str):
    """Быстрый расчет стоимости для КАРГО"""
    try:
        # Т1: Китай → Алматы
        product_type_lower = product_type.lower()
        t1_rate = T1_RATES.get(product_type_lower, 2.40)
        t1_cost_usd = weight * t1_rate
        
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
        total_cost_usd = t1_cost_usd * 1.20
        total_cost_kzt = (t1_cost_usd * EXCHANGE_RATE + t2_cost_kzt) * 1.20
        
        return {
            't1_cost_usd': t1_cost_usd,
            't2_cost_kzt': t2_cost_kzt,
            'total_usd': total_cost_usd,
            'total_kzt': total_cost_kzt,
            'zone': zone,
            't2_rate': t2_rate
        }
    except Exception as e:
        logger.error(f"Ошибка расчета: {e}")
        return None

def get_delivery_procedure():
    return """📦 **Процедура доставки:**

1. **Прием груза в Китае:** Ваш груз прибудет на наш склад в Китае (ИУ или Гуанчжоу)
2. **Осмотр и обработка:** Взвешиваем, фотографируем, упаковываем
3. **Подтверждение:** Присылаем детали груза
4. **Отправка:** Доставляем до Алматы (Т1) или до двери (Т2)
5. **Получение и оплата:** Забираете груз и оплачиваете удобным способом

💳 **Оплата:** пост-оплата при получении (наличные, Kaspi, Halyk, Freedom Bank, безнал)"""

def save_application(details):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"Новая заявка: {timestamp}\n{details}\n"
        with open("applications.txt", "a", encoding="utf-8") as f: 
            f.write("="*50 + "\n" + log_entry + "="*50 + "\n\n")
        logger.info(f"Заявка сохранена: {details}")
    except Exception as e: 
        logger.error(f"Ошибка сохранения: {e}")

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
            'электроника': ['электроника', 'смартфон', 'телефон', 'ноутбук', 'гаджет', 'чайник', 'электрический'],
            'мебель': ['мебель', 'стол', 'стул', 'кровать'],
            'посуда': ['посуда', 'тарелки', 'чашки'],
            'игрушки': ['игрушки', 'игрушек', 'детские'],
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

def extract_contact_info(text):
    """Умное извлечение контактных данных"""
    name = None
    phone = None
    
    clean_text = re.sub(r'\s+', ' ', text.strip()).lower()
    
    # Поиск имени
    name_match = re.search(r'^([а-яa-z]{2,})', clean_text)
    if name_match:
        name = name_match.group(1).capitalize()
    
    # Поиск телефона
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

# --- НОВЫЕ ФУНКЦИИ ДЛЯ ИНВОЙС РАСЧЕТА ---
def generate_invoice_calculation(product_name, tnved_code, product_value_usd, weight, volume, country, origin_certificate, city):
    """Генерация полного расчета для инвойса"""
    try:
        # Расчет доставки Т1
        product_type_lower = product_name.lower()
        t1_rate = T1_RATES.get(product_type_lower, 2.40)
        t1_cost_usd = weight * t1_rate
        
        # Расчет доставки Т2
        city_lower = city.lower()
        if city_lower == "алматы" or city_lower == "алмата":
            t2_rate = T2_RATES["алматы"]
        else:
            zone = DESTINATION_ZONES.get(city_lower, 3)
            t2_rate = T2_RATES.get(zone, 250)
        
        t2_cost_kzt = weight * t2_rate
        
        # Расчет таможенных платежей
        customs_costs = calculate_customs_costs(product_value_usd, weight, tnved_code, origin_certificate, t1_cost_usd)
        
        if not customs_costs:
            return "Ошибка расчета таможенных платежей"
        
        # Дополнительные услуги
        additional_services = []
        additional_costs = 0
        
        if origin_certificate == "Нет, но нужен":
            additional_services.append(f"• Оформление сертификата происхождения: {CERTIFICATE_OF_ORIGIN_COST} USD")
            additional_costs += CERTIFICATE_OF_ORIGIN_COST * EXCHANGE_RATE
        
        # Сертификат соответствия (примерно для электроники)
        if "электро" in product_name.lower():
            additional_services.append(f"• Сертификат соответствия: {CERTIFICATE_OF_CONFORMITY_COST:,} ₸")
            additional_costs += CERTIFICATE_OF_CONFORMITY_COST
        
        # Предупреждение о больших суммах
        warning = ""
        if product_value_usd > 50000:
            warning = "⚠️ **ВНИМАНИЕ:** Сумма контракта превышает $50,000. Потребуется регистрация в Нацбанке РК.\n\n"
        
        # Формирование ответа
        response = f"""📊 **РАСЧЕТ ДЛЯ: {product_name} (Код ТН ВЭД: {tnved_code})**

{warning}**1. Стоимость доставки:**
• Тариф Т1 до Алматы ({weight} кг × ${t1_rate:.2f}/кг): **${t1_cost_usd:.2f}**
• Тариф Т2 до {city.capitalize()} ({weight} кг × {t2_rate} ₸/кг): **{t2_cost_kzt:,} ₸**

**2. Таможенные платежи:**
• Таможенная стоимость: ${customs_costs['customs_value_usd']:.2f} ({customs_costs['customs_value_kzt']:,} ₸)
• Таможенная пошлина ({customs_costs['duty_rate']*100:.1f}%): {customs_costs['customs_duty']:,.0f} ₸
• Сбор за таможенное оформление: {customs_costs['customs_fee']:,} ₸
• НДС (12%): {customs_costs['vat']:,.0f} ₸
• **Итого таможенные платежи: {customs_costs['total_customs']:,.0f} ₸**

**3. Дополнительные услуги:**
{chr(10).join(additional_services) if additional_services else "• Нет"}

**ОБЩАЯ ПРИБЛИЗИТЕЛЬНАЯ СТОИМОСТЬ:**
• **Доставка: ${t1_cost_usd:.2f} + {t2_cost_kzt:,} ₸**
• **Таможенные платежи: {customs_costs['total_customs']:,.0f} ₸**
• **Дополнительно: {additional_costs:,} ₸**

**💎 ИТОГО: ~${t1_cost_usd + additional_costs/EXCHANGE_RATE:.2f} USD / ~{t1_cost_usd * EXCHANGE_RATE + t2_cost_kzt + customs_costs['total_customs'] + additional_costs:,.0f} ₸**

---

**📋 ВАЖНАЯ ИНФОРМАЦИЯ:**

**Необходимые документы:**
✅ Коммерческий инвойс
✅ Упаковочный лист  
✅ Внешнеэкономический контракт

**Требования:**
⚠️ Для вашего товара требуется сертификация
⚠️ Маркировка на русском и казахском языках
⚠️ Бесплатное хранение на СВХ — 2 месяца

**✅ Ваша заявка отправлена нашему таможенному брокеру.**
**📞 С вами свяжутся в течение часа для уточнения деталей.**"""
        
        return response
        
    except Exception as e:
        logger.error(f"Ошибка генерации расчета инвойса: {e}")
        return "Произошла ошибка при расчете. Пожалуйста, попробуйте еще раз."

# --- ОБНОВЛЕННЫЕ ROUTES ---
@app.route('/')
def index(): 
    if 'delivery_data' not in session:
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None}
    if 'invoice_data' not in session:
        session['invoice_data'] = {'product_name': None, 'tnved_code': None, 'product_value': None, 'weight': None, 'volume': None, 'country': 'Китай', 'origin_certificate': None}
    if 'chat_history' not in session:
        session['chat_history'] = []
    if 'waiting_for_contacts' not in session:
        session['waiting_for_contacts'] = False
    if 'waiting_for_invoice_data' not in session:
        session['waiting_for_invoice_data'] = False
    if 'current_step' not in session:
        session['current_step'] = 'start'
    
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message:
            return jsonify({"response": "Пожалуйста, введите сообщение."})
        
        # Инициализация сессий
        delivery_data = session.get('delivery_data', {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None})
        invoice_data = session.get('invoice_data', {'product_name': None, 'tnved_code': None, 'product_value': None, 'weight': None, 'volume': None, 'country': 'Китай', 'origin_certificate': None})
        chat_history = session.get('chat_history', [])
        waiting_for_contacts = session.get('waiting_for_contacts', False)
        waiting_for_invoice_data = session.get('waiting_for_invoice_data', False)
        current_step = session.get('current_step', 'start')
        
        chat_history.append(f"Клиент: {user_message}")
        
        # Приветствия
        if user_message.lower() in GREETINGS or current_step == 'start':
            session.update({
                'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None},
                'invoice_data': {'product_name': None, 'tnved_code': None, 'product_value': None, 'weight': None, 'volume': None, 'country': 'Китай', 'origin_certificate': None},
                'chat_history': [f"Клиент: {user_message}"],
                'waiting_for_contacts': False,
                'waiting_for_invoice_data': False,
                'current_step': 'greeting'
            })
            response = """Привет! 👋 Я ваш ИИ-помощник Post Pro.
🚚 Рассчитаю доставку из Китая в Казахстан:

💡 Просто напишите:
- Вес груза
- Тип товара  
- Город доставки

И я сразу покажу расчет! ✨"""
            chat_history.append(f"Ассистент: {response}")
            session['current_step'] = 'awaiting_delivery_info'
            return jsonify({"response": response})
        
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
                if delivery_data['delivery_type']:
                    details += f", Тип: {delivery_data['delivery_type']}"
                
                save_application(details)
                
                # Очищаем сессию
                session.update({
                    'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'delivery_type': None},
                    'invoice_data': {'product_name': None, 'tnved_code': None, 'product_value': None, 'weight': None, 'volume': None, 'country': 'Китай', 'origin_certificate': None},
                    'chat_history': [],
                    'waiting_for_contacts': False,
                    'waiting_for_invoice_data': False,
                    'current_step': 'start'
                })
                
                return jsonify({"response": "🎉 Спасибо, что выбрали Post Pro! Менеджер свяжется с вами в течение 15 минут. 📞"})
            else:
                return jsonify({"response": "Не удалось распознать контакты. Пожалуйста, укажите в формате: 'Имя, 87001234567'"})
        
        # Если ждем данные для инвойса
        if waiting_for_invoice_data:
            current_step = session.get('invoice_current_step', 'product_name')
            
            if current_step == 'product_name':
                # Извлекаем название товара и возможно код ТН ВЭД
                if ',' in user_message:
                    parts = user_message.split(',')
                    invoice_data['product_name'] = parts[0].strip()
                    invoice_data['tnved_code'] = parts[1].strip() if len(parts) > 1 else None
                else:
                    invoice_data['product_name'] = user_message
                
                # Проверяем код ТН ВЭД через Gemini
                if invoice_data['tnved_code']:
                    tnved_info = get_tnved_code_info(invoice_data['product_name'], invoice_data['tnved_code'])
                    response = f"✅ Код ТН ВЭД {invoice_data['tnved_code']} для {invoice_data['product_name']} проверен.\n\n{tnved_info}\n\n2. Общая стоимость товара по инвойсу (USD):"
                    session['invoice_current_step'] = 'product_value'
                else:
                    tnved_info = get_tnved_code_info(invoice_data['product_name'])
                    response = f"🔍 Для товара '{invoice_data['product_name']}' я нашел возможные коды ТН ВЭД:\n\n{tnved_info}\n\nПожалуйста, выберите код из списка или введите свой:"
                    session['invoice_current_step'] = 'tnved_code'
                
                session['invoice_data'] = invoice_data
                chat_history.append(f"Ассистент: {response}")
                return jsonify({"response": response})
            
            elif current_step == 'tnved_code':
                invoice_data['tnved_code'] = user_message
                tnved_info = get_tnved_code_info(invoice_data['product_name'], invoice_data['tnved_code'])
                response = f"✅ Код ТН ВЭД {invoice_data['tnved_code']} принят.\n\n{tnved_info}\n\n2. Общая стоимость товара по инвойсу (USD):"
                session['invoice_current_step'] = 'product_value'
                session['invoice_data'] = invoice_data
                chat_history.append(f"Ассистент: {response}")
                return jsonify({"response": response})
            
            elif current_step == 'product_value':
                try:
                    value = float(re.search(r'(\d+(?:\.\d+)?)', user_message).group(1))
                    invoice_data['product_value'] = value
                    
                    if value > 50000:
                        warning = "⚠️ Внимание: Сумма контракта превышает $50,000. Потребуется регистрация в Нацбанке РК.\n\n"
                    else:
                        warning = ""
                    
                    response = f"{warning}3. Вес брутто (кг):"
                    session['invoice_current_step'] = 'weight'
                    session['invoice_data'] = invoice_data
                    chat_history.append(f"Ассистент: {response}")
                    return jsonify({"response": response})
                except:
                    return jsonify({"response": "Пожалуйста, введите корректную сумму в USD:"})
            
            elif current_step == 'weight':
                try:
                    weight = float(re.search(r'(\d+(?:\.\d+)?)', user_message).group(1))
                    invoice_data['weight'] = weight
                    response = "4. Объем (м³):"
                    session['invoice_current_step'] = 'volume'
                    session['invoice_data'] = invoice_data
                    chat_history.append(f"Ассистент: {response}")
                    return jsonify({"response": response})
                except:
                    return jsonify({"response": "Пожалуйста, введите корректный вес в кг:"})
            
            elif current_step == 'volume':
                try:
                    volume = float(re.search(r'(\d+(?:\.\d+)?)', user_message).group(1))
                    invoice_data['volume'] = volume
                    response = "5. Страна происхождения товара:\n(по умолчанию Китай) Уточните, если страна другая:"
                    session['invoice_current_step'] = 'country'
                    session['invoice_data'] = invoice_data
                    chat_history.append(f"Ассистент: {response}")
                    return jsonify({"response": response})
                except:
                    return jsonify({"response": "Пожалуйста, введите корректный объем в м³:"})
            
            elif current_step == 'country':
                if user_message.lower() not in ['китай', 'china', '']:
                    invoice_data['country'] = user_message
                response = "6. Есть ли у вас сертификат происхождения (Form A) на данный товар?\nОтветьте: 'Да' / 'Нет, но нужен' / 'Нет, не нужен'"
                session['invoice_current_step'] = 'origin_certificate'
                session['invoice_data'] = invoice_data
                chat_history.append(f"Ассистент: {response}")
                return jsonify({"response": response})
            
            elif current_step == 'origin_certificate':
                if user_message.lower() in ['да', 'нет, но нужен', 'нет, не нужен']:
                    invoice_data['origin_certificate'] = user_message
                    
                    # Генерируем полный расчет
                    calculation = generate_invoice_calculation(
                        invoice_data['product_name'],
                        invoice_data['tnved_code'],
                        invoice_data['product_value'],
                        invoice_data['weight'],
                        invoice_data['volume'],
                        invoice_data['country'],
                        invoice_data['origin_certificate'],
                        delivery_data['city']
                    )
                    
                    response = f"{calculation}\n\n✅ Ваша заявка отправлена нашему таможенному брокеру.\n📞 С вами свяжутся в течение часа для уточнения деталей.\n\nХотите оставить контакты для связи? (имя и телефон)"
                    
                    session['waiting_for_invoice_data'] = False
                    session['waiting_for_contacts'] = True
                    session['invoice_data'] = invoice_data
                    chat_history.append(f"Ассистент: {response}")
                    return jsonify({"response": response})
                else:
                    return jsonify({"response": "Пожалуйста, выберите один из вариантов: 'Да' / 'Нет, но нужен' / 'Нет, не нужен'"})
        
        # Выбор типа доставки
        if current_step == 'awaiting_delivery_info' and not delivery_data['delivery_type']:
            # Извлекаем данные о доставке
            weight, product_type, city = extract_delivery_info(user_message)
            if weight:
                delivery_data['weight'] = weight
            if product_type:
                delivery_data['product_type'] = product_type
            if city:
                delivery_data['city'] = city
            
            # Если есть достаточно данных, предлагаем выбор типа доставки
            if delivery_data['weight'] and delivery_data['city']:
                response = f"""Отлично! Для расчета нужно уточнить:

Выберите тип доставки:

🟢 **КАРГО** (упрощенная доставка)
• Подходит для личных вещей, пробных партий
• Расчет по готовым тарифам Т1 и Т2
• Быстрый предварительный расчет

🔵 **ИНВОЙС** (полное таможенное оформление)  
• Для коммерческих партий с оформлением документов
• Полный расчет таможенных платежей (пошлина, НДС, сертификаты)
• Подробный анализ по коду ТН ВЭД

Что вам подходит? (напишите "Карго" или "Инвойс")"""
                
                session['current_step'] = '
