# generate_test_data.py
import random
import string

def generate_track_numbers(count=20):
    """
    Генерирует тестовые трек-номера в формате: PP + 8 цифр + KZ
    """
    track_numbers = []
    for i in range(count):
        number = 'PP' + ''.join(random.choices(string.digits, k=8)) + 'KZ'
        track_numbers.append(number)
    return track_numbers

def main():
    # Генерируем 20 трек-номеров
    test_track_numbers = generate_track_numbers(20)
    
    print("Тестовые трек-номера для POST PRO:")
    print("=" * 40)
    
    for i, track in enumerate(test_track_numbers, 1):
        print(f"{i:2d}. {track}")
    
    # Сохраняем в файл
    with open('test_track_numbers.txt', 'w', encoding='utf-8') as f:
        for track in test_track_numbers:
            f.write(track + '\n')
    
    print("\n✅ Трек-номера сохранены в файл: test_track_numbers.txt")

if __name__ == "__main__":
    main()
