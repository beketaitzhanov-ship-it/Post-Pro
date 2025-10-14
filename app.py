from flask import Flask, request, jsonify, session
from flask_session import Session
import re
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os
import subprocess
import tempfile
import socket

app = Flask(__name__)
app.config['SECRET_KEY'] = 'postpro-secret-key-2024'
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Константы
EXCHANGE_RATE = 550  # ₸/USD
CUSTOMS_RATES = {
    'одежда': 0.10,
    'электроника': 0.05,
    'мебель': 0.10,
    'общие товары': 0.00
}
CUSTOMS_FEES = {
    'брокер': 60000,
    'декларация': 15000,
    'сертификат': 120000
}
T1_RATES = {
    'одежда': [(50, 1.50), (100, 1.20), (200, 1.00), (float('inf'), 0.80)],
    'электроника': [(50, 2.50), (100, 2.30), (200, 2.10), (float('inf'), 1.80)],
    'мебель': [(50, 1.80), (100, 1.60), (200, 1.40), (float('inf'), 1.20)],
    'общие товары': [(50, 1.50), (100, 1.20), (200, 1.00), (float('inf'), 0.80)]
}
ZONES = {
    'алматы': 1,
    'астана': 3,
    'шымкент': 2,
    'караганда': 4
}
T2_RATES = {
    1: (4200, 210),
    2: (4400, 220),
    3: (4700, 236),
    4: (5000, 250)
}

# Вспомогательные функции
def calculate_t1_rate_by_density(density: float, product_type: str) -> float:
    """Рассчитывает ставку T1 на основе плотности груза."""
    for threshold, rate in T1_RATES.get(product_type, T1_RATES['общие товары']):
        if density <= threshold:
            return rate
    return T1_RATES[product_type][-1][1]

def calculate_t2_rate(zone: int, weight: float) -> float:
    """Рассчитывает стоимость T2 доставки."""
    base_rate, extra_rate = T2_RATES.get(zone, (5000, 250))
    if weight <= 20:
        return base_rate
    return base_rate + (weight - 20) * extra_rate

def calculate_quick_cost(weight: float, product_type: str, city: str, volume: float = None,
                        dimensions: dict = None, is_fragile: bool = False, is_village: bool = False) -> dict:
    """Рассчитывает стоимость доставки T1 и T2."""
    try:
        if volume is None and dimensions:
            volume = (dimensions['length'] * dimensions['width'] * dimensions['height']) / 1000000
        if volume <= 0 or weight <= 0:
            return {'error': 'Вес и объем должны быть больше 0.'}
        
        density = weight / volume
        if density < 50 or density > 1000:
            return {'error': f"Плотность груза ({density:.1f} кг/м³) кажется необычной. Уточните вес или объем."}
        
        unit = 'kg' if density >= 200 else 'm3'
        t1_rate = calculate_t1_rate_by_density(density, product_type)
        t1_cost = weight * t1_rate if unit == 'kg' else volume * t1_rate
        
        zone = ZONES.get(city.lower(), 4)
        t2_rate = calculate_t2_rate(zone, weight)
        t2_cost = t2_rate
        if is_fragile:
            t2_cost *= 1.5
        if is_village:
            t2_cost *= 2.0
        
        return {
            't1_cost': t1_cost,
            't2_cost': t2_cost,
            't1_rate': t1_rate,
            't2_rate': t2_rate,
            'unit': unit,
            'density': density,
            'zone': zone
        }
    except Exception as e:
        logger.error(f"Ошибка расчета доставки: {e}")
        return {'error': 'Ошибка расчета доставки.'}

def calculate_customs_cost(invoice_value: float, product_type: str, weight: float,
                          has_certificate: bool, needs_certificate: bool) -> dict:
    """Рассчитывает таможенные платежи."""
    try:
        customs_rate = CUSTOMS_RATES.get(product_type, 0.0)
        duty_usd = invoice_value * customs_rate
        duty_kzt = duty_usd * EXCHANGE_RATE
        vat_usd = (invoice_value + duty_usd) * 0.12
        vat_kzt = vat_usd * EXCHANGE_RATE
        total_kzt = duty_kzt + vat_kzt + CUSTOMS_FEES['брокер'] + CUSTOMS_FEES['декларация']
        if needs_certificate and not has_certificate:
            total_kzt += CUSTOMS_FEES['сертификат']
        
        return {
            'duty_usd': round(duty_usd),
            'duty_kzt': round(duty_kzt),
            'vat_usd': round(vat_usd),
            'vat_kzt': round(vat_kzt),
            'total_kzt': round(total_kzt),
            'customs_rate': customs_rate
        }
    except Exception as e:
        logger.error(f"Ошибка расчета таможни: {e}")
        return {'error': 'Ошибка расчета таможни.'}

def check_certification_requirements(product_type: str) -> bool:
    """Проверяет, требуется ли сертификат."""
    return product_type in ['электроника', 'мебель']

def get_tnved_code(product_type: str) -> str:
    """Возвращает код ТНВЭД для типа товара."""
    codes = {
        'одежда': '6109 10 000 0',
        'электроника': '8517 12 000 0',
        'мебель': '9403 60 100 0'
    }
    return codes.get(product_type, '0000 00 000 0')

def detect_language(text: str) -> str:
    """Определяет язык ввода."""
    if re.search(r'[\u4e00-\u9fff]', text):
        return 'cn'
    elif re.search(r'[а-яА-ЯәғқңөұүіӘҒҚҢӨҰҮІ]', text):
        return 'kz' if re.search(r'[әғқңөұүіӘҒҚҢӨҰҮІ]', text) else 'ru'
    return 'ru'

def get_welcome_message(lang: str = 'ru') -> tuple:
    """Возвращает приветственное сообщение и клавиатуру."""
    if lang == 'kz':
        return (
            "Сәлеметсіз бе! PostPro сізге Қытайдан Қазақстанға жеткізу құнын есептеуге көмектеседі.\n\n"
            "📦 **КАРГО** - жеке заттар мен сынама партиялар үшін\n"
            "📄 **ИНВОЙС** - кедендік рәсімдеумен коммерциялық партиялар үшін\n\n"
            "💡 **Есептеу үшін жазыңыз:**\n"
            "• Жүктің салмағы (мысалы: 50 кг)\n"
            "• Жүктің көлемі (м³) немесе өлшемдері (Ұ×Е×Б см-де)\n"
            "• Товар түрі (киім, электроника т.б.)\n"
            "• Қазақстандағы жеткізу қаласы\n"
            "• ИНВОЙС үшін: USD-дағы құны\n"
            "• Сынғыш жүк немесе ауылға жеткізу (қолданылса)\n\n"
            "✨ **Сұрау мысалдары:**\n"
            "\"50 кг киім Астанаға, көлемі 0.5 м³\"\n"
            "\"Карго 100 кг электроника Алматыға, өлшемдері 120x80x60 см\"\n"
            "\"Инвойс 200 кг жиһаз Шымкентке 5000 USD, көлемі 2.5 м³, сынғыш\"\n\n"
            "💬 Тілді ауыстыру үшін батырманы таңдаңыз."
        ), [
            {'text': 'Русский', 'callback_data': 'lang_ru'},
            {'text': 'Қазақша', 'callback_data': 'lang_kz'},
            {'text': '中文', 'callback_data': 'lang_cn'}
        ]
    elif lang == 'cn':
        return (
            "您好！PostPro 帮助您计算从中国到哈萨克斯坦的运输费用。\n\n"
            "📦 **货运** - 适用于个人物品和小批量试货\n"
            "📄 **发票** - 适用于需要清关的商业批次\n\n"
            "💡 **请提供以下信息:**\n"
            "• 货物重量（例如：50 公斤)\n"
            "• 货物体积（立方米）或尺寸（长×宽×高，厘米）\n"
            "• 商品类型（服装、电子产品等）\n"
            "• 哈萨克斯坦的送货城市\n"
            "• 发票：美元金额\n"
            "• 易碎货物或乡村送货（如适用）\n\n"
            "✨ **请求示例:**\n"
            "\"50 公斤服装到阿斯塔纳, 体积 0.5 立方米\"\n"
            "\"货运 100 公斤电子产品到阿拉木图, 尺寸 120x80x60 厘米\"\n"
            "\"发票 200 公斤家具到奇姆肯特 5000 美元, 体积 2.5 立方米, 易碎\"\n\n"
            "💬 为更改语言, 请选择下面的按钮."
        ), [
            {'text': 'Русский', 'callback_data': 'lang_ru'},
            {'text': 'Қазақша', 'callback_data': 'lang_kz'},
            {'text': '中文', 'callback_data': 'lang_cn'}
        ]
    return (
        "🚚 Добро пожаловать в PostPro!\n\n"
        "Я помогу вам рассчитать стоимость доставки из Китая в Казахстан.\n\n"
        "📦 **КАРГО** - для личных вещей и пробных партий\n"
        "📄 **ИНВОЙС** - для коммерческих партий с растаможкой\n\n"
        "💡 **Для расчета укажите:**\n"
        "• Вес груза (например: 50 кг)\n"
        "• Объем груза (м³) или габариты (Д×Ш×В в см)\n"
        "• Тип товара (одежда, электроника и т.д.)\n"
        "• Город доставки в Казахстане\n"
        "• ИНВОЙС: стоимость в USD (если применимо)\n"
        "• Сынғыш/хрупкий груз или доставка в деревню (если применимо)\n\n"
        "✨ **Примеры запросов:**\n"
        "\"50 кг одежды в Астану, объем 0.5 м³\"\n"
        "\"Карго 100 кг электроники в Алматы, габариты 120x80x60 см\"\n"
        "\"Инвойс 200 кг мебели в Шымкент 5000 USD, объем 2.5 м³\"\n\n"
        "💎 *Расчет производится по плотности груза для оптимальной стоимости*"
    ), [
        {'text': 'Русский', 'callback_data': 'lang_ru'},
        {'text': 'Қазақша', 'callback_data': 'lang_kz'},
        {'text': '中文', 'callback_data': 'lang_cn'}
    ]

def get_comparison_chart(t1_total: float, t2_total: float) -> str:
    """Генерирует HTML-код для графика сравнения стоимости."""
    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Сравнение стоимости доставки</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{ font-family: Arial, sans-serif; text-align: center; margin: 20px; }}
        canvas {{ max-width: 600px; margin: 0 auto; }}
    </style>
</head>
<body>
    <h2>Сравнение стоимости доставки</h2>
    <canvas id="costChart"></canvas>
    <script>
        const ctx = document.getElementById('costChart').getContext('2d');
        const chart = new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: ['T1 (самовывоз)', 'T1+T2 (до двери)'],
                datasets: [{{
                    label: 'Стоимость (₸)',
                    data: [{t1_total}, {t2_total}],
                    backgroundColor: ['#36A2EB', '#FF6384'],
                    borderColor: ['#36A2EB', '#FF6384'],
                    borderWidth: 1
                }}]
            }},
            options: {{
                scales: {{
                    y: {{ beginAtZero: true, title: {{ display: true, text: 'Стоимость (₸)' }} }},
                    x: {{ title: {{ display: true, text: 'Вариант доставки' }} }}
                }},
                plugins: {{ title: {{ display: true, text: 'Сравнение стоимости доставки' }} }}
            }}
        }});
    </script>
</body>
</html>
"""

def generate_pdf_report(delivery_data: dict, customs_data: dict, client_name: str, client_phone: str,
                        total_cost: float, language: str = 'ru') -> str:
    """Генерирует LaTeX-код для PDF-отчета."""
    labels = {
        'ru': {
            'title': 'Отчет о расчете доставки (PostPro)',
            'details': 'Детали заявки',
            'client': 'Клиент',
            'phone': 'Телефон',
            'delivery_type': 'Тип доставки',
            'weight': 'Вес груза',
            'volume': 'Объем груза',
            'density': 'плотность',
            'product_type': 'Тип товара',
            'city': 'Город доставки',
            'invoice_value': 'Стоимость инвойса',
            'tnved_code': 'Код ТНВЭД',
            'cost_calc': 'Расчет стоимости',
            'delivery': 'Доставка',
            'customs': 'Таможенные платежи',
            'duty': 'Пошлина',
            'vat': 'НДС',
            'broker': 'Брокер',
            'declaration': 'Декларация',
            'certificate': 'Сертификат',
            'total': 'Итоговая стоимость',
            'notes': 'Примечания',
            'service_fee': 'Сервисный сбор (20%) учтен в стоимости доставки.',
            'fragile': 'Хрупкий груз: учтен (+50% к T2).',
            'village': 'Доставка в деревню: учтен (+100% к T2).',
            'thanks': 'Спасибо за выбор PostPro! Свяжемся с вами в течение 15 минут.'
        },
        'kz': {
            'title': 'Жеткізу есебі (PostPro)',
            'details': 'Тапсырыс мәліметтері',
            'client': 'Клиент',
            'phone': 'Телефон',
            'delivery_type': 'Жеткізу түрі',
            'weight': 'Жүктің салмағы',
            'volume': 'Жүктің көлемі',
            'density': 'тығыздығы',
            'product_type': 'Товар түрі',
            'city': 'Жеткізу қаласы',
            'invoice_value': 'Инвойс құны',
            'tnved_code': 'ТН ВЭД коды',
            'cost_calc': 'Құн есебі',
            'delivery': 'Жеткізу',
            'customs': 'Кедендік төлемдер',
            'duty': 'Кедендік баж',
            'vat': 'ҚҚС',
            'broker': 'Брокер',
            'declaration': 'Декларация',
            'certificate': 'Сертификат',
            'total': 'Жалпы құны',
            'notes': 'Ескертпелер',
            'service_fee': 'Қызмет ақысы (20%) жеткізу құнында ескерілген.',
            'fragile': 'Сынғыш жүк: ескерілген (+50% T2).',
            'village': 'Ауылға жеткізу: ескерілген (+100% T2).',
            'thanks': 'PostPro таңдағаныңызға рахмет! Сізбен 15 минут ішінде хабарласамыз.'
        },
        'cn': {
            'title': '运输费用报告 (PostPro)',
            'details': '订单详情',
            'client': '客户',
            'phone': '电话',
            'delivery_type': '运输类型',
            'weight': '货物重量',
            'volume': '货物体积',
            'density': '密度',
            'product_type': '商品类型',
            'city': '送货城市',
            'invoice_value': '发票金额',
            'tnved_code': 'HS编码',
            'cost_calc': '费用计算',
            'delivery': '运输',
            'customs': '海关费用',
            'duty': '关税',
            'vat': '增值税',
            'broker': '经纪人',
            'declaration': '申报',
            'certificate': '证书',
            'total': '总费用',
            'notes': '备注',
            'service_fee': '服务费 (20%) 已包含在运输费用中。',
            'fragile': '易碎货物：已考虑 (+50% T2)。',
            'village': '乡村送货：已考虑 (+100% T2)。',
            'thanks': '感谢选择 PostPro！我们将在15分钟内与您联系。'
        }
    }
    
    l = labels[language]
    delivery_label = 'T1+T2' if delivery_data.get('delivery_option') == '2' else 'T1'
    city_suffix = (' (деревня, хрупкий груз)' if language == 'ru' else ' (ауыл, сынғыш жүк)' if language == 'kz' else ' (乡村，易碎货物)') \
                  if delivery_data.get('is_village') and delivery_data.get('is_fragile') else \
                  (' (хрупкий груз)' if language == 'ru' else ' (сынғыш жүк)' if language == 'kz' else ' (易碎货物)') \
                  if delivery_data.get('is_fragile') else \
                  (' (деревня)' if language == 'ru' else ' (ауыл)' if language == 'kz' else ' (乡村)') \
                  if delivery_data.get('is_village') else ''
    
    # Исправленная f-строка с удвоенными фигурными скобками для LaTeX
    return f"""
\\documentclass[a4paper,12pt]{{article}}
\\usepackage[utf8]{{inputenc}}
\\usepackage{{xeCJK}}
\\usepackage[russian]{{babel}}
\\usepackage{{xecyr}}
\\usepackage{{fontspec}}
\\setmainfont{{Noto Serif}}
\\setCJKmainfont{{Noto Serif CJK SC}}
\\usepackage{{geometry}}
\\geometry{{margin=2cm}}
\\usepackage{{parskip}}
\\usepackage{{enumitem}}
\\usepackage{{graphicx}}
\\usepackage{{amsmath}}

\\begin{{document}}

\\begin{{center}}
    \\textbf{{{{\\Large {l['title']}}}}}
\\end{{center}}

\\section*{{{l['details']}}}
\\begin{{itemize}}[leftmargin=*]
    \\item \\textbf{{{l['client']}}}: {client_name}
    \\item \\textbf{{{l['phone']}}}: +7 ({client_phone[:3]}) {client_phone[3:6]}-{client_phone[6:8]}-{client_phone[8:10]}
    \\item \\textbf{{{l['delivery_type']}}}: {'ИНВОЙС' if customs_data.get('invoice_value') else 'КАРГО'}
    \\item \\textbf{{{l['weight']}}}: {delivery_data['weight']} кг
    \\item \\textbf{{{l['volume']}}}: {delivery_data['volume']} м³ ({l['density']}: {delivery_data['density']:.1f} кг/м³)
    \\item \\textbf{{{l['product_type']}}}: {delivery_data['product_type']}
    \\item \\textbf{{{l['city']}}}: {delivery_data['city'].capitalize()}{city_suffix}
    \\item \\textbf{{{l['invoice_value']}}}: {customs_data.get('invoice_value', '–')} USD
    \\item \\textbf{{{l['tnved_code']}}}: {customs_data.get('tnved_code', '–')}
\\end{{itemize}}

\\section*{{{l['cost_calc']}}}
\\begin{{itemize}}[leftmargin=*]
    \\item \\textbf{{{l['delivery']} ({delivery_label})}}: {total_cost - customs_data.get('total_kzt', 0):,.0f} ₸
    \\begin{{itemize}}
        \\item T1 ({'до Алматы' if language == 'ru' else 'Алматыға' if language == 'kz' else '到阿拉木图'}): {delivery_data['t1_cost'] * 1.20:,.0f} ₸ ({delivery_data['t1_rate']:.2f} USD/{delivery_data['unit']})
        {'\\item T2 (' + ('до двери' if language == 'ru' else 'есікке дейін' if language == 'kz' else '到门') + f'): {delivery_data["t2_cost"] * (1.5 if delivery_data.get("is_fragile") else 1.0) * (2.0 if delivery_data.get("is_village") else 1.0) * 1.20:,.0f} ₸ (зона {delivery_data["zone"]}, {delivery_data["t2_rate"]:.0f} ₸/кг' + (' × 1.5 (' + l['fragile'].split(':')[0] + ')' if delivery_data.get('is_fragile') else '') + (' × 2.0 (' + l['village'].split(':')[0] + ')' if delivery_data.get('is_village') else '') + ')' if delivery_data['delivery_option'] == '2' else ''}
    \\end{{itemize}}
    \\item \\textbf{{{l['customs']}}}: {customs_data.get('total_kzt', 0):,.0f} ₸
    \\begin{{itemize}}
        \\item {l['duty']} ({customs_data.get('customs_rate', 0) * 100:.0f}\\%): {customs_data.get('duty_kzt', 0):,.0f} ₸ ({customs_data.get('duty_usd', 0):,.0f} USD)
        \\item {l['vat']} (12\\%): {customs_data.get('vat_kzt', 0):,.0f} ₸ ({customs_data.get('vat_usd', 0):,.0f} USD)
        \\item {l['broker']}: {CUSTOMS_FEES['брокер']:,.0f} ₸
        \\item {l['declaration']}: {CUSTOMS_FEES['декларация']:,.0f} ₸
        \\item {l['certificate']}: {CUSTOMS_FEES['сертификат'] if customs_data.get('needs_certificate') else 0:,.0f} ₸ {'(требуется)' if language == 'ru' else '(қажет)' if language == 'kz' else '(必需)' if customs_data.get('needs_certificate') else '(не требуется)' if language == 'ru' else '(қажет емес)' if language == 'kz' else '(非必需)'}
    \\end{{itemize}}
    \\item \\textbf{{{l['total']}}}: {total_cost:,.0f} ₸
\\end{{itemize}}

\\section*{{{l['notes']}}}
\\begin{{itemize}}[leftmargin=*]
    \\item {l['service_fee']}
    {'\\item ' + l['fragile'] if delivery_data.get('is_fragile') else ''}
    {'\\item ' + l['village'] if delivery_data.get('is_village') else ''}
\\end{{itemize}}

\\begin{{center}}
    \\textit{{{l['thanks']}}}
\\end{{center}}

\\end{{document}}
"""

def generate_pdf_file(latex_content: str, output_filename: str = 'delivery_report.pdf') -> str:
    """Генерирует PDF из LaTeX-кода."""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tex', delete=False) as tex_file:
            tex_file.write(latex_content)
            tex_file_path = tex_file.name
        
        subprocess.run(['xelatex', '-output-directory', os.path.dirname(tex_file_path), tex_file_path], check=True)
        pdf_path = os.path.join(os.path.dirname(tex_file_path), os.path.splitext(os.path.basename(tex_file_path))[0] + '.pdf')
        if os.path.exists(pdf_path):
            os.rename(pdf_path, output_filename)
        os.remove(tex_file_path)
        return output_filename
    except Exception as e:
        logger.error(f"Ошибка генерации PDF: {e}")
        return None

def send_pdf_email(client_name: str, client_email: str, pdf_path: str, language: str = 'ru') -> bool:
    """Отправляет PDF-отчет по email."""
    try:
        labels = {
            'ru': {'subject': f'Отчет о расчете доставки для {client_name}', 'body': f'Уважаемый(ая) {client_name},\n\nПрилагаем отчет о расчете доставки.\nСпасибо за выбор PostPro!\n'},
            'kz': {'subject': f'{client_name} үшін жеткізу есебі', 'body': f'Құрметті {client_name},\n\nЖеткізу есебін қоса береміз.\nPostPro таңдағаныңызға рахмет!\n'},
            'cn': {'subject': f'{client_name} 的运输费用报告', 'body': f'尊敬的 {client_name}，\n\n附件为运输费用报告。\n感谢选择 PostPro！\n'}
        }
        l = labels[language]
        
        msg = MIMEMultipart()
        msg['From'] = 'postpro@example.com'
        msg['To'] = client_email
        msg['Subject'] = l['subject']
        msg.attach(MIMEText(l['body'], 'plain'))
        
        with open(pdf_path, 'rb') as attachment:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())
        encoders.encode_base64(part)
        
        part.add_header('Content-Disposition', 'attachment; filename=delivery_report.pdf')
        msg.attach(part)
        
        with smtplib.SMTP('smtp.example.com', 587) as server:
            server.starttls()
            server.login('postpro@example.com', 'your_password')
            server.send_message(msg)
        
        logger.info(f"PDF отправлен на: {client_email}")
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки email: {e}")
        return False

def get_customs_full_calculation(delivery_data: dict, customs_data: dict, language: str = 'ru') -> tuple:
    """Генерирует полный расчет для ИНВОЙС."""
    labels = {
        'ru': {
            'title': 'Детальный расчет для ИНВОЙС',
            'cargo': 'Данные груза',
            'weight': 'Вес',
            'volume': 'Объем',
            'density': 'плотность',
            'product': 'Товар',
            'city': 'Город',
            'invoice': 'Стоимость инвойса',
            'tnved': 'Код ТНВЭД',
            'customs': 'Таможенные платежи',
            'duty': 'Пошлина',
            'vat': 'НДС',
            'broker': 'Брокер',
            'declaration': 'Декларация',
            'certificate': 'Сертификат',
            'total_customs': 'Итого таможня',
            'options': 'Выберите вариант доставки',
            't1': 'Вариант 1: Доставка до Алматы (Т1)',
            't1_desc': 'Доставка до склада (самовывоз)',
            't2': 'Вариант 2: Доставка до двери (Т1+Т2)',
            't2_desc': 'Доставка до вашего адреса',
            'additional': 'Дополнительно',
            'service_fee': 'Сервисный сбор: 20% (учтен в доставке)',
            'fragile': 'Хрупкий груз: учтен (+50% к T2)',
            'village': 'Доставка в деревню: учтен (+100% к T2)',
            'choose': 'Напишите "1" или "2" для выбора доставки'
        },
        'kz': {
            'title': 'ИНВОЙС үшін егжей-тегжейлі есептеу',
            'cargo': 'Жүк туралы мәліметтер',
            'weight': 'Салмағы',
            'volume': 'Көлемі',
            'density': 'тығыздығы',
            'product': 'Товар',
            'city': 'Жеткізу қаласы',
            'invoice': 'Инвойс құны',
            'tnved': 'ТН ВЭД коды',
            'customs': 'Кедендік төлемдер',
            'duty': 'Кедендік баж',
            'vat': 'ҚҚС',
            'broker': 'Брокер',
            'declaration': 'Декларация',
            'certificate': 'Сертификат',
            'total_customs': 'Кеден барлығы',
            'options': 'Жеткізу нұсқасын таңдаңыз',
            't1': '1-нұсқа: Алматыға жеткізу (Т1)',
            't1_desc': 'Алматыдағы қоймаға жеткізу (өзін-өзі алып кету)',
            't2': '2-нұсқа: Есікке дейін жеткізу (Т1+Т2)',
            't2_desc': 'Сіздің мекенжайыңызға жеткізу',
            'additional': 'Қосымша',
            'service_fee': 'Қызмет ақысы: 20% (жеткізу құнында ескерілген)',
            'fragile': 'Сынғыш жүк: ескерілген (+50% T2)',
            'village': 'Ауылға жеткізу: ескерілген (+100% T2)',
            'choose': 'Жеткізу нұсқасын таңдау үшін "1" немесе "2" деп жазыңыз'
        },
        'cn': {
            'title': '发票详细费用计算',
            'cargo': '货物详情',
            'weight': '重量',
            'volume': '体积',
            'density': '密度',
            'product': '商品',
            'city': '送货城市',
            'invoice': '发票金额',
            'tnved': 'HS编码',
            'customs': '海关费用',
            'duty': '关税',
            'vat': '增值税',
            'broker': '经纪人',
            'declaration': '申报',
            'certificate': '证书',
            'total_customs': '海关总计',
            'options': '选择送货方式',
            't1': '选项1: 送货至阿拉木图 (T1)',
            't1_desc': '送货至仓库 (自取)',
            't2': '选项2: 送货上门 (T1+T2)',
            't2_desc': '送货至您的地址',
            'additional': '附加信息',
            'service_fee': '服务费: 20% (已包含在送货费用中)',
            'fragile': '易碎货物: 已考虑 (+50% T2)',
            'village': '乡村送货: 已考虑 (+100% T2)',
            'choose': '请输入“1”或“2”选择送货方式'
        }
    }
    
    l = labels[language]
    needs_certificate = check_certification_requirements(delivery_data['product_type'])
    customs_cost = calculate_customs_cost(
        customs_data['invoice_value'], delivery_data['product_type'],
        delivery_data['weight'], customs_data['has_certificate'], needs_certificate
    )
    delivery_cost = calculate_quick_cost(
        delivery_data['weight'], delivery_data['product_type'], delivery_data['city'],
        delivery_data['volume'], is_fragile=delivery_data.get('is_fragile', False),
        is_village=delivery_data.get('is_village', False)
    )
    
    if 'error' in delivery_cost or (customs_data.get('invoice_value') and 'error' in customs_cost):
        return "Ошибка расчета. Проверьте данные.", None, None
    
    t1_total = delivery_cost['t1_cost'] * 1.20 + customs_cost.get('total_kzt', 0)
    t2_total = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20 + customs_cost.get('total_kzt', 0)
    delivery_data.update(delivery_cost)
    
    city_suffix = (' (деревня, хрупкий груз)' if language == 'ru' else ' (ауыл, сынғыш жүк)' if language == 'kz' else ' (乡村，易碎货物)') \
                  if delivery_data.get('is_village') and delivery_data.get('is_fragile') else \
                  (' (хрупкий груз)' if language == 'ru' else ' (сынғыш жүк)' if language == 'kz' else ' (易碎货物)') \
                  if delivery_data.get('is_fragile') else \
                  (' (деревня)' if language == 'ru' else ' (ауыл)' if language == 'kz' else ' (乡村)') \
                  if delivery_data.get('is_village') else ''
    
    response = (
        f"🔍 {'Получены данные. Определяем код ТНВЭД для' if language == 'ru' else 'Деректер алынды. ТН ВЭД кодын анықтау' if language == 'kz' else '已接收数据。正在为商品确定HS编码'} “{delivery_data['product_type']}”...\n"
        f"✅ {'Код найден' if language == 'ru' else 'Код табылды' if language == 'kz' else '找到编码'}: {customs_data.get('tnved_code', '–')}\n\n"
        f"📊 **{l['title']}**:\n\n"
        f"📦 **{l['cargo']}**:\n"
        f"• {l['weight']}: {delivery_data['weight']} кг\n"
        f"• {l['volume']}: {delivery_data['volume']} м³ ({l['density']}: {delivery_cost['density']:.1f} кг/м³)\n"
        f"• {l['product']}: {delivery_data['product_type']}\n"
        f"• {l['city']}: {delivery_data['city'].capitalize()}{city_suffix}\n"
        f"• {l['invoice']}: {customs_data.get('invoice_value', '–')} USD\n"
        f"• {l['tnved']}: {customs_data.get('tnved_code', '–')}\n\n"
        f"💰 **{l['customs']}**:\n"
        f"• {l['duty']} ({customs_cost.get('customs_rate', 0) * 100:.0f}%): {customs_cost.get('duty_kzt', 0):,.0f} ₸ ({customs_cost.get('duty_usd', 0):,.0f} USD)\n"
        f"• {l['vat']} (12%): {customs_cost.get('vat_kzt', 0):,.0f} ₸ ({customs_cost.get('vat_usd', 0):,.0f} USD)\n"
        f"• {l['broker']}: {CUSTOMS_FEES['брокер']:,.0f} ₸\n"
        f"• {l['declaration']}: {CUSTOMS_FEES['декларация']:,.0f} ₸\n"
        f"• {l['certificate']}: {CUSTOMS_FEES['сертификат'] if needs_certificate else 0:,.0f} ₸ {'(требуется)' if language == 'ru' else '(қажет)' if language == 'kz' else '(必需)' if needs_certificate else '(не требуется)' if language == 'ru' else '(қажет емес)' if language == 'kz' else '(非必需)'}\n"
        f"• **{l['total_customs']}**: {customs_cost.get('total_kzt', 0):,.0f} ₸\n\n"
        f"🏷️ **{l['options']}**:\n\n"
        f"🚚 **{l['t1']}**:\n"
        f"• {l['t1_desc']}: {delivery_cost['t1_cost'] * 1.20:,.0f} ₸ ({delivery_cost['t1_rate']:.2f} USD/{delivery_cost['unit']})\n"
        f"• {l['customs']}: {customs_cost.get('total_kzt', 0):,.0f} ₸\n"
        f"• **{l['total']}**: {t1_total:,.0f} ₸\n\n"
        f"🏠 **{l['t2']}**:\n"
        f"• {l['t2_desc']} ({delivery_data['city'].capitalize()}): {(delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20:,.0f} ₸\n"
        f"  - T1: {delivery_cost['t1_cost'] * 1.20:,.0f} ₸ ({delivery_cost['t1_rate']:.2f} USD/{delivery_cost['unit']})\n"
        f"  - T2: {delivery_cost['t2_cost'] * 1.20:,.0f} ₸ (зона {delivery_cost['zone']}, {delivery_cost['t2_rate']:.0f} ₸/кг{' × 1.5 (' + l['fragile'].split(':')[0] + ')' if delivery_data.get('is_fragile') else ''}{' × 2.0 (' + l['village'].split(':')[0] + ')' if delivery_data.get('is_village') else ''})\n"
        f"• {l['customs']}: {customs_cost.get('total_kzt', 0):,.0f} ₸\n"
        f"• **{l['total']}**: {t2_total:,.0f} ₸\n\n"
        f"📋 **{l['additional']}**:\n"
        f"• {l['service_fee']}\n"
        f"• {l['fragile']}\n"
        f"• {l['village']}\n\n"
        f"💡 **{l['choose']}**"
    )
    return response, t1_total, t2_total

def extract_delivery_info(message: str, delivery_data: dict, customs_data: dict, language: str = 'ru') -> dict:
    """Извлекает данные доставки из сообщения."""
    missing_fields = []
    
    if not delivery_data.get('weight'):
        weight_match = re.search(r'(\d+\.?\d*)\s*(кг|kg|公斤)', message, re.IGNORECASE)
        if weight_match:
            delivery_data['weight'] = float(weight_match.group(1))
        else:
            missing_fields.append('вес груза (кг)' if language == 'ru' else 'жүктің салмағы (кг)' if language == 'kz' else '货物重量 (公斤)')
    
    if not delivery_data.get('product_type'):
        product_match = re.search(r'(одежда|электроника|мебель|общие товары|киім|жиһаз|электроника|жалпы тауарлар|服装|电子产品|家具|普通商品)', message, re.IGNORECASE)
        if product_match:
            product = product_match.group(1).lower()
            product_map = {
                'киім': 'одежда', 'жиһаз': 'мебель', 'электроника': 'электроника', 'жалпы тауарлар': 'общие товары',
                '服装': 'одежда', '电子产品': 'электроника', '家具': 'мебель', '普通商品': 'общие товары'
            }
            delivery_data['product_type'] = product_map.get(product, product)
        else:
            missing_fields.append('тип товара' if language == 'ru' else 'товар түрі' if language == 'kz' else '商品类型')
    
    if not delivery_data.get('city'):
        city_match = re.search(r'(алматы|астана|шымкент|караганда|阿拉木图|阿斯塔纳|奇姆肯特|卡拉干达)', message, re.IGNORECASE)
        if city_match:
            city = city_match.group(1).lower()
            city_map = {'ала木图': 'алматы', 'астана': 'астана', 'шымкент': 'шымкент', 'караганда': 'караганда'}
            delivery_data['city'] = city_map.get(city, city)
        else:
            missing_fields.append('город доставки' if language == 'ru' else 'жеткізу қаласы' if language == 'kz' else '送货城市')
    
    if not delivery_data.get('volume'):
        volume_match = re.search(r'(\d+\.?\d*)\s*(м3|м³|m3|立方米)', message, re.IGNORECASE)
        dimensions_match = re.search(r'(\d+)\s*[xх]\s*(\d+)\s*[xх]\s*(\d+)\s*(см|cm|厘米)', message, re.IGNORECASE)
        if volume_match:
            delivery_data['volume'] = float(volume_match.group(1))
        elif dimensions_match:
            length, width, height = map(float, dimensions_match.groups()[:3])
            delivery_data['volume'] = (length * width * height) / 1000000
        else:
            missing_fields.append('объем груза (м³) или габариты (Д×Ш×В в см)' if language == 'ru' else 'жүктің көлемі (м³) немесе өлшемдері (Ұ×Е×Б см-де)' if language == 'kz' else '货物体积 (立方米) или尺寸 (长×宽×高，厘米)')
    
    if not customs_data.get('invoice_value') and re.search(r'инвойс|invoice|发票', message, re.IGNORECASE):
        invoice_match = re.search(r'(\d+\.?\d*)\s*(usd|долларов|долл|美元)', message, re.IGNORECASE)
        if invoice_match:
            customs_data['invoice_value'] = float(invoice_match.group(1))
        else:
            missing_fields.append('стоимость инвойса (USD)' if language == 'ru' else 'инвойс құны (USD)' if language == 'kz' else '发票金额 (美元)')
    
    if re.search(r'хрупкий|сынғыш|易碎', message, re.IGNORECASE):
        delivery_data['is_fragile'] = True
    if re.search(r'деревня|ауыл|乡村', message, re.IGNORECASE):
        delivery_data['is_village'] = True
    
    if missing_fields:
        return {'error': f"{'Пожалуйста, укажите' if language == 'ru' else 'Көрсетіңіз' if language == 'kz' else '请提供'}: {', '.join(missing_fields)}."}
    
    return delivery_data

def doesnt_know_tnved(message: str) -> bool:
    """Проверяет, указал ли пользователь, что не знает код ТНВЭД."""
    return message.lower() in ['не знаю', 'помоги', 'dont know', '不知道', 'жоқ', 'авто']

@app.route('/chat', methods=['POST'])
def chat():
    """Основной эндпоинт для обработки запросов."""
    try:
        data = request.json
        user_message = data.get('message', '').strip()
        callback_data = data.get('callback_data', '')
        
        # Инициализация сессии
        if 'delivery_data' not in session:
            session['delivery_data'] = {
                'weight': None, 'product_type': None, 'city': None, 'volume': None,
                'delivery_type': None, 'delivery_option': None, 'is_fragile': False, 'is_village': False
            }
        if 'customs_data' not in session:
            session['customs_data'] = {
                'invoice_value': None, 'has_certificate': False,
                'needs_certificate': False, 'tnved_code': None
            }
        if 'chat_history' not in session:
            session['chat_history'] = []
        if 'language' not in session:
            session['language'] = 'ru'
        
        delivery_data = session['delivery_data']
        customs_data = session['customs_data']
        chat_history = session['chat_history']
        language = session['language']
        
        # Автоматическое определение языка
        if user_message and not callback_data:
            detected_lang = detect_language(user_message)
            if session.get('language_locked', False) is False:
                language = detected_lang
                session['language'] = language
        
        # Обработка выбора языка
        if callback_data in ['lang_ru', 'lang_kz', 'lang_cn']:
            language = callback_data.split('_')[1]
            session['language'] = language
            session['language_locked'] = True
            message, keyboard = get_welcome_message(language)
            logger.info(f"Клиент выбрал язык: {language}")
            chat_history.append(f"Ассистент: {message}")
            session['chat_history'] = chat_history
            return jsonify({"response": message, "keyboard": keyboard})
        
        # Сброс сессии
        if user_message.lower() in ['/start', 'сброс', 'начать заново', 'новый расчет', 'старт'] or callback_data == 'start':
            session.clear()
            message, keyboard = get_welcome_message(language)
            return jsonify({"response": message, "keyboard": keyboard})
        
        # Обработка подтверждения заявки
        if session.get('waiting_for_order_confirmation'):
            if user_message.lower() in ['да', 'иә', 'yes', '是'] or callback_data == 'confirm_yes':
                session['waiting_for_order_confirmation'] = False
                session['waiting_for_contacts'] = True
                response = {
                    'ru': "📞 Чтобы оформить, введите ваше имя и телефон (например: Иван, 87771234567)",
                    'kz': "📞 Рәсімдеу үшін атыңызды және телефоныңызды енгізіңіз (мысалы: Иван, 87771234567)",
                    'cn': "📞 要下订单，请输入您的姓名和电话号码 (例如: Иван, 87771234567)"
                }[language]
                keyboard = None
            elif user_message.lower() in ['нет', 'жоқ', 'no', '否'] or callback_data == 'confirm_no':
                session['waiting_for_order_confirmation'] = False
                response = {
                    'ru': "Хорошо, заявка не оформлена. Для нового расчета напишите 'старт'.",
                    'kz': "Жақсы, тапсырыс рәсімделмеді. Жаңа есептеу үшін 'старт' деп жазыңыз.",
                    'cn': "好的，订单未下达。要重新计算，请输入 'старт'。"
                }[language]
                keyboard = [{"text": "Старт", "callback_data": "start"}]
            else:
                response = {
                    'ru': "Пожалуйста, напишите 'да' или 'нет' для подтверждения оформления заявки.",
                    'kz': "Тапсырысты растау үшін 'иә' немесе 'жоқ' деп жазыңыз.",
                    'cn': "请输入 'да' (是) 或 'нет' (否) 以确认订单。"
                }[language]
                keyboard = [
                    {"text": "Да", "callback_data": "confirm_yes"},
                    {"text": "Нет", "callback_data": "confirm_no"}
                ]
            return jsonify({"response": response, "keyboard": keyboard})

        # Обработка контактов
        if session.get('waiting_for_contacts'):
            name_match = re.search(r'([а-яА-Яa-zA-Z\s]+)', user_message)
            phone_match = re.search(r'(\d{10,11})', user_message)
            if name_match and phone_match:
                name = name_match.group(1).strip()
                phone = phone_match.group(1)
                session['waiting_for_contacts'] = False
                
                # Здесь будет логика сохранения заявки и отправки отчета
                response = {
                    'ru': f"✅ Заявка оформлена! {name}, мы свяжемся с вами по телефону {phone} в течение 15 минут. Для нового расчета напишите 'старт'.",
                    'kz': f"✅ Тапсырыс рәсімделді! {name}, сізбен {phone} телефоны арқылы 15 минут ішінде хабарласамыз. Жаңа есептеу үшін 'старт' деп жазыңыз.",
                    'cn': f"✅ 订单已下达！{name}，我们将在15分钟内通过电话 {phone} 与您联系。要重新计算，请输入 'старт'。"
                }[language]
                return jsonify({"response": response})
            else:
                response = {
                    'ru': "Не удалось распознать контакты. Пожалуйста, введите имя и телефон в формате: `Иван, 87771234567`",
                    'kz': "Контактілерді тану мүмкін болмады. Атыңызды және телефоныңызды келесі форматта енгізіңіз: `Иван, 87771234567`",
                    'cn': "无法识别联系方式。请输入姓名和电话号码，格式为：`Иван, 87771234567`"
                }[language]
                return jsonify({"response": response})
        
        # Обработка выбора доставки
        if session.get('waiting_for_delivery_choice'):
            if user_message in ['1', '2'] or callback_data in ['delivery_1', 'delivery_2']:
                option = user_message or callback_data.split('_')[1]
                session['waiting_for_delivery_choice'] = False
                delivery_data['delivery_option'] = option
                
                delivery_cost = calculate_quick_cost(
                    delivery_data['weight'], delivery_data['product_type'], delivery_data['city'], 
                    delivery_data['volume'], delivery_data.get('is_fragile'), delivery_data.get('is_village')
                )
                
                customs_total = customs_data.get('total_kzt', 0)
                t1_delivery_cost = delivery_cost.get('t1_cost', 0) * 1.20
                t2_delivery_cost = (delivery_cost.get('t1_cost', 0) + delivery_cost.get('t2_cost', 0)) * 1.20
                
                total_cost = (t1_delivery_cost if option == '1' else t2_delivery_cost) + customs_total
                
                response_text = {
                    'ru': (f"✅ Выбран вариант {option}.\n"
                           f"💰 Итоговая стоимость: {total_cost:,.0f} ₸\n\n"
                           "Хотите оформить заявку?"),
                    'kz': (f"✅ {option}-нұсқа таңдалды.\n"
                           f"💰 Жалпы құны: {total_cost:,.0f} ₸\n\n"
                           "Тапсырысты рәсімдегіңіз келе ме?"),
                    'cn': (f"✅ 已选择选项 {option}。\n"
                           f"💰 总费用: {total_cost:,.0f} ₸\n\n"
                           "您想下订单吗？")
                }[language]

                session['waiting_for_order_confirmation'] = True
                keyboard = [
                    {"text": {'ru': "Да", 'kz': "Иә", 'cn': "是"}[language], "callback_data": "confirm_yes"},
                    {"text": {'ru': "Нет", 'kz': "Жоқ", 'cn': "否"}[language], "callback_data": "confirm_no"}
                ]
                return jsonify({"response": response_text, "keyboard": keyboard})
            else:
                response = {
                    'ru': "Пожалуйста, выберите вариант доставки, написав '1' или '2'.",
                    'kz': "Жеткізу нұсқасын таңдау үшін '1' немесе '2' деп жазыңыз.",
                    'cn': "请输入 '1' 或 '2' 选择送货方式。"
                }[language]
                return jsonify({"response": response})
        
        # Основной процесс обработки
        extracted_data = extract_delivery_info(user_message, delivery_data, customs_data, language)

        if 'error' in extracted_data:
            return jsonify({"response": extracted_data['error']})
        
        delivery_data.update(extracted_data)
        
        # Определение типа доставки (КАРГО или ИНВОЙС)
        is_invoice = customs_data.get('invoice_value') is not None or re.search(r'инвойс|invoice|发票', user_message, re.IGNORECASE)
        
        # Расчет для КАРГО
        if not is_invoice:
            delivery_cost = calculate_quick_cost(
                delivery_data['weight'], delivery_data['product_type'], delivery_data['city'], 
                delivery_data['volume'], delivery_data.get('is_fragile'), delivery_data.get('is_village')
            )
            if 'error' in delivery_cost:
                return jsonify({"response": delivery_cost['error']})
            
            t1_total = delivery_cost['t1_cost'] * 1.20
            t2_total = (delivery_cost['t1_cost'] + delivery_cost['t2_cost']) * 1.20
            
            response = {
                'ru': (
                    f"📊 Расчет КАРГО для {delivery_data['weight']} кг {delivery_data['product_type']} в г. {delivery_data['city'].capitalize()}:\n\n"
                    f"🚚 **Вариант 1 (до Алматы):** {t1_total:,.0f} ₸\n"
                    f"🏠 **Вариант 2 (до двери):** {t2_total:,.0f} ₸\n\n"
                    "💡 Напишите '1' или '2' чтобы выбрать и оформить заявку."
                ),
                'kz': (
                    f"📊 {delivery_data['city'].capitalize()} қаласына {delivery_data['weight']} кг {delivery_data['product_type']} үшін КАРГО есебі:\n\n"
                    f"🚚 **1-нұсқа (Алматыға дейін):** {t1_total:,.0f} ₸\n"
                    f"🏠 **2-нұсқа (есікке дейін):** {t2_total:,.0f} ₸\n\n"
                    "💡 Таңдау және тапсырыс беру үшін '1' немесе '2' деп жазыңыз."
                ),
                'cn': (
                    f"📊 到 {delivery_data['city'].capitalize()} 的 {delivery_data['weight']} 公斤 {delivery_data['product_type']} 的货运计算:\n\n"
                    f"🚚 **选项 1 (到阿拉木图):** {t1_total:,.0f} ₸\n"
                    f"🏠 **选项 2 (送货上门):** {t2_total:,.0f} ₸\n\n"
                    "💡 请输入 '1' 或 '2' 进行选择并下订单。"
                )
            }[language]
            
            session['waiting_for_delivery_choice'] = True
            session['delivery_data'] = delivery_data
            session['customs_data'] = customs_data
            
            return jsonify({"response": response})
        
        # Расчет для ИНВОЙС
        else:
            customs_data['tnved_code'] = get_tnved_code(delivery_data['product_type'])
            needs_cert = check_certification_requirements(delivery_data['product_type'])
            
            customs_calc = calculate_customs_cost(
                customs_data['invoice_value'], delivery_data['product_type'], 
                delivery_data['weight'], customs_data.get('has_certificate', False), needs_cert
            )
            if 'error' in customs_calc:
                return jsonify({"response": customs_calc['error']})
            
            customs_data.update(customs_calc)
            
            response, t1_total, t2_total = get_customs_full_calculation(delivery_data, customs_data, language)
            
            session['waiting_for_delivery_choice'] = True
            session['delivery_data'] = delivery_data
            session['customs_data'] = customs_data
            
            return jsonify({"response": response})

    except Exception as e:
        logger.error(f"Ошибка в /chat: {e}", exc_info=True)
        return jsonify({"response": "Произошла внутренняя ошибка. Попробуйте начать заново, написав 'старт'."})


@app.route('/clear', methods=['POST'])
def clear_chat():
    session.clear()
    return jsonify({"status": "success"})

if __name__ == '__main__':
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        local_ip = '127.0.0.1'
    
    logger.info(f"=== PostPro Chat Bot запущен ===")
    logger.info(f"Локальный доступ: http://localhost:5000")
    logger.info(f"Сетевой доступ: http://{local_ip}:5000")
    logger.info(f"=================================")
    
    app.run(host='0.0.0.0', port=5000, debug=True)
