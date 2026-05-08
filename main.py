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

BASE_CATEGORIES = ["дом", "семья", "машина", "еда", "развлечения", "здоровье"]

CURRENCY_MAP = {
    "usd": "USD", "$": "USD", "dollar": "USD", "dollars": "USD",
    "бакс": "USD", "баксы": "USD", "доллар": "USD", "доллары": "USD",
    "eur": "EUR", "€": "EUR", "euro": "EUR", "euros": "EUR",
    "евро": "EUR", "еврики": "EUR",
    "uah": "UAH", "₴": "UAH", "грн": "UAH",
    "гривна": "UAH", "гривны": "UAH", "grn": "UAH",
}

CURRENCIES = ["USD", "EUR", "UAH"]

main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Трата"), KeyboardButton(text="💰 Доход")],
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="📅 Сегодня"), KeyboardButton(text="📅 Неделя"), KeyboardButton(text="📅 Месяц")],
        [KeyboardButton(text="🧾 История")],
    ],
    resize_keyboard=True,
)


# =========================
# PARSERS
# =========================

def parse_amount(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def parse_currency(text: str) -> str | None:
    t = text.lower()
    for key, val in CURRENCY_MAP.items():
        if key in t:
            return val
    return None


def strip_amount_and_currency(text: str) -> str:
    result = re.sub(r"\d+(?:\.\d+)?", "", text, count=1)
    patterns = [
        r"\$", r"€", r"₴",
        r"\busd\b", r"\beur\b", r"\buah\b", r"\bgrn\b",
        r"\bdollar[s]?\b", r"\beuro[s]?\b",
        r"\bбаксы?\b", r"\bдоллары?\b", r"\bевро\b", r"\bеврики\b",
        r"\bгривны?\b", r"\bгрн\b",
    ]
    for p in patterns:
        result = re.sub(p, "", result, flags=re.IGNORECASE)
    return result.strip().lower()


def try_parse_percent_income(text: str) -> dict | None:
    """'5% с 5000$'  /  '10% от 3000€'"""
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:процентов|процента|процент|%)\s+(?:с|от|of|from)\s+(\d+(?:\.\d+)?)",
        text, re.IGNORECASE,
    )
    if not m:
        return None
    percent = float(m.group(1))
    base = float(m.group(2))
    currency = parse_currency(text)
    if not currency:
        return None
    return {
        "type": "percent_income",
        "percent": percent,
        "base": base,
        "amount": round(base * percent / 100, 2),
        "currency": currency,
    }


def try_parse_expense(text: str, categories: list[str]) -> dict | None:
    """'1500$ здоровье'"""
    if not re.search(r"\d", text):
        return None
    amount = parse_amount(text)
    currency = parse_currency(text)
    if not amount or not currency:
        return None
    category = strip_amount_and_currency(text)
    if not category:
        return None
    return {
        "amount": amount,
        "currency": currency,
        "category": category,
        "known": category in categories,
    }


def try_parse_income(text: str) -> dict | None:
    """'5000$' — только сумма + валюта, больше ничего."""
    if not re.search(r"\d", text):
        return None
    leftover = strip_amount_and_currency(text)
    if leftover:
        return None
    amount = parse_amount(text)
    currency = parse_currency(text)
    if not amount or not currency:
        return None
    return {"type": "income", "amount": amount, "currency": currency}


# =========================
# DATABASE
# =========================

async def init_db():
    async with aiosqlite.connect("finance.db") as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                amount     REAL    NOT NULL,
                currency   TEXT    NOT NULL,
                category   TEXT    NOT NULL,
                type       TEXT    NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name    TEXT    NOT NULL
            )
        """)
        await db.commit()


async def get_categories(user_id: int) -> list[str]:
    async with aiosqlite.connect("finance.db") as db:
        cur = await db.execute(
            "SELECT name FROM categories WHERE user_id = ? ORDER BY name", (user_id,)
        )
        return [r[0] for r in await cur.fetchall()]


async def seed_categories(user_id: int):
    async with aiosqlite.connect("finance.db") as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM categories WHERE user_id = ?", (user_id,)
        )
        if (await cur.fetchone())[0] == 0:
            for cat in BASE_CATEGORIES:
                await db.execute(
                    "INSERT INTO categories (user_id, name) VALUES (?, ?)", (user_id, cat)
                )
            await db.commit()


async def insert_transaction(user_id: int, amount: float, currency: str, category: str, tx_type: str):
    async with aiosqlite.connect("finance.db") as db:
        await db.execute(
            "INSERT INTO transactions (user_id, amount, currency, category, type) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, currency, category, tx_type),
        )
        await db.commit()


# =========================
# STATISTICS
# =========================

async def build_stats(user_id: int, date_filter: str, title: str) -> str:
    income: dict[str, float] = {c: 0.0 for c in CURRENCIES}
    expense: dict[str, float] = {c: 0.0 for c in CURRENCIES}
    # {category: {currency: amount}}
    by_cat: dict[str, dict[str, float]] = {}

    async with aiosqlite.connect("finance.db") as db:
        cur = await db.execute(f"""
            SELECT currency, SUM(amount) FROM transactions
            WHERE user_id=? AND type IN ('income','percent_income') AND {date_filter}
            GROUP BY currency
        """, (user_id,))
        for currency, total in await cur.fetchall():
            if currency in income:
                income[currency] = round(total or 0, 2)

        cur = await db.execute(f"""
            SELECT currency, SUM(amount) FROM transactions
            WHERE user_id=? AND type='expense' AND {date_filter}
            GROUP BY currency
        """, (user_id,))
        for currency, total in await cur.fetchall():
            if currency in expense:
                expense[currency] = round(total or 0, 2)

        cur = await db.execute(f"""
            SELECT category, currency, SUM(amount) FROM transactions
            WHERE user_id=? AND type='expense' AND {date_filter}
            GROUP BY category, currency
            ORDER BY category, SUM(amount) DESC
        """, (user_id,))
        for category, currency, total in await cur.fetchall():
            by_cat.setdefault(category, {})[currency] = round(total or 0, 2)

    lines = [title, ""]

    # Income line
    inc_parts = [f"{c} {income[c]}" for c in CURRENCIES if income[c] > 0]
    lines.append("💰 Доход: " + (", ".join(inc_parts) if inc_parts else "—"))

    # Expense line
    exp_parts = [f"{c} {expense[c]}" for c in CURRENCIES if expense[c] > 0]
    lines.append("💸 Расход: " + (", ".join(exp_parts) if exp_parts else "—"))

    # Balance line
    bal_parts = []
    for c in CURRENCIES:
        if income[c] > 0 or expense[c] > 0:
            bal_parts.append(f"{c} {round(income[c] - expense[c], 2)}")
    lines.append("🏦 Баланс: " + (", ".join(bal_parts) if bal_parts else "—"))

    # Categories
    if by_cat:
        lines.append("")
        lines.append("📂 По категориям:")
        for cat in sorted(by_cat):
            lines.append(f"{cat}:")
            for c in CURRENCIES:
                if c in by_cat[cat]:
                    lines.append(f"  {c}: {by_cat[cat][c]}")
    else:
        lines.append("📂 Расходов нет")

    return "\n".join(lines)


async def build_history(user_id: int) -> str:
    async with aiosqlite.connect("finance.db") as db:
        cur = await db.execute("""
            SELECT id, type, category, amount, currency, created_at
            FROM transactions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 20
        """, (user_id,))
        rows = await cur.fetchall()

    if not rows:
        return "Пока нет операций."

    lines = ["🧾 Последние 20 операций:\n"]
    for op_id, op_type, category, amount, currency, created_at in rows:
        label = "💰" if op_type in ("income", "percent_income") else "💸"
        date = created_at[:10] if created_at else ""
        lines.append(f"ID {op_id} {label} {amount} {currency} — {category} [{date}]")

    lines.append("\n✏️ /edit ID сумма   ❌ /delete ID")
    return "\n".join(lines)


# =========================
# CALLBACKS (inline buttons)
# =========================

@dp.callback_query(lambda c: c.data.startswith("addcat:"))
async def cb_addcat(callback: CallbackQuery):
    uid = callback.from_user.id
    _, amount_s, currency, category = callback.data.split(":", 3)
    amount = float(amount_s)

    async with aiosqlite.connect("finance.db") as db:
        await db.execute("INSERT INTO categories (user_id, name) VALUES (?, ?)", (uid, category))
        await db.execute(
            "INSERT INTO transactions (user_id, amount, currency, category, type) VALUES (?, ?, ?, ?, ?)",
            (uid, amount, currency, category, "expense"),
        )
        await db.commit()

    await callback.message.edit_text(
        f"✅ Категория «{category}» добавлена.\n✅ Расход: {amount} {currency} ({category})"
    )
    await callback.answer()


@dp.callback_query(lambda c: c.data.startswith("misc:"))
async def cb_misc(callback: CallbackQuery):
    uid = callback.from_user.id
    _, amount_s, currency = callback.data.split(":", 2)
    await insert_transaction(uid, float(amount_s), currency, "прочее", "expense")
    await callback.message.edit_text(f"✅ Расход: {amount_s} {currency} (прочее)")
    await callback.answer()


# =========================
# COMMANDS
# =========================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await seed_categories(message.from_user.id)
    await message.answer(
        "👋 Привет!\n\n"
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
        await message.answer("Использование: /addcat название")
        return
    cat = parts[1].strip().lower()
    async with aiosqlite.connect("finance.db") as db:
        await db.execute("INSERT INTO categories (user_id, name) VALUES (?, ?)", (message.from_user.id, cat))
        await db.commit()
    await message.answer(f"✅ Категория добавлена: {cat}", reply_markup=main_kb)


@dp.message(Command("delcat"))
async def cmd_delcat(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /delcat название")
        return
    cat = parts[1].strip().lower()
    async with aiosqlite.connect("finance.db") as db:
        cur = await db.execute(
            "SELECT id FROM categories WHERE user_id=? AND name=?", (message.from_user.id, cat)
        )
        if not await cur.fetchone():
            await message.answer(f"Категория «{cat}» не найдена.")
            return
        await db.execute("DELETE FROM categories WHERE user_id=? AND name=?", (message.from_user.id, cat))
        await db.commit()
    await message.answer(f"🗑 Категория «{cat}» удалена.", reply_markup=main_kb)


@dp.message(Command("delete"))
async def cmd_delete(message: types.Message):
    uid = message.from_user.id
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /delete ID")
        return
    op_id = int(parts[1])
    async with aiosqlite.connect("finance.db") as db:
        cur = await db.execute(
            "SELECT id FROM transactions WHERE id=? AND user_id=?", (op_id, uid)
        )
        if not await cur.fetchone():
            await message.answer("Операция не найдена.")
            return
        await db.execute("DELETE FROM transactions WHERE id=?", (op_id,))
        await db.commit()
    await message.answer(f"🗑 Операция ID {op_id} удалена.", reply_markup=main_kb)


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
    if not new_amount or not new_currency:
        await message.answer("Неверный формат суммы. Пример: /edit 5 250$")
        return
    async with aiosqlite.connect("finance.db") as db:
        cur = await db.execute(
            "SELECT id FROM transactions WHERE id=? AND user_id=?", (op_id, uid)
        )
        if not await cur.fetchone():
            await message.answer("Операция не найдена.")
            return
        await db.execute(
            "UPDATE transactions SET amount=?, currency=? WHERE id=?",
            (new_amount, new_currency, op_id),
        )
        await db.commit()
    await message.answer(f"✅ Операция ID {op_id}: {new_amount} {new_currency}", reply_markup=main_kb)


# =========================
# MAIN HANDLER
# =========================

@dp.message()
async def handler(message: types.Message):
    uid = message.from_user.id
    text = message.text.strip()

    # Menu buttons
    if text == "➕ Трата":
        user_state[uid] = "expense"
        await message.answer(
            "Напиши расход в формате:\n<code>1500$ здоровье</code>",
            parse_mode="HTML",
        )
        return

    if text == "💰 Доход":
        user_state[uid] = "income"
        await message.answer(
            "Напиши доход в формате:\n"
            "<code>5000$</code> или <code>5% с 5000$</code>",
            parse_mode="HTML",
        )
        return

    if text == "📊 Статистика":
        await message.answer(await build_stats(uid, "1=1", "📊 Статистика (всё время)"), reply_markup=main_kb)
        return

    if text == "📅 Сегодня":
        await message.answer(
            await build_stats(uid, "DATE(created_at)=DATE('now','localtime')", "📅 Сегодня"),
            reply_markup=main_kb,
        )
        return

    if text == "📅 Неделя":
        await message.answer(
            await build_stats(uid, "DATE(created_at)>=DATE('now','-7 day','localtime')", "📅 Неделя"),
            reply_markup=main_kb,
        )
        return

    if text == "📅 Месяц":
        await message.answer(
            await build_stats(uid, "DATE(created_at)>=DATE('now','-30 day','localtime')", "📅 Месяц"),
            reply_markup=main_kb,
        )
        return

    if text == "🧾 История":
        await message.answer(await build_history(uid), reply_markup=main_kb)
        return

    # Quick input parsing
    cats = await get_categories(uid)

    # 1. Percent income: "5% с 5000$"
    p = try_parse_percent_income(text)
    if p:
        await insert_transaction(uid, p["amount"], p["currency"], "доход", "percent_income")
        await message.answer(
            f"✅ Доход: {p['percent']}% с {p['base']} {p['currency']} = {p['amount']} {p['currency']}",
            reply_markup=main_kb,
        )
        user_state.pop(uid, None)
        return

    # 2. Quick expense: "1500$ здоровье"
    e = try_parse_expense(text, cats)
    if e:
        if e["known"]:
            await insert_transaction(uid, e["amount"], e["currency"], e["category"], "expense")
            await message.answer(
                f"✅ Расход: {e['amount']} {e['currency']} ({e['category']})",
                reply_markup=main_kb,
            )
        else:
            cat = e["category"]
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=f"➕ Добавить «{cat}»",
                    callback_data=f"addcat:{e['amount']}:{e['currency']}:{cat}",
                )],
                [InlineKeyboardButton(
                    text="Записать как «прочее»",
                    callback_data=f"misc:{e['amount']}:{e['currency']}",
                )],
            ])
            await message.answer(
                f"❓ Категория «{cat}» не найдена.\nЧто сделать с {e['amount']} {e['currency']}?",
                reply_markup=kb,
            )
        user_state.pop(uid, None)
        return

    # 3. Quick income: "5000$"
    i = try_parse_income(text)
    if i:
        await insert_transaction(uid, i["amount"], i["currency"], "доход", "income")
        await message.answer(f"✅ Доход: {i['amount']} {i['currency']}", reply_markup=main_kb)
        user_state.pop(uid, None)
        return

    # Fallback
    await message.answer(
        "❌ Не понял формат.\n\n"
        "<b>Примеры:</b>\n"
        "• <code>1500$ здоровье</code> — расход\n"
        "• <code>5000$</code> — доход\n"
        "• <code>5% с 5000$</code> — % от суммы\n\n"
        "Категории: /addcat название\n"
        "Удалить категорию: /delcat название",
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
