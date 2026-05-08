import re
import asyncio
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

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
    "гривна": "UAH", "гривны": "UAH", "grn": "UAH",
}

CURRENCY_EMOJI = {"USD": "💵", "EUR": "💶", "UAH": "💴"}

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📅 Сегодня"), KeyboardButton(text="📅 Неделя")],
        [KeyboardButton(text="📅 Месяц")],
        [KeyboardButton(text="🧾 Последние операции")],
        [KeyboardButton(text="📋 Категории")],
    ],
    resize_keyboard=True
)

back_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🔙 Назад")]],
    resize_keyboard=True
)


# =========================
# PARSERS
# =========================

def parse_amount(text):
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def parse_currency(text):
    text_lower = text.lower()
    for key, value in CURRENCY_MAP.items():
        if key in text_lower:
            return value
    return "USD"


def strip_currency_and_amount(text):
    result = re.sub(r"\d+(?:\.\d+)?", "", text, count=1)
    tokens = [
        r"\$", r"€", r"₴",
        r"\busd\b", r"\beur\b", r"\buah\b", r"\bgrn\b",
        r"\bdollar[s]?\b", r"\beuro[s]?\b",
        r"\bбаксы?\b", r"\bдоллары?\b", r"\bевро\b", r"\bеврики\b",
        r"\bгривны?\b", r"\bгрн\b",
    ]
    for token in tokens:
        result = re.sub(token, "", result, flags=re.IGNORECASE)
    return result.strip().lower()


def parse_percent_income(text):
    """'5% с 5000$', '10% от 3000€', '5 процентов с 5000 usd'"""
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:процентов|процента|процент|%)\s+(?:с|от|of|from)\s+(\d+(?:\.\d+)?)",
        text, re.IGNORECASE,
    )
    if not match:
        return None
    percent = float(match.group(1))
    base = float(match.group(2))
    currency = parse_currency(text)
    final = round(base * percent / 100, 2)
    return {"percent": percent, "base": base, "amount": final, "currency": currency}


def parse_quick_expense(text, categories):
    """'1500$ здоровье' → {found, amount, currency, category}"""
    if not re.search(r"\d", text):
        return None
    amount = parse_amount(text)
    currency = parse_currency(text)
    category = strip_currency_and_amount(text)
    if not category:
        return None
    return {
        "found": category in categories,
        "amount": amount,
        "currency": currency,
        "category": category,
    }


def parse_quick_income(text):
    """'5000$' — amount + currency only, nothing else."""
    if not re.search(r"\d", text):
        return None
    leftover = strip_currency_and_amount(text)
    if leftover:
        return None
    amount = parse_amount(text)
    currency = parse_currency(text)
    return {"amount": amount, "currency": currency}


# =========================
# DATABASE
# =========================

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
                    "INSERT INTO categories (user_id, name) VALUES (?, ?)", (user_id, cat)
                )
            await db.commit()


async def add_transaction(user_id, amount, currency, category, tx_type):
    async with aiosqlite.connect("finance.db") as db:
        await db.execute(
            "INSERT INTO transactions (user_id, amount, currency, category, type, final_amount) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, amount, currency, category, tx_type, amount),
        )
        await db.commit()


# =========================
# STATISTICS
# =========================

async def get_stats_text(user_id, date_filter="1=1", title="📊 Финансовая статистика"):
    currencies = ["USD", "EUR", "UAH"]
    income_data = {cur: 0.0 for cur in currencies}
    expense_data = {cur: 0.0 for cur in currencies}
    category_data: dict = {}

    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute(f"""
            SELECT currency, SUM(amount) FROM transactions
            WHERE type = 'income' AND user_id = ? AND {date_filter}
            GROUP BY currency
        """, (user_id,))
        for currency, total in await cursor.fetchall():
            if currency in income_data:
                income_data[currency] = round(total or 0, 2)

        cursor = await db.execute(f"""
            SELECT currency, SUM(amount) FROM transactions
            WHERE type = 'expense' AND user_id = ? AND {date_filter}
            GROUP BY currency
        """, (user_id,))
        for currency, total in await cursor.fetchall():
            if currency in expense_data:
                expense_data[currency] = round(total or 0, 2)

        cursor = await db.execute(f"""
            SELECT currency, category, SUM(amount) FROM transactions
            WHERE type = 'expense' AND user_id = ? AND {date_filter}
            GROUP BY currency, category
            ORDER BY currency, SUM(amount) DESC
        """, (user_id,))
        for currency, category, total in await cursor.fetchall():
            if currency not in category_data:
                category_data[currency] = {}
            category_data[currency][category] = round(total or 0, 2)

    text = f"{title}\n\n"

    text += "💰 Доход:\n"
    any_income = any(v > 0 for v in income_data.values())
    for cur in currencies:
        if income_data[cur] > 0:
            text += f"  {CURRENCY_EMOJI.get(cur, '')} {cur}: {income_data[cur]}\n"
    if not any_income:
        text += "  Нет доходов\n"

    text += "\n💸 Расход:\n"
    any_expense = any(v > 0 for v in expense_data.values())
    for cur in currencies:
        if expense_data[cur] > 0:
            text += f"  {CURRENCY_EMOJI.get(cur, '')} {cur}: {expense_data[cur]}\n"
    if not any_expense:
        text += "  Нет расходов\n"

    text += "\n🏦 Баланс:\n"
    any_ops = any(income_data[c] > 0 or expense_data[c] > 0 for c in currencies)
    for cur in currencies:
        if income_data[cur] > 0 or expense_data[cur] > 0:
            balance = round(income_data[cur] - expense_data[cur], 2)
            text += f"  {CURRENCY_EMOJI.get(cur, '')} {cur}: {balance}\n"
    if not any_ops:
        text += "  Нет операций\n"

    text += "\n📂 Расходы по категориям:\n"
    if category_data:
        for cur in currencies:
            if cur in category_data:
                text += f"\n  {CURRENCY_EMOJI.get(cur, '')} {cur}:\n"
                for cat, total in category_data[cur].items():
                    text += f"    {cat}: {total}\n"
    else:
        text += "  Нет расходов\n"

    return text


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

    for op_id, category, amount, currency, op_type in rows:
        label = "Доход" if op_type == "income" else "Расход"
        text += f"ID {op_id} | {category} | {amount} {currency} | {label}\n"

    text += "\n✏️ Редактировать: /edit ID новая_сумма"
    text += "\n❌ Удалить: /delete ID"
    return text


# =========================
# CALLBACKS
# =========================

@dp.callback_query(lambda c: c.data.startswith("add_cat:"))
async def cb_add_category(callback: CallbackQuery):
    uid = callback.from_user.id
    _, amount_str, currency, category = callback.data.split(":", 3)
    amount = float(amount_str)

    async with aiosqlite.connect("finance.db") as db:
        await db.execute(
            "INSERT INTO categories (user_id, name) VALUES (?, ?)", (uid, category)
        )
        await db.execute(
            "INSERT INTO transactions (user_id, amount, currency, category, type, final_amount) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, amount, currency, category, "expense", amount),
        )
        await db.commit()

    await callback.message.edit_text(
        f"✅ Категория «{category}» добавлена.\n"
        f"✅ Расход: {amount} {currency} ({category})"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("use_misc:"))
async def cb_use_misc(callback: CallbackQuery):
    uid = callback.from_user.id
    _, amount_str, currency = callback.data.split(":", 2)
    amount = float(amount_str)

    await add_transaction(uid, amount, currency, "прочее", "expense")
    await callback.message.edit_text(f"✅ Расход: {amount} {currency} (прочее)")
    await callback.answer()


# =========================
# COMMANDS
# =========================

@dp.message(Command("start"))
async def start(message: types.Message):
    await seed_categories(message.from_user.id)
    await message.answer(
        "👋 Привет! Я финансовый бот.\n\n"
        "<b>Быстрый ввод:</b>\n"
        "• Расход: <code>1500$ здоровье</code>\n"
        "• Доход: <code>5000$</code>\n"
        "• % от суммы: <code>5% с 5000$</code>",
        reply_markup=main_kb,
        parse_mode="HTML",
    )


@dp.message(Command("addcat"))
async def cmd_addcat(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /addcat название\nПример: /addcat спорт")
        return
    cat = parts[1].strip().lower()
    async with aiosqlite.connect("finance.db") as db:
        await db.execute(
            "INSERT INTO categories (user_id, name) VALUES (?, ?)",
            (message.from_user.id, cat),
        )
        await db.commit()
    await message.answer(f"✅ Категория добавлена: {cat}", reply_markup=main_kb)


@dp.message(Command("delete"))
async def cmd_delete(message: types.Message):
    uid = message.from_user.id
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /delete ID")
        return

    op_id = int(parts[1])
    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute(
            "SELECT id FROM transactions WHERE id = ? AND user_id = ?", (op_id, uid)
        )
        if not await cursor.fetchone():
            await message.answer("Операция не найдена.")
            return
        await db.execute("DELETE FROM transactions WHERE id = ?", (op_id,))
        await db.commit()

    await message.answer(f"✅ Операция ID {op_id} удалена.", reply_markup=main_kb)


@dp.message(Command("edit"))
async def cmd_edit(message: types.Message):
    uid = message.from_user.id
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer("Использование: /edit ID сумма\nПример: /edit 5 250$")
        return

    op_id = int(parts[1])
    new_amount = parse_amount(parts[2])
    new_currency = parse_currency(parts[2])

    if not new_amount:
        await message.answer("Неверный формат суммы. Пример: /edit 5 250$")
        return

    async with aiosqlite.connect("finance.db") as db:
        cursor = await db.execute(
            "SELECT id FROM transactions WHERE id = ? AND user_id = ?", (op_id, uid)
        )
        if not await cursor.fetchone():
            await message.answer("Операция не найдена.")
            return
        await db.execute(
            "UPDATE transactions SET amount = ?, currency = ?, final_amount = ? WHERE id = ?",
            (new_amount, new_currency, new_amount, op_id),
        )
        await db.commit()

    await message.answer(
        f"✅ Операция ID {op_id} обновлена: {new_amount} {new_currency}",
        reply_markup=main_kb,
    )


# =========================
# MAIN HANDLER
# =========================

@dp.message()
async def handler(message: types.Message):
    uid = message.from_user.id
    text = message.text.strip()

    if text == "🔙 Назад":
        user_state.pop(uid, None)
        user_temp.pop(uid, None)
        await message.answer("Главное меню", reply_markup=main_kb)
        return

    if text == "📊 Статистика":
        await message.answer(await get_stats_text(uid), reply_markup=main_kb)
        return

    if text == "📅 Сегодня":
        await message.answer(
            await get_stats_text(uid, "DATE(created_at) = DATE('now', 'localtime')", "📅 Сегодня"),
            reply_markup=main_kb,
        )
        return

    if text == "📅 Неделя":
        await message.answer(
            await get_stats_text(uid, "DATE(created_at) >= DATE('now', '-7 day', 'localtime')", "📅 Неделя"),
            reply_markup=main_kb,
        )
        return

    if text == "📅 Месяц":
        await message.answer(
            await get_stats_text(uid, "DATE(created_at) >= DATE('now', '-30 day', 'localtime')", "📅 Месяц"),
            reply_markup=main_kb,
        )
        return

    if text == "🧾 Последние операции":
        await message.answer(await get_last_operations_text(uid), reply_markup=main_kb)
        return

    if text == "📋 Категории":
        cats = await get_categories(uid)
        cat_list = "\n".join(f"• {c}" for c in cats)
        await message.answer(
            f"📋 Твои категории:\n\n{cat_list}\n\nДобавить: /addcat название",
            reply_markup=main_kb,
        )
        return

    if user_state.get(uid) == "add_category":
        cat = text.lower()
        async with aiosqlite.connect("finance.db") as db:
            await db.execute(
                "INSERT INTO categories (user_id, name) VALUES (?, ?)", (uid, cat)
            )
            await db.commit()
        user_state.pop(uid, None)
        await message.answer(f"✅ Категория добавлена: {cat}", reply_markup=main_kb)
        return

    # --- QUICK INPUT ---
    cats = await get_categories(uid)

    # 1. Percent income: "5% с 5000$"
    percent = parse_percent_income(text)
    if percent:
        await add_transaction(uid, percent["amount"], percent["currency"], "доход", "income")
        await message.answer(
            f"✅ Доход: {percent['percent']}% с {percent['base']} {percent['currency']} "
            f"= {percent['amount']} {percent['currency']}",
            reply_markup=main_kb,
        )
        return

    # 2. Quick expense: "1500$ здоровье"
    expense = parse_quick_expense(text, cats)
    if expense:
        if expense["found"]:
            await add_transaction(uid, expense["amount"], expense["currency"], expense["category"], "expense")
            await message.answer(
                f"✅ Расход: {expense['amount']} {expense['currency']} ({expense['category']})",
                reply_markup=main_kb,
            )
        else:
            cat = expense["category"]
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"➕ Добавить «{cat}»",
                    callback_data=f"add_cat:{expense['amount']}:{expense['currency']}:{cat}",
                )],
                [InlineKeyboardButton(
                    text="Использовать «прочее»",
                    callback_data=f"use_misc:{expense['amount']}:{expense['currency']}",
                )],
            ])
            await message.answer(
                f"❓ Категория «{cat}» не найдена.\n"
                f"Что сделать с {expense['amount']} {expense['currency']}?",
                reply_markup=kb,
            )
        return

    # 3. Quick income: "5000$"
    income = parse_quick_income(text)
    if income:
        await add_transaction(uid, income["amount"], income["currency"], "доход", "income")
        await message.answer(
            f"✅ Доход: {income['amount']} {income['currency']}",
            reply_markup=main_kb,
        )
        return

    await message.answer(
        "❌ Не понял формат.\n\n"
        "<b>Примеры:</b>\n"
        "• Расход: <code>1500$ здоровье</code>\n"
        "• Доход: <code>5000$</code>\n"
        "• % от суммы: <code>5% с 5000$</code>",
        parse_mode="HTML",
        reply_markup=main_kb,
    )


# =========================
# RUN
# =========================

async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
