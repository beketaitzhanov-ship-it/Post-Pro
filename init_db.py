import sqlite3

# Устанавливаем соединение с базой данных
# Файл 'applications.db' будет создан автоматически
connection = sqlite3.connect('applications.db')
cursor = connection.cursor()

# SQL-команда для создания таблицы
# Мы определяем колонки и типы данных для каждой заявки
create_table_query = """
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

# Выполняем команду
cursor.execute(create_table_query)

# Сохраняем изменения и закрываем соединение
connection.commit()
connection.close()

print("✅ База данных 'applications.db' и таблица 'applications' успешно созданы!")
