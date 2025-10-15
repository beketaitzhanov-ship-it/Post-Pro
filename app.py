from flask import Flask, render_template, request, jsonify, session
import os
import re
import json
from datetime import datetime
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from dotenv import load_dotenv
import logging
from calculation import (
    calculate_t2_cost, calculate_large_parcel_cost, extract_dimensions, 
    extract_volume, find_product_category, find_destination_zone, 
    calculate_quick_cost, calculate_detailed_cost, extract_delivery_info,
    parse_multiple_items, calculate_multiple_items, format_multiple_items_response,
    has_multiple_items
)

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
        message_lower = message.lower()
        
        # 1. Проверяем числа с единицами измерения (строгая проверка)
        has_parameters = self._has_delivery_parameters(message_lower)
        
        # 2. Проверяем ключевые слова параметров (дополнительная проверка)
        has_parameter_keywords = any(
            keyword in message_lower 
            for keyword in self.config["delivery_triggers"]["parameter_keywords"]
        )
        
        # 3. Проверяем явные ключевые слова доставки
        has_delivery_keywords = any(
            keyword in message_lower 
            for keyword in self.config["delivery_triggers"]["explicit_keywords"]
        )
        
        # 4. Проверяем города доставки
        has_city = any(
            city in message_lower 
            for city in self.config["delivery_triggers"]["city_keywords"]
        )
        
        # 5. Проверяем типы товаров
        has_product = any(
            product in message_lower 
            for product in self.config["delivery_triggers"]["product_keywords"]
        )
        
        # АКТИВИРУЕМ РЕЖИМ ДОСТАВКИ ТОЛЬКО ЕСЛИ:
        # - Есть параметры (числа + единицы) ИЛИ есть слова параметров ИЛИ
        # - Явный запрос доставки И (есть город ИЛИ есть товар ИЛИ есть слова параметров)
        if has_parameters or has_parameter_keywords or (has_delivery_keywords and (has_city or has_product or has_parameter_keywords)):
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
    CUSTOMS_RATES = config.get("CUSTOMS_RATES", {})
    CUSTOMS_FEES = config.get("CUSTOMS_FEES", {})
    GREETINGS = config.get("GREETINGS", [])
    PRODUCT_CATEGORIES = config.get("PRODUCT_CATEGORIES", {})
else:
    logger.error("!!! Приложение запускается с значениями по умолчанию из-за ошибки загрузки config.json")
    EXCHANGE_RATE, DESTINATION_ZONES, T1_RATES_DENSITY, T2_RATES, CUSTOMS_RATES, CUSTOMS_FEES, GREETINGS, PRODUCT_CATEGORIES = 550, {}, {}, {}, {}, {}, [], {}

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
Ты — умный ассистент компании PostPro. Твоя главная цель — помочь клиенту рассчитать стоимость доставки и оформить заявку.

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
        quick_cost = calculate_quick_cost(weight, product_type, city, volume, EXCHANGE_RATE, DESTINATION_ZONES, T1_RATES_DENSITY, T2_RATES)
        
        if quick_cost:
            return calculate_detailed_cost(quick_cost, weight, product_type, city, EXCHANGE_RATE)
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
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
                'calculation_shown': False,
                'multiple_calculation': None,
                'quick_cost': None
            })
            return jsonify({"response": "Привет! 👋 Я ассистент Post Pro. Помогу рассчитать доставку из Китая в Казахстан!\n\n📦 **Для расчета укажите параметры:**\n• **Вес груза** (в кг)\n• **Тип товара** (мебель, техника, одежда и т.д.)\n• **Габариты** (Д×Ш×В в метрах или сантиметрах)\n• **Город доставки**\n\n💡 **Примеры:**\n• Один товар: \"50 кг мебель в Астану, габариты 120×80×50\"\n• Несколько товаров: \"2 коробки по 10кг 30×30×30см, 3 пакета по 5кг 20×20×20см в Алматы\""})

        # Обработка команды "Старт" для нового расчета
        if user_message.lower() in ['старт', 'start', 'новый расчет', 'сначала', 'новая заявка']:
            session.update({
                'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'volume': None},
                'chat_history': [],
                'waiting_for_contacts': False,
                'calculation_shown': False,
                'multiple_calculation': None,
                'quick_cost': None
            })
            return jsonify({"response": "🔄 Начинаем новый расчет!\n\n📦 **Для расчета укажите параметры:**\n• **Вес груза** (в кг)\n• **Тип товара** (мебель, техника, одежда и т.д.)\n• **Габариты** (Д×Ш×В в метрах или сантиметрах)\n• **Город доставки**\n\n💡 **Примеры:**\n• Один товар: \"50 кг мебель в Астану, габариты 120×80×50\"\n• Несколько товаров: \"2 коробки по 10кг 30×30×30см, 3 пакета по 5кг 20×20×20см в Алматы\""})
        
        # Если ждем контакты (после показа расчета)
        if waiting_for_contacts:
            name, phone = extract_contact_info(user_message)
            
            if name and phone:
                details = f"Имя: {name}, Телефон: {phone}"
                
                # Для множественных товаров
                if session.get('multiple_calculation'):
                    multiple_calculation = session['multiple_calculation']
                    details += f", Множественные товары:"
                    for item in multiple_calculation['items']:
                        details += f" {item['product_type']} ({item['quantity']} шт)"
                    details += f", Общий вес: {multiple_calculation['totals']['total_weight']} кг"
                    details += f", Город: {delivery_data['city']}"
                # Для одиночных товаров
                else:
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
                    'calculation_shown': False,
                    'multiple_calculation': None,
                    'quick_cost': None
                })
                
                return jsonify({"response": "🎉 Спасибо, что выбрали Post Pro! Менеджер свяжется с вами в течение 15 минут. 📞"})
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
                return jsonify({"response": get_payment_info()})
            
            # Запросы о тарифах Т1/Т2
            if any(word in user_message.lower() for word in ['т1', 'т2', 'тариф', 'что такое т', 'объясни тариф']):
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
        
        # Извлечение данных о доставке (обновленная версия с поддержкой множественных товаров)
        delivery_info = extract_delivery_info(user_message, DESTINATION_ZONES, PRODUCT_CATEGORIES)

        # Проверяем, есть ли множественные товары
        if delivery_info.get('multiple_items', False):
            # Обрабатываем множественные товары
            items = delivery_info['items']
            city = delivery_info['city']
            
            if items and city:
                # Расчет для множественных товаров
                multiple_calculation = calculate_multiple_items(
                    items, city, EXCHANGE_RATE, DESTINATION_ZONES, T1_RATES_DENSITY, T2_RATES
                )
                
                if multiple_calculation:
                    # Сохраняем результат расчета в сессии
                    session['multiple_calculation'] = multiple_calculation
                    session['calculation_shown'] = True
                    session['waiting_for_contacts'] = True
                    session['delivery_data'] = {
                        'weight': multiple_calculation['totals']['total_weight'],
                        'product_type': "разные товары",
                        'city': city,
                        'volume': multiple_calculation['totals']['total_volume']
                    }
                    
                    # Показываем детальный расчет для множественных товаров
                    response_message = format_multiple_items_response(multiple_calculation, city)
                    session['chat_history'] = chat_history
                    return jsonify({"response": response_message})
                else:
                    return jsonify({"response": "❌ Не удалось рассчитать стоимость для вашего заказа. Проверьте данные."})
            
            # Если не хватает данных для множественных товаров
            if not city:
                return jsonify({"response": "🏙️ Укажите город доставки (Алматы, Астана и т.д.)"})
            if not items:
                return jsonify({"response": "📦 Не удалось распознать товары. Укажите в формате: '5 коробок вещей 45×40×40 по 40 кг'"})

        # Продолжаем старую логику для одиночных товаров
        weight = delivery_info.get('weight')
        product_type = delivery_info.get('product_type')
        city = delivery_info.get('city')

        length, width, height = extract_dimensions(user_message)
        volume_direct = extract_volume(user_message)

        data_updated = False
        confirmation_parts = []

        # Только для одиночных товаров (не множественных)
        if not delivery_info.get('multiple_items', False):
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
        
        # Пошаговый сбор данных
        if not has_all_data and not calculation_shown and not data_updated:
            missing_data = []
            if not delivery_data['weight']:
                missing_data.append("вес груза (в кг)")
            if not delivery_data['product_type']:
                missing_data.append("тип товара")
            if not delivery_data.get('volume'):
                missing_data.append("габариты (Д×Ш×В в метрах или сантиметрах)")
            if not delivery_data['city']:
                missing_data.append("город доставки")
            
            if missing_data:
                response_message = "📝 Для расчета укажите: " + ", ".join(missing_data)
                
                # Конкретные подсказки
                if not delivery_data.get('volume') and delivery_data['weight']:
                    response_message += "\n\n💡 **Пример габаритов:** \"1.2×0.8×0.5\" или \"120×80×50\""
                elif not delivery_data['weight'] and delivery_data.get('volume'):
                    response_message += "\n\n💡 **Пример веса:** \"50 кг\" или \"вес 50\""
                
                session['delivery_data'] = delivery_data
                session['chat_history'] = chat_history
                return jsonify({"response": response_message})
        
        # ТРИГГЕР РАСЧЕТА - когда все данные собраны и расчет еще не показан
        if has_all_data and not calculation_shown:
            # Производим расчет
            quick_cost = calculate_quick_cost(
    delivery_data['weight'], 
    delivery_data['product_type'], 
    delivery_data['city'],
    delivery_data.get('volume'),
    EXCHANGE_RATE,
    DESTINATION_ZONES,
    T1_RATES_DENSITY,
    T2_RATES
            )
            
            if quick_cost:
                # Сразу показываем детальный расчет вместо вопроса
                detailed_response = calculate_detailed_cost(
                    quick_cost,
                    delivery_data['weight'], 
                    delivery_data['product_type'], 
                    delivery_data['city'],
                    EXCHANGE_RATE
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
            # Для множественных товаров
            if session.get('multiple_calculation'):
                # Запрос детального расчета
                if any(word in user_message.lower() for word in ['детальн', 'подробн', 'разбей', 'тариф', 'да', 'yes', 'конечно']):
                    multiple_calculation = session.get('multiple_calculation')
                    detailed_response = format_multiple_items_response(multiple_calculation, delivery_data['city'])
                    session['waiting_for_contacts'] = True
                    session['chat_history'] = chat_history
                    return jsonify({"response": detailed_response})
                
                # Запрос на оформление заявки
                if any(word in user_message.lower() for word in ['заявк', 'оставь', 'свяж', 'контакт', 'позвон', 'менеджер', 'дальше', 'продолж']):
                    session['waiting_for_contacts'] = True
                    session['chat_history'] = chat_history
                    return jsonify({"response": "Отлично! Для связи укажите:\n• Ваше имя\n• Номер телефона\n\nНапример: 'Аслан, 87001234567'"})
            
            # Старая логика для одиночных товаров
            else:
                # Запрос детального расчета
                if any(word in user_message.lower() for word in ['детальн', 'подробн', 'разбей', 'тариф', 'да', 'yes', 'конечно']):
                    detailed_response = calculate_detailed_cost(
                        session.get('quick_cost'),
                        delivery_data['weight'], 
                        delivery_data['product_type'], 
                        delivery_data['city'],
                        EXCHANGE_RATE
                    )
                    session['waiting_for_contacts'] = True
                    session['chat_history'] = chat_history
                    return jsonify({"response": detailed_response})
                
                # Запрос на оформление заявки
                if any(word in user_message.lower() for word in ['заявк', 'оставь', 'свяж', 'контакт', 'позвон', 'менеджер', 'дальше', 'продолж']):
                    session['waiting_for_contacts'] = True
                    session['chat_history'] = chat_history
                    return jsonify({"response": "Отлично! Для связи укажите:\n• Ваше имя\n• Номер телефона\n\nНапример: 'Аслан, 87001234567'"})
        
        # Обработка общих вопросов через Gemini (fallback)
        context_lines = []
        if len(chat_history) > 0:
            context_lines.append("История диалога:")
            for msg in chat_history[-3:]:
                context_lines.append(msg)
        
        context_lines.append("\nКонтекст диалога (история + данные):")
        if delivery_data['weight']:
            context_lines.append(f"- Вес: {delivery_data['weight']} кг")
        if delivery_data['product_type']:
            context_lines.append(f"- Товар: {delivery_data['product_type']}")
        if delivery_data['city']:
            context_lines.append(f"- Город: {delivery_data['city']}")
        if delivery_data.get('volume'):
            context_lines.append(f"- Объем: {delivery_data['volume']:.3f} м³")
        if calculation_shown:
            context_lines.append(f"- Расчет показан: Да")
        if session.get('multiple_calculation'):
            context_lines.append(f"- Множественные товары: Да")
        
        context = "\n".join(context_lines)
        bot_response = get_gemini_response(user_message, context)
        chat_history.append(f"Ассистент: {bot_response}")
        
        # Ограничение истории
        if len(chat_history) > 8:
            chat_history = chat_history[-8:]
        
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

