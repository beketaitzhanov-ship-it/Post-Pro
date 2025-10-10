import google.generativeai as genai
import os
from dotenv import load_dotenv

# Загружаем ключ из .env файла
load_dotenv()
GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")

if not GEMINI_API_KEY:
    print("Ошибка: Не удалось найти GOOGLE_API_KEY в файле .env")
else:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        print("--- Список доступных моделей для вашего ключа ---")
        for model in genai.list_models():
            # Проверяем, поддерживает ли модель метод generateContent
            if 'generateContent' in model.supported_generation_methods:
                print(model.name)
        print("-------------------------------------------------")
    except Exception as e:
        print(f"Произошла ошибка при подключении к Google API: {e}")