from flask import Flask, render_template, request, jsonify, session
import os
import re
import json
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
    EXCHANGE_RATE = config.get("EXCHANGE_RATE", 550)
    DESTINATION_ZONES = config.get("DESTINATION_ZONES", {})
    T1_RATES_DENSITY = config.get("T1_RATES_DENSITY", {})
    T2_RATES = config.get("T2_RATES", {})
    CUSTOMS_RATES = config.get("CUSTOMS_RATES", {})
    CUSTOMS_FEES = config.get("CUSTOMS_FEES", {})
    GREETINGS = config.get("GREETINGS", [])
else:
    logger.error("!!! Приложение запускается с значениями по умолчанию из-за ошибки загрузки config.json")
    EXCHANGE_RATE, DESTINATION_ZONES, T1_RATES_DENSITY, T2_RATES, CUSTOMS_RATES, CUSTOMS_FEES, GREETINGS = 550, {}, {}, {}, {}, {}, []

# --- НОВАЯ ФУНКЦИЯ ЗАГРУЗКИ ПРОМПТА ЛИЧНОСТИ ---
def load_personality_prompt():
    """Загружает промпт личности из файла personality_prompt.txt."""
    try:
        with open('personality_prompt.txt', 'r', encoding='utf-8') as f:
            prompt_text = f.read()
            logger.info(">>> Файл personality_prompt.txt успешно загружен.")
            return prompt_text
    except FileNotFoundError:
        logger.error("!!! Файл personality_prompt.txt не найден! Бот будет отвечать стандартно.")
        return "Ты — полезный ассистент."

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

# --- ФУНКЦИИ РАСЧЕТА ---
def extract_dimensions(text):
    """Извлекает габариты (длина, ширина, высота) из текста в метрах с конвертацией сантиметров."""
    pattern = r'(\d+(?:\.\d+)?)\s*[мmxх]?\s*(\d+(?:\.\d+)?)\s*[мmxх]?\s*(\d+(?:\.\d+)?)'
    match = re.search(pattern, text.lower())
    if match:
        try:
            length = float(match.group(1))
            width = float(match.group(2))
            height = float(match.group(3))
            
            # Конвертация сантиметров в метры (если число > 5, считаем что это сантиметры)
            if length > 5:
                length = length / 100
            if width > 5:
                width = width / 100
            if height > 5:
                height = height / 100
                
            return length, width, height
        except (ValueError, IndexError):
            return None, None, None
    return None, None, None

def get_t1_density_rule(product_type, weight, volume):
    """Находит и возвращает правило тарифа Т1 на основе плотности груза."""
    if not volume or volume <= 0:
        return None, None

    density = weight / volume
    
    rules = T1_RATES_DENSITY.get(product_type.lower())
    if not rules:
        rules = T1_RATES_DENSITY.get("мебель")

    for rule in sorted(rules, key=lambda x: x['min_density'], reverse=True):
        if density >= rule['min_density']:
            return rule, density
            
    return None, density

def calculate_quick_cost(weight: float, product_type: str, city: str, volume: float = None):
    """Быстрый расчет стоимости - единый центр всех расчетов"""
    try:
        # Получаем правило тарифа и плотность
        rule, density = get_t1_density_rule(product_type, weight, volume)
        if not rule:
            return None
        
        # Расчет Т1 на основе полученного правила
        price = rule['price']
        unit = rule['unit']
        
        if unit == "kg":
            cost_usd = price * weight
        elif unit == "m3":
            cost_usd = price * volume
        else:
            cost_usd = price * weight 
        
        t1_cost_kzt = cost_usd * EXCHANGE_RATE
        
        # Т2: Определяем тариф для города
        city_lower = city.lower()
        if city_lower == "алматы" or city_lower == "алмата":
            t2_rate = T2_RATES.get("алматы", 120)
            zone = "алматы"
        else:
            zone = DESTINATION_ZONES.get(city_lower, 3)
            t2_rate = T2_RATES.get(str(zone), 250)
        
        t2_cost_kzt = weight * t2_rate
        
        # Итоговая стоимость (Т1 + Т2 + 20% комиссия)
        total_cost = (t1_cost_kzt + t2_cost_kzt) * 1.20
        
        return {
            't1_cost': t1_cost_kzt,
            't2_cost': t2_cost_kzt, 
            'total': total_cost,
            'zone': zone,
            't2_rate': t2_rate,
            'volume': volume,
            'density': density,
            'rule': rule,
            't1_cost_usd': cost_usd
        }
    except Exception as e:
        logger.error(f"Ошибка расчета: {e}")
        return None

def calculate_detailed_cost(quick_cost, weight: float, product_type: str, city: str):
    """Детальный расчет с разбивкой по плотности (использует готовые данные из calculate_quick_cost)"""
    if not quick_cost:
        return "Ошибка расчета"
    
    # Извлекаем все данные из готового расчета
    t1_cost = quick_cost['t1_cost']
    t2_cost = quick_cost['t2_cost'] 
    total = quick_cost['total']
    zone = quick_cost['zone']
    t2_rate = quick_cost['t2_rate']
    volume = quick_cost['volume']
    density = quick_cost['density']
    rule = quick_cost['rule']
    t1_cost_usd = quick_cost['t1_cost_usd']
    
    # Формируем текст расчета Т1 в зависимости от единицы измерения
    price = rule['price']
    unit = rule['unit']
    if unit == "kg":
        calculation_text = f"${price}/кг × {weight} кг = ${t1_cost_usd:.2f} USD"
    elif unit == "m3":
        calculation_text = f"${price}/м³ × {volume:.3f} м³ = ${t1_cost_usd:.2f} USD"
    else:
        calculation_text = f"${price}/кг × {weight} кг = ${t1_cost_usd:.2f} USD"
    
    # Определяем текст для Т2 в зависимости от города
    city_name = city.capitalize()
    if zone == "алматы":
        t2_explanation = f"• Доставка по городу Алматы до вашего адреса"
        zone_text = "город Алматы"
        comparison_text = f"💡 **Если самовывоз со склада в Алматы:** {t1_cost:.0f} тенге"
    else:
        t2_explanation = f"• Доставка до вашего адреса в {city_name}"
        zone_text = f"Зона {zone}"
        comparison_text = f"💡 **Если самовывоз из Алматы:** {t1_cost:.0f} тенге"
    
    response = (
        f"📊 **Детальный расчет для {weight} кг «{product_type}» в г. {city_name}:**\n\n"
        
        f"**Т1: Доставка из Китая до Алматы**\n"
        f"• Плотность вашего груза: **{density:.1f} кг/м³**\n"
        f"• Применен тариф Т1: **${price} за {unit}**\n"
        f"• Расчет: {calculation_text}\n"
        f"• По курсу {EXCHANGE_RATE} тенге/$ = **{t1_cost:.0f} тенге**\n\n"
        
        f"**Т2: Доставка до двери ({zone_text})**\n"
        f"{t2_explanation}\n"
        f"• {t2_rate} тенге/кг × {weight} кг = **{t2_cost:.0f} тенге**\n\n"
        
        f"**Комиссия компании (20%):**\n"
        f"• ({t1_cost:.0f} + {t2_cost:.0f}) × 20% = **{(t1_cost + t2_cost) * 0.20:.0f} тенге**\n\n"
        
        f"------------------------------------\n"
        f"💰 **ИТОГО с доставкой до двери:** ≈ **{total:,.0f} тенге**\n\n"
        
        f"{comparison_text}\n\n"
        f"💡 **Страхование:** дополнительно 1% от стоимости груза\n"
        f"💳 **Оплата:** пост-оплата при получении\n\n"
        f"✅ **Хотите оставить заявку?** Напишите ваше имя и телефон!"
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

# --- ОБНОВЛЕННАЯ ФУНКЦИЯ ДЛЯ ОБЩЕНИЯ С GEMINI ---
def get_gemini_response(user_message, context=""):
    """Получает ответ от Gemini для общих вопросов."""
    if not model:
        return "Извините, сейчас я могу отвечать только на вопросы по доставке."
    
    try:
        # Формируем полный промпт, используя PERSONALITY_PROMPT
        full_prompt = f"{PERSONALITY_PROMPT}\n\nТекущий контекст диалога:\n{context}\n\nВопрос клиента: {user_message}\n\nТвой ответ:"
        
        response = model.generate_content(
            full_prompt,
            generation_config=GenerationConfig(
                temperature=0.8, # Повышена температура для более креативных ответов
                max_output_tokens=1000,
            )
        )
        return response.text
    except Exception as e:
        logger.error(f"Ошибка Gemini: {e}")
        return "Ой, кажется, у меня что-то пошло не так с креативной частью! Давайте лучше вернемся к расчету доставки, с этим я точно справлюсь. 😊"

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
        
        # Поиск типа товара (расширенные категории)
        product_keywords = {
            'мебель': ['мебель', 'стол', 'стул', 'кровать', 'шкаф', 'диван'],
            'автозапчасти': ['автозапчасти', 'запчасти', 'аксессуары авто', 'авто'],
            'аксессуары': ['аксессуары', 'сумк', 'ремен', 'очки', 'украшен'],
            'техника': ['техника', 'телефон', 'ноутбук', 'гаджет', 'электроника'],
            'продукты': ['продукты', 'еда', 'питание', 'напитки'],
            'ткани': ['ткани', 'текстиль', 'материал'],
            'инструменты': ['инструменты', 'инструмент', 'оборудование'],
            'белье': ['белье', 'бельё', 'белья', 'белью'],
            'игрушки': ['игрушки', 'игрушк', 'игра'],
            'одежда': ['одежда', 'адежда', 'одежд', 'костюм', 'платье'],
            'лекарства': ['лекарства', 'лекарсива', 'медикаменты', 'препарат'],
            'косметика': ['косметика', 'крем', 'шампунь', 'макияж', 'парфюм'],
            'посуда': ['посуда', 'тарелки', 'чашки', 'кухонная утварь'],
            'общие товары': ['товары', 'товар', 'разное', 'прочее', 'прочие']
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
    
    # Поиск имени (первое слово из 2+ русских/английских букв)
    name_match = re.search(r'^([а-яa-z]{2,})', clean_text)
    if name_match:
        name = name_match.group(1).capitalize()
    
    # Поиск телефона (разные форматы)
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

# --- ROUTES ---
@app.route('/')
def index(): 
    if 'delivery_data' not in session:
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None, 'volume': None}
    if 'chat_history' not in session:
        session['chat_history'] = []
    if 'waiting_for_contacts' not in session:
        session['waiting_for_contacts'] = False
    if 'calculation_shown' not in session:
        session['calculation_shown'] = False
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message:
            return jsonify({"response": "Пожалуйста, введите сообщение."})
        
        # Инициализация сессий
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
            return jsonify({"response": "Привет! 👋 Я ассистент Post Pro. Помогу рассчитать доставку из Китая в Казахстан!\n\n📦 **Для расчета укажите 4 параметра:**\n• **Вес груза** (в кг)\n• **Тип товара** (мебель, техника, одежда и т.д.)\n• **Габариты** (Д×Ш×В в метрах или сантиметрах)\n• **Город доставки**\n\n💡 **Пример:** \"50 кг мебель в Астану, габариты 120×80×50\""})
        
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
                
                return jsonify({"response": "🎉 Спасибо, что выбрали Post Pro! Менеджер свяжется с вами в течение 15 минут. 📞"})
            else:
                return jsonify({"response": "Не удалось распознать контакты. Пожалуйста, укажите в формате: 'Имя, 87001234567'"})
        
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
        
        # Извлечение данных о доставке
        weight, product_type, city = extract_delivery_info(user_message)
        length, width, height = extract_dimensions(user_message)

        if weight:
            delivery_data['weight'] = weight
        if product_type:
            delivery_data['product_type'] = product_type
        if city:
            delivery_data['city'] = city
        if length and width and height:
            delivery_data['length'] = length
            delivery_data['width'] = width  
            delivery_data['height'] = height
            delivery_data['volume'] = length * width * height
        
        # Проверка наличия всех данных для расчета
        has_all_data = (
            delivery_data['weight'] and 
            delivery_data['product_type'] and 
            delivery_data['city'] and 
            delivery_data.get('volume')
        )
        
        # Пошаговый сбор данных
        if not has_all_data and not calculation_shown:
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
                delivery_data.get('volume')
            )
            
            if quick_cost:
                # Показываем итоговую стоимость
                total_cost = quick_cost['total']
                response_message = (
                    f"✅ **Все данные получены!**\n\n"
                    f"📦 **Параметры груза:**\n"
                    f"• Вес: {delivery_data['weight']} кг\n"
                    f"• Товар: {delivery_data['product_type']}\n"
                    f"• Объем: {delivery_data['volume']:.3f} м³\n"
                    f"• Город: {delivery_data['city'].capitalize()}\n\n"
                    f"💰 **Примерная стоимость доставки:** ~**{total_cost:,.0f} ₸**\n\n"
                    f"📊 Хотите увидеть детальный расчет с разбивкой по тарифам?"
                )
                
                # Сохраняем результат расчета в сессии
                session['quick_cost'] = quick_cost
                session['calculation_shown'] = True
                session['delivery_data'] = delivery_data
                session['chat_history'] = chat_history
                
                return jsonify({"response": response_message})
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
        
        # Обработка общих вопросов через Gemini
        context_lines = []
        if len(chat_history) > 0:
            context_lines.append("История диалога:")
            for msg in chat_history[-3:]:
                context_lines.append(msg)
        
        context_lines.append("\nТекущие данные:")
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
