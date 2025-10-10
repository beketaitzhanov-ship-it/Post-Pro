from flask import Flask, render_template, request, jsonify, session
import os
import re
from datetime import datetime
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from dotenv import load_dotenv
import socket

# Загружаем переменные из .env файла
load_dotenv()

GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")

app = Flask(__name__)
app.secret_key = 'postpro-secret-key-2024'

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

1. **РАСЧЕТ СТОИМОСТИ:**
   - Когда собраны вес, товар и город - СРАЗУ показывай полный расчет стоимости
   - НЕ используй формат [РАСЧЕТ] - показывай готовые цифры
   - После расчета спроси: "Хотите узнать подробнее о процедуре доставки?"

2. **ПОНИМАНИЕ КОНТЕКСТА:** 
   - Понимай сообщения с орфографическими и грамматическими ошибками
   - Исправляй ошибки автоматически и понимай настоящий смысл
   - Запоминай всю информацию из диалога

3. **СБОР ДАННЫХ ДЛЯ РАСЧЕТА:**
   - Основные данные: вес, тип товара, город доставки
   - Если данных достаточно - сразу делай расчет
   - Если не хватает данных - спроси ТОЛЬКО недостающие

4. **ФОРМАТЫ ВЫВОДА:**
   - Заявка: `[ЗАЯВКА] Имя: [Имя], Телефон: [Номер]`
   - Процедура: `[ПРОЦЕДУРА]`

5. **УМНЫЙ ДИАЛОГ:**
   - Если клиент говорит "сколько будет стоит?" после указания данных - сразу делай расчет
   - Если клиент меняет данные - обновляй информацию
   - Всегда будь полезным и отвечай на любые вопросы

Примеры понимания:
- "у маня 50кг адежда в астану" → сразу покажи расчет для 50кг одежды в Астану
- "сколка будет стоить?" → сделай расчет с текущими данными
- "лекарсива" → понимай как "лекарства"

Всегда будь дружелюбным и профессиональным! 😊
"""

# --- НАСТРОЙКА И ИНИЦИАЛИЗАЦИЯ МОДЕЛИ GEMINI ---
model = None
try:
    if not GEMINI_API_KEY:
        print("!!! КРИТИЧЕСКАЯ ОШИБКА: API-ключ не найден в .env файле.")
    else:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name='models/gemini-2.0-flash',
            system_instruction=SYSTEM_INSTRUCTION
        )
        print(">>> Модель Gemini успешно инициализирована.")
except Exception as e:
    print(f"!!! КРИТИЧЕСКАЯ ОШИБКА: Не удалось настроить Gemini. Ошибка: {e}")

# --- КАЛЬКУЛЯТОР ТАРИФОВ ---
def calculate_delivery_cost(weight: float, product_type: str, city: str):
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
        f"   - ${price_per_kg}/кг × {weight} кг = ${cost_to_almaty_usd:,.2f} USD\n"
        f"   - По курсу {EXCHANGE_RATE} тенге/$ = {cost_to_almaty_kzt:,.0f} тенге\n\n"
        f"2. **Доставка по Казахстану (Зона {zone}):**\n"
        f"   - {zone_rates_kzt.get(zone, 250)} тенге/кг × {weight} кг = {delivery_in_kz_kzt:,.0f} тенге\n\n"
        f"3. **Комиссия компании (20%):**\n"
        f"   - ({cost_to_almaty_kzt:,.0f} + {delivery_in_kz_kzt:,.0f}) × 20% = {(cost_to_almaty_kzt + delivery_in_kz_kzt) * 0.20:,.0f} тенге\n\n"
        f"------------------------------------\n"
        f"💰 **ИТОГО:** ≈ **{total_cost:,.0f} тенге**\n\n"
        f"💡 **Страхование груза:** дополнительно 1% от стоимости груза\n\n"
        f"Хотите узнать подробнее о процедуре доставки? ✨"
    )
    return response_text.replace(",", " ")

def get_delivery_procedure():
    return """📦 **Процедура доставки:**

1. **Прием груза в Китае:** Ваш груз прибудет на наш склад в Китае (ИУ или Гуанчжоу)
2. **Осмотр и обработка:** Мы проводим внешний осмотр груза, взвешиваем, фотографируем
3. **Дополнительные услуги:** При необходимости предлагаем услуги дополнительного сканирования и обрешетки (упаковка в защитную пленку)
4. **Подтверждение:** Присылаем Вам уведомление с деталями груза
5. **Отправка:** После Вашего согласия отправляем груз до нашего сортировочного центра в Алматы
6. **Получение:** Вы можете забрать груз с сортировочного склада в Алматы или заказать доставку до двери по Казахстану

Есть ли еще вопросы? 😊"""

def save_application(details):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"----------------------------------------\nНовая заявка: {timestamp}\n{details}\n----------------------------------------\n\n"
        with open("applications.txt", "a", encoding="utf-8") as f: 
            f.write(log_entry)
    except Exception as e: 
        print(f"Ошибка при сохранении заявки: {e}")

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
                max_output_tokens=1500,
            )
        )
        return response.text
    except Exception as e:
        print(f"!!! ОШИБКА API ПРИ ГЕНЕРАЦИИ ОТВЕТА: {e}")
        return "Извините, в данный момент сервис временно недоступен. Пожалуйста, попробуйте позже."

# --- УМНОЕ ИЗВЛЕЧЕНИЕ ДАННЫХ ---
def extract_delivery_info(text):
    """Извлекает информацию о доставке из текста с учетом ошибок"""
    weight = None
    product_type = None
    city = None
    
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

@app.route('/')
def index(): 
    if 'delivery_data' not in session:
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None}
    if 'chat_history' not in session:
        session['chat_history'] = []
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json['message'].strip()
    
    # Инициализируем сессии
    if 'delivery_data' not in session:
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None}
    if 'chat_history' not in session:
        session['chat_history'] = []
    
    delivery_data = session['delivery_data']
    chat_history = session['chat_history']
    
    # Добавляем сообщение в историю
    chat_history.append(f"Клиент: {user_message}")
    
    # Приветственные сообщения
    if user_message.lower() in GREETINGS:
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None}
        session['chat_history'] = [f"Клиент: {user_message}"]
        return jsonify({"response": "Приветики! ✨ Рада вас видеть! Чем могу помочь? Рассчитать доставочку? 🚚"})
    
    # Проверяем запросы о процедуре доставки
    if any(word in user_message.lower() for word in ['процедур', 'процесс', 'как достав', 'как получ', 'расскажи о доставк']):
        return jsonify({"response": get_delivery_procedure()})
    
    # Проверяем запросы о стоимости (даже с ошибками)
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
    if len(chat_history) > 10:
        chat_history = chat_history[-10:]
    
    session['chat_history'] = chat_history
    
    # Обработка специальных команд
    if bot_response.strip().startswith("[ЗАЯВКА]"):
        details = bot_response.replace("[ЗАЯВКА]", "").strip()
        save_application(details)
        session['delivery_data'] = {'weight': None, 'product_type': None, 'city': None}
        session['chat_history'] = []
        return jsonify({"response": "Готово! Ваша заявочка сохранена! ✨ Наш менеджер скоро с вами свяжется."})
    
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
    print("")
    print("🔗 Чтобы получить ссылку для ДРУГА (из интернета):")
    print("   1. Открой НОВОЕ окно командной строки")
    print("   2. Выполни: ssh -R 80:localhost:5000 serveo.net")
    print("   3. На вопрос 'Are you sure...' напиши: yes")
    print("   4. Скопируй полученную ссылку и отправь другу")
    print("")
    print("⏹️  Чтобы остановить: Ctrl+C")
    print("")
    
    # Запускаем сервер
    app.run(debug=True, host='0.0.0.0', port=5000)
