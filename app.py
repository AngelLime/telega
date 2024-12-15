import os
import shutil
import zipfile
import sqlite3
import asyncio
import aiohttp
import re
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Конфигурация
API_TOKEN = "7797780063:AAEpngh3P19Pl51sP3LkVuNFEAW3Gok1zHg"  # Укажите ваш токен
DATABASE = "mods_signature.db"
ADMIN_IDS = [6471833607, 1819089399]  # Укажите ID администраторов
CURSEFORGE_API_KEY = "$2a$10$J9E/.7dds4AWc4HMYzsmZO3dIgpXn15qajYz9EHqL.l.VqOxqwXOO"  # Получите API ключ CurseForge

# Инициализация бота, диспетчера и FSM
bot = Bot(token=API_TOKEN)
router = Router()
dp = Dispatcher(storage=MemoryStorage())


# Состояния FSM
class ModStates(StatesGroup):
    waiting_for_clean_mods = State()
    waiting_for_dirty_mods = State()
    waiting_for_check_mods = State()
    waiting_for_modrinth_query = State()
    waiting_for_modrinth_save = State()


# Подключение к базе данных
def init_database():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('clean', 'dirty'))
        )
    """)
    conn.commit()
    conn.close()


# Извлечение содержимого из .jar/.zip файла

def extract_class_contents(jar_path):
    extracted_content = set()
    with zipfile.ZipFile(jar_path, 'r') as zip_ref:
        for file_name in zip_ref.namelist():
            if file_name.endswith(".class"):
                with zip_ref.open(file_name) as class_file:
                    try:
                        # Читаем бинарное содержимое файла
                        content = class_file.read()
                        # Извлекаем только текстовые строки
                        strings = re.findall(rb'[\x20-\x7E]{4,}', content)
                        extracted_content.update(s.decode('utf-8') for s in strings)
                    except Exception as e:
                        print(f"Ошибка при чтении {file_name}: {e}")
    return extracted_content


# Сохранение данных в базу
def add_to_database(content, mod_type):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS mods (id INTEGER PRIMARY KEY, content TEXT, type TEXT)")

    chunk_size = 30
    content_chunks = [list(content)[i:i + chunk_size] for i in range(0, len(content), chunk_size)]

    for chunk in content_chunks:
        cursor.execute("INSERT INTO mods (content, type) VALUES (?, ?)", ("\n".join(chunk), mod_type))

    conn.commit()
    conn.close()


# Очистка грязных модов от чистых строк

def clean_dirty_mods(clean_content):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, content FROM mods WHERE type = 'dirty'")
    dirty_mods = cursor.fetchall()

    for mod_id, dirty_content in dirty_mods:
        dirty_lines = set(dirty_content.split("\n"))
        cleaned_lines = dirty_lines - clean_content
        cursor.execute("UPDATE mods SET content = ? WHERE id = ?", ("\n".join(cleaned_lines), mod_id))

    conn.commit()
    conn.close()


# Проверка мода

def check_mod(content):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM mods WHERE type = 'dirty'")
    dirty_mods = cursor.fetchall()
    conn.close()

    for dirty_content in dirty_mods:
        dirty_lines = set(dirty_content[0].split("\n"))
        if content & dirty_lines:
            return True
    return False


# Клавиатура для обычных пользователей
def get_user_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Проверить моды", callback_data="check_mods")]
    ])
    return keyboard


def get_admin_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Проверить моды", callback_data="check_mods")],
        [InlineKeyboardButton(text="➕ Добавить чистые моды", callback_data="add_clean")],
        [InlineKeyboardButton(text="➕ Добавить грязные моды", callback_data="add_dirty")],
        [InlineKeyboardButton(text="🔎 Найти на CurseForge", callback_data="search_curseforge")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
        [InlineKeyboardButton(text="   Выход", callback_data="exit")]
    ])
    return keyboard


# Проверка прав администратора
def is_admin(user_id):
    return user_id in ADMIN_IDS


# Обработка команды /start
@router.message(F.text == "/start")
async def start_command(message: types.Message):
    if is_admin(message.from_user.id):
        keyboard = get_admin_keyboard()
        await message.reply("Добро пожаловать, администратор! Выберите действие:", reply_markup=keyboard)
    else:
        keyboard = get_user_keyboard()
        await message.reply("Добро пожаловать! Выберите действие:", reply_markup=keyboard)


# Обработка нажатий на кнопки
@router.callback_query()
async def handle_callback(query: types.CallbackQuery, state: FSMContext):
    data = query.data

    # Обработка проверки модов
    if data == "check_mods":
        await query.message.answer("Отправьте архив с модами (.zip), чтобы проверить его.")
        await state.set_state(ModStates.waiting_for_check_mods)

    # Обработка добавления чистых модов
    elif data == "add_clean" and is_admin(query.from_user.id):
        await query.message.answer("Вы вошли в режим добавления чистых модов. Отправьте архив с модами (.zip).")
        await state.set_state(ModStates.waiting_for_clean_mods)

    # Обработка добавления грязных модов
    elif data == "add_dirty" and is_admin(query.from_user.id):
        await query.message.answer("Вы вошли в режим добавления грязных модов. Отправьте архив с модами (.zip).")
        await state.set_state(ModStates.waiting_for_dirty_mods)

    elif data == "search_curseforge" and is_admin(query.from_user.id):
        await query.message.answer("Вы вошли в режим добавления грязных модов. Отправьте архив с модами (.zip).")
        await state.set_state(ModStates.waiting_for_modrinth_query)

    # Отмена текущего действия
    elif data == "cancel":
        await state.clear()
        await query.message.answer("Действие отменено.", reply_markup=None)

    else:
        await query.message.answer("У вас нет прав для выполнения этого действия.")


# Обработка получения файла для проверки
@router.message(ModStates.waiting_for_check_mods, F.document & F.document.file_name.endswith(".zip"))
async def process_check_mods(message: types.Message, state: FSMContext):
    document = message.document

    max_size = 20 * 1024 * 1024
    if document.file_size > max_size:
        await message.reply("⛔ Файл слишком большой. Попробуйте загрузить файл меньшего размера.")
        return

    file_path = f"./{document.file_name}"
    file = await bot.get_file(document.file_id)
    await bot.download_file(file.file_path, destination=file_path)

    extract_path = "./temp_check"
    os.makedirs(extract_path, exist_ok=True)

    results = []
    try:
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
            file_list = zip_ref.namelist()
            for jar_file in file_list:
                if not jar_file.endswith(".jar"):
                    continue
                jar_path = os.path.join(extract_path, jar_file)
                jar_content = extract_class_contents(jar_path)
                if check_mod(jar_content):
                    results.append(f"❌ {jar_file} содержит признаки чита.")
                else:
                    results.append(f"✅ {jar_file} не содержит подозрительных данных.")
    except zipfile.BadZipFile:
        results.append("⛔ Архив повреждён или имеет неправильный формат.")
    finally:
        os.remove(file_path)
        shutil.rmtree(extract_path, ignore_errors=True)

    response = "\n".join(results) if results else "⛔ В архиве не найдено подходящих файлов."
    await message.reply(response)

    await state.clear()


@router.message(ModStates.waiting_for_clean_mods, F.document & F.document.file_name.endswith(".zip"))
async def process_clean_mods(message: types.Message, state: FSMContext):
    document = message.document

    file_path = f"./{document.file_name}"
    file = await bot.get_file(document.file_id)
    await bot.download_file(file.file_path, destination=file_path)

    extract_path = "./temp_clean"
    os.makedirs(extract_path, exist_ok=True)

    clean_content = set()
    try:
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
            for file_name in zip_ref.namelist():
                if file_name.endswith(".jar"):
                    jar_path = os.path.join(extract_path, file_name)
                    clean_content.update(extract_class_contents(jar_path))

        add_to_database(clean_content, "clean")
        clean_dirty_mods(clean_content)

        await message.reply(f"✅ Все моды из {document.file_name} добавлены как чистые. Грязные моды обновлены.")
    except zipfile.BadZipFile:
        await message.reply("⛔ Архив повреждён или имеет неправильный формат.")
    finally:
        os.remove(file_path)
        shutil.rmtree(extract_path, ignore_errors=True)

    await state.clear()


@router.message(ModStates.waiting_for_dirty_mods, F.document & F.document.file_name.endswith(".zip"))
async def process_dirty_mods(message: types.Message, state: FSMContext):
    document = message.document

    file_path = f"./{document.file_name}"
    file = await bot.get_file(document.file_id)
    await bot.download_file(file.file_path, destination=file_path)

    extract_path = "./temp_dirty"
    os.makedirs(extract_path, exist_ok=True)

    dirty_content = set()
    try:
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)
            for file_name in zip_ref.namelist():
                if file_name.endswith(".jar"):
                    jar_path = os.path.join(extract_path, file_name)
                    dirty_content.update(extract_class_contents(jar_path))

        add_to_database(dirty_content, "dirty")

        clean_content = set()
        conn = sqlite3.connect(DATABASE)
        cursor = conn.cursor()
        cursor.execute("SELECT content FROM mods WHERE type = 'clean'")
        for row in cursor.fetchall():
            clean_content.update(row[0].split("\n"))
        conn.close()

        clean_dirty_mods(clean_content)

        await message.reply(f"❌ Все моды из {document.file_name} добавлены как грязные. Чистый код удалён.")
    except zipfile.BadZipFile:
        await message.reply("⛔ Архив повреждён или имеет неправильный формат.")
    finally:
        os.remove(file_path)
        shutil.rmtree(extract_path, ignore_errors=True)

    await state.clear()


async def handle_callback(query: types.CallbackQuery, state: FSMContext):
    data = query.data

    # Обработка выхода
    if data == "exit":
        await state.clear()  # Сброс состояния
        await query.message.answer("🚪 Вы вышли из бота. Чтобы вернуться, введите /start.", reply_markup=None)
        return


def resolve_conflicts(content: set, mod_type: str):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    if mod_type == "dirty":
        # Удаляем пересечения с чистым кодом
        cursor.execute("SELECT id, content FROM mods WHERE type = 'clean'")
        clean_rows = cursor.fetchall()
        for clean_id, clean_content in clean_rows:
            clean_set = set(clean_content.split("\n"))
            overlap = content.intersection(clean_set)
            if overlap:
                updated_clean = clean_set - overlap
                if updated_clean:
                    cursor.execute(
                        "UPDATE mods SET content = ? WHERE id = ?",
                        ("\n".join(updated_clean), clean_id)
                    )
                else:
                    cursor.execute("DELETE FROM mods WHERE id = ?", (clean_id,))
    elif mod_type == "clean":
        # Удаляем пересечения с грязным кодом
        cursor.execute("SELECT id, content FROM mods WHERE type = 'dirty'")
        dirty_rows = cursor.fetchall()
        for dirty_id, dirty_content in dirty_rows:
            dirty_set = set(dirty_content.split("\n"))
            overlap = content.intersection(dirty_set)
            if overlap:
                updated_dirty = dirty_set - overlap
                if updated_dirty:
                    cursor.execute(
                        "UPDATE mods SET content = ? WHERE id = ?",
                        ("\n".join(updated_dirty), dirty_id)
                    )
                else:
                    cursor.execute("DELETE FROM mods WHERE id = ?", (dirty_id,))

    conn.commit()
    conn.close()


MODRINTH_URL_REGEX = r"https?://(?:www\.)?modrinth\.com/mod/([^/]+)"


async def extract_mod_id_from_slug(slug: str):
    """Получение ID мода по slug через точный запрос к Modrinth API."""
    url = f"https://api.modrinth.com/v2/project/{slug}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("id")
            else:
                print(f"⛔️ API Error: {response.status} {await response.text()}")
    return None


@router.message(ModStates.waiting_for_modrinth_query)
async def process_modrinth_query(message: types.Message, state: FSMContext):
    query = message.text

    # Если это ссылка, извлечь slug
    if query.startswith("http://") or query.startswith("https://"):
        match = re.search(MODRINTH_URL_REGEX, query)
        if match:
            slug = match.group(1)
            mod_id = await extract_mod_id_from_slug(slug)
            if mod_id:
                await message.reply(
                    f"🔍 Найден ID мода: `{mod_id}`. Укажите его тип (clean/dirty), например: `{mod_id} clean`"
                )
                await state.set_state(ModStates.waiting_for_modrinth_save)
                return
            else:
                await message.reply("⛔ Не удалось найти мод по указанной ссылке.")
                return
        else:
            await message.reply("⛔ Ссылка недействительна или не содержит мод.")
            return

    # Если это название, ищем моды
    results = await search_modrinth(query)

    if results:
        response = "Результаты поиска модов на Modrinth:\n\n"
        for mod in results[:5]:  # Ограничиваемся 5 результатами
            response += f"\u2022 [{mod['title']}]({mod['url']}) (ID: {mod['id']})\n"
        response += "\nОтправьте ID мода и укажите его тип (clean/dirty), например: `123456 clean`"
        await message.reply(response, disable_web_page_preview=True)
        await state.set_state(ModStates.waiting_for_modrinth_save)
    else:
        await message.reply("Моды не найдены.")
        await state.clear()


async def search_modrinth(query: str):
    """Поиск модов на Modrinth по названию."""
    url = f"https://api.modrinth.com/v2/search?query={query}&limit=5"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                return [
                    {
                        "title": mod["title"],
                        "id": mod["project_id"],
                        "url": f"https://modrinth.com/mod/{mod['slug']}"
                    }
                    for mod in data.get("hits", [])
                ]
            else:
                print(f"⛔️ API Error: {response.status} {await response.text()}")
    return []


def clear_directory(directory: str):
    if os.path.exists(directory):
        for file in os.listdir(directory):
            file_path = os.path.join(directory, file)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except Exception as e:
                print(f"Не удалось удалить файл {file_path}: {e}")


@router.message(ModStates.waiting_for_modrinth_save)
async def process_modrinth_save(message: types.Message, state: FSMContext):
    try:
        # Парсим ввод пользователя
        mod_id, mod_type = message.text.split()
        mod_id = mod_id.strip()
        if mod_type not in {"clean", "dirty"}:
            raise ValueError("Invalid mod type")

        # Получение данных о моде
        mod_url = f"https://api.modrinth.com/v2/project/{mod_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(mod_url) as mod_response:
                if mod_response.status == 200:
                    mod_data = await mod_response.json()
                    mod_name = mod_data.get("title")

                    # Получение списка файлов мода
                    files_url = f"https://api.modrinth.com/v2/project/{mod_id}/version"
                    async with session.get(files_url) as files_response:
                        if files_response.status == 200:
                            files_data = await files_response.json()
                            mod_content = set()

                            # Подготовка прогресса
                            total_files = sum(
                                1 for version in files_data
                                for file in version.get("files", [])
                                if file.get("filename", "").endswith(".jar")
                            )
                            downloaded_files = 0

                            # Инициализация сообщения с прогрессом
                            progress_message = await message.reply(f"⏳ Загрузка: 0 из {total_files} файлов...")

                            for version in files_data:
                                # Проверяем файлы в версии
                                for file in version.get("files", []):
                                    filename = file.get("filename", "")
                                    download_url = file.get("url")

                                    if filename.endswith(".jar") and download_url:
                                        # Подготовка пути
                                        os.makedirs("./mods", exist_ok=True)
                                        file_path = f"./mods/{filename}"

                                        # Скачиваем файл
                                        async with session.get(download_url) as file_response:
                                            if file_response.status == 200:
                                                with open(file_path, "wb") as mod_file:
                                                    mod_file.write(await file_response.read())

                                                # Извлечение содержимого jar-файла
                                                mod_content.update(extract_class_contents(file_path))

                                                # Обновление прогресса
                                                downloaded_files += 1
                                                await progress_message.edit_text(
                                                    f"⏳ Загрузка: {downloaded_files} из {total_files} файлов..."
                                                )

                            # Разрешаем конфликты и добавляем мод в БД
                            resolve_conflicts(mod_content, mod_type)
                            add_to_database(mod_content, mod_type)

                            await progress_message.edit_text(
                                f"✅ Все доступные версии мода '{mod_name}' добавлены как {mod_type}.")
                        else:
                            await message.reply("⛔ Не удалось получить список файлов мода.")
                else:
                    await message.reply("⛔ Не удалось получить данные о моде. Проверьте ID.")
    except Exception as e:
        await message.reply(f"⛔ Ошибка: {e}")
    finally:
        clear_directory("./mods")
        await state.clear()


# Основная функция
async def main():
    # Инициализация базы данных
    init_database()
    print("Бот запущен!")
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
