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
    1: 150,  # Зона 1 (Алматы и область)
    2: 200,  # Зона 2 (Южный Казахстан)
    3: 250,  # Зона 3 (Центральный и Северный Казахстан)
    4: 350,  # Зона 4 (Западный Казахстан)
    5: 450   # Зона 5 (Прикаспийский регион)
}

GREETINGS = ["привет", "здравствуй", "здравствуйте", "салем", "сәлем", "добрый день", "добрый вечер", "доброе утро"]

# --- СИСТЕМНЫЙ ПРОМПТ ---
SYSTEM_INSTRUCTION = """
Ты — умный ассистент компании PostPro. Твоя главная цель — помочь клиенту рассчитать стоимость доставки и оформить заявку.

***ВАЖНЫЕ ПРАВИЛА:***

1. **СКЛАДЫ В КИТАЕ:** У нас только 2 склада - ИУ и Гуанчжоу. Если клиент спрашивает "откуда заберете?" - отвечай: "Уточните у вашего поставщика, какой склад ему ближе - ИУ или Гуанчжоу"

2. **ТАРИФЫ:**
   - Т1: Доставка из Китая до Алматы
   - Т2: Доставка по Казахстану до двери

3. **ЛОГИКА ДИАЛОГА:**
   - Сначала быстрый расчет
   - Потом предлагай детальный расчет
   - В конце предлагай заявку

4. **СБОР ЗАЯВКИ:**
   - Когда клиент пишет имя и телефон - сохраняй заявку
   - Формат: [ЗАЯВКА] Имя: [имя], Телефон: [телефон]

5. **НЕ УПОМИНАЙ:** другие города Китая кроме ИУ и Гуанчжоу

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

# --- ФУНКЦИИ РАСЧЕТА ---
def calculate_quick_cost(weight: float, product_type: str, city: str):
    """Быстрый расчет стоимости"""
    try:
        # Т1: Китай → Алматы
        product_type_lower = product_type.lower()
        t1_rate = T1_RATES.get(product_type_lower, 2.40)
        t1_cost_usd = weight * t1_rate
        t1_cost_kzt = t1_cost_usd * EXCHANGE_RATE
        
        # Т2: Алматы → город назначения
        zone = DESTINATION_ZONES.get(city.lower(), 3)
        t2_rate = T2_RATES.get(zone, 250)
        t2_cost_kzt = weight * t2_rate
        
        # Итоговая стоимость (Т1 + Т2 + 20% комиссия)
        total_cost = (t1_cost_kzt + t2_cost_kzt) * 1.20
        
        return {
            't1_cost': t1_cost_kzt,
            't2_cost': t2_cost_kzt, 
            'total': total_cost,
            'zone': zone
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
    
    # Находим актуальную ставку Т1
    product_type_lower = product_type.lower()
    t1_rate = T1_RATES.get(product_type_lower, 2.40)
    
    response = (
        f"📊 **Детальный расчет для {weight} кг «{product_type}» в г. {city.capitalize()}:**\n\n"
        f"**Т1: Доставка из Китая до Алматы**\n"
        f"• ${t1_rate}/кг × {weight} кг = ${weight * t1_rate:.2f} USD\n"
        f"• По курсу {EXCHANGE_RATE} тенге/$ = {t1_cost:.0f} тенге\n\n"
        f"**Т2: Доставка по Казахстану (Зона {zone})**\n"
        f"• {T2_RATES[zone]} тенге/кг × {weight} кг = {t2_cost:.0f} тенге\n\n"
        f"**Комиссия компании (20%):**\n"
        f"• ({t1_cost:.0f} + {t2_cost:.0f}) × 20% = {(t1_cost + t2_cost) * 0.20:.0f} тенге\n\n"
        f"------------------------------------\n"
        f"💰 **ИТОГО:** ≈ **{total:.0f} тенге**\n\n"
        f"💡 **Страхование:** дополнительно 1% от стоимости груза\n\n"
        f"✅ **Хотите оставить заявку?** Напишите ваше имя и телефон!"
    )
    return response

def get_delivery_procedure():
    return """📦 **Процедура доставки:**

1. **Прием груза в Китае:** Ваш груз прибудет на наш склад в Китае (ИУ или Гуанчжоу)
2. **Осмотр и обработка:** Взвешиваем, фотографируем, упаковываем
3. **Подтверждение:** Присылаем детали груза
4. **Отправка:** Доставляем до Алматы (Т1) или до двери (Т2)
5. **Получение:** Забираете в Алматы или получаете доставку

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

def extract_contact_info(text):
    """Извлечение контактных данных"""
    name = None
    phone = None
    
    # Поиск телефона
    phone_match = re.search(r'[\+]?[7|8]?[\s]?[\(]?(\d{3})[\)]?[\s]?(\d{3})[\s]?[\-]?(\d{2})[\s]?[\-]?(\d{2})', text)
    if phone_match:
        phone = re.sub(r'\D', '', text)
        if phone.startswith('8'):
            phone = '7' + phone[1:]
        elif not phone.startswith('7'):
            phone = '7' + phone
    
    # Поиск имени (первое слово из 2+ букв)
    name_match = re.search(r'\b([А-Яа-яA-Za-z]{2,})\b', text)
    if name_match:
        name = name_match.group(1).capitalize()
    
    return name, phone

# --- ROUTES ---
@app.route('/')
def index(): 
    if 'delivery_data' not in session:
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None}
    if 'chat_history' not in session:
        session['chat_history'] = []
    if 'waiting_for_contacts' not in session:
        session['waiting_for_contacts'] = False
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message:
            return jsonify({"response": "Пожалуйста, введите сообщение."})
        
        # Инициализация сессий
        delivery_data = session.get('delivery_data', {'weight': None, 'product_type': None, 'city': None})
        chat_history = session.get('chat_history', [])
        waiting_for_contacts = session.get('waiting_for_contacts', False)
        
        chat_history.append(f"Клиент: {user_message}")
        
        # Приветствия
        if user_message.lower() in GREETINGS:
            session.update({
                'delivery_data': {'weight': None, 'product_type': None, 'city': None},
                'chat_history': [f"Клиент: {user_message}"],
                'waiting_for_contacts': False
            })
            return jsonify({"response": "Привет! 👋 Я ассистент Post Pro. Помогу рассчитать доставку из Китая в Казахстан! Укажите вес, тип товара и город доставки."})
        
        # Если ждем контакты
        if waiting_for_contacts:
            name, phone = extract_contact_info(user_message)
            if name and phone:
                details = f"Имя: {name}, Телефон: {phone}"
                if delivery_data['weight'] and delivery_data['city']:
                    details += f", Вес: {delivery_data['weight']} кг, Товар: {delivery_data.get('product_type', 'общие товары')}, Город: {delivery_data['city']}"
                
                save_application(details)
                session.update({
                    'delivery_data': {'weight': None, 'product_type': None, 'city': None},
                    'chat_history': [],
                    'waiting_for_contacts': False
                })
                return jsonify({"response": "🎉 Спасибо, что выбрали Post Pro! Менеджер свяжется с вами в течение 15 минут. 📞"})
            else:
                return jsonify({"response": "Пожалуйста, укажите имя и телефон. Например: 'Аслан, 87001234567'"})
        
        # Запросы о заявке
        if any(word in user_message.lower() for word in ['заявк', 'оставь', 'свяж', 'контакт', 'позвон', 'менеджер']):
            session['waiting_for_contacts'] = True
            return jsonify({"response": "Отлично! Для связи укажите:\n• Ваше имя\n• Номер телефона\n\nНапример: 'Аслан, 87001234567'"})
        
        # Процедура доставки
        if any(word in user_message.lower() for word in ['процедур', 'процесс', 'как достав', 'как получ']):
            return jsonify({"response": get_delivery_procedure()})
        
        # Технология
        if any(word in user_message.lower() for word in ['на каком ии', 'какой ии', 'технология']):
            return jsonify({"response": "Я работаю на базе Post Pro ИИ! 🚀"})
        
        # Извлечение данных о доставке
        weight, product_type, city = extract_delivery_info(user_message)
        if weight:
            delivery_data['weight'] = weight
        if product_type:
            delivery_data['product_type'] = product_type
        if city:
            delivery_data['city'] = city
        
        # Если есть все данные для расчета
        if delivery_data['weight'] and delivery_data['city']:
            if not delivery_data['product_type']:
                delivery_data['product_type'] = "общие товары"
            
           # Быстрый расчет
quick_cost = calculate_quick_cost(
    delivery_data['weight'], 
    delivery_data['product_type'], 
    delivery_data['city']
)

if quick_cost:
    # ПРОВЕРЯЕМ ДЕТАЛЬНЫЙ РАСЧЕТ В ОТДЕЛЬНОМ УСЛОВИИ
    if any(word in user_message.lower() for word in ['детальн', 'подробн', 'разбей', 'тариф', 'да']):
        detailed_response = calculate_detailed_cost(
            delivery_data['weight'], 
            delivery_data['product_type'], 
            delivery_data['city']
        )
        session['delivery_data'] = delivery_data
        session['chat_history'] = chat_history
        return jsonify({"response": detailed_response})
    
    # ЕСЛИ НЕ ДЕТАЛЬНЫЙ - ПОКАЗЫВАЕМ БЫСТРЫЙ
    quick_response = (
        f"🚚 **Быстрый расчет:**\n"
        f"• {delivery_data['weight']} кг «{delivery_data['product_type']}» в {delivery_data['city'].capitalize()}\n"
        f"• 💰 Примерная стоимость: **~{quick_cost['total']:.0f} тенге**\n\n"
        f"📊 Хотите детальный расчет с разбивкой по тарифам?"
    )
    
    session['delivery_data'] = delivery_data
    session['chat_history'] = chat_history
    return jsonify({"response": quick_response})
                else:
                    session['delivery_data'] = delivery_data
                    session['chat_history'] = chat_history
                    return jsonify({"response": quick_response})
        
        # Контекст для ИИ
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
        
        context = "\n".join(context_lines)
        bot_response = get_gemini_response(user_message, context)
        chat_history.append(f"Ассистент: {bot_response}")
        
        # Ограничение истории
        if len(chat_history) > 8:
            chat_history = chat_history[-8:]
        
        session['chat_history'] = chat_history
        session['delivery_data'] = delivery_data
        
        # Обработка специальных команд
        if bot_response.strip().startswith("[ЗАЯВКА]"):
            session['waiting_for_contacts'] = True
            return jsonify({"response": "Отлично! Для связи укажите:\n• Ваше имя\n• Номер телефона\n\nНапример: 'Аслан, 87001234567'"})
        
        return jsonify({"response": bot_response})
        
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        return jsonify({"response": "Извините, произошла ошибка. Попробуйте еще раз."})

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == '__main__':
    print("🎉 Бот запущен!")
    app.run(debug=False, host='0.0.0.0', port=5000)

