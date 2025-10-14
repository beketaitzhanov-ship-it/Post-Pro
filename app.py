from flask import Flask, render_template, request, jsonify, session
import os
import re
import json
from datetime import datetime
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from dotenv import load_dotenv
import logging

app = Flask(__name__)
app.secret_key = 'postpro-secret-key-2024'
app.config['PERMANENT_SESSION_LIFETIME'] = 1800

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")

# --- LOADING CONFIGURATION ---
def load_config():
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            config_data = json.load(f)
            logger.info("Configuration file 'config.json' loaded successfully.")
            return config_data
    except FileNotFoundError:
        logger.error("CRITICAL ERROR: File 'config.json' not found!")
    except json.JSONDecodeError:
        logger.error("CRITICAL ERROR: Invalid JSON format in 'config.json!'")
    except Exception as e:
        logger.error(f"CRITICAL ERROR loading config.json: {e}")

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
    logger.error("Application running with default values due to config.json loading error")
    EXCHANGE_RATE, DESTINATION_ZONES, T1_RATES_DENSITY, T2_RATES, CUSTOMS_RATES, CUSTOMS_FEES, GREETINGS = 550, {}, {}, {}, {}, {}, []

# --- PERSONALITY PROMPT LOADING ---
def load_personality_prompt():
    try:
        with open('personality_prompt.txt', 'r', encoding='utf-8') as f:
            prompt_text = f.read()
            logger.info("Personality prompt file loaded successfully.")
            return prompt_text
    except FileNotFoundError:
        logger.error("Personality prompt file not found! Using default prompt.")
        return "You are a helpful assistant."

PERSONALITY_PROMPT = load_personality_prompt()

# --- GEMINI MODEL INITIALIZATION ---
model = None
try:
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name='models/gemini-2.0-flash'
        )
        logger.info("Gemini model initialized successfully.")
    else:
        logger.error("API key not found")
except Exception as e:
    logger.error(f"Error initializing Gemini: {e}")

# --- IMPROVED DIMENSION EXTRACTION FUNCTIONS ---
def extract_dimensions(text):
    """Extracts dimensions (length, width, height) from text in any format."""
    patterns = [
        # Main pattern: numbers with separators and possible units
        r'(?:габарит\w*|размер\w*|дшв|длш|разм)?\s*'
        r'(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m|сантиметр\w*|метр\w*)?\s*'
        r'[xх*×на\s\-]+\s*'
        r'(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m|сантиметр\w*|метр\w*)?\s*'
        r'[xх*×на\s\-]+\s*'
        r'(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m|сантиметр\w*|метр\w*)?'
    ]
    
    text_lower = text.lower()
    
    for pattern in patterns:
        matches = re.finditer(pattern, text_lower)
        for match in matches:
            try:
                l = float(match.group(1).replace(',', '.'))
                w = float(match.group(2).replace(',', '.'))
                h = float(match.group(3).replace(',', '.'))
                
                match_text = match.group(0).lower()
                has_explicit_cm = any(word in match_text for word in ['см', 'cm', 'сантим'])
                has_explicit_m = any(word in match_text for word in ['м', 'm', 'метр'])
                
                is_cm = (
                    has_explicit_cm or
                    (l > 5 or w > 5 or h > 5) and not has_explicit_m
                )
                
                if is_cm:
                    l = l / 100
                    w = w / 100
                    h = h / 100
                
                logger.info(f"Extracted dimensions: {l:.3f}x{w:.3f}x{h:.3f} m")
                return l, w, h
                
            except (ValueError, IndexError) as e:
                logger.warning(f"Error converting dimensions: {e}")
                continue
    
    # Additional pattern for "length X width Y height Z" format
    pattern_dl_sh_v = r'(?:длин[аы]?|length)\s*(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m)?\s*(?:ширин[аы]?|width)\s*(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m)?\s*(?:высот[аы]?|height)\s*(\d+(?:[.,]\d+)?)\s*(?:см|cm|м|m)?'
    
    match = re.search(pattern_dl_sh_v, text_lower)
    if match:
        try:
            l = float(match.group(1).replace(',', '.'))
            w = float(match.group(2).replace(',', '.'))
            h = float(match.group(3).replace(',', '.'))
            
            match_text = match.group(0).lower()
            has_explicit_cm = any(word in match_text for word in ['см', 'cm', 'сантим'])
            has_explicit_m = any(word in match_text for word in ['м', 'm', 'метр'])
            
            is_cm = (
                has_explicit_cm or
                (l > 5 or w > 5 or h > 5) and not has_explicit_m
            )
            
            if is_cm:
                l = l / 100
                w = w / 100
                h = h / 100
            
            logger.info(f"Extracted dimensions (LWH format): {l:.3f}x{w:.3f}x{h:.3f} m")
            return l, w, h
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Error converting LWH dimensions: {e}")
    
    # Pattern for three consecutive numbers
    pattern_three_numbers = r'(?<!\d)(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)(?!\d)'
    
    match = re.search(pattern_three_numbers, text_lower)
    if match:
        try:
            l = float(match.group(1).replace(',', '.'))
            w = float(match.group(2).replace(',', '.'))
            h = float(match.group(3).replace(',', '.'))
            
            if l > 5 and w > 5 and h > 5:
                l = l / 100
                w = w / 100
                h = h / 100
            
            logger.info(f"Extracted dimensions (three numbers): {l:.3f}x{w:.3f}x{h:.3f} m")
            return l, w, h
            
        except (ValueError, IndexError) as e:
            logger.warning(f"Error converting three numbers: {e}")
    
    return None, None, None

def extract_volume(text):
    """Extracts volume from text in any format."""
    patterns = [
        r'(\d+(?:[.,]\d+)?)\s*(?:куб\.?\s*м|м³|м3|куб\.?|кубическ\w+\s*метр\w*|кубометр\w*)',
        r'(?:объем|volume)\w*\s*(\d+(?:[.,]\d+)?)\s*(?:куб\.?\s*м|м³|м3|куб\.?)?',
        r'(\d+(?:[.,]\d+)?)\s*(?:cubic|cub)',
        r'(\d+(?:[.,]\d+)?)\s*(?=куб|м³|м3|объем)'
    ]
    
    text_lower = text.lower()
    
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            try:
                volume = float(match.group(1).replace(',', '.'))
                logger.info(f"Extracted volume: {volume} m³")
                return volume
            except (ValueError, IndexError) as e:
                logger.warning(f"Error converting volume: {e}")
                continue
    
    return None

# --- CALCULATION FUNCTIONS ---
def get_t1_density_rule(product_type, weight, volume):
    """Finds T1 tariff rule based on cargo density."""
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
    """Quick cost calculation - single center for all calculations"""
    try:
        rule, density = get_t1_density_rule(product_type, weight, volume)
        if not rule:
            return None
        
        price = rule['price']
        unit = rule['unit']
        
        if unit == "kg":
            cost_usd = price * weight
        elif unit == "m3":
            cost_usd = price * volume
        else:
            cost_usd = price * weight 
        
        t1_cost_kzt = cost_usd * EXCHANGE_RATE
        
        city_lower = city.lower()
        if city_lower == "алматы" or city_lower == "алмата":
            t2_rate = T2_RATES.get("алматы", 120)
            zone = "алматы"
        else:
            zone = DESTINATION_ZONES.get(city_lower, 3)
            t2_rate = T2_RATES.get(str(zone), 250)
        
        t2_cost_kzt = weight * t2_rate
        
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
        logger.error(f"Calculation error: {e}")
        return None

def calculate_detailed_cost(quick_cost, weight: float, product_type: str, city: str):
    """Detailed calculation with breakdown"""
    if not quick_cost:
        return "Calculation error"
    
    t1_cost = quick_cost['t1_cost']
    t2_cost = quick_cost['t2_cost'] 
    total = quick_cost['total']
    zone = quick_cost['zone']
    t2_rate = quick_cost['t2_rate']
    volume = quick_cost['volume']
    density = quick_cost['density']
    rule = quick_cost['rule']
    t1_cost_usd = quick_cost['t1_cost_usd']
    
    price = rule['price']
    unit = rule['unit']
    if unit == "kg":
        calculation_text = f"${price}/kg × {weight} kg = ${t1_cost_usd:.2f} USD"
    elif unit == "m3":
        calculation_text = f"${price}/m³ × {volume:.3f} m³ = ${t1_cost_usd:.2f} USD"
    else:
        calculation_text = f"${price}/kg × {weight} kg = ${t1_cost_usd:.2f} USD"
    
    city_name = city.capitalize()
    if zone == "алматы":
        t2_explanation = f"• Delivery within Almaty city to your address"
        zone_text = "Almaty city"
        comparison_text = f"💡 **If pickup from warehouse in Almaty:** {t1_cost:.0f} tenge"
    else:
        t2_explanation = f"• Delivery to your address in {city_name}"
        zone_text = f"Zone {zone}"
        comparison_text = f"💡 **If pickup from Almaty:** {t1_cost:.0f} tenge"
    
    response = (
        f"📊 **Detailed calculation for {weight} kg «{product_type}» to {city_name}:**\n\n"
        
        f"**T1: Delivery from China to Almaty**\n"
        f"• Your cargo density: **{density:.1f} kg/m³**\n"
        f"• Applied T1 tariff: **${price} per {unit}**\n"
        f"• Calculation: {calculation_text}\n"
        f"• At exchange rate {EXCHANGE_RATE} tenge/$ = **{t1_cost:.0f} tenge**\n\n"
        
        f"**T2: Door delivery ({zone_text})**\n"
        f"{t2_explanation}\n"
        f"• {t2_rate} tenge/kg × {weight} kg = **{t2_cost:.0f} tenge**\n\n"
        
        f"**Company commission (20%):**\n"
        f"• ({t1_cost:.0f} + {t2_cost:.0f}) × 20% = **{(t1_cost + t2_cost) * 0.20:.0f} tenge**\n\n"
        
        f"------------------------------------\n"
        f"💰 **TOTAL with door delivery:** ≈ **{total:,.0f} tenge**\n\n"
        
        f"{comparison_text}\n\n"
        f"💡 **Insurance:** additional 1% of cargo value\n"
        f"💳 **Payment:** post-payment upon receipt\n\n"
        f"✅ **Want to submit an application?** Please provide your name and phone number!"
    )
    return response

# --- HELPER FUNCTIONS ---
def extract_delivery_info(text):
    """Extracts delivery information from text"""
    weight = None
    product_type = None
    city = None
    
    try:
        weight_patterns = [
            r'(\d+(?:\.\d+)?)\s*(?:кг|kg|килограмм|кило)',
            r'вес\s*[:\-]?\s*(\d+(?:\.\d+)?)',
        ]
        
        for pattern in weight_patterns:
            match = re.search(pattern, text.lower())
            if match:
                weight = float(match.group(1))
                break
        
        text_lower = text.lower()
        for city_name in DESTINATION_ZONES:
            if city_name in text_lower:
                city = city_name
                break
        
        product_keywords = {
            'мебель': ['мебель', 'стол', 'стул', 'кровать', 'шкаф', 'диван'],
            'автозапчасти': ['автозапчасти', 'запчасти', 'аксессуары авто', 'авто'],
            'аксессуары': ['аксессуары', 'сумк', 'ремен', 'очки', 'украшен'],
            'техника': ['техника', 'телефон', 'ноутбук', 'гаджет', 'электроника'],
            'одежда': ['одежда', 'адежда', 'одежд', 'костюм', 'платье'],
            'общие товары': ['товары', 'товар', 'разное', 'прочее', 'прочие']
        }
        
        for prod_type, keywords in product_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                product_type = prod_type
                break
        
        return weight, product_type, city
    except Exception as e:
        logger.error(f"Error extracting data: {e}")
        return None, None, None

def extract_contact_info(text):
    """Extracts contact information from text"""
    name = None
    phone = None
    
    clean_text = re.sub(r'\s+', ' ', text.strip()).lower()
    
    name_match = re.search(r'^([а-яa-z]{2,})', clean_text)
    if name_match:
        name = name_match.group(1).capitalize()
    
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

def get_gemini_response(user_message, context=""):
    """Gets response from Gemini for general questions"""
    if not model:
        return "I'm sorry, I can only answer delivery-related questions at the moment."
    
    try:
        full_prompt = f"{PERSONALITY_PROMPT}\n\nCurrent conversation context:\n{context}\n\nCustomer question: {user_message}\n\nYour response:"
        
        response = model.generate_content(
            full_prompt,
            generation_config=GenerationConfig(
                temperature=0.8,
                max_output_tokens=1000,
            )
        )
        return response.text
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return "Oops, something went wrong with my creative side! Let's get back to delivery calculations, I'm really good at that. 😊"

def save_application(details):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"New application: {timestamp}\n{details}\n"
        with open("applications.txt", "a", encoding="utf-8") as f: 
            f.write("="*50 + "\n" + log_entry + "="*50 + "\n\n")
        logger.info(f"Application saved: {details}")
    except Exception as e: 
        logger.error(f"Error saving: {e}")

# --- ROUTES ---
@app.route('/')
def index():
    session.clear()
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message:
            return jsonify({"response": "Please enter a message."})
        
        # Initialize session data
        delivery_data = session.get('delivery_data', {'weight': None, 'product_type': None, 'city': None, 'volume': None})
        chat_history = session.get('chat_history', [])
        waiting_for_contacts = session.get('waiting_for_contacts', False)
        calculation_shown = session.get('calculation_shown', False)
        
        chat_history.append(f"Customer: {user_message}")
        
        # Greetings
        if user_message.lower() in GREETINGS:
            session.update({
                'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'volume': None},
                'chat_history': [f"Customer: {user_message}"],
                'waiting_for_contacts': False,
                'calculation_shown': False
            })
            return jsonify({"response": "Hello! 👋 I am the Post Pro assistant. I'll help you calculate delivery from China to Kazakhstan!\n\n📦 **For calculation please provide 4 parameters:**\n• **Cargo weight** (in kg)\n• **Product type** (furniture, electronics, clothing, etc.)\n• **Dimensions** (L×W×H in meters or centimeters)\n• **Destination city**\n\n💡 **Example:** \"50 kg furniture to Astana, dimensions 120×80×50\""})
        
        # If waiting for contacts (after showing calculation)
        if waiting_for_contacts:
            name, phone = extract_contact_info(user_message)
            
            if name and phone:
                details = f"Name: {name}, Phone: {phone}"
                if delivery_data['weight']:
                    details += f", Weight: {delivery_data['weight']} kg"
                if delivery_data['product_type']:
                    details += f", Product: {delivery_data['product_type']}"
                if delivery_data['city']:
                    details += f", City: {delivery_data['city']}"
                if delivery_data.get('volume'):
                    details += f", Volume: {delivery_data['volume']:.3f} m³"
                
                save_application(details)
                
                session.update({
                    'delivery_data': {'weight': None, 'product_type': None, 'city': None, 'volume': None},
                    'chat_history': [],
                    'waiting_for_contacts': False,
                    'calculation_shown': False
                })
                
                return jsonify({"response": "🎉 Thank you for choosing Post Pro! Our manager will contact you within 15 minutes. 📞"})
            else:
                return jsonify({"response": "Could not recognize contact information. Please provide in format: 'Name, 87001234567'"})
        
        # Extract delivery information with improved functions
        weight, product_type, city = extract_delivery_info(user_message)
        length, width, height = extract_dimensions(user_message)
        volume_direct = extract_volume(user_message)

        # Update session data with confirmation
        data_updated = False
        confirmation_parts = []

        if weight and weight != delivery_data['weight']:
            delivery_data['weight'] = weight
            data_updated = True
            confirmation_parts.append(f"📊 **Weight:** {weight} kg")

        if product_type and product_type != delivery_data['product_type']:
            delivery_data['product_type'] = product_type
            data_updated = True
            confirmation_parts.append(f"📦 **Product:** {product_type}")

        if city and city != delivery_data['city']:
            delivery_data['city'] = city
            data_updated = True
            confirmation_parts.append(f"🏙️ **City:** {city.capitalize()}")

        # Handle dimensions and volume (volume has priority)
        if volume_direct and volume_direct != delivery_data.get('volume'):
            delivery_data['volume'] = volume_direct
            delivery_data['length'] = None
            delivery_data['width'] = None
            delivery_data['height'] = None
            data_updated = True
            confirmation_parts.append(f"📏 **Volume:** {volume_direct:.3f} m³")
        elif length and width and height:
            calculated_volume = length * width * height
            if abs(calculated_volume - delivery_data.get('volume', 0)) > 0.001:
                delivery_data['length'] = length
                delivery_data['width'] = width
                delivery_data['height'] = height
                delivery_data['volume'] = calculated_volume
                data_updated = True
                confirmation_parts.append(f"📐 **Dimensions:** {length:.2f}×{width:.2f}×{height:.2f} m")
                confirmation_parts.append(f"📏 **Volume:** {calculated_volume:.3f} m³")
        
        # Show confirmation if data was updated
        if data_updated and not calculation_shown:
            response_message = "✅ **Data updated:**\n" + "\n".join(confirmation_parts) + "\n\n"
            
            # Check if all data is collected
            has_all_data = (
                delivery_data['weight'] and 
                delivery_data['product_type'] and 
                delivery_data['city'] and 
                delivery_data.get('volume')
            )
            
            if has_all_data:
                response_message += "📋 **All data collected!** Ready to calculate delivery cost."
            else:
                missing_data = []
                if not delivery_data['weight']:
                    missing_data.append("cargo weight")
                if not delivery_data['product_type']:
                    missing_data.append("product type")
                if not delivery_data.get('volume'):
                    missing_data.append("dimensions or volume")
                if not delivery_data['city']:
                    missing_data.append("destination city")
                
                response_message += f"📝 **Still need to provide:** {', '.join(missing_data)}"
            
            session['delivery_data'] = delivery_data
            session['chat_history'] = chat_history
            return jsonify({"response": response_message})
        
        # Check if all data is available for calculation
        has_all_data = (
            delivery_data['weight'] and 
            delivery_data['product_type'] and 
            delivery_data['city'] and 
            delivery_data.get('volume')
        )
        
        # Step-by-step data collection
        if not has_all_data and not calculation_shown and not data_updated:
            missing_data = []
            if not delivery_data['weight']:
                missing_data.append("cargo weight (in kg)")
            if not delivery_data['product_type']:
                missing_data.append("product type")
            if not delivery_data.get('volume'):
                missing_data.append("dimensions (L×W×H in meters or centimeters)")
            if not delivery_data['city']:
                missing_data.append("destination city")
            
            if missing_data:
                response_message = "📝 For calculation please provide: " + ", ".join(missing_data)
                
                # Specific hints
                if not delivery_data.get('volume') and delivery_data['weight']:
                    response_message += "\n\n💡 **Example dimensions:** \"1.2×0.8×0.5\" or \"120×80×50\""
                elif not delivery_data['weight'] and delivery_data.get('volume'):
                    response_message += "\n\n💡 **Example weight:** \"50 kg\" or \"weight 50\""
                
                session['delivery_data'] = delivery_data
                session['chat_history'] = chat_history
                return jsonify({"response": response_message})
        
        # CALCULATION TRIGGER - when all data is collected and calculation not shown yet
        if has_all_data and not calculation_shown:
            quick_cost = calculate_quick_cost(
                delivery_data['weight'], 
                delivery_data['product_type'], 
                delivery_data['city'],
                delivery_data.get('volume')
            )
            
            if quick_cost:
                total_cost = quick_cost['total']
                response_message = (
                    f"✅ **All data received!**\n\n"
                    f"📦 **Cargo parameters:**\n"
                    f"• Weight: {delivery_data['weight']} kg\n"
                    f"• Product: {delivery_data['product_type']}\n"
                    f"• Volume: {delivery_data['volume']:.3f} m³\n"
                    f"• City: {delivery_data['city'].capitalize()}\n\n"
                    f"💰 **Approximate delivery cost:** ~**{total_cost:,.0f} ₸**\n\n"
                    f"📊 Would you like to see detailed calculation with tariff breakdown?"
                )
                
                session['quick_cost'] = quick_cost
                session['calculation_shown'] = True
                session['delivery_data'] = delivery_data
                session['chat_history'] = chat_history
                
                return jsonify({"response": response_message})
            else:
                return jsonify({"response": "❌ Could not calculate cost. Please check the provided data."})
        
        # Processing after showing calculation
        if calculation_shown:
            # Request for detailed calculation
            if any(word in user_message.lower() for word in ['детальн', 'подробн', 'разбей', 'тариф', 'да', 'yes', 'конечно', 'detail', 'breakdown']):
                detailed_response = calculate_detailed_cost(
                    session.get('quick_cost'),
                    delivery_data['weight'], 
                    delivery_data['product_type'], 
                    delivery_data['city']
                )
                session['waiting_for_contacts'] = True
                session['chat_history'] = chat_history
                return jsonify({"response": detailed_response})
            
            # Request to submit application
            if any(word in user_message.lower() for word in ['заявк', 'оставь', 'свяж', 'контакт', 'позвон', 'менеджер', 'дальше', 'продолж', 'application', 'contact']):
                session['waiting_for_contacts'] = True
                session['chat_history'] = chat_history
                return jsonify({"response": "Great! For contact please provide:\n• Your name\n• Phone number\n\nFor example: 'Aslan, 87001234567'"})
        
        # General questions handling via Gemini
        context_lines = []
        if len(chat_history) > 0:
            context_lines.append("Conversation history:")
            for msg in chat_history[-3:]:
                context_lines.append(msg)
        
        context_lines.append("\nCurrent data:")
        if delivery_data['weight']:
            context_lines.append(f"- Weight: {delivery_data['weight']} kg")
        if delivery_data['product_type']:
            context_lines.append(f"- Product: {delivery_data['product_type']}")
        if delivery_data['city']:
            context_lines.append(f"- City: {delivery_data['city']}")
        if delivery_data.get('volume'):
            context_lines.append(f"- Volume: {delivery_data['volume']:.3f} m³")
        if calculation_shown:
            context_lines.append(f"- Calculation shown: Yes")
        
        context = "\n".join(context_lines)
        bot_response = get_gemini_response(user_message, context)
        chat_history.append(f"Assistant: {bot_response}")
        
        # Limit history length
        if len(chat_history) > 8:
            chat_history = chat_history[-8:]
        
        session['chat_history'] = chat_history
        session['delivery_data'] = delivery_data
        
        return jsonify({"response": bot_response})
        
    except Exception as e:
        logger.error(f"Processing error: {e}")
        return jsonify({"response": "Sorry, an error occurred. Please try again."})

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
