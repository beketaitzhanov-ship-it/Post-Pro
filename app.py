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
    """Расчет тарифа T1 на основе плотности груза - возвращает (ставка, единица_измерения)"""
    
    # 1. Мебель, стройматериалы, оборудование, посуда, лампы
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
    
    # 2. Автозапчасти
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
    
    # 3. Аксессуары для телефонов, косметика, головные уборы, сумки
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
    
    # 4. Мелкая бытовая техника
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
    
    # 5. Продукты питания, чай
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
    
    # 6. Ткани / Текстиль / Одежда
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
        else: return None  # Для плотности ниже 100 требуется индивидуальный расчет
    
    # 7. Инструменты
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
    
    # 8. Постельное белье, полотенца, одеяла, белье
    elif product_type in ['белье', 'постельное белье', 'полотенца', 'одеяла']:
        if density >= 180: return (1.30, 'kg')
        else: return None  # Цена по запросу
    
    # 9. Игрушки
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
    
    # 10. Лекарства, медикаменты
    elif product_type in ['лекарства', 'медикаменты']:
        if density >= 300: return (2.90, 'kg')
        elif 200 <= density < 300: return (3.00, 'kg')
        elif 100 <= density < 200: return (3.10, 'kg')
        else: return (320, 'm3')
    
    # 11. Общие товары, вещи
    elif product_type in ['общие товары', 'вещи']:
        if density >= 400: return (2.20, 'kg')
        elif 300 <= density < 400: return (2.30, 'kg')
        elif 200 <= density < 300: return (2.40, 'kg')
        elif 100 <= density < 200: return (2.50, 'kg')
        else: return (260, 'm3')
    
    # Стандартный тариф для неизвестных категорий
    else:
        if density >= 200: return (2.40, 'kg')
        else: return (250, 'm3')

# --- ТАРИФЫ Т2 (Казпочта) ---
def calculate_t2_cost(weight, zone, is_fragile=False, is_village=False):
    """Расчет стоимости доставки Т2 по Казахстану"""
    # Базовые тарифы за первые 20 кг для каждой зоны
    base_rates = {
        1: 4200,  # Зона 1
        2: 4400,  # Зона 2  
        3: 4600,  # Зона 3
        4: 4800,  # Зона 4
        5: 5000   # Зона 5
    }
    
    # Тарифы за каждый последующий кг после 20 кг
    per_kg_rates = {
        1: 210,  # Зона 1
        2: 220,  # Зона 2
        3: 230,  # Зона 3
        4: 240,  # Зона 4
        5: 250   # Зона 5
    }
    
    if weight <= 20:
        # Для веса до 20 кг используем пропорциональный расчет
        base_rate = base_rates.get(zone, 4600)
        cost = (base_rate / 20) * weight
    else:
        # Для веса свыше 20 кг: базовый тариф + (вес - 20) * тариф за кг
        base_rate = base_rates.get(zone, 4600)
        per_kg = per_kg_rates.get(zone, 230)
        cost = base_rate + (weight - 20) * per_kg
    
    # Применяем коэффициенты
    if is_fragile:
        cost *= 1.5
    if is_village:
        cost *= 2.0
    
    return cost

# --- ОСНОВНАЯ ФУНКЦИЯ РАСЧЕТА СТОИМОСТИ ---
def calculate_quick_cost(weight: float, volume: float, product_type: str, city: str):
    """Расчет стоимости доставки с учетом плотности груза"""
    if volume is None or volume <= 0:
        return None

    # Рассчитываем плотность
    density = weight / volume
    
    # Получаем тариф T1
    t1_result = calculate_t1_rate_by_density(product_type, density)
    if t1_result is None:
        return None  # Требуется индивидуальный расчет
    
    t1_rate, unit = t1_result
    
    # Расчет стоимости T1
    if unit == 'kg':
        t1_cost_usd = weight * t1_rate
        t1_description = f"{weight} кг × {t1_rate} $/кг"
    else:  # unit == 'm3'
        t1_cost_usd = volume * t1_rate
        t1_description = f"{volume} м³ × {t1_rate} $/м³"
    
    # Конвертируем в тенге
    t1_cost_kzt = t1_cost_usd * EXCHANGE_RATE
    
    # Определяем зону для T2
    city_lower = city.lower()
    if city_lower in ["алматы", "алмата"]:
        zone = 1
        t2_cost_kzt = 120 * weight  # Доставка по Алматы
        t2_description = f"{weight} кг × 120 ₸/кг (Алматы)"
    else:
        zone = DESTINATION_ZONES.get(city_lower, 3)
        t2_cost_kzt = calculate_t2_cost(weight, zone)
        t2_description = f"Зона {zone}, {weight} кг"
    
    # Общая стоимость без сервисного сбора
    total_without_service = t1_cost_kzt + t2_cost_kzt
    
    # Сервисный сбор 20%
    service_fee = total_without_service * 0.20
    total_with_service = total_without_service + service_fee
    
    return {
        't1_cost': t1_cost_kzt,
        't2_cost': t2_cost_kzt,
        'service_fee': service_fee,
        'total': total_with_service,
        'zone': zone,
        'density': density,
        't1_rate_usd': t1_rate,
        't1_unit': unit,
        't1_description': t1_description,
        't2_description': t2_description
    }

# --- СИСТЕМНЫЕ ПРОМПТЫ ---
MAIN_SYSTEM_INSTRUCTION = """
Ты — умный ассистент компании PostPro. Твоя главная цель — помочь клиенту рассчитать стоимость доставки и оформить заявку.

***ВАЖНЫЕ ПРАВИЛА:***

1. **РАСЧЕТ ПО ПЛОТНОСТИ:** Всегда запрашивай объем груза (куб.м или габариты) для точного расчета
2. **ТАРИФЫ:** Стоимость зависит от плотности груза (вес/объем)
3. **СКЛАДЫ В КИТАЕ:** У нас только 2 склада - ИУ и Гуанчжоу
4. **ДОСТАВКА:** Т1 (Китай-Алматы) + Т2 (Алматы-город в Казахстане)
5. **СЕРВИСНЫЙ СБОР:** 20% от стоимости доставки

Всегда будь дружелюбным и профессиональным! 😊
"""

# --- ФУНКЦИИ ОБРАБОТКИ ДАННЫХ ---
def extract_delivery_info(text):
    """Извлечение информации о доставке из текста"""
    delivery_data = {}
    
    # Поиск веса
    weight_pattern = r'(\d+[,.]?\d*)\s*(кг|kg|kг)'
    weight_match = re.search(weight_pattern, text.lower())
    if weight_match:
        delivery_data['weight'] = float(weight_match.group(1).replace(',', '.'))
    
    # Поиск объема в м³
    volume_pattern = r'(\d+[,.]?\d*)\s*(м³|m³|м3|m3|куб|куб\.?м)'
    volume_match = re.search(volume_pattern, text.lower())
    if volume_match:
        delivery_data['volume'] = float(volume_match.group(1).replace(',', '.'))
    
    # Поиск габаритов
    dimensions_pattern = r'(\d+)\s*[хx×]\s*(\d+)\s*[хx×]\s*(\d+)\s*(см|cm)'
    dimensions_match = re.search(dimensions_pattern, text.lower())
    if dimensions_match:
        length = int(dimensions_match.group(1))
        width = int(dimensions_match.group(2))
        height = int(dimensions_match.group(3))
        delivery_data['volume'] = (length * width * height) / 1000000  # м³
    
    # Поиск города
    cities = list(DESTINATION_ZONES.keys()) + ["алматы", "алмата", "астана", "шымкент"]
    for city in cities:
        if city in text.lower():
            delivery_data['city'] = city
            break
    
    # Поиск категории товара
    product_categories = [
        'мебель', 'стройматериалы', 'оборудование', 'посуда', 'лампы',
        'автозапчасти', 'аксессуары для телефонов', 'косметика', 'головные уборы', 'сумки',
        'малая техника', 'продукты', 'чай', 'ткани', 'текстиль', 'инструменты',
        'белье', 'постельное белье', 'полотенца', 'одеяла', 'игрушки',
        'одежда', 'электроника', 'техника', 'лекарства', 'медикаменты', 'вещи', 'общие товары'
    ]
    
    for category in product_categories:
        if category in text.lower():
            delivery_data['product_type'] = category
            break
    
    # Поиск стоимости инвойса
    invoice_pattern = r'(\d+[,.]?\d*)\s*(usd|\$|доллар)'
    invoice_match = re.search(invoice_pattern, text.lower())
    if invoice_match:
        delivery_data['invoice_value'] = float(invoice_match.group(1).replace(',', '.'))
    
    return delivery_data

def get_missing_data(delivery_data):
    """Определение недостающих данных"""
    missing = []
    
    if not delivery_data.get('weight'):
        missing.append("вес груза (кг)")
    
    if not delivery_data.get('volume'):
        missing.append("объем груза (м³) или габариты (Д×Ш×В в см)")
    
    if not delivery_data.get('city'):
        missing.append("город назначения")
    
    if not delivery_data.get('product_type'):
        missing.append("категория товара")
    
    return missing

def format_cost(cost_dict):
    """Форматирование результата расчета стоимости"""
    if not cost_dict:
        return "❌ Не удалось рассчитать стоимость. Проверьте введенные данные."
    
    density = cost_dict.get('density', 0)
    
    result = f"""📊 **Результат расчета:**

📦 Плотность груза: {density:.1f} кг/м³
💰 **Стоимость доставки:**

• Т1 (Китай → Алматы): {cost_dict['t1_cost']:,.0f} ₸
  ({cost_dict['t1_description']})

• Т2 (Алматы → город назначения): {cost_dict['t2_cost']:,.0f} ₸
  ({cost_dict['t2_description']})

• Услуга сервиса (20%): {cost_dict['service_fee']:,.0f} ₸

💎 **Итого к оплате: {cost_dict['total']:,.0f} ₸**

💡 *Расчет основан на плотности груза*
"""
    return result

# --- ИНИЦИАЛИЗАЦИЯ МОДЕЛЕЙ ---
main_model = None

def initialize_models():
    """Инициализация моделей Gemini"""
    global main_model
    try:
        if GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            main_model = genai.GenerativeModel(
                model_name='models/gemini-2.0-flash',
                system_instruction=MAIN_SYSTEM_INSTRUCTION
            )
            logger.info(">>> Модели Gemini успешно инициализированы.")
            return True
        else:
            logger.error("!!! API ключ не найден")
            return False
    except Exception as e:
        logger.error(f"!!! Ошибка инициализации Gemini: {e}")
        return False

# --- FLASK ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        
        if not user_message:
            return jsonify({'response': 'Пожалуйста, введите сообщение'})
        
        # Инициализация сессии
        if 'delivery_data' not in session:
            session['delivery_data'] = {}
        if 'waiting_for_contacts' not in session:
            session['waiting_for_contacts'] = False
        
        delivery_data = session['delivery_data']
        
        # Извлечение данных из сообщения
        extracted_data = extract_delivery_info(user_message)
        delivery_data.update(extracted_data)
        
        # Проверка на приветствие
        greetings = ["привет", "здравствуй", "здравствуйте", "салем", "сәлем", "добрый день", "добрый вечер", "доброе утро"]
        if any(greeting in user_message.lower() for greeting in greetings):
            response = "🚚 Добро пожаловать в PostPro!\n\nЯ помогу рассчитать стоимость доставки из Китая в Казахстан.\n\n💡 **Для расчета укажите:**\n• Вес груза (например: 50 кг)\n• Объем (м³) или габариты (Д×Ш×В в см)\n• Тип товара (одежда, электроника, мебель и т.д.)\n• Город доставки в Казахстане\n\n✨ **Примеры запросов:**\n\"50 кг одежды в Астану, объем 0.5 м³\"\n\"100 кг электроники в Алматы, габариты 120x80x60 см\"\n\"200 кг мебели в Шымкент, объем 2.5 м³\"\n\n💎 *Расчет производится по плотности груза для оптимальной стоимости*"
            return jsonify({'response': response})
        
        # Проверка недостающих данных
        missing = get_missing_data(delivery_data)
        
        if missing:
            if 'объем' in ' '.join(missing).lower() and delivery_data.get('weight'):
                response = f"📏 Для точного расчета укажите объем груза (в м³) или габариты (Д×Ш×В в см)"
            else:
                response = f"📋 Для расчета стоимости укажите: {', '.join(missing)}"
            return jsonify({'response': response})
        
        # Расчет стоимости
        cost_result = calculate_quick_cost(
            weight=delivery_data['weight'],
            volume=delivery_data['volume'],
            product_type=delivery_data['product_type'],
            city=delivery_data['city']
        )
        
        if cost_result is None:
            density = delivery_data['weight'] / delivery_data['volume']
            response = f"❌ Для {delivery_data['product_type']} с плотностью {density:.1f} кг/м³ требуется индивидуальный расчет.\n\n📞 Пожалуйста, свяжитесь с менеджером: +7 XXX XXX-XX-XX"
        else:
            response = format_cost(cost_result)
            
            # Предложение оформить заявку
            response += "\n\n✅ Хотите оформить доставку? Укажите ваши контактные данные (имя и телефон)"
            session['waiting_for_contacts'] = True
        
        session['delivery_data'] = delivery_data
        return jsonify({'response': response})
        
    except Exception as e:
        logger.error(f"Ошибка в chat: {e}")
        return jsonify({'response': 'Произошла ошибка. Пожалуйста, попробуйте еще раз.'})

@app.route('/clear', methods=['POST'])
def clear_session():
    """Очистка сессии"""
    session.clear()
    return jsonify({'status': 'success'})

# --- ЗАПУСК ПРИЛОЖЕНИЯ ---
if __name__ == '__main__':
    if initialize_models():
        # Получаем IP-адрес для доступа с других устройств
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        logger.info(f"=== PostPro Chat Bot запущен ===")
        logger.info(f"Локальный доступ: http://localhost:5000")
        logger.info(f"Сетевой доступ: http://{local_ip}:5000")
        logger.info(f"=================================")
        
        app.run(host='0.0.0.0', port=5000, debug=True)
    else:
        logger.error("!!! Не удалось инициализировать модели Gemini")
