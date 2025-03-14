import os
import sys
import codecs
import requests
import psycopg2
import json
import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import dotenv
import logging

# Установка UTF-8 для вывода
if sys.stdout.encoding != 'utf-8':
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer)

# Отключаем подробные логи httpx
logging.getLogger("httpx").setLevel(logging.WARNING)

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.handlers = [handler]

# Загрузка переменных из .env
dotenv.load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
PROVIDER_TOKEN = os.getenv('TELEGRAM_PROVIDER_TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
DB_PASSWORD = os.getenv('DB_PASSWORD')
ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
WEATHER_API_KEY = os.getenv('WEATHER_API_KEY')

# Главное меню (InlineKeyboardMarkup)
MAIN_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("☀️ Погода", callback_data="weather"), InlineKeyboardButton("👤 Админ", callback_data="admin")],
    [InlineKeyboardButton("⭐ Любимый город", callback_data="favorite_weather")],
    [InlineKeyboardButton("🎮 Поиграть", callback_data="play")]
])

# Меню игр (InlineKeyboardMarkup)
GAMES_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🏙️ Города", callback_data="game_cities"), InlineKeyboardButton("🔢 Угадай число", callback_data="game_guess")],
    [InlineKeyboardButton("🗺️ Квест", callback_data="game_quest"), InlineKeyboardButton("🧩 Логика", callback_data="game_logic")],
    [InlineKeyboardButton("🔙 Назад", callback_data="main")]
])

# Загрузка списка городов
try:
    with open("cities.txt", "r", encoding="utf-8") as f:
        VALID_CITIES = set(line.strip().lower() for line in f)
except FileNotFoundError:
    logger.error("Файл cities.txt не найден!")
    VALID_CITIES = set()

# Квест
QUEST_STAGES = [
    "Ты в тёмном лесу. Куда пойдёшь? (вперёд/назад)",
    "Ты нашёл сундук. Открыть? (да/нет)",
    "Внутри сундука ключ. Взять? (да/нет)"
]

# Загадки для логики
LOGIC_RIDDLES = [
    {"riddle": "Число: 2, 4, 6, ?. Какое следующее?", "answer": "8"},
    {"riddle": "Что всегда идёт, но никогда не приходит?", "answer": "время"},
    {"riddle": "У меня есть города, но нет домов. Что я?", "answer": "карта"}
]

# Инициализация базы данных
def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS weather_requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                city TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS favorite_cities (
                user_id BIGINT,
                city TEXT,
                PRIMARY KEY (user_id, city)
            );
            CREATE TABLE IF NOT EXISTS game_progress (
                user_id BIGINT PRIMARY KEY,
                game_name TEXT,
                score INTEGER DEFAULT 0,
                state JSONB DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS user_achievements (
                user_id BIGINT,
                achievement TEXT,
                PRIMARY KEY (user_id, achievement)
            );
            CREATE TABLE IF NOT EXISTS user_stars (
                user_id BIGINT PRIMARY KEY,
                stars INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS game_results (
                user_id BIGINT,
                game_name TEXT,
                score INTEGER,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")
    finally:
        cursor.close()
        conn.close()

def log_weather_request(user_id, city):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO weather_requests (user_id, city) VALUES (%s, %s)", (user_id, city))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при логировании запроса погоды: {e}")
    finally:
        cursor.close()
        conn.close()

def save_favorite_city(user_id, city):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO favorite_cities (user_id, city) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, city))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при сохранении любимого города: {e}")
    finally:
        cursor.close()
        conn.close()

def get_favorite_city(user_id):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT city FROM favorite_cities WHERE user_id = %s LIMIT 1", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка при получении любимого города: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def start_game(user_id, game_name):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO game_progress (user_id, game_name, score, state) VALUES (%s, %s, 0, %s) ON CONFLICT (user_id) DO UPDATE SET game_name = %s, score = 0, state = %s", 
                       (user_id, game_name, '{}', game_name, '{}'))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при старте игры: {e}")
    finally:
        cursor.close()
        conn.close()

def update_game_state(user_id, score, state):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("UPDATE game_progress SET score = %s, state = %s WHERE user_id = %s", (score, json.dumps(state), user_id))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при обновлении состояния игры: {e}")
    finally:
        cursor.close()
        conn.close()

def get_game_state(user_id):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT game_name, score, state FROM game_progress WHERE user_id = %s", (user_id,))
        result = cursor.fetchone()
        return result if result else (None, 0, '{}')
    except Exception as e:
        logger.error(f"Ошибка при получении состояния игры: {e}")
        return (None, 0, '{}')
    finally:
        cursor.close()
        conn.close()

def award_achievement(user_id, achievement):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO user_achievements (user_id, achievement) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user_id, achievement))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при выдаче достижения: {e}")
    finally:
        cursor.close()
        conn.close()

def get_stars(user_id):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("SELECT stars FROM user_stars WHERE user_id = %s", (user_id,))
        result = cursor.fetchone()
        if not result:
            cursor.execute("INSERT INTO user_stars (user_id, stars) VALUES (%s, 0)", (user_id,))
            conn.commit()
            result = (0,)
        return result[0]
    except Exception as e:
        logger.error(f"Ошибка при получении звёзд: {e}")
        return 0
    finally:
        cursor.close()
        conn.close()

def update_stars(user_id, amount):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO user_stars (user_id, stars) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET stars = user_stars.stars + %s", 
                       (user_id, amount, amount))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при обновлении звёзд: {e}")
    finally:
        cursor.close()
        conn.close()

def save_game_result(user_id, game_name, score):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO game_results (user_id, game_name, score) VALUES (%s, %s, %s)", (user_id, game_name, score))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при сохранении результата игры: {e}")
    finally:
        cursor.close()
        conn.close()

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Привет! Я простой бот. Выбери опцию:", reply_markup=MAIN_KEYBOARD)

# Функция для прогноза на 5 дней
def get_forecast(city):
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=ru"
    try:
        response = requests.get(url)
        data = response.json()
        if data["cod"] == "200":
            forecast_text = f"Прогноз на 5 дней в {city}:\n"
            daily_data = {}
            for entry in data["list"]:
                date = entry["dt_txt"].split(" ")[0]
                if date not in daily_data and "12:00:00" in entry["dt_txt"]:
                    temp = entry["main"]["temp"]
                    desc = entry["weather"][0]["description"]
                    daily_data[date] = f"{date}: {temp}°C, {desc}"
            forecast_text += "\n".join(daily_data.values())
            return forecast_text
        else:
            return "Не удалось найти город для прогноза."
    except Exception as e:
        logger.error(f"Ошибка при запросе прогноза: {e}")
        return "Произошла ошибка при получении прогноза."

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    user_id = update.message.from_user.id

    if text == "Меню":
        logger.info(f"Пользователь {user_id} вернулся в главное меню")
        await update.message.reply_text("Вы вернулись в главное меню:", reply_markup=MAIN_KEYBOARD)
        context.user_data.clear()
        return

    if context.user_data.get("awaiting_city"):
        city = text.strip()
        forecast_info = get_forecast(city)
        weather_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐ Сохранить как любимый", callback_data=f"save_city_{city}")]
        ])
        await update.message.reply_text(forecast_info, reply_markup=weather_keyboard)
        log_weather_request(user_id, city)
        context.user_data["awaiting_city"] = False

    elif context.user_data.get("awaiting_game") == "Cities":
        city = text.strip().lower()
        game_name, score, state = get_game_state(user_id)
        if game_name != "Cities":
            await update.message.reply_text("Вы не играете в 'Города' сейчас.", reply_markup=MAIN_KEYBOARD)
            context.user_data["awaiting_game"] = False
            return
        state = json.loads(state) if isinstance(state, str) else state
        last_city = state.get("last_city", "")
        used_cities = state.get("used_cities", [])

        if city not in VALID_CITIES:
            await update.message.reply_text("Неверный город или его нет в списке. Назови другой город или напиши 'Меню' для выхода.")
            return
        elif city in used_cities:
            await update.message.reply_text("Этот город уже был назван! Назови другой город или напиши 'Меню' для выхода.")
            return
        
        # Определяем последнюю играющую букву, исключая 'ь' и 'ъ'
        last_letter = last_city
        while last_letter and last_letter[-1] in 'ьъ':
            last_letter = last_letter[:-1]
        last_letter = last_letter[-1] if last_letter else ''

        if last_city and city[0] != last_letter:
            await update.message.reply_text(f"Город должен начинаться с буквы '{last_letter.upper()}'. Назови другой город или напиши 'Меню' для выхода.")
            return
        else:
            score += 10
            used_cities.append(city)
            # Определяем последнюю играющую букву нового города
            next_letter = city
            while next_letter and next_letter[-1] in 'ьъ':
                next_letter = next_letter[:-1]
            next_letter = next_letter[-1] if next_letter else ''
            
            available_cities = [c for c in VALID_CITIES if c.startswith(next_letter) and c not in used_cities]
            bot_city = random.choice(available_cities) if available_cities else None
            if bot_city:
                used_cities.append(bot_city)
                state["last_city"] = bot_city
                state["used_cities"] = used_cities
                update_game_state(user_id, score, state)
                save_game_result(user_id, "Cities", score)
                if score >= 100:
                    award_achievement(user_id, "Мастер городов")
                    await context.bot.send_sticker(chat_id=update.message.chat_id, sticker="CAACAgIAAxkBAAEKDhJk2Xh-RpP8uN8GLgG8o8nK0oW6rwAC5y0AAp8oyEmQ8eL5oIElbjME")
                    await update.message.reply_text(f"Правильно! Бот: {bot_city.capitalize()}\nДостижение 'Мастер городов' (🏙️) получено! Очки: {score}", reply_markup=MAIN_KEYBOARD)
                    update_stars(user_id, 10)
                    context.user_data["awaiting_game"] = False
                else:
                    await update.message.reply_text(f"Правильно! Бот: {bot_city.capitalize()}\nОчки: {score}. Назови следующий город:")
            else:
                save_game_result(user_id, "Cities", score)
                await update.message.reply_text(f"Правильно, но я не нашёл города на '{next_letter.upper()}'. Ты победил! Очки: {score}", reply_markup=MAIN_KEYBOARD)
                if score >= 100:
                    award_achievement(user_id, "Мастер городов")
                    await context.bot.send_sticker(chat_id=update.message.chat_id, sticker="CAACAgIAAxkBAAEKDhJk2Xh-RpP8uN8GLgG8o8nK0oW6rwAC5y0AAp8oyEmQ8eL5oIElbjME")
                    update_stars(user_id, 10)
                context.user_data["awaiting_game"] = False

    elif context.user_data.get("awaiting_game") == "Guess":
        try:
            guess = int(text.strip())
            game_name, score, state = get_game_state(user_id)
            if game_name != "Guess":
                await update.message.reply_text("Вы не играете в 'Угадай число' сейчас.", reply_markup=MAIN_KEYBOARD)
                context.user_data["awaiting_game"] = False
                return
            state = json.loads(state) if isinstance(state, str) else state
            target = state.get("target")
            attempts = state.get("attempts", 0) + 1

            if guess == target:
                score += 10
                state["attempts"] = attempts
                update_game_state(user_id, score, state)
                save_game_result(user_id, "Guess", score)
                if score >= 100:
                    award_achievement(user_id, "Мастер угадывания")
                    await context.bot.send_sticker(chat_id=update.message.chat_id, sticker="CAACAgIAAxkBAAEKDhJk2Xh-RpP8uN8GLgG8o8nK0oW6rwAC5y0AAp8oyEmQ8eL5oIElbjME")
                    await update.message.reply_text(f"Угадал с {attempts} попытки! Достижение 'Мастер угадывания' (🎲) получено! Очки: {score}", reply_markup=MAIN_KEYBOARD)
                    update_stars(user_id, 10)
                    context.user_data["awaiting_game"] = False
                else:
                    state["target"] = random.randint(1, 100)
                    state["attempts"] = 0
                    update_game_state(user_id, score, state)
                    await update.message.reply_text(f"Угадал с {attempts} попытки! Очки: {score}. Я загадал новое число от 1 до 100. Угадай:")
            elif guess < target:
                state["attempts"] = attempts
                update_game_state(user_id, score, state)
                await update.message.reply_text("Моё число больше. Попробуй ещё:")
            else:
                state["attempts"] = attempts
                update_game_state(user_id, score, state)
                await update.message.reply_text("Моё число меньше. Попробуй ещё:")
        except ValueError:
            await update.message.reply_text("Введи число от 1 до 100! Или напиши 'Меню' для выхода.")

    elif context.user_data.get("awaiting_game") == "Quest":
        game_name, score, state = get_game_state(user_id)
        if game_name != "Quest":
            await update.message.reply_text("Вы не играете в 'Квест' сейчас.", reply_markup=MAIN_KEYBOARD)
            context.user_data["awaiting_game"] = False
            return
        state = json.loads(state) if isinstance(state, str) else state
        stage = state.get("stage", 0)
        if text.lower() in ["вперёд", "да"] and stage < len(QUEST_STAGES):
            score += 10
            state["stage"] = stage + 1
            update_game_state(user_id, score, state)
            save_game_result(user_id, "Quest", score)
            if stage + 1 >= len(QUEST_STAGES):
                if score >= 100:
                    award_achievement(user_id, "Мастер приключений")
                    await context.bot.send_sticker(chat_id=update.message.chat_id, sticker="CAACAgIAAxkBAAEKDhJk2Xh-RpP8uN8GLgG8o8nK0oW6rwAC5y0AAp8oyEmQ8eL5oIElbjME")
                    await update.message.reply_text(f"Ты прошёл квест! Достижение 'Мастер приключений' (🗺️) получено! Очки: {score}", reply_markup=MAIN_KEYBOARD)
                    update_stars(user_id, 10)
                else:
                    await update.message.reply_text(f"Ты прошёл квест! Очки: {score}", reply_markup=MAIN_KEYBOARD)
                context.user_data["awaiting_game"] = False
            else:
                await update.message.reply_text(QUEST_STAGES[stage + 1])
        else:
            save_game_result(user_id, "Quest", score)
            await update.message.reply_text("Неверный выбор, квест провален!", reply_markup=MAIN_KEYBOARD)
            context.user_data["awaiting_game"] = False

    elif context.user_data.get("awaiting_game") == "Logic":
        game_name, score, state = get_game_state(user_id)
        if game_name != "Logic":
            await update.message.reply_text("Вы не играете в 'Логику' сейчас.", reply_markup=MAIN_KEYBOARD)
            context.user_data["awaiting_game"] = False
            return
        state = json.loads(state) if isinstance(state, str) else state
        riddle_idx = state.get("riddle_idx", 0)
        riddle = LOGIC_RIDDLES[riddle_idx]
        if text.strip().lower() == riddle["answer"]:
            score += 10
            riddle_idx = (riddle_idx + 1) % len(LOGIC_RIDDLES)
            state["riddle_idx"] = riddle_idx
            update_game_state(user_id, score, state)
            save_game_result(user_id, "Logic", score)
            if score >= 100:
                award_achievement(user_id, "Мастер логики")
                await context.bot.send_sticker(chat_id=update.message.chat_id, sticker="CAACAgIAAxkBAAEKDhJk2Xh-RpP8uN8GLgG8o8nK0oW6rwAC5y0AAp8oyEmQ8eL5oIElbjME")
                await update.message.reply_text(f"Правильно! Достижение 'Мастер логики' (🧩) получено! Очки: {score}", reply_markup=MAIN_KEYBOARD)
                update_stars(user_id, 10)
                context.user_data["awaiting_game"] = False
            else:
                await update.message.reply_text(f"Правильно! Очки: {score}\n{LOGIC_RIDDLES[riddle_idx]['riddle']}")
        else:
            save_game_result(user_id, "Logic", score)
            await update.message.reply_text(f"Неверно! Правильный ответ: {riddle['answer']}. Игра окончена.", reply_markup=MAIN_KEYBOARD)
            context.user_data["awaiting_game"] = False

# Обработка callback-запросов
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Не удалось ответить на callback-запрос {query.id}: {e}")

    current_text = query.message.text
    current_markup = query.message.reply_markup

    logger.info(f"Callback от {user_id}: {query.data}")

    if query.data == "weather":
        if current_text != "Введите название города для прогноза на 5 дней:":
            await query.edit_message_text("Введите название города для прогноза на 5 дней:")
            await query.message.reply_text("Введи город:")
        context.user_data["awaiting_city"] = True

    elif query.data == "admin":
        if current_text != "Вы направили запрос Администратору.":
            await query.edit_message_text("Вы направили запрос Администратору.", reply_markup=MAIN_KEYBOARD)
            username = query.from_user.username
            message = f'Пользователь <a href="tg://user?id={user_id}">@{username or user_id}</a> запросил связь с админом!'
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=message, parse_mode='HTML')

    elif query.data == "favorite_weather":
        city = get_favorite_city(user_id)
        if city:
            forecast_info = get_forecast(city)
            if current_text != forecast_info or current_markup != MAIN_KEYBOARD:
                await query.edit_message_text(forecast_info, reply_markup=MAIN_KEYBOARD)
        else:
            if current_text != "У вас нет любимого города. Введите город для прогноза:":
                await query.edit_message_text("У вас нет любимого города. Введите город для прогноза:")
                await query.message.reply_text("Введи город:")
            context.user_data["awaiting_city"] = True

    elif query.data.startswith("save_city_"):
        city = query.data.split("_")[2]
        save_favorite_city(user_id, city)
        if current_text != f"Город {city} сохранён как любимый!":
            await query.edit_message_text(f"Город {city} сохранён как любимый!", reply_markup=MAIN_KEYBOARD)

    elif query.data == "play":
        if current_text != "Выбери игру:" or current_markup != GAMES_KEYBOARD:
            await query.edit_message_text("Выбери игру:", reply_markup=GAMES_KEYBOARD)

    elif query.data == "game_cities":
        start_game(user_id, "Cities")
        if current_text != "Игра 'Города' началась! Назови первый город:":
            await query.edit_message_text("Игра 'Города' началась! Назови первый город:")
            await query.message.reply_text("Назови город:")
        context.user_data["awaiting_game"] = "Cities"

    elif query.data == "game_guess":
        start_game(user_id, "Guess")
        target = random.randint(1, 100)
        state = {"target": target, "attempts": 0}
        update_game_state(user_id, 0, state)
        if current_text != "Я загадал число от 1 до 100. Угадай:":
            await query.edit_message_text("Я загадал число от 1 до 100. Угадай:")
            await query.message.reply_text("Введи число:")
        context.user_data["awaiting_game"] = "Guess"

    elif query.data == "game_quest":
        start_game(user_id, "Quest")
        state = {"stage": 0}
        update_game_state(user_id, 0, state)
        if current_text != QUEST_STAGES[0]:
            await query.edit_message_text(QUEST_STAGES[0])
            await query.message.reply_text("Введи ответ:")
 контекст.пользователь_данные["ожидание_игры"] = "Квест"контекст.пользователь_данные["ожидание_игры"] = "Квест"

 элиф запрос.данные == "игра_логика":элиф запрос.данные == "игра_логика":
 start_igrarastart_game(user_id, "Логика")(user_id, "Логика")
 государство = { "загадка_idx": 0}государство = { "загадка_idx": 0}
 obnovlеniе_game_stateupdate_game_state(user_id, 0, костойяни)(user_id, 0, костойяни)
 если теку итерий_текст!= LOGIC_RIDDLES[0]["загадка"]:teku iteriyiy_tecst!= LOGIC_RIDDLES[0]["загадка"]:
 запрос jdaty.edit_message_text(ЛОГИКА_ЗАГАДКИ[0]["загадка])jdaty query.edit_message_text(LOGIC_RIDDLES[0]["загадка])
 запрос jdem.сообщение.ответить_text("Введиответ:")запрос jdem.сообщение.ответить_text("Введиответ:")
 контекст.пользователь_данные["ожидание_игры"] = "Логика"контекст.пользователь_данные["ожидание_игры"] = "Логика"

 элиф запрос.данные == "основой":элиф запрос.данные == "основой":
 если теку итерий_текст!= "Вы вернулись в главное:" ili tekuyiy_markup!= MAIN_KEYBOARD:если теку итерий_текст!= "Вы вернулись в главное мне:" ili tekuyiy_markup != MAIN_KEYBOARD:
 jdem query.edit_message_text("Вы вёрь: главное:", responte_markup=MAIN_KEYBOARD)jdem query.edit_message_text("Вы вёрнус в глявно:", responte_markup=MAIN_KEYBOARD)jdem query.edit_message_text("Вы вёрнус в клявно:", responte_markupMAIN_KEYBOARD)
 контекст.user_data.clear()context.user_data.clear()

#Команда dlia demostracii plelégewsie sistemys
async def pay (obnovlennie: Obnovléniе, kontecst: ContextTypes.DEFAULT_TYPE) -> Нет:
 dodojdatiсya update.message.reply_text(f"Demonstracia platega сo ispollzovaniem PROVIDER_TOKEN: {PROVIDER_TOKEN[:5]}...", responte_markup=MAIN_KEYBOARD)dodojdatiсya update.message.reply_text(f"Demonstracia platega сospollzovaniem PROVIDER_TOKEN:{PROVIDER_TOKEN[:5]}...", responte_markup=MAIN_KEYBOARD)

#Обработка ошибок
oshibka Async def (obnovlеniе: Обновление, конец: ContextTypes.DEFAULT_TYPE) -> Нет:
 logger.error (f'Proizooshla oshibka: {context.error}')logger.error (f'Proizooshla oshibka: {context.error}')
 error_message = "Proizoshlа oshibka, poprobuyte pozgе."error_message = "Proizoshlа oshibka, poprobuyte popzgesh."
 если обновленные не обсудят: если обновленные не обсудят:
 esli update.message: #Для обычных с обениеслы update.message: #Для обычных с обени
 logger.info ("Ошибка в обинах, отправлёт ответе с MAIN_KEYBOARD")logger.info ("Ошибка в собинах, отправлёт ответ ветве с MAIN_KEYBOARD")
 dodojdatiсya update.message.reply_text(error_message, reple_markup=MAIN_KEYBOARD)dodojdatiсya update.message.reply_text(error_message, reple_markup=MAIN_KEYBOARD)
 elif update.callback_query: #Для обратного звонок-запросовелиф update.callback_query: #Для обратного звонок-запросов
 zaproс = update.callback_queryzaproс = update.callback_query
 если query.message.text!= error_message:ecli query.message.text!= error_message:
 logger.info("Ошибка в обратном вызове, reretaktiruеm sobeniе с MAIN_KEYBOARD")logger.info("Ошибка в обратном вызове, reretaktiruеm sobeniе с MAIN_KEYBOARD")
 ogjidaniе query.edit_message_text(error_message, replete_markup=MAIN_KEYBOARD)ogjidaniе query.edit_message_text(error_message, replete_markup=MAIN_KEYBOARD)

async def post_init (приложенье: Приложенье) -> Нет:
 #Настройка кнопки Мен-Для Вызова команд#Настройка кнопки Мен-Для Вызова команд
 prilogeniе Ojidaniе.bot.set_chat_menu_button(menu_button={"тип: "commands"})prilogeniе Ojidaniе.bot.set_chat_menu_button(menu_button={"тип: "commands"})

asyncasync def post_init (приложенье: Приложенье) -> Net:def post_init (приложенье: Приложенье) -> Net:
 #Настройка кнопки Мен-Для Вызова команд#Настройка кнопки Мен-Для Вызова команд
 prilogeniе Ojidaniе.bot.set_chat_menu_button(menu_button={"тип: "команды"})bot.set_chat_menu_button(menu_button={"тип: "команды"})

ménunus async def (obnovlеniе: Obnovlеniе, kontecst: ContextTypes.DEFAULT_TYPE) -> Нет:
 #Обрабочик Команды /menu∞, otobrarajaet glavnoe mugchiny#Обрабочик Команды /menu∞, otobrarajaet glavnoe mugchiny
 dodojdatiсya update.message.reply_text("Вы, отркрылы глевное:", responte_markup=MAIN_KEYBOARD)dodojdatiсya update.message.reply_text("Вы, отркрылы глевное мне:", responte_markup=MAIN_KEYBOARD)

def main() -> Нет:
 init_db()init_db()
 prilogeniе = Application.builder().token(TOKEN).post_init(post_init).build()prilogeniе = Application.builder().token(TOKEN).post_init(post_init).build()
 application.add_handler(CommandHandler("zappuctitiсia", zappuctitiсcya))application.add_handler(CommandHandler("zappoustitliсia", zappucitliсia))
 application.add_handler(CommandHandler("плати", Platti))application.add_handler(CommandHandler("плати", Platti))
 application.add_handler(CommandHandler("menuly∞", mеnush)) #Novая Komandaapplication.add_handler(CommandHandler("menuly∞", menush)) #Новая Команда
 application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
 приложение.add_handler (CallbackQueryHandler (handle_callback))application.add_handler (CallbackQueryHandler (handle_callback))
 prilogeniе.add_error_handler(ошибка)prilogeniе.add_error_handler(ошибка)

 распечтат ("Bott zapu charletеn!".kodirovaty().decode('utf-8'))распечтат ("Bott zapu charletеn!".kodirovaty().decode('utf-8'))
 распочехаты(f"Podklulucheniе k babaze dannyх: {DATABASE_URL[:13]}... (screyt dlia bezogapatsnosti)".encode().decode('utf-8'))raspechechaty(f"Podklulucheniе k babaze dannyх: {DATABASE_URL[:13]}... (screyt dlia bezogapatsnosti)".encode().decode('utf-8'))
 распочехаты(f"Paroly babazy dannyх: {DB_PASSWORD[:0]}... (screyt dlya bezopasnocti)".kod().decode('utf-8'))raspechechaty(f"Paroly babazy dannyх: {DB_PASSWORD[:0]}... (screyt dlya bezopasnocti)".encode().decode('utf-8'))

 application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

esli __name__ == '__main__':
 asyncio.run(glawnyy ()) #Запуск асинхроний функциц и черёз асинсио.runasyncio.run (glawnyy ()) #Запуск асинхроний функциц и черёз асинсио.run (glawnyyy ()) #Запуск асинсьо.run
