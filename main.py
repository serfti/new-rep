import re
import asyncio
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

TOKEN = "8651001854:AAHB3JzrryC23XywhRhMGwBEZnA4Qz2CEr0"

bot = Bot(token=TOKEN)
dp = Dispatcher()

user_state = {}
user_temp = {}

BASE_CATEGORIES = ["дом", "семья", "машина", "еда", "развлечения", "здоровье"]

CURRENCY_MAP = {
    "usd": "USD", "$": "USD", "dollar": "USD", "dollars": "USD",
    "бакс": "USD", "баксы": "USD", "доллар": "USD", "доллары": "USD",
    "eur": "EUR", "€": "EUR", "euro": "EUR", "euros": "EUR",
    "евро": "EUR", "еврики": "EUR",
    "uah": "UAH", "₴": "UAH", "грн": "UAH",
    "гривна": "UAH", "гривны": "UAH", "grn": "UAH"
}

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Трата"), KeyboardButton(text="💰 Доход")],
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📅 Сегодня"), KeyboardButton(text="📅 Неделя")],
        [KeyboardButton(text="📅 Месяц")],
        [KeyboardButton(text="🧾 Последние операции")],
    ],
    resize_keyboard=True
)

back_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Назад")]],
    resize_keyboard=True
)


def expense_kb_static(categories):
    buttons = [[KeyboardButton(text=c)] for c in categories]
    buttons.append([KeyboardButton(text="➕ Добавить категорию")])
    buttons.append([KeyboardButton(text="🔙 Назад")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def parse_amount(text):
    match = re.search(r"(\d+(\.\d+)?)", text)
    return float(match.group(1)) if match else None


def parse_currency(text):
    text = text.lower()
    for key, value in CURRENCY_MAP.items():
        if key in text:
            return value
    return "USD"


async def init_db():
    async with aiosqlite.connect("finance.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                currency TEXT,
                category TEXT,
                type TEXT,
                final_amount REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT
            )
        """)
        await db.commit()


async def get_categories(user_id):
    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute(
            "SELECT name FROM categories WHERE user_id = ?", (user_id,)
        )
        rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def seed_categories(user_id):
    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM categories WHERE user_id = ?", (user_id,)
        )
        count = (await cursor.fetchone())[0]
        if count == 0:
            for cat in BASE_CATEGORIES:
                await db.execute(
                    "INSERT INTO categories (user_id, name) VALUES (?, ?)",
                    (user_id, cat)
                )
            await db.commit()


async def get_last_operations_text(user_id):
    text = "🧾 Последние 15 операций:\n\n"
    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute("""
            SELECT id, category, amount, currency, type
            FROM transactions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 15
        """, (user_id,))
        rows = await cursor.fetchall()

    if not rows:
        return "Пока нет операций"

    for row in rows:
        op_id, category, amount, currency, op_type = row
        op_type_text = "Доход" if op_type == "income" else "Расход"
        text += f"ID {op_id} | {category} | {amount} {currency} | {op_type_text}\n"

    text += "\n✏️ Редактировать: /edit ID новая_сумма"
    text += "\n❌ Удалить: /delete ID"
    return text


async def get_stats_text(user_id):
    currencies = ["USD", "EUR", "UAH"]
    income_data = {cur: 0 for cur in currencies}
    expense_data = {cur: 0 for cur in currencies}
    category_data = {}

    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute("""
            SELECT currency, SUM(amount) FROM transactions
            WHERE type = 'income' AND user_id = ? GROUP BY currency
        """, (user_id,))
        for currency, total in await cursor.fetchall():
            income_data[currency] = round(total or 0, 2)

        cursor = await db.execute("""
            SELECT currency, SUM(amount) FROM transactions
            WHERE type = 'expense' AND user_id = ? GROUP BY currency
        """, (user_id,))
        for currency, total in await cursor.fetchall():
            expense_data[currency] = round(total or 0, 2)

        cursor = await db.execute("""
            SELECT category, SUM(amount) FROM transactions
            WHERE type = 'expense' AND user_id = ?
            GROUP BY category ORDER BY SUM(amount) DESC
        """, (user_id,))
        for category, total in await cursor.fetchall():
            category_data[category] = round(total or 0, 2)

    text = "📊 Финансовая статистика\n\n💰 Доход:\n\n"
    for cur in currencies:
        text += f"{cur}: {income_data[cur]}\n"
    text += "\n💸 Расход:\n\n"
    for cur in currencies:
        text += f"{cur}: {expense_data[cur]}\n"
    text += "\n🏦 Баланс:\n\n"
    for cur in currencies:
        text += f"{cur}: {round(income_data[cur] - expense_data[cur], 2)}\n"
    text += "\n📂 По категориям:\n\n"
    if category_data:
        for category, total in category_data.items():
            text += f"{category}: {total}\n"
    else:
        text += "Пока нет расходов\n"
    return text


async def get_period_stats_text(user_id, period="today"):
    currencies = ["USD", "EUR", "UAH"]
    income_data = {cur: 0 for cur in currencies}
    expense_data = {cur: 0 for cur in currencies}
    category_data = {}

    if period == "today":
        date_filter = "DATE(created_at) = DATE('now', 'localtime')"
        title = "📅 Статистика за сегодня"
    elif period == "week":
        date_filter = "DATE(created_at) >= DATE('now', '-7 day', 'localtime')"
        title = "📅 Статистика за неделю"
    elif period == "month":
        date_filter = "DATE(created_at) >= DATE('now', '-30 day', 'localtime')"
        title = "📅 Статистика за месяц"
    else:
        date_filter = "1=1"
        title = "📅 Статистика"

    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute(f"""
            SELECT currency, SUM(amount) FROM transactions
            WHERE type = 'income' AND user_id = ? AND {date_filter}
            GROUP BY currency
        """, (user_id,))
        for currency, total in await cursor.fetchall():
            income_data[currency] = round(total or 0, 2)

        cursor = await db.execute(f"""
            SELECT currency, SUM(amount) FROM transactions
            WHERE type = 'expense' AND user_id = ? AND {date_filter}
            GROUP BY currency
        """, (user_id,))
        for currency, total in await cursor.fetchall():
            expense_data[currency] = round(total or 0, 2)

        cursor = await db.execute(f"""
            SELECT category, SUM(amount) FROM transactions
            WHERE type = 'expense' AND user_id = ? AND {date_filter}
            GROUP BY category ORDER BY SUM(amount) DESC
        """, (user_id,))
        for category, total in await cursor.fetchall():
            category_data[category] = round(total or 0, 2)

    text = f"{title}\n\n💰 Доход:\n\n"
    for cur in currencies:
        text += f"{cur}: {income_data[cur]}\n"
    text += "\n💸 Расход:\n\n"
    for cur in currencies:
        text += f"{cur}: {expense_data[cur]}\n"
    text += "\n🏦 Баланс:\n\n"
    for cur in currencies:
        text += f"{cur}: {round(income_data[cur] - expense_data[cur], 2)}\n"
    text += "\n📂 По категориям:\n\n"
    if category_data:
        for category, total in category_data.items():
            text += f"{category}: {total}\n"
    else:
        text += "Нет операций за этот период\n"
    return text


@dp.message(Command("start"))
async def start(message: types.Message):
    await seed_categories(message.from_user.id)
    await message.answer("Выбери действие:", reply_markup=main_kb)


@dp.message(Command("delete"))
async def delete_transaction(message: types.Message):
    uid = message.from_user.id
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /delete ID")
        return

    op_id = int(parts[1])
    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute(
            "SELECT id FROM transactions WHERE id = ? AND user_id = ?",
            (op_id, uid)
        )
        row = await cursor.fetchone()
        if not row:
            await message.answer("Операция не найдена.")
            return
        await db.execute("DELETE FROM transactions WHERE id = ?", (op_id,))
        await db.commit()

    await message.answer(f"✅ Операция ID {op_id} удалена.", reply_markup=main_kb)


@dp.message(Command("edit"))
async def edit_transaction(message: types.Message):
    uid = message.from_user.id
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer("Использование: /edit ID новая_сумма\nПример: /edit 5 250$")
        return

    op_id = int(parts[1])
    new_amount = parse_amount(parts[2])
    new_currency = parse_currency(parts[2])

    if not new_amount:
        await message.answer("Неверный формат суммы. Пример: /edit 5 250$")
        return

    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute(
            "SELECT id FROM transactions WHERE id = ? AND user_id = ?",
            (op_id, uid)
        )
        row = await cursor.fetchone()
        if not row:
            await message.answer("Операция не найдена.")
            return
        await db.execute(
            "UPDATE transactions SET amount = ?, currency = ?, final_amount = ? WHERE id = ?",
            (new_amount, new_currency, new_amount, op_id)
        )
        await db.commit()

    await message.answer(
        f"✅ Операция ID {op_id} обновлена: {new_amount} {new_currency}",
        reply_markup=main_kb
    )


@dp.message()
async def handler(message: types.Message):
    uid = message.from_user.id
    text = message.text

    if text == "🔙 Назад":
        user_state.pop(uid, None)
        user_temp.pop(uid, None)
        await message.answer("Главное меню", reply_markup=main_kb)
        return

    if text == "📊 Статистика":
        await message.answer(await get_stats_text(uid), reply_markup=main_kb)
        return

    if text == "📅 Сегодня":
        await message.answer(await get_period_stats_text(uid, "today"), reply_markup=main_kb)
        return

    if text == "📅 Неделя":
        await message.answer(await get_period_stats_text(uid, "week"), reply_markup=main_kb)
        return

    if text == "📅 Месяц":
        await message.answer(await get_period_stats_text(uid, "month"), reply_markup=main_kb)
        return

    if text == "🧾 Последние операции":
        await message.answer(await get_last_operations_text(uid), reply_markup=main_kb)
        return

    if text == "➕ Трата":
        user_state[uid] = "expense_choose"
        cats = await get_categories(uid)
        await message.answer("Выбери категорию:", reply_markup=expense_kb_static(cats))
        return

    if text == "💰 Доход":
        user_state[uid] = "income_wait"
        await message.answer("Отправь сумму дохода:", reply_markup=back_kb)
        return

    if text == "➕ Добавить категорию":
        user_state[uid] = "add_category"
        await message.answer("Напиши новую категорию:", reply_markup=back_kb)
        return

    if user_state.get(uid) == "income_wait":
        amount = parse_amount(text)
        currency = parse_currency(text)
        if not amount:
            await message.answer("Введи сумму типа: 120$ / 100€ / 3000₴")
            return
        async with aiosqlite.connect("finance.db") as db:
            await db.execute(
                "INSERT INTO transactions (user_id, amount, currency, category, type, final_amount) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, amount, currency, "доход", "income", amount)
            )
            await db.commit()
        await message.answer(f"✅ Доход: {amount} {currency}", reply_markup=main_kb)
        user_state.pop(uid, None)
        return

    if user_state.get(uid) == "expense_choose":
        cats = await get_categories(uid)
        if text in cats:
            user_temp[uid] = {"category": text}
            user_state[uid] = "expense_amount"
            await message.answer("Теперь введи сумму:", reply_markup=back_kb)
            return

    if user_state.get(uid) == "expense_amount":
        amount = parse_amount(text)
        currency = parse_currency(text)
        if not amount:
            await message.answer("Введи сумму типа: 120$ / 3000₴ / 50€")
            return
        category = user_temp.get(uid, {}).get("category", "прочее")
        async with aiosqlite.connect("finance.db") as db:
            await db.execute(
                "INSERT INTO transactions (user_id, amount, currency, category, type, final_amount) VALUES (?, ?, ?, ?, ?, ?)",
                (uid, amount, currency, category, "expense", amount)
            )
            await db.commit()
        await message.answer(f"✅ Расход: {amount} {currency} ({category})", reply_markup=main_kb)
        user_state.pop(uid, None)
        user_temp.pop(uid, None)
        return

    if user_state.get(uid) == "add_category":
        async with aiosqlite.connect("finance.db") as db:
            await db.execute(
                "INSERT INTO categories (user_id, name) VALUES (?, ?)", (uid, text)
            )
            await db.commit()
        user_state.pop(uid, None)
        await message.answer(f"✅ Категория добавлена: {text}", reply_markup=main_kb)
        return


async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
