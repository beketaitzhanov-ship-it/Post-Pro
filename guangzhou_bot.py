import os
import logging
import random
import psycopg2
import requests # 👈 Добавили библиотеку для Make
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from dotenv import load_dotenv

# --- НАСТРОЙКИ ---
load_dotenv()
TOKEN = os.getenv('GUANGZHOU_BOT_TOKEN') 
DATABASE_URL = os.getenv('DATABASE_URL')

# 👇 ВСТАВЬ СЮДА НОВУЮ ССЫЛКУ ИЗ MAKE (Сценарий 3)
MAKE_WAREHOUSE_WEBHOOK = "https://hook.eu1.make.com/qjsepifbths7ek1hkv91cdid7kt4xjqx"

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

GUANGZHOU_CONFIG = {
    "warehouse_name": "Гуанчжоу",
    "track_prefix": "GZ"
}

WAITING_FIO, WAITING_PRODUCT, WAITING_WEIGHT, WAITING_VOLUME, WAITING_PHONE = range(5)
WAITING_ACTUAL_WEIGHT = 5 
WAITING_STATUS_TRACK = 6

class GuangzhouBot:
    def __init__(self):
        self.token = TOKEN
        self.application = None
        self.setup_bot()
    
    def setup_bot(self):
        if not self.token:
            logger.error("❌ ОШИБКА: Токен не найден!")
            return
        self.application = Application.builder().token(self.token).build()
        self.setup_handlers()
    
    def get_db_connection(self):
        try:
            return psycopg2.connect(DATABASE_URL)
        except Exception as e:
            logger.error(f"❌ Ошибка БД: {e}")
            return None

    # --- ФУНКЦИЯ ОТПРАВКИ В MAKE ---
    def notify_make(self, event_type, data):
        """Отправляет событие в Make для уведомления клиента"""
        if not MAKE_WAREHOUSE_WEBHOOK: return
        
        payload = {
            "event": event_type, # "received", "sent", "border", "delivered"
            "track": data.get('track_number'),
            "fio": data.get('fio'),
            "phone": data.get('phone'),
            "weight": data.get('actual_weight') or data.get('weight'),
            "status": data.get('status'),
            "manager": data.get('manager'),
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            # Запускаем в отдельном потоке или просто fire-and-forget, чтобы не тормозить бота
            requests.post(MAKE_WAREHOUSE_WEBHOOK, json=payload, timeout=1)
        except Exception as e:
            logger.error(f"⚠️ Ошибка отправки в Make: {e}")

    # --- СЦЕНАРИЙ 1: НОВЫЙ ГРУЗ ---
    async def start_new_cargo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("👤 Введите **ФИО клиента**:")
        return WAITING_FIO

    async def get_fio(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['new_fio'] = update.message.text
        await update.message.reply_text("📦 **Что за товар?**:")
        return WAITING_PRODUCT

    async def get_product(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data['new_product'] = update.message.text
        await update.message.reply_text("⚖️ **Вес (кг):**")
        return WAITING_WEIGHT

    async def get_weight(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            w = float(update.message.text.replace(',', '.'))
            context.user_data['new_weight'] = w
            await update.message.reply_text("📏 **Объем (м³):**")
            return WAITING_VOLUME
        except ValueError:
            await update.message.reply_text("❌ Введите число:")
            return WAITING_WEIGHT

    async def get_volume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            v = float(update.message.text.replace(',', '.'))
            context.user_data['new_volume'] = v
            await update.message.reply_text("📞 **Телефон клиента:**")
            return WAITING_PHONE
        except ValueError:
            await update.message.reply_text("❌ Введите число:")
            return WAITING_VOLUME

    async def get_phone_and_save(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        phone = update.message.text
        track = f"{GUANGZHOU_CONFIG['track_prefix']}{random.randint(100000, 999999)}"
        
        conn = self.get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                sql = """
                INSERT INTO shipments (
                    track_number, fio, phone, product, 
                    declared_weight, actual_weight, declared_volume, actual_volume,
                    status, route_progress, warehouse_code, manager, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """
                
                shipment_data = {
                    'track_number': track,
                    'fio': context.user_data['new_fio'],
                    'phone': phone,
                    'actual_weight': context.user_data['new_weight'],
                    'status': "принят на складе",
                    'manager': update.message.from_user.first_name
                }

                cur.execute(sql, (
                    track,
                    shipment_data['fio'],
                    shipment_data['phone'],
                    context.user_data['new_product'],
                    context.user_data['new_weight'], 
                    context.user_data['new_weight'],
                    context.user_data['new_volume'],
                    context.user_data['new_volume'],
                    shipment_data['status'],
                    0,
                    GUANGZHOU_CONFIG['warehouse_name'],
                    shipment_data['manager']
                ))
                conn.commit()
                
                # 🔥 ОТПРАВЛЯЕМ В MAKE
                self.notify_make("received", shipment_data)

                await update.message.reply_text(f"✅ **ГРУЗ {track} ПРИНЯТ!**\n💾 Сохранено в базу + Make.")
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка БД: {e}")
            finally:
                conn.close()
        return ConversationHandler.END

    # --- СЦЕНАРИЙ 2: ОЖИДАЕМЫЕ ---
    async def show_expected(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        conn = self.get_db_connection()
        if not conn: return
        try:
            cur = conn.cursor()
            cur.execute("SELECT contract_num, fio, product FROM shipments WHERE status = 'Оформлен' ORDER BY created_at DESC LIMIT 10")
            rows = cur.fetchall()
            if not rows:
                await update.message.reply_text("📋 Ожидаемых нет.")
                return
            text = "📋 **ОЖИДАЮТСЯ:**\n\n"
            for row in rows:
                text += f"🔹 `{row[0]}` — {row[1]} ({row[2]})\n"
            text += "👇 Введи номер CN-..., чтобы принять."
            await update.message.reply_text(text)
        finally:
            conn.close()

    async def start_contract_receive(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        track = update.message.text.strip().upper()
        context.user_data['receiving_track'] = track
        
        conn = self.get_db_connection()
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT fio, phone FROM shipments WHERE contract_num = %s OR track_number = %s", (track, track))
            row = cur.fetchone()
            conn.close()
            if row:
                context.user_data['receiving_fio'] = row[0]
                context.user_data['receiving_phone'] = row[1]
                await update.message.reply_text(f"📥 Приемка **{track}**\n👤 {row[0]}\n⚖️ **Введите ФАКТ. вес (кг):**")
                return WAITING_ACTUAL_WEIGHT
            else:
                await update.message.reply_text("❌ Не найдено.")
                return ConversationHandler.END

    async def save_contract_receive(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            actual_weight = float(update.message.text.replace(',', '.'))
            track = context.user_data['receiving_track']
            
            conn = self.get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE shipments 
                    SET status = 'принят на складе', actual_weight = %s, created_at = NOW() 
                    WHERE contract_num = %s OR track_number = %s
                """, (actual_weight, track, track))
                conn.commit()
                conn.close()
                
                # 🔥 ОТПРАВЛЯЕМ В MAKE
                self.notify_make("received", {
                    "track_number": track,
                    "fio": context.user_data.get('receiving_fio'),
                    "phone": context.user_data.get('receiving_phone'),
                    "actual_weight": actual_weight,
                    "status": "принят на складе",
                    "manager": update.message.from_user.first_name
                })

                await update.message.reply_text(f"✅ Груз {track} принят! Вес {actual_weight} кг.")
        except ValueError:
            await update.message.reply_text("❌ Введите число.")
            return WAITING_ACTUAL_WEIGHT
        return ConversationHandler.END

    # --- СЦЕНАРИЙ 3: СМЕНА СТАТУСА ---
    async def set_status_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        mode = "sent" if "ОТПРАВЛЕНО" in text else "border" if "НА ГРАНИЦЕ" in text else "delivered"
        context.user_data['status_mode'] = mode
        await update.message.reply_text(f"🔄 Режим: **{text}**\n👇 Сканируй треки:")
        return WAITING_STATUS_TRACK

    async def update_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        track = update.message.text.strip().upper()
        if track in ["➕ НОВЫЙ ГРУЗ", "📋 ОЖИДАЕМЫЕ ГРУЗЫ", "🚚 ОТПРАВЛЕНО", "🛃 НА ГРАНИЦЕ", "✅ ДОСТАВЛЕНО"]: return ConversationHandler.END

        mode = context.user_data.get('status_mode')
        status_map = {
            "sent": ("в пути до границы", "🚚 Уехал"),
            "border": ("на границе", "🛃 На границе"),
            "delivered": ("доставлен", "✅ Выдан")
        }
        
        if mode in status_map:
            new_status, msg = status_map[mode]
            conn = self.get_db_connection()
            if conn:
                cur = conn.cursor()
                # Сначала читаем данные (чтобы отправить в Make)
                cur.execute("SELECT fio, phone, actual_weight FROM shipments WHERE track_number = %s OR contract_num = %s", (track, track))
                row = cur.fetchone()
                
                if row:
                    # Обновляем
                    cur.execute("UPDATE shipments SET status = %s WHERE track_number = %s OR contract_num = %s", (new_status, track, track))
                    conn.commit()
                    
                    # 🔥 ОТПРАВЛЯЕМ В MAKE
                    self.notify_make(mode, {
                        "track_number": track,
                        "fio": row[0],
                        "phone": row[1],
                        "actual_weight": row[2],
                        "status": new_status,
                        "manager": update.message.from_user.first_name
                    })
                    
                    await update.message.reply_text(f"{msg} {track}")
                else:
                    await update.message.reply_text("❌ Не найден.")
                conn.close()
        return WAITING_STATUS_TRACK

    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🏠 Меню.")
        return ConversationHandler.END

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [KeyboardButton("➕ НОВЫЙ ГРУЗ"), KeyboardButton("📋 ОЖИДАЕМЫЕ ГРУЗЫ")],
            [KeyboardButton("🚚 ОТПРАВЛЕНО"), KeyboardButton("🛃 НА ГРАНИЦЕ")],
            [KeyboardButton("✅ ДОСТАВЛЕНО")]
        ]
        await update.message.reply_text("🏭 **СКЛАД ГУАНЧЖОУ**\nMake подключен 🟢", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))

    def setup_handlers(self):
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(MessageHandler(filters.Regex('^(📋 ОЖИДАЕМЫЕ ГРУЗЫ)$'), self.show_expected))
        
        self.application.add_handler(ConversationHandler(
            entry_points=[MessageHandler(filters.Regex('^(➕ НОВЫЙ ГРУЗ)'), self.start_new_cargo)],
            states={
                WAITING_FIO: [MessageHandler(filters.TEXT, self.get_fio)],
                WAITING_PRODUCT: [MessageHandler(filters.TEXT, self.get_product)],
                WAITING_WEIGHT: [MessageHandler(filters.TEXT, self.get_weight)],
                WAITING_VOLUME: [MessageHandler(filters.TEXT, self.get_volume)],
                
                WAITING_PHONE: [MessageHandler(filters.TEXT, self.get_phone_and_save)],
            },
            fallbacks=[CommandHandler('cancel', self.cancel)]
        ))

        self.application.add_handler(ConversationHandler(
            entry_points=[MessageHandler(filters.Regex(r'^CN-\d+'), self.start_contract_receive)],
            states={WAITING_ACTUAL_WEIGHT: [MessageHandler(filters.TEXT, self.save_contract_receive)]},
            fallbacks=[CommandHandler('cancel', self.cancel)]
        ))

        self.application.add_handler(ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex('^(🚚 ОТПРАВЛЕНО)$'), self.set_status_mode),
                MessageHandler(filters.Regex('^(🛃 НА ГРАНИЦЕ)$'), self.set_status_mode),
                MessageHandler(filters.Regex('^(✅ ДОСТАВЛЕНО)$'), self.set_status_mode)
            ],
            states={WAITING_STATUS_TRACK: [MessageHandler(filters.TEXT, self.update_status)]},
            fallbacks=[CommandHandler('cancel', self.cancel), MessageHandler(filters.Regex('^➕'), self.cancel)]
        ))

    def run(self):
        self.application.run_polling()

if __name__ == '__main__':
    bot = GuangzhouBot()
    bot.run()
