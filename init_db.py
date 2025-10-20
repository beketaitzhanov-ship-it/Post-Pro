# init_db.py
import sqlite3
import os
from typing import NoReturn

def init_database() -> NoReturn:
    """
    Инициализирует все таблицы базы данных.
    Возвращает NoReturn для явного указания, что функция не возвращает значение.
    """
    
    # Создаем папку data если её нет
    os.makedirs('data', exist_ok=True)
    
    # ПРАВИЛЬНЫЙ путь к базе данных в папке data
    db_path = 'data/applications.db'
    connection: sqlite3.Connection = None
    cursor: sqlite3.Cursor = None
    
    try:
        # Подключаемся к базе данных
        connection = sqlite3.connect(db_path)
        cursor = connection.cursor()
        
        # ВКЛЮЧАЕМ поддержку FOREIGN KEY
        cursor.execute("PRAGMA foreign_keys = ON;")
        cursor.execute("PRAGMA encoding = 'UTF-8';")
        cursor.execute("PRAGMA journal_mode = WAL;")  # Для лучшей производительности

        print("🔄 Начинаю инициализацию базы данных...")
        
        # 1. Создаем таблицу applications (независимая таблица)
        applications_table = """
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            weight REAL,
            product_type TEXT,
            city TEXT
        );
        """
        cursor.execute(applications_table)
        print("✅ Таблица 'applications' создана/проверена")

        # 2. Создаем таблицу orders (основная таблица для трекинга)
        orders_table = """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_number TEXT UNIQUE NOT NULL,
            customer_name TEXT,
            customer_phone TEXT,
            weight_kg REAL,
            volume_m3 REAL,
            product_type TEXT,
            destination_city TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            estimated_delivery TEXT,
            route_type TEXT DEFAULT 'guangzhou_almaty',
            current_status TEXT DEFAULT 'created'
        );
        """
        cursor.execute(orders_table)
        print("✅ Таблица 'orders' создана/проверена")

        # 3. Создаем таблицу telegram_users (независимая таблица)
        telegram_users_table = """
        CREATE TABLE IF NOT EXISTS telegram_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT,
            role TEXT DEFAULT 'operator',
            is_active INTEGER DEFAULT 1 CHECK (is_active IN (0, 1)),
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
        cursor.execute(telegram_users_table)
        print("✅ Таблица 'telegram_users' создана/проверена")

        # 4. Создаем таблицу shipment_status (зависит от orders - ПОСЛЕДНЯЯ!)
        status_table = """
        CREATE TABLE IF NOT EXISTS shipment_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_number TEXT NOT NULL,
            status TEXT NOT NULL,
            location TEXT,
            description TEXT,
            photos TEXT,
            coordinates TEXT,
            timestamp TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (track_number) REFERENCES orders(track_number) ON DELETE CASCADE ON UPDATE CASCADE
        );
        """
        cursor.execute(status_table)
        print("✅ Таблица 'shipment_status' создана/проверена")

        # Коммитим все таблицы
        connection.commit()

        # 5. Создаем индексы для оптимизации
        indexes = [
            # Индексы для orders
            "CREATE INDEX IF NOT EXISTS idx_orders_track_number ON orders(track_number);",
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(current_status);",
            "CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);",
            
            # Индексы для shipment_status  
            "CREATE INDEX IF NOT EXISTS idx_shipment_status_track_number ON shipment_status(track_number);",
            "CREATE INDEX IF NOT EXISTS idx_shipment_status_timestamp ON shipment_status(timestamp);",
            "CREATE INDEX IF NOT EXISTS idx_shipment_status_status ON shipment_status(status);",
            
            # Индексы для telegram_users
            "CREATE INDEX IF NOT EXISTS idx_telegram_users_telegram_id ON telegram_users(telegram_id);",
            "CREATE INDEX IF NOT EXISTS idx_telegram_users_role ON telegram_users(role);",
            
            # Индексы для applications
            "CREATE INDEX IF NOT EXISTS idx_applications_timestamp ON applications(timestamp);",
            "CREATE INDEX IF NOT EXISTS idx_applications_phone ON applications(phone);"
        ]

        created_indexes = 0
        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
                created_indexes += 1
            except sqlite3.Error as e:
                print(f"⚠️ Предупреждение при создании индекса: {e}")

        connection.commit()
        print(f"✅ Создано/проверено индексов: {created_indexes}/{len(indexes)}")

        # ===== ВАЛИДАЦИЯ И ПРОВЕРКИ =====
        
        # Проверяем существование таблиц
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' 
            AND name NOT LIKE 'sqlite_%'
            ORDER BY name;
        """)
        tables = [table[0] for table in cursor.fetchall()]
        
        expected_tables = ['applications', 'orders', 'shipment_status', 'telegram_users']
        missing_tables = set(expected_tables) - set(tables)
        
        if missing_tables:
            raise Exception(f"Отсутствуют таблицы: {missing_tables}")

        print("\n📊 Таблицы в базе данных:")
        for table in sorted(tables):
            print(f"   - {table}")

        # Проверяем FOREIGN KEY
        cursor.execute("PRAGMA foreign_keys;")
        foreign_keys_status = cursor.fetchone()[0]
        if not foreign_keys_status:
            raise Exception("FOREIGN KEY support is disabled")

        print(f"🔗 Поддержка FOREIGN KEY: ВКЛЮЧЕНА ✅")

        # Проверяем структуру таблиц
        print("\n🔍 Структура таблиц:")
        table_columns = {
            'applications': 7,
            'orders': 11, 
            'shipment_status': 8,
            'telegram_users': 6
        }
        
        for table_name, expected_columns in table_columns.items():
            cursor.execute(f"PRAGMA table_info({table_name});")
            columns = cursor.fetchall()
            actual_columns = len(columns)
            status = "✅" if actual_columns == expected_columns else f"❌ (ожидалось {expected_columns})"
            print(f"   {table_name}: {actual_columns} колонок {status}")

        # Финальная проверка целостности
        cursor.execute("PRAGMA integrity_check;")
        integrity_result = cursor.fetchone()[0]
        if integrity_result != "ok":
            raise Exception(f"Проверка целостности не пройдена: {integrity_result}")

        print(f"🔍 Проверка целостности: ✅")
        print(f"\n🎉 База данных успешно инициализирована: {db_path}")
            
    except sqlite3.Error as e:
        print(f"❌ Ошибка SQLite: {e}")
        if connection:
            connection.rollback()
        raise  # Пробрасываем исключение дальше
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        if connection:
            connection.rollback()
        raise
    finally:
        if connection:
            connection.close()
            print("🔒 Соединение с базой данных закрыто")

if __name__ == "__main__":
    init_database()
