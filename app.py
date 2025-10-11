from flask import Flask, render_template, request, jsonify, session
import requests
import os
import logging
import re
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import uuid

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'post-pro-secret-key-2024')

# Настройка расширенного логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация API
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'your_gemini_api_key_here')
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"

# Расширенные тарифы доставки (в USD)
TARIFFS = {
    'T1': {
        'name': 'Склад-склад',
        'rate_per_kg': 2.5,
        'rate_per_m3': 350,
        'min_cost': 50,
        'transit_time': '7-10 дней',
        'description': 'Доставка до склада в Алматы'
    },
    'T2': {
        'name': 'До двери',
        'rate_per_kg': 1.8,
        'rate_per_m3': 280,
        'min_cost': 40,
        'transit_time': '10-14 дней',
        'description': 'Доставка до указанного адреса в Казахстане'
    }
}

# Полная база сертификатов и услуг
SERVICES = {
    'certificate_origin': {
        'cost': 500,
        'currency': 'USD',
        'name': 'Сертификат происхождения Form A',
        'processing_time': '3-5 рабочих дней'
    },
    'certificate_conformity': {
        'cost': 120000,
        'currency': 'KZT',
        'name': 'Сертификат соответствия',
        'processing_time': '5-7 рабочих дней'
    },
    'broker_consultation': {
        'cost': 300,
        'currency': 'USD',
        'name': 'Консультация таможенного брокера',
        'processing_time': '1 день'
    },
    'customs_clearance': {
        'cost': 200,
        'currency': 'USD',
        'name': 'Таможенное оформление',
        'processing_time': '2-3 дня'
    },
    'phytosanitary_certificate': {
        'cost': 350,
        'currency': 'USD',
        'name': 'Фитосанитарный сертификат',
        'processing_time': '4-6 рабочих дней'
    }
}

# База городов доставки
DELIVERY_CITIES = {
    'алматы': {'zone': 'A', 'surcharge': 0},
    'астана': {'zone': 'B', 'surcharge': 50},
    'шымкент': {'zone': 'B', 'surcharge': 40},
    'актобе': {'zone': 'C', 'surcharge': 80},
    'караганда': {'zone': 'B', 'surcharge': 60},
    'тараз': {'zone': 'B', 'surcharge': 45},
    'урьльск': {'zone': 'C', 'surcharge': 100},
    'павлодар': {'zone': 'B', 'surcharge': 70},
    'костанай': {'zone': 'C', 'surcharge': 90},
    'усть-каменогорск': {'zone': 'C', 'surcharge': 85}
}

class AdvancedSessionManager:
    """Продвинутый менеджер сессий с TTL и сохранением состояния"""
    
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
        self.session_ttl = timedelta(hours=2)
    
    def create_session(self, session_id: str) -> Dict:
        """Создает новую сессию"""
        session_data = {
            'id': session_id,
            'created_at': datetime.now(),
            'last_activity': datetime.now(),
            'step': 'initial',
            'data': {},
            'history': []
        }
        self.sessions[session_id] = session_data
        return session_data
    
    def get_session(self, session_id: str) -> Optional[Dict]:
        """Получает сессию по ID"""
        if session_id in self.sessions:
            session = self.sessions[session_id]
            # Проверяем TTL
            if datetime.now() - session['last_activity'] > self.session_ttl:
                del self.sessions[session_id]
                return None
            session['last_activity'] = datetime.now()
            return session
        return None
    
    def update_session(self, session_id: str, updates: Dict):
        """Обновляет данные сессии"""
        if session_id in self.sessions:
            self.sessions[session_id].update(updates)
            self.sessions[session_id]['last_activity'] = datetime.now()
    
    def add_to_history(self, session_id: str, message: str, is_user: bool = True):
        """Добавляет сообщение в историю"""
        if session_id in self.sessions:
            self.sessions[session_id]['history'].append({
                'timestamp': datetime.now(),
                'message': message,
                'is_user': is_user
            })

class AdvancedTNVEDProcessor:
    """Продвинутый процессор для работы с кодами ТН ВЭД"""
    
    def __init__(self):
        self.cache = {}
    
    async def analyze_product_with_gemini(self, product_name: str, tnved_code: str = None) -> Dict[str, Any]:
        """Анализирует товар с помощью Gemini AI"""
        cache_key = f"{product_name}_{tnved_code}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        try:
            async with aiohttp.ClientSession() as session:
                prompt = self._build_tnved_prompt(product_name, tnved_code)
                
                payload = {
                    "contents": [{
                        "parts": [{"text": prompt}]
                    }],
                    "generationConfig": {
                        "temperature": 0.1,
                        "topK": 40,
                        "topP": 0.95,
                        "maxOutputTokens": 1024,
                    }
                }
                
                async with session.post(GEMINI_URL, json=payload, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        result = self._parse_gemini_response(data, product_name, tnved_code)
                        self.cache[cache_key] = result
                        return result
                    else:
                        logger.error(f"Gemini API error: {response.status}")
                        return self._get_fallback_tnved_data(product_name, tnved_code)
                        
        except Exception as e:
            logger.error(f"Error in Gemini analysis: {e}")
            return self._get_fallback_tnved_data(product_name, tnved_code)
    
    def _build_tnved_prompt(self, product_name: str, tnved_code: str = None) -> str:
        """Строит промпт для Gemini"""
        base_prompt = f"""
        Товар: "{product_name}"
        
        Задача: Проанализировать для таможенного оформления в Казахстане.
        """
        
        if tnved_code:
            base_prompt += f"Проверить код ТН ВЭД: {tnved_code}\n"
        else:
            base_prompt += "Определить наиболее вероятные коды ТН ВЭД ЕАЭС\n"
        
        base_prompt += """
        Требуется структурированная информация в JSON формате:
        {
            "verified": boolean,
            "confidence_score": 0-100,
            "tnved_codes": [
                {
                    "code": "XXXX XX XXX X",
                    "description": "полное описание",
                    "probability": 0-100,
                    "customs_duty_rate": число,
                    "vat_rate": 12,
                    "requires_certification": boolean,
                    "certification_types": ["тип1", "тип2"],
                    "restrictions": ["ограничение1", "ограничение2"],
                    "notes": "дополнительные заметки"
                }
            ],
            "recommendations": ["рекомендация1", "рекомендация2"],
            "warnings": ["предупреждение1", "предупреждение2"]
        }
        
        Важно: Учитывать особенности законодательства ЕАЭС и Казахстана.
        """
        
        return base_prompt
    
    def _parse_gemini_response(self, data: Dict, product_name: str, tnved_code: str = None) -> Dict:
        """Парсит ответ от Gemini"""
        try:
            text = data['candidates'][0]['content']['parts'][0]['text']
            # Ищем JSON в тексте ответа
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            else:
                return self._get_fallback_tnved_data(product_name, tnved_code)
        except Exception as e:
            logger.error(f"Error parsing Gemini response: {e}")
            return self._get_fallback_tnved_data(product_name, tnved_code)
    
    def _get_fallback_tnved_data(self, product_name: str, tnved_code: str = None) -> Dict:
        """Возвращает fallback данные при ошибке API"""
        return {
            "verified": tnved_code is not None,
            "confidence_score": 75,
            "tnved_codes": [
                {
                    "code": tnved_code or "8516 79 910 0",
                    "description": f"Товар: {product_name}",
                    "probability": 85,
                    "customs_duty_rate": 5.0,
                    "vat_rate": 12,
                    "requires_certification": True,
                    "certification_types": ["Сертификат соответствия ТР ТС"],
                    "restrictions": [],
                    "notes": "Автоматически определено системой"
                }
            ],
            "recommendations": [
                "Рекомендуется проконсультироваться с таможенным брокером",
                "Уточните код ТН ВЭД для точного расчета"
            ],
            "warnings": [
                "Неправильный код ТН ВЭД может привести к штрафам",
                "Требуется сертификация для данного типа товаров"
            ]
        }

class AdvancedShippingCalculator:
    """Продвинутый калькулятор доставки и таможенных платежей"""
    
    def __init__(self):
        self.exchange_rate = 550  # USD to KZT
    
    def calculate_comprehensive_shipping(self, weight: float, volume: float, 
                                       delivery_type: str, city: str) -> Dict:
        """Рассчитывает полную стоимость доставки"""
        tariff = TARIFFS[delivery_type]
        city_info = DELIVERY_CITIES.get(city.lower(), {'surcharge': 0})
        
        # Расчет по весу
        weight_cost = weight * tariff['rate_per_kg']
        
        # Расчет по объему
        volume_cost = volume * tariff['rate_per_m3'] if volume else 0
        
        # Базовая стоимость
        base_cost = max(weight_cost, volume_cost, tariff['min_cost'])
        
        # Надбавка за город
        total_cost = base_cost + city_info['surcharge']
        
        return {
            'base_cost': base_cost,
            'city_surcharge': city_info['surcharge'],
            'total_cost': total_cost,
            'transit_time': tariff['transit_time'],
            'description': tariff['description']
        }
    
    def calculate_advanced_customs_duty(self, product_value: float, 
                                      tnved_data: Dict, 
                                      has_origin_cert: bool = False,
                                      is_small_business: bool = False) -> Dict:
        """Рассчитывает расширенные таможенные платежи"""
        tnved_code = tnved_data['tnved_codes'][0]
        
        # Базовая пошлина
        duty_rate = tnved_code['customs_duty_rate']
        base_duty = product_value * (duty_rate / 100)
        
        # Применяем льготы
        if has_origin_cert:
            base_duty *= 0.75  # 25% скидка за сертификат происхождения
        
        if is_small_business and product_value < 10000:
            base_duty *= 0.9  # 10% скидка для малого бизнеса
        
        # НДС
        vat_base = product_value + base_duty
        vat = vat_base * (tnved_code['vat_rate'] / 100)
        
        # Таможенные сборы (прогрессивная шкала)
        customs_fee = self._calculate_customs_fee(product_value)
        
        # Акцизы (если применимо)
        excise = self._calculate_excise_tax(tnved_code, product_value)
        
        total_customs = base_duty + vat + customs_fee + excise
        
        return {
            'duty': {
                'rate': duty_rate,
                'amount': base_duty,
                'has_origin_cert_discount': has_origin_cert
            },
            'vat': {
                'rate': tnved_code['vat_rate'],
                'amount': vat
            },
            'customs_fee': customs_fee,
            'excise_tax': excise,
            'total_customs': total_customs,
            'breakdown': {
                'duty': base_duty,
                'vat': vat,
                'customs_fee': customs_fee,
                'excise': excise
            }
        }
    
    def _calculate_customs_fee(self, product_value: float) -> float:
        """Рассчитывает таможенный сбор по прогрессивной шкале"""
        if product_value <= 1000:
            return 50
        elif product_value <= 10000:
            return 100
        elif product_value <= 50000:
            return 200
        else:
            return 300
    
    def _calculate_excise_tax(self, tnved_code: Dict, product_value: float) -> float:
        """Рассчитывает акцизный налог (для подакцизных товаров)"""
        # Определяем подакцизные товары по коду ТН ВЭД
        excise_codes = ['2203', '2204', '2205', '2402']  # Алкоголь, табак
        code_prefix = tnved_code['code'][:4]
        
        if code_prefix in excise_codes:
            return product_value * 0.2  # 20% акциз
        return 0

class PostProChatProcessor:
    """Главный процессор чат-бота Post Pro с полной функциональностью"""
    
    def __init__(self):
        self.session_manager = AdvancedSessionManager()
        self.tnved_processor = AdvancedTNVEDProcessor()
        self.calculator = AdvancedShippingCalculator()
        self.user_states = {}
    
    async def process_message(self, message: str, session_id: str) -> str:
        """Основной метод обработки сообщений"""
        # Получаем или создаем сессию
        user_session = self.session_manager.get_session(session_id)
        if not user_session:
            user_session = self.session_manager.create_session(session_id)
        
        # Добавляем в историю
        self.session_manager.add_to_history(session_id, message, True)
        
        # Обрабатываем сообщение в зависимости от текущего шага
        response = await self._handle_message_by_step(message, user_session)
        
        # Добавляем ответ в историю
        self.session_manager.add_to_history(session_id, response, False)
        
        return response
    
    async def _handle_message_by_step(self, message: str, user_session: Dict) -> str:
        """Обрабатывает сообщение в зависимости от шага сессии"""
        current_step = user_session['step']
        
        if current_step == 'initial':
            return await self._handle_initial_step(message, user_session)
        
        elif current_step == 'choose_delivery_type':
            return await self._handle_delivery_type_step(message, user_session)
        
        elif current_step == 'cargo_calculation':
            return await self._handle_cargo_calculation(message, user_session)
        
        elif current_step == 'invoice_tnved_input':
            return await self._handle_invoice_tnved_step(message, user_session)
        
        elif current_step == 'invoice_tnved_verification':
            return await self._handle_tnved_verification_step(message, user_session)
        
        elif current_step == 'invoice_product_value':
            return await self._handle_product_value_step(message, user_session)
        
        elif current_step == 'invoice_origin_certificate':
            return await self._handle_origin_certificate_step(message, user_session)
        
        elif current_step == 'invoice_business_type':
            return await self._handle_business_type_step(message, user_session)
        
        elif current_step == 'invoice_final_calculation':
            return await self._handle_final_calculation(message, user_session)
        
        else:
            return "Извините, произошла ошибка в системе. Давайте начнем заново."
    
    async def _handle_initial_step(self, message: str, user_session: Dict) -> str:
        """Обрабатывает начальный шаг - сбор базовой информации"""
        extracted_info = self._extract_detailed_info(message)
        
        if not self._validate_initial_info(extracted_info):
            return self._get_initial_validation_error(extracted_info)
        
        user_session['data'].update(extracted_info)
        user_session['step'] = 'choose_delivery_type'
        
        return self._format_initial_response(extracted_info)
    
    def _extract_detailed_info(self, message: str) -> Dict:
        """Расширенное извлечение информации из сообщения"""
        info = {
            'weight': None,
            'volume': None,
            'product_type': '',
            'city': '',
            'quantity': 1
        }
        
        # Поиск веса
        weight_patterns = [
            r'(\d+(?:\.\d+)?)\s*(кг|kg|kilogram|килограмм)',
            r'(\d+(?:\.\d+)?)\s*(kг|кg)',
        ]
        
        for pattern in weight_patterns:
            match = re.search(pattern, message.lower())
            if match:
                info['weight'] = float(match.group(1))
                break
        
        # Поиск объема
        volume_pattern = r'(\d+(?:\.\d+)?)\s*(м³|m³|m3|куб|куб\.?м)'
        volume_match = re.search(volume_pattern, message.lower())
        if volume_match:
            info['volume'] = float(volume_match.group(1))
        
        # Поиск города
        for city in DELIVERY_CITIES:
            if city in message.lower():
                info['city'] = city.capitalize()
                break
        
        # Поиск количества
        quantity_pattern = r'(\d+)\s*(шт|штук|pcs|pieces)'
        quantity_match = re.search(quantity_pattern, message.lower())
        if quantity_match:
            info['quantity'] = int(quantity_match.group(1))
        
        # Тип товара - все остальное
        words = message.split()
        exclude_words = []
        if info['weight']:
            exclude_words.append(str(info['weight']))
            exclude_words.append('кг')
        if info['volume']:
            exclude_words.append(str(info['volume']))
            exclude_words.extend(['м³', 'куб'])
        if info['city']:
            exclude_words.append(info['city'].lower())
        
        product_words = [w for w in words if w.lower() not in exclude_words]
        info['product_type'] = ' '.join(product_words).strip()
        
        return info
    
    def _validate_initial_info(self, info: Dict) -> bool:
        """Проверяет корректность извлеченной информации"""
        return all([info['weight'] or info['volume'], info['city']])
    
    def _get_initial_validation_error(self, info: Dict) -> str:
        """Возвращает сообщение об ошибке валидации"""
        if not info['weight'] and not info['volume']:
            return "Пожалуйста, укажите вес или объем груза. Например: '100 кг электроники в Астану' или '2 м³ мебели в Алматы'"
        if not info['city']:
            return "Пожалуйста, укажите город доставки. Например: '100 кг электроники в Астану'"
        return "Пожалуйста, укажите все необходимые данные в формате: 'Вес/Объем тип_товара город'"
    
    def _format_initial_response(self, info: Dict) -> str:
        """Форматирует ответ после начального шага"""
        response = f"""✅ **Информация получена:**

• {'Вес: ' + str(info['weight']) + ' кг' if info['weight'] else 'Объем: ' + str(info['volume']) + ' м³'}
• Товар: {info['product_type'] or 'не указан'}
• Город: {info['city']}
• Количество: {info['quantity']} шт

**Выберите тип доставки:**

🚛 **КАРГО** (упрощенная доставка)
✓ Для личных вещей, пробных партий
✓ Расчет по тарифам Т1 и Т2
✓ Быстрый предварительный расчет
✓ Минимальное оформление

📋 **ИНВОЙС** (полное таможенное оформление)
✓ Для коммерческих поставок
✓ Полный расчет таможенных платежей
✓ Определение кода ТН ВЭД
✓ Сертификация и разрешения
✓ Работа с документами

**Напишите "Карго" или "Инвойс" для продолжения**"""
        
        return response
    
    async def _handle_delivery_type_step(self, message: str, user_session: Dict) -> str:
        """Обрабатывает выбор типа доставки"""
        message_lower = message.lower()
        
        if any(word in message_lower for word in ['карго', 'cargo', 'простой']):
            user_session['delivery_type'] = 'cargo'
            user_session['step'] = 'cargo_calculation'
            return await self._handle_cargo_calculation(message, user_session)
        
        elif any(word in message_lower for word in ['инвойс', 'invoice', 'тамож', 'растамож']):
            user_session['delivery_type'] = 'invoice'
            user_session['step'] = 'invoice_tnved_input'
            return self._get_tnved_input_prompt(user_session)
        
        else:
            return '''Пожалуйста, выберите тип доставки:

🟢 **КАРГО** - если это личные вещи или пробная партия
🔵 **ИНВОЙС** - если это коммерческая поставка с полным оформлением

Напишите "Карго" или "Инвойс"'''
    
    async def _handle_cargo_calculation(self, message: str, user_session: Dict) -> str:
        """Обрабатывает расчет карго доставки"""
        data = user_session['data']
        
        # Расчет для обоих тарифов
        t1_calc = self.calculator.calculate_comprehensive_shipping(
            data['weight'] or 0, data['volume'] or 0, 'T1', data['city']
        )
        t2_calc = self.calculator.calculate_comprehensive_shipping(
            data['weight'] or 0, data['volume'] or 0, 'T2', data['city']
        )
        
        user_session['step'] = 'initial'  # Сброс для нового запроса
        user_session['last_calculation'] = {
            'type': 'cargo',
            'timestamp': datetime.now(),
            'results': {'T1': t1_calc, 'T2': t2_calc}
        }
        
        return self._format_cargo_response(data, t1_calc, t2_calc)
    
    def _format_cargo_response(self, data: Dict, t1_calc: Dict, t2_calc: Dict) -> str:
        """Форматирует ответ для карго расчета"""
        return f"""📦 **РАСЧЕТ ДОСТАВКИ КАРГО**

**Параметры груза:**
• {f"Вес: {data['weight']} кг" if data['weight'] else f"Объем: {data['volume']} м³"}
• Товар: {data['product_type'] or 'не указан'}
• Город: {data['city']}
• Количество: {data['quantity']} шт

**Тариф Т1 - Склад-склад:**
💵 Стоимость: ${t1_calc['total_cost']:.2f} USD
⏱️ Срок: {t1_calc['transit_time']}
📝 {t1_calc['description']}

**Тариф Т2 - До двери:**
💵 Стоимость: ${t2_calc['total_cost']:.2f} USD  
⏱️ Срок: {t2_calc['transit_time']}
📝 {t2_calc['description']}

💡 *В стоимость включена надбавка за город: ${t1_calc['city_surcharge']:.2f} USD*

⚠️ **Примечания:**
• Стоимость является предварительной
• Для точного расчета требуется точный объем
• Возможны дополнительные сборы

✅ **Заявка принята! Менеджер свяжется в течение часа для уточнения деталей.**

_Для нового расчета напишите параметры груза_"""
    
    def _get_tnved_input_prompt(self, user_session: Dict) -> str:
        """Возвращает промпт для ввода кода ТН ВЭД"""
        data = user_session['data']
        return f"""🔵 **РАСЧЕТ С РАСТАМОЖКОЙ**

Товар: **{data['product_type'] or 'не указан'}**

Для точного расчета таможенных платежей нужен код ТН ВЭД.

**Выберите вариант:**
1. 📋 **Знаю код ТН ВЭД** - напишите код (формат: XXXX XX XXX X)
2. 🔍 **Определить автоматически** - напишите "Определить" или точное название товара
3. 👨‍💼 **Помощь брокера** - напишите "Помощь" для консультации

*Код ТН ВЭД - это 10-значный код товарной номенклатуры, определяет ставки пошлин и требования*"""
    
    async def _handle_invoice_tnved_step(self, message: str, user_session: Dict) -> str:
        """Обрабатывает ввод кода ТН ВЭД для инвойса"""
        data = user_session['data']
        
        if message.lower() in ['помощь', 'help', 'консультация']:
            user_session['step'] = 'invoice_tnved_verification'
            return '''👨‍💼 **Помощь таможенного брокера**

Ваша заявка передана специалисту. В течение 15 минут с вами свяжутся для:
• Точного определения кода ТН ВЭД
• Консультации по документам
• Расчету оптимальной схемы оформления

📞 _Ожидайте звонка на указанный номер_

Либо вы можете самостоятельно ввести код ТН ВЭД или название товара для автоматического определения.'''
        
        elif message.lower() in ['определить', 'авто', 'auto']:
            # Используем название товара из начальных данных
            product_name = data['product_type']
        elif re.match(r'\d{4}\s\d{2}\s\d{3}\s\d', message):
            # Пользователь ввел код ТН ВЭД
            product_name = data['product_type']
            tnved_code = message
        else:
            # Пользователь ввел уточненное название товара
            product_name = message
            tnved_code = None
        
        # Анализируем товар через Gemini
        tnved_info = await self.tnved_processor.analyze_product_with_gemini(product_name, tnved_code)
        user_session['tnved_info'] = tnved_info
        
        if tnved_code and not tnved_info.get('verified', False):
            user_session['step'] = 'invoice_tnved_verification'
            return self._format_tnved_verification_prompt(tnved_info, tnved_code, product_name)
        
        user_session['step'] = 'invoice_product_value'
        return self._format_tnved_success_response(tnved_info, product_name)
    
    def _format_tnved_verification_prompt(self, tnved_info: Dict, tnved_code: str, product_name: str) -> str:
        """Форматирует запрос на верификацию кода ТН ВЭД"""
        return f"""⚠️ **ПРОВЕРКА КОДА ТН ВЭД**

Введенный код: **{tnved_code}**
Товар: **{product_name}**

Система обнаружила возможное несоответствие. 

**Рекомендуемые коды:**
{chr(10).join([f"• {code['code']} - {code['description']} (вероятность: {code['probability']}%)" for code in tnved_info['tnved_codes']])}

**Варианты действий:**
1. ✅ **Подтвердить код** {tnved_code} - напишите "Подтверждаю"
2. 🔄 **Выбрать другой код** - напишите нужный код из списка
3. 👨‍💼 **Консультация брокера** - напишите "Помощь"

❌ *Неправильный код ТН ВЭД может привести к:*
   • Штрафам от таможенных органов
   • Задержке груза
   • Дополнительным платежам"""
    
    def _format_tnved_success_response(self, tnved_info: Dict, product_name: str) -> str:
        """Форматирует успешный ответ по определению ТН ВЭД"""
        main_code = tnved_info['tnved_codes'][0]
        return f"""✅ **КОД ТН ВЭД ОПРЕДЕЛЕН**

Товар: **{product_name}**
Основной код: **{main_code['code']}**
Описание: {main_code['description']}
Доверие системы: {tnved_info['confidence_score']}%

**Параметры:**
• Пошлина: {main_code['customs_duty_rate']}%
• НДС: {main_code['vat_rate']}%
• Сертификация: {'Требуется' if main_code['requires_certification'] else 'Не требуется'}

**Теперь укажите стоимость товара по инвойсу (USD):**"""
    
    async def _handle_tnved_verification_step(self, message: str, user_session: Dict) -> str:
        """Обрабатывает верификацию кода ТН ВЭД"""
        if 'подтверждаю' in message.lower():
            user_session['step'] = 'invoice_product_value'
            return "Код ТН ВЭД подтвержден. Теперь укажите стоимость товара по инвойсу (USD):"
        
        elif 'помощь' in message.lower():
            return '''👨‍💼 **Помощь таможенного брокера**

Специалист свяжется с вами в течение 15 минут для:
• Точного определения кода ТН ВЭД
• Консультации по ставкам пошлин
• Подготовки полного расчета

📞 _Ожидайте звонка_

Либо введите код ТН ВЭД для продолжения самостоятельного расчета.'''
        
        elif re.match(r'\d{4}\s\d{2}\s\d{3}\s\d', message):
            # Пользователь ввел новый код
            user_session['tnved_info']['tnved_codes'][0]['code'] = message
            user_session['tnved_info']['verified'] = True
            user_session['step'] = 'invoice_product_value'
            return f"✅ Выбран код: {message}\n\nТеперь укажите стоимость товара по инвойсу (USD):"
        
        else:
            return "Пожалуйста, выберите один из вариантов: 'Подтверждаю', введите код ТН ВЭД или 'Помощь'"
    
    async def _handle_product_value_step(self, message: str, user_session: Dict) -> str:
        """Обрабатывает ввод стоимости товара"""
        try:
            product_value = float(message.replace(',', ''))
            user_session['data']['product_value'] = product_value
            
            # Проверяем на необходимость валютного контроля
            currency_warning = ""
            if product_value > 50000:
                currency_warning = "\n\n⚠️ **ВНИМАНИЕ:** Сумма контракта превышает $50,000. Требуется регистрация в Нацбанке РК."
            
            user_session['step'] = 'invoice_origin_certificate'
            
            return f"""💰 **СТОИМОСТЬ ТОВАРА:** ${product_value:,.2f} USD{currency_warning}

**Есть ли у вас сертификат происхождения Form A?**

📄 **Сертификат происхождения** дает право на снижение пошлины на 25%

Выберите вариант:
• ✅ **Да, есть** - у меня есть сертификат
• 📝 **Нет, но нужен** - нужно оформить (+${SERVICES['certificate_origin']['cost']} USD)
• ❌ **Нет, не нужен** - не требуется"""
            
        except ValueError:
            return "Пожалуйста, введите корректную сумму в USD (только число, например: 15000):"
    
    async def _handle_origin_certificate_step(self, message: str, user_session: Dict) -> str:
        """Обрабатывает вопрос о сертификате происхождения"""
        message_lower = message.lower()
        
        if any(word in message_lower for word in ['да', 'есть', 'имеется']):
            user_session['has_origin_cert'] = True
            user_session['needs_origin_cert'] = False
        elif any(word in message_lower for word in ['нет, но нужен', 'нужен', 'оформить']):
            user_session['has_origin_cert'] = False
            user_session['needs_origin_cert'] = True
        else:
            user_session['has_origin_cert'] = False
            user_session['needs_origin_cert'] = False
        
        user_session['step'] = 'invoice_business_type'
        
        return """🏢
