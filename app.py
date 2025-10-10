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

# Загружаем переменные из .env файла
load_dotenv()

GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")

app = Flask(__name__)
app.secret_key = 'postpro-secret-key-2024'
app.config['PERMANENT_SESSION_LIFETIME'] = 1800  # 30 минут

# --- БАЗА ДАННЫХ И КОНСТАНТЫ ---
DESTINATION_ZONES = {
    "талдыкорган": 1, "конаев": 1, "текели": 1, "капчагай": 1, "есик": 1, "талгар": 1, "каскелен": 1, "жаркент": 1, "сарканд": 1, "аксу": 1,
    "тараз": 2, "шымкент": 2, "туркестан": 2, "аулиеата": 2, "кордай": 2, "мерке": 2, "мойынкум": 2, "жанатас": 2, "каратау": 2, "шу": 2, "кент": 2,
    "астана": 3, "кокшетау": 3, "степногорск": 3, "атбасар": 3, "ерементау": 3, "макинск": 3, "караганда": 3, "балхаш": 3, "темиртау": 3, "шахтинск": 3, "жезказган": 3, "сатпаев": 3, "кызылорда": 3, "казалынск": 3, "жанакорган": 3, "петропавловск": 3, "павлодар": 3, "экибастуз": 3, "костанай": 3, "рудный": 3, "семей": 3, "курчатов": 3, "аягоз": 3,
    "актобе": 4, "хромтау": 4, "шалкар": 4, "уральск": 4, "аксай": 4, "чингирлау": 4,
    "атырау": 5, "кульсары": 5, "актау": 5, "жанаозен": 5, "бейнеу": 5
}
EXCHANGE_RATE = 550
PRODUCT_TYPES = {
    "ткани": 1.70, "одежда": 1.70, "инструменты": 2.10, "общие товары": 2.40, "мебель": 2.10, 
    "косметика": 2.30, "автозапчасти": 2.40, "малая техника": 2.50, "продукты": 2.70, 
    "белье": 2.80, "лекарства": 2.90, "лекарсива": 2.90, "медикаменты": 2.90, "посуда": 2.20
}
GREETINGS = ["привет", "здравствуй", "здравствуйте", "салем", "сәлем", "добрый день", "добрый вечер", "доброе утро", "саламалейкум", "ассаламу алейкум", "hi", "hello"]

# --- "МОЗГ" БОТА ---
SYSTEM_INSTRUCTION = f"""
Ты — умный и дружелюбный ИИ-ассистент компании PostPro. 

***ТВОЯ УЛУЧШЕННАЯ ЛОГИКА:***

1. **СБОР ЗАЯВОК - ГЛАВНЫЙ ПРИОРИТЕТ:**
   - Когда клиент говорит "хочу оставить заявку", "контакты", "свяжитесь со мной", "менеджер", "позвоните" - сразу переходи к сбору данных
   - Формат: "Отлично! Для связи с вами, укажите пожалуйста:\n• Ваше имя\n• Номер телефона\n\nИли просто напишите имя и телефон в одном сообщении 📞"

2. **РАСЧЕТ СТОИМОСТИ:**
   - После расчета всегда предлагай: "Хотите оставить заявку на доставку?"
   - Если клиент соглашается - переходи к сбору контактов

3. **КОНТАКТНЫЕ ДАННЫЕ:**
   - При запросе контактов компании отвечай: "Свяжитесь с нами:\n📞 +7 777 777 77 77\n📧 info@postpro.kz\n\nИли оставьте ваши данные для обратной связи!"

4. **ПОДТВЕРЖДЕНИЕ ЗАЯВКИ:**
   - После получения имени и телефона: "Спасибо, что выбрали компанию Post Pro! 🎉 Наш менеджер свяжется с вами в течение 15 минут."

5. **ФОРМАТЫ ВЫВОДА:**
   - Заявка: `[ЗАЯВКА] Имя: [имя], Телефон: [телефон]`
   - Контакты: `[КОНТАКТЫ]`

Всегда будь дружелюбным и веди клиента к оформлению заявки! 😊
"""

# --- НАСТРОЙКА И ИНИЦИАЛИЗАЦИЯ МОДЕЛИ GEMINI ---
model = None
try:
    if not GEMINI_API_KEY:
        logger.error("!!! КРИТИЧЕСКАЯ ОШИБКА: API-ключ не найден в .env файле.")
    else:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name='models/gemini-2.0-flash',
            system_instruction=SYSTEM_INSTRUCTION
        )
        logger.info(">>> Модель Gemini успешно инициализирована.")
except Exception as e:
    logger.error(f"!!! КРИТИЧЕСКАЯ ОШИБКА: Не удалось настроить Gemini. Ошибка: {e}")

# --- КАЛЬКУЛЯТОР ТАРИФОВ ---
def calculate_delivery_cost(weight: float, product_type: str, city: str):
    try:
        # Нормализуем тип товара
        product_type_lower = product_type.lower()
        price_per_kg = PRODUCT_TYPES.get(product_type_lower, 2.40)
        
        # Если точного совпадения нет, ищем частичное
        if product_type_lower not in PRODUCT_TYPES:
            for key in PRODUCT_TYPES:
                if key in product_type_lower or product_type_lower in key:
                    price_per_kg = PRODUCT_TYPES[key]
                    product_type = key
                    break
        
        cost_to_almaty_usd = weight * price_per_kg
        cost_to_almaty_kzt = cost_to_almaty_usd * EXCHANGE_RATE
        zone = DESTINATION_ZONES.get(city.lower(), 3)
        zone_rates_kzt = {1: 150, 2: 200, 3: 250, 4: 350, 5: 450}
        delivery_in_kz_kzt = weight * zone_rates_kzt.get(zone, 250)
        total_cost = (cost_to_almaty_kzt + delivery_in_kz_kzt) * 1.20
        
        response_text = (
            f"📊 **Детальный расчет для {weight} кг «{product_type}» в г. {city.capitalize()}:**\n\n"
            f"1. **Доставка из Китая до Алматы:**\n"
            f"   - ${price_per_kg}/кг × {weight} кг = ${cost_to_almaty_usd:.2f} USD\n"
            f"   - По курсу {EXCHANGE_RATE} тенге/$ = {cost_to_almaty_kzt:.0f} тенге\n\n"
            f"2. **Доставка по Казахстану (Зона {zone}):**\n"
            f"   - {zone_rates_kzt.get(zone, 250)} тенге/кг × {weight} кг = {delivery_in_kz_kzt:.0f} тенге\n\n"
            f"3. **Комиссия компании (20%):**\n"
            f"   - ({cost_to_almaty_kzt:.0f} + {delivery_in_kz_kzt:.0f}) × 20% = {(cost_to_almaty_kzt + delivery_in_kz_kzt) * 0.20:.0f} тенге\n\n"
            f"------------------------------------\n"
            f"💰 **ИТОГО:** ≈ **{total_cost:.0f} тенге**\n\n"
            f"💡 **Страхование груза:** дополнительно 1% от стоимости груза\n\n"
            f"✅ **Хотите оставить заявку на доставку?** Просто напишите 'Оформить заявку' или укажите ваше имя и телефон! 📞"
        )
        return response_text
    except Exception as e:
        logger.error(f"Ошибка в расчете стоимости: {e}")
        return "Извините, произошла ошибка при расчете стоимости. Пожалуйста, попробуйте еще раз."

def get_delivery_procedure():
    return """📦 **Процедура доставки:**

1. **Прием груза в Китае:** Ваш груз прибудет на наш склад в Китае (ИУ или Гуанчжоу)
2. **Осмотр и обработка:** Мы проводим внешний осмотр груза, взвешиваем, фотографируем
3. **Дополнительные услуги:** При необходимости предлагаем услуги дополнительного сканирования и обрешетки
4. **Подтверждение:** Присылаем Вам уведомление с деталями груза
5. **Отправка:** После Вашего согласия отправляем груз до нашего сортировочного центра в Алматы
6. **Получение:** Вы можете забрать груз с сортировочного склада в Алматы или заказать доставку до двери

✅ **Хотите оформить заявку?** Напишите "Оформить заявку" или ваше имя и телефон! 😊"""

def save_application(details):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"----------------------------------------\nНовая заявка: {timestamp}\n{details}\n----------------------------------------\n\n"
        with open("applications.txt", "a", encoding="utf-8") as f: 
            f.write(log_entry)
        logger.info(f"Заявка сохранена: {details}")
    except Exception as e: 
        logger.error(f"Ошибка при сохранении заявки: {e}")

# --- ФУНКЦИЯ ОБЩЕНИЯ С ИИ ---
def get_gemini_response(user_message, context=""):
    if not model:
        return "Критическая ошибка: модель Gemini не была загружена."
    
    try:
        # Добавляем контекст диалога
        full_prompt = f"Контекст: {context}\n\nСообщение клиента: {user_message}\n\nТвой ответ:"
        
        response = model.generate_content(
            full_prompt,
            generation_config=GenerationConfig(
                temperature=0.7,
                top_p=0.9,
                top_k=50,
                max_output_tokens=1000,
            )
        )
        return response.text
    except Exception as e:
        logger.error(f"!!! ОШИБКА API ПРИ ГЕНЕРАЦИИ ОТВЕТА: {e}")
        return "Извините, в данный момент сервис временно недоступен. Пожалуйста, попробуйте позже."

# --- УМНОЕ ИЗВЛЕЧЕНИЕ ДАННЫХ ---
def extract_contact_info(text):
    """Извлекает имя и телефон из текста"""
    name = None
    phone = None
    
    # Поиск телефона
    phone_patterns = [
        r'[\+]?[7|8]?[\s]?[\(]?(\d{3})[\)]?[\s]?(\d{3})[\s]?[\-]?(\d{2})[\s]?[\-]?(\d{2})',
        r'(\d{3})[\s]?[\-]?(\d{3})[\s]?[\-]?(\d{2})[\s]?[\-]?(\d{2})',
        r'[\+]?7[\s]?\(?(\d{3})\)?[\s]?(\d{3})[\s]?[\-]?(\d{2})[\s]?[\-]?(\d{2})'
    ]
    
    for pattern in phone_patterns:
        phone_match = re.search(pattern, text)
        if phone_match:
            phone = re.sub(r'\D', '', text)  # Оставляем только цифры
            if phone.startswith('8'):
                phone = '7' + phone[1:]
            elif not phone.startswith('7'):
                phone = '7' + phone
            break
    
    # Простой поиск имени (первое слово из 2+ букв)
    name_match = re.search(r'\b([А-Яа-яA-Za-z]{2,})\b', text)
    if name_match:
        name = name_match.group(1).capitalize()
    
    return name, phone

def extract_delivery_info(text):
    """Извлекает информацию о доставке из текста с учетом ошибок"""
    weight = None
    product_type = None
    city = None
    
    try:
        # Улучшенный поиск веса
        weight_patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:кг|kg|килограмм|кило|кг)',
            r'вес\s*[:\-]?\s*(\d+(?:\.\d+)?)',
            r'(\d+)\s*(?:кило|кг)'
        ]
        
        for pattern in weight_patterns:
            weight_match = re.search(pattern, text.lower())
            if weight_match:
                weight = float(weight_match.group(1))
                break
        
        # Улучшенный поиск города (с учетом опечаток)
        text_lower = text.lower()
        for city_name in DESTINATION_ZONES:
            # Поиск точного или частичного совпадения
            if city_name in text_lower:
                city = city_name
                break
        
        # Улучшенный поиск типа товара (с учетом опечаток)
        product_keywords = {
            'одежда': ['одежда', 'адежда', 'одежд', 'кофта', 'футболка', 'куртка', 'брюки', 'верхняя', 'штаны'],
            'лекарства': ['лекарства', 'лекарсива', 'медикаменты', 'таблетки', 'препараты', 'лекарств'],
            'косметика': ['косметика', 'крем', 'шампунь', 'макияж', 'косметка'],
            'техника': ['техника', 'телефон', 'ноутбук', 'гаджет', 'электроника', 'техник'],
            'мебель': ['мебель', 'стол', 'стул', 'кровать', 'шкаф', 'мебел'],
            'посуда': ['посуда', 'посуду', 'тарелки', 'чашки', 'кастрюли', 'посуди'],
            'общие товары': ['товары', 'товар', 'разное', 'прочее', 'другое']
        }
        
        for prod_type, keywords in product_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                product_type = prod_type
                break
        
        return weight, product_type, city
    except Exception as e:
        logger.error(f"Ошибка при извлечении данных: {e}")
        return None, None, None

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
        
        # Инициализируем сессии
        if 'delivery_data' not in session:
            session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None}
        if 'chat_history' not in session:
            session['chat_history'] = []
        if 'waiting_for_contacts' not in session:
            session['waiting_for_contacts'] = False
        
        delivery_data = session['delivery_data']
        chat_history = session['chat_history']
        waiting_for_contacts = session['waiting_for_contacts']
        
        # Добавляем сообщение в историю
        chat_history.append(f"Клиент: {user_message}")
        
        # Приветственные сообщения
        if user_message.lower() in GREETINGS:
            session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None}
            session['chat_history'] = [f"Клиент: {user_message}"]
            session['waiting_for_contacts'] = False
            return jsonify({"response": "Привет! ✨ Рада вас видеть! Чем могу помочь? Рассчитать доставку? 🚚"})
        
        # Если ждем контакты от клиента
        if waiting_for_contacts:
            name, phone = extract_contact_info(user_message)
            
            if name and phone:
                # Сохраняем заявку
                details = f"Имя: {name}, Телефон: {phone}"
                if delivery_data['weight'] and delivery_data['city']:
                    details += f", Вес: {delivery_data['weight']} кг, Товар: {delivery_data.get('product_type', 'общие товары')}, Город: {delivery_data['city']}"
                
                save_application(details)
                
                # Сбрасываем состояние
                session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None}
                session['chat_history'] = []
                session['waiting_for_contacts'] = False
                
                return jsonify({"response": "🎉 Спасибо, что выбрали компанию Post Pro! Наш менеджер свяжется с вами в течение 15 минут. 📞\n\nХорошего дня! ✨"})
            else:
                # Если не удалось извлечь данные, просим еще раз
                return jsonify({"response": "Пожалуйста, укажите ваше имя и номер телефона для связи. Например: 'Иван, 87771234567' 📝"})
        
        # Проверяем запросы о заявке и контактах
        contact_keywords = ['заявк', 'оставь', 'свяж', 'контакт', 'позвон', 'менеджер', 'звонок', 'обратн', 'связь']
        if any(keyword in user_message.lower() for keyword in contact_keywords):
            session['waiting_for_contacts'] = True
            return jsonify({"response": "Отлично! Для связи с вами, укажите пожалуйста:\n• Ваше имя\n• Номер телефона\n\nИли просто напишите имя и телефон в одном сообщении 📞"})
        
        # Проверяем запросы о процедуре доставки
        if any(word in user_message.lower() for word in ['процедур', 'процесс', 'как достав', 'как получ', 'расскажи о доставк']):
            return jsonify({"response": get_delivery_procedure()})
        
        # Проверяем запросы о технологии
        if any(word in user_message.lower() for word in ['на каком ии', 'какой ии', 'кто тебя создал', 'технология', 'алгоритм']):
            return jsonify({"response": "Я работаю на базе Post Pro ИИ, специально разработанного для расчета доставки! 🚀"})
        
        # Проверяем запросы о стоимости
        cost_keywords = ['сколка', 'сколько', 'стоит', 'стоить', 'стоемость', 'цена', 'цену', 'расчет', 'рассчитай', 'посчитай']
        if any(keyword in user_message.lower() for keyword in cost_keywords):
            # Если есть достаточно данных для расчета
            if delivery_data['weight'] and delivery_data['city']:
                if not delivery_data['product_type']:
                    delivery_data['product_type'] = "общие товары"
                
                session['delivery_data'] = delivery_data
                calculation = calculate_delivery_cost(
                    delivery_data['weight'], 
                    delivery_data['product_type'], 
                    delivery_data['city']
                )
                return jsonify({"response": calculation})
        
        # Извлекаем информацию о доставке
        weight, product_type, city = extract_delivery_info(user_message)
        
        # Обновляем данные
        if weight:
            delivery_data['weight'] = weight
        if product_type:
            delivery_data['product_type'] = product_type
        if city:
            delivery_data['city'] = city
        
        # Формируем контекст для ИИ
        context_lines = []
        
        # Добавляем историю диалога (последние 3 сообщения)
        if len(chat_history) > 0:
            context_lines.append("История диалога:")
            for msg in chat_history[-3:]:
                context_lines.append(msg)
        
        # Добавляем текущие данные о доставке
        context_lines.append("\nТекущие данные для расчета:")
        if delivery_data['weight']:
            context_lines.append(f"- Вес: {delivery_data['weight']} кг")
        if delivery_data['product_type']:
            context_lines.append(f"- Товар: {delivery_data['product_type']}")
        if delivery_data['city']:
            context_lines.append(f"- Город: {delivery_data['city']}")
        
        context = "\n".join(context_lines)
        
        # Получаем ответ от ИИ
        bot_response = get_gemini_response(user_message, context)
        
        # Добавляем ответ бота в историю
        chat_history.append(f"Ассистент: {bot_response}")
        
        # Ограничиваем историю
        if len(chat_history) > 8:
            chat_history = chat_history[-8:]
        
        session['chat_history'] = chat_history
        session['delivery_data'] = delivery_data
        
        # Обработка специальных команд
        if bot_response.strip().startswith("[ЗАЯВКА]"):
            session['waiting_for_contacts'] = True
            return jsonify({"response": "Отлично! Для связи с вами, укажите пожалуйста:\n• Ваше имя\n• Номер телефона\n\nИли просто напишите имя и телефон в одном сообщении 📞"})
        
        if bot_response.strip().startswith("[КОНТАКТЫ]"):
            return jsonify({"response": "📞 **Контакты компании Post Pro:**\n\n• Телефон: +7 777 777 77 77\n• Email: info@postpro.kz\n• Instagram: @postpro.kz\n\n✅ Хотите, чтобы мы вам перезвонили? Просто напишите ваше имя и телефон! 📝"})
        
        if bot_response.strip().startswith("[ПРОЦЕДУРА]"):
            return jsonify({"response": get_delivery_procedure()})
        
        # АВТОМАТИЧЕСКИЙ РАСЧЕТ если есть все данные
        if delivery_data['weight'] and delivery_data['city']:
            if not delivery_data['product_type']:
                delivery_data['product_type'] = "общие товары"
            
            # Если в ответе ИИ нет расчета, но данные есть - делаем расчет
            if "расчет" not in bot_response.lower() and "стоимость" not in bot_response.lower():
                calculation = calculate_delivery_cost(
                    delivery_data['weight'], 
                    delivery_data['product_type'], 
                    delivery_data['city']
                )
                return jsonify({"response": calculation})
        
        return jsonify({"response": bot_response})
        
    except Exception as e:
        logger.error(f"Ошибка в обработке сообщения: {e}")
        return jsonify({"response": "Извините, произошла ошибка. Пожалуйста, попробуйте еще раз."})

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

def get_local_ip():
    """Получает локальный IP адрес"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

if __name__ == '__main__':
    local_ip = get_local_ip()
    
    print("🎉 Бот запущен!")
    print(f"📱 Для доступа в локальной сети: http://{local_ip}:5000")
    print("⏹️  Чтобы остановить: Ctrl+C")
    
    # Запускаем сервер
    app.run(debug=False, host='0.0.0.0', port=5000)
