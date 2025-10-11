from flask import Flask, render_template, request, jsonify
import requests
import os
import logging
import re
from datetime import datetime

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'your_gemini_api_key_here')
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"

# Тарифы доставки (в USD)
TARIFFS = {
    'T1': {
        'rate_per_kg': 2.5,
        'rate_per_m3': 350,
        'min_cost': 50
    },
    'T2': {
        'rate_per_kg': 1.8,
        'rate_per_m3': 280,
        'min_cost': 40
    }
}

# Сертификаты и дополнительные услуги
SERVICES = {
    'certificate_origin': 500,  # USD
    'certificate_conformity': 120000,  # Tenge
    'broker_consultation': 300  # USD
}

class ChatProcessor:
    def __init__(self):
        self.user_data = {}
    
    def extract_initial_info(self, message):
        """Извлекает вес, тип товара и город из сообщения пользователя"""
        info = {'weight': None, 'product_type': '', 'city': ''}
        
        # Поиск веса (число с единицами измерения)
        weight_pattern = r'(\d+(?:\.\d+)?)\s*(кг|kg|kilogram)'
        weight_match = re.search(weight_pattern, message.lower())
        if weight_match:
            info['weight'] = float(weight_match.group(1))
        
        # Поиск города (простейший вариант)
        cities = ['алмат', 'астана', 'шымкент', 'актобе', 'караганда', 'тараз', 'урьльск', 'павлодар']
        for city in cities:
            if city in message.lower():
                info['city'] = city.capitalize()
                break
        
        # Тип товара - оставшийся текст
        words = message.split()
        if info['weight']:
            words = [w for w in words if str(info['weight']) not in w]
        if info['city']:
            words = [w for w in words if info['city'].lower() not in w.lower()]
        
        info['product_type'] = ' '.join(words)
        
        return info
    
    async def get_tnved_info(self, product_name, tnved_code=None):
        """Получает информацию о коде ТН ВЭД через Gemini API"""
        if not GEMINI_API_KEY or GEMINI_API_KEY == 'your_gemini_api_key_here':
            return self.get_mock_tnved_info(product_name, tnved_code)
        
        try:
            prompt = f"""
            Проанализируй товар: "{product_name}" 
            """
            if tnved_code:
                prompt += f"Проверь правильность кода ТН ВЭД: {tnved_code}"
            else:
                prompt += "Предложи 2-3 наиболее вероятных кода ТН ВЭД ЕАЭС"
            
            prompt += """
            Ответь в формате JSON:
            {
                "verified": true/false,
                "tnved_codes": ["код1", "код2"],
                "customs_duty_rate": число,
                "requires_certification": true/false,
                "certification_type": "тип сертификата",
                "product_description": "описание"
            }
            """
            
            response = requests.post(
                GEMINI_URL,
                json={
                    "contents": [{
                        "parts": [{"text": prompt}]
                    }]
                },
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Gemini API error: {response.status_code}")
                return self.get_mock_tnved_info(product_name, tnved_code)
                
        except Exception as e:
            logger.error(f"Error calling Gemini: {e}")
            return self.get_mock_tnved_info(product_name, tnved_code)
    
    def get_mock_tnved_info(self, product_name, tnved_code=None):
        """Мок-данные для тестирования"""
        mock_data = {
            "verified": True,
            "tnved_codes": ["8516 79 910 0", "8516 79 900 0"],
            "customs_duty_rate": 5.0,
            "requires_certification": True,
            "certification_type": "Сертификат соответствия ТР ТС 004/2011",
            "product_description": "Электрические приборы для нагрева жидкостей"
        }
        return mock_data
    
    def calculate_shipping_cost(self, weight, volume, delivery_type):
        """Рассчитывает стоимость доставки"""
        tariff = TARIFFS[delivery_type]
        
        # Расчет по весу
        weight_cost = weight * tariff['rate_per_kg']
        
        # Расчет по объему (если указан)
        volume_cost = volume * tariff['rate_per_m3'] if volume else 0
        
        # Берем максимальную стоимость
        shipping_cost = max(weight_cost, volume_cost, tariff['min_cost'])
        
        return shipping_cost
    
    def calculate_customs_duty(self, product_value, duty_rate, has_origin_cert=False):
        """Рассчитывает таможенные платежи"""
        # Таможенная пошлина
        duty = product_value * (duty_rate / 100)
        
        # Скидка при наличии сертификата происхождения
        if has_origin_cert:
            duty *= 0.75  # 25% скидка
        
        # НДС (12% от стоимости товара + пошлина)
        vat = (product_value + duty) * 0.12
        
        # Таможенный сбор (фиксированный для примера)
        customs_fee = 100  # USD
        
        total_customs = duty + vat + customs_fee
        
        return {
            'duty': duty,
            'vat': vat,
            'customs_fee': customs_fee,
            'total_customs': total_customs
        }

chat_processor = ChatProcessor()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
async def chat():
    try:
        user_message = request.json.get('message', '').strip()
        user_session = request.json.get('session_id', 'default')
        
        if user_session not in chat_processor.user_data:
            chat_processor.user_data[user_session] = {
                'step': 'initial',
                'data': {}
            }
        
        session_data = chat_processor.user_data[user_session]
        response = await process_message(user_message, session_data)
        
        return jsonify(response)
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        return jsonify({
            'response': 'Извините, произошла ошибка. Пожалуйста, попробуйте еще раз.',
            'session_data': chat_processor.user_data.get(user_session, {})
        })

async def process_message(message, session_data):
    """Обрабатывает сообщение пользователя"""
    
    if session_data['step'] == 'initial':
        # Первое сообщение - извлекаем информацию
        initial_info = chat_processor.extract_initial_info(message)
        session_data['data'].update(initial_info)
        
        if not all([initial_info['weight'], initial_info['product_type'], initial_info['city']]):
            return {
                'response': 'Пожалуйста, укажите все данные в формате: "Вес тип_товара город"\nНапример: "100 кг электроники в Астану"',
                'session_data': session_data
            }
        
        # Переходим к выбору типа доставки
        session_data['step'] = 'choose_delivery_type'
        return {
            'response': f'''Отлично! Я понял:
• Вес: {initial_info["weight"]} кг
• Товар: {initial_info["product_type"]}
• Город: {initial_info["city"]}

Выберите тип доставки:

🟢 **КАРГО** (упрощенная доставка)
• Подходит для личных вещей, пробных партий
• Расчет по готовым тарифам Т1 и Т2
• Быстрый предварительный расчет

🔵 **ИНВОЙС** (полное таможенное оформление)
• Для коммерческих партий с оформлением документов
• Полный расчет таможенных платежей (пошлина, НДС, сертификаты)
• Подробный анализ по коду ТН ВЭД

**Что вам подходит?** (напишите "Карго" или "Инвойс")''',
            'session_data': session_data
        }
    
    elif session_data['step'] == 'choose_delivery_type':
        message_lower = message.lower()
        
        if 'карго' in message_lower:
            session_data['delivery_type'] = 'cargo'
            session_data['step'] = 'cargo_calculation'
            return await handle_cargo_calculation(session_data)
        
        elif 'инвойс' in message_lower:
            session_data['delivery_type'] = 'invoice'
            session_data['step'] = 'get_tnved_code'
            return {
                'response': 'Отлично! Для полного расчета потребуется код ТН ВЭД.\n\nВведите код ТН ВЭД, если он вам известен, или просто напишите название товара для автоматического определения.',
                'session_data': session_data
            }
        
        else:
            return {
                'response': 'Пожалуйста, выберите тип доставки: напишите "Карго" или "Инвойс"',
                'session_data': session_data
            }
    
    elif session_data['step'] == 'get_tnved_code':
        # Определяем код ТН ВЭД через Gemini
        product_name = session_data['data']['product_type']
        tnved_code = message if re.match(r'\d{4}\s\d{2}\s\d{3}\s\d', message) else None
        
        tnved_info = await chat_processor.get_tnved_info(product_name, tnved_code)
        session_data['tnved_info'] = tnved_info
        
        if tnved_code and not tnved_info.get('verified', False):
            session_data['step'] = 'tnved_not_verified'
            return {
                'response': f'''⚠️ **Внимание!** Код ТН ВЭД {tnved_code} может быть неверным для товара "{product_name}".

Вот наиболее вероятные коды:
{chr(10).join([f"• {code}" for code in tnved_info.get('tnved_codes', [])])}

**Рекомендация:** Для точного определения кода ТН ВЭД и избежания штрафов рекомендуем проконсультироваться с нашим брокером.

Выберите код из списка или подтвердите текущий (ответьте "Подтверждаю" для кода {tnved_code})''',
                'session_data': session_data
            }
        
        session_data['step'] = 'get_product_value'
        return {
            'response': f'''✅ Код ТН ВЭД определен: {tnved_info.get('tnved_codes', [''])[0]}
Описание: {tnved_info.get('product_description', '')}

Теперь укажите общую стоимость товара по инвойсу (в USD):''',
            'session_data': session_data
        }
    
    elif session_data['step'] == 'tnved_not_verified':
        if 'подтверждаю' in message.lower():
            session_data['step'] = 'get_product_value'
            return {
                'response': 'Теперь укажите общую стоимость товара по инвойсу (в USD):',
                'session_data': session_data
            }
        else:
            # Пользователь выбрал другой код
            session_data['tnved_info']['tnved_codes'] = [message]
            session_data['tnved_info']['verified'] = True
            session_data['step'] = 'get_product_value'
            return {
                'response': f'✅ Выбран код ТН ВЭД: {message}\n\nТеперь укажите общую стоимость товара по инвойсу (в USD):',
                'session_data': session_data
            }
    
    elif session_data['step'] == 'get_product_value':
        try:
            product_value = float(message)
            session_data['data']['product_value'] = product_value
            session_data['step'] = 'get_origin_certificate'
            
            currency_warning = ''
            if product_value > 50000:
                currency_warning = '\n\n⚠️ **Внимание:** Сумма контракта превышает $50,000. Потребуется регистрация в Нацбанке РК.'
            
            return {
                'response': f'''Есть ли у вас сертификат происхождения (Form A) на данный товар?
• **Да** - есть сертификат
• **Нет, но нужен** - нужно оформить
• **Нет, не нужен** - не требуется{currency_warning}''',
                'session_data': session_data
            }
        except ValueError:
            return {
                'response': 'Пожалуйста, введите корректную сумму в USD (только число):',
                'session_data': session_data
            }
    
    elif session_data['step'] == 'get_origin_certificate':
        message_lower = message.lower()
        
        if 'да' in message_lower:
            session_data['has_origin_cert'] = True
            session_data['needs_origin_cert'] = False
        elif 'нет, но нужен' in message_lower or 'нужен' in message_lower:
            session_data['has_origin_cert'] = False
            session_data['needs_origin_cert'] = True
        else:
            session_data['has_origin_cert'] = False
            session_data['needs_origin_cert'] = False
        
        return await generate_final_invoice_calculation(session_data)
    
    elif session_data['step'] == 'cargo_calculation':
        # Для карго просто рассчитываем доставку
        return await handle_cargo_calculation(session_data)
    
    return {
        'response': 'Извините, я не понял ваш запрос. Пожалуйста, начните заново.',
        'session_data': {'step': 'initial', 'data': {}}
    }

async def handle_cargo_calculation(session_data):
    """Обрабатывает расчет карго доставки"""
    data = session_data['data']
    weight = data['weight']
    
    # Расчет стоимости доставки
    t1_cost = chat_processor.calculate_shipping_cost(weight, None, 'T1')
    t2_cost = chat_processor.calculate_shipping_cost(weight, None, 'T2')
    
    response = f'''📦 **РАСЧЕТ ДОСТАВКИ КАРГО**

• Вес: {weight} кг
• Товар: {data["product_type"]}
• Город: {data["city"]}

**Тариф Т1** (склад-склад):
- Стоимость: ${t1_cost:.2f} USD
- Срок: 7-10 дней

**Тариф Т2** (до двери):
- Стоимость: ${t2_cost:.2f} USD  
- Срок: 10-14 дней

💡 *Стоимость является предварительной. Для точного расчета нужен объем груза.*

✅ **Ваша заявка принята!** Менеджер свяжется с вами в течение часа для уточнения деталей.'''
    
    # Сбрасываем сессию
    session_data['step'] = 'initial'
    session_data['data'] = {}
    
    return {
        'response': response,
        'session_data': session_data
    }

async def generate_final_invoice_calculation(session_data):
    """Генерирует финальный расчет для инвойса"""
    data = session_data['data']
    tnved_info = session_data['tnved_info']
    
    # Расчет доставки
    shipping_cost = chat_processor.calculate_shipping_cost(data['weight'], None, 'T2')
    
    # Расчет таможенных платежей
    customs_calc = chat_processor.calculate_customs_duty(
        data['product_value'],
        tnved_info.get('customs_duty_rate', 5),
        session_data.get('has_origin_cert', False)
    )
    
    # Дополнительные услуги
    additional_services = []
    total_additional = 0
    
    if session_data.get('needs_origin_cert'):
        additional_services.append(f"• Сертификат происхождения: ${SERVICES['certificate_origin']} USD")
        total_additional += SERVICES['certificate_origin']
    
    if tnved_info.get('requires_certification'):
        additional_services.append(f"• {tnved_info.get('certification_type', 'Сертификат соответствия')}: {SERVICES['certificate_conformity']:,} ₸")
        total_additional += SERVICES['certificate_conformity'] / 550  # Конвертация в USD
    
    # Общая стоимость
    total_cost_usd = shipping_cost + customs_calc['total_customs'] + total_additional
    total_cost_kzt = total_cost_usd * 550
    
    # Формируем ответ
    response = f'''📊 **ПОЛНЫЙ РАСЧЕТ ДОСТАВКИ С РАСТАМОЖКОЙ**

**Параметры груза:**
• Товар: {data["product_type"]}
• Код ТН ВЭД: {tnved_info.get('tnved_codes', [''])[0]}
• Вес: {data["weight"]} кг
• Город: {data["city"]}
• Стоимость товара: ${data["product_value"]:,.2f} USD

**Стоимость доставки (Тариф Т2):**
• Доставка до двери: ${shipping_cost:.2f} USD

**Таможенные платежи:**
• Пошлина ({tnved_info.get('customs_duty_rate', 5)}%): ${customs_calc['duty']:.2f} USD
• НДС (12%): ${customs_calc['vat']:.2f} USD
• Сбор за оформление: ${customs_calc['customs_fee']:.2f} USD
• **Итого таможенные: ${customs_calc['total_customs']:.2f} USD**

**Дополнительные услуги:**
{chr(10).join(additional_services) if additional_services else "• Нет"}

**ОБЩАЯ ПРИБЛИЗИТЕЛЬНАЯ СТОИМОСТЬ:**
**${total_cost_usd:.2f} USD / {total_cost_kzt:,.0f} ₸** (курс 1 USD = 550 ₸)

---

⚠️ **ВНИМАНИЕ:**
• Стоимость является предварительной
• Точный расчет после предоставления всех документов
• Срок бесплатного хранения на СВХ — 2 месяца

✅ **Ваша заявка отправлена таможенному брокеру!**
📞 Свяжемся в течение часа (с 10:00 до 18:00, Астана)'''
    
    # Сбрасываем сессию
    session_data['step'] = 'initial'
    session_data['data'] = {}
    
    return {
        'response': response,
        'session_data': session_data
    }

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
