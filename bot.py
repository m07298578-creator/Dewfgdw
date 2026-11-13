import asyncio
import asyncpg
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = "9023924024:AAHsVjuygA6foum-kHATKp8TKmGp2Xf2lXI"
DATABASE_URL = "postgresql://neondb_owner:npg_hRwYWpb5C0ry@ep-fancy-shadow-bescuz4s-pooler.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

CHANNEL_URL = "https://t.me/WxSubNews"
CHANNEL_ID = "-1003725062404"
CURRENCY = "WxSub"
HOLD_DAYS = 3
BOT_USERNAME = "@WxSubBot"
REFUND_FEE = 0.05
PRIVACY_URL = "https://telegra.ph/WxSub-11-12"

# ================== ЕЖЕДНЕВНЫЙ БОНУС ==================
BONUS_BASE = 500
BONUS_STEP = 500
BONUS_MAX = 5000
BONUS_COOLDOWN_HOURS = 24

# ================== РЕФЕРАЛКА ==================
REF_REWARD = 5000

# ================== ЛИМИТЫ ПРОДВИЖЕНИЯ ==================
MIN_PRICE = 100
MAX_PRICE = 1000

# ================== АДМИНЫ ==================
ADMIN_IDS = [8786951363]
# ================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
pool: asyncpg.Pool = None


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ================== БАЗА ==================
async def init_db():
    global pool
    pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=10, ssl="require")
    async with pool.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            balance DOUBLE PRECISION DEFAULT 0,
            start_date TIMESTAMP,
            tasks_done INTEGER DEFAULT 0,
            subscribed BOOLEAN DEFAULT FALSE
        );

        CREATE TABLE IF NOT EXISTS channels (
            id SERIAL PRIMARY KEY,
            owner_id BIGINT NOT NULL,
            chat_id BIGINT NOT NULL,
            title TEXT,
            link TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            limit_total INTEGER DEFAULT 0,
            done_count INTEGER DEFAULT 0,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            channel_id INTEGER NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            status TEXT DEFAULT 'active',
            done_at TIMESTAMP DEFAULT NOW(),
            check_until TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS promotions (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            channel_id BIGINT NOT NULL,
            channel_title TEXT,
            price_per DOUBLE PRECISION NOT NULL,
            count INTEGER NOT NULL,
            done_count INTEGER DEFAULT 0,
            total DOUBLE PRECISION NOT NULL,
            status TEXT DEFAULT 'active',
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY,
            referrer_id BIGINT NOT NULL,
            referred_id BIGINT NOT NULL UNIQUE,
            reward DOUBLE PRECISION NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );

        ALTER TABLE promotions ADD COLUMN IF NOT EXISTS done_count INTEGER DEFAULT 0;
        ALTER TABLE promotions ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS bonus_streak INTEGER DEFAULT 0;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bonus TIMESTAMP;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by BIGINT;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_rewarded BOOLEAN DEFAULT FALSE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_count INTEGER DEFAULT 0;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS ref_earned DOUBLE PRECISION DEFAULT 0;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS banned BOOLEAN DEFAULT FALSE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_at TIMESTAMP;
        """)


async def get_user(user_id: int):
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)


async def create_user(user_id: int, username: str):
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO users (user_id, username, balance, start_date, tasks_done, subscribed)
               VALUES ($1,$2,0,$3,0,FALSE) ON CONFLICT (user_id) DO NOTHING""",
            user_id, username, datetime.now()
        )


# ================== ПОДПИСКА ==================
async def check_subscription(user_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False


async def bot_is_admin(chat_id: int) -> bool:
    try:
        m = await bot.get_chat_member(chat_id=chat_id, user_id=bot.id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False


# ================== РЕФЕРАЛКА ==================
async def try_reward_referrer(referred_id: int):
    async with pool.acquire() as conn:
        u = await conn.fetchrow(
            "SELECT referred_by, ref_rewarded FROM users WHERE user_id = $1", referred_id
        )
    if not u or not u["referred_by"] or u["ref_rewarded"]:
        return
    if not await check_subscription(referred_id):
        return

    referrer_id = u["referred_by"]
    async with pool.acquire() as conn:
        updated = await conn.execute("""
            UPDATE users SET ref_rewarded = TRUE WHERE user_id = $1 AND ref_rewarded = FALSE
        """, referred_id)
        if updated == "UPDATE 0":
            return
        await conn.execute("""
            UPDATE users SET balance = balance + $1,
                             ref_count = ref_count + 1,
                             ref_earned = ref_earned + $1
            WHERE user_id = $2
        """, REF_REWARD, referrer_id)
        await conn.execute("""
            INSERT INTO referrals (referrer_id, referred_id, reward)
            VALUES ($1, $2, $3) ON CONFLICT (referred_id) DO NOTHING
        """, referrer_id, referred_id, REF_REWARD)

    try:
        await bot.send_message(
            referrer_id,
            f"🎉 <b>Новый реферал!</b>\n\nТвой друг подписался на канал.\n"
            f"💰 Начислено: <b>{int(REF_REWARD)} {CURRENCY}</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass


# ================== КЛАВИАТУРЫ ==================
def subscribe_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Проверить", callback_data="check_sub")]
    ])


def main_menu_kb(user_id: int):
    rows = [
        [InlineKeyboardButton(text="💰 Заработать", callback_data="earn")],
        [InlineKeyboardButton(text="📈 Продвижение", callback_data="promote")],
        [InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton(text="📜 Политика конфиденциальности", callback_data="privacy")]
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb(cb="main_menu"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=cb)]
    ])


def cancel_kb(cb="main_menu"):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data=cb)]
    ])


def profile_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Мои задания", callback_data="my_tasks")],
        [InlineKeyboardButton(text="🔗 Моя реферальная ссылка", callback_data="ref_link")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])


def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="💰 Выдать баланс", callback_data="adm_give")],
        [InlineKeyboardButton(text="💸 Забрать баланс", callback_data="adm_take")],
        [InlineKeyboardButton(text="🚫 Забанить", callback_data="adm_ban")],
        [InlineKeyboardButton(text="✅ Разбанить", callback_data="adm_unban")],
        [InlineKeyboardButton(text="👤 Инфо о юзере", callback_data="adm_info")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])


def welcome_done_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Продолжить", callback_data="main_menu")]
    ])


# ================== FSM ==================
class Promo(StatesGroup):
    waiting_price = State()
    waiting_count = State()
    confirm = State()


class AddMore(StatesGroup):
    waiting_count = State()


class AdminStates(StatesGroup):
    give_id = State()
    give_amount = State()
    take_id = State()
    take_amount = State()
    ban_id = State()
    unban_id = State()
    info_id = State()
    broadcast_text = State()


# ================== /start ==================
@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "user"

    existed = await get_user(user_id)
    await create_user(user_id, username)

    fresh = await get_user(user_id)
    if fresh and fresh["banned"]:
        await message.answer("🚫 Ты забанен в боте. Обратись к администратору.")
        return

    if not existed and command.args and command.args.startswith("ref_"):
        try:
            referrer_id = int(command.args.replace("ref_", ""))
        except ValueError:
            referrer_id = None
        if referrer_id and referrer_id != user_id:
            async with pool.acquire() as conn:
                ref_exists = await conn.fetchval(
                    "SELECT 1 FROM users WHERE user_id = $1", referrer_id)
                if ref_exists:
                    await conn.execute(
                        "UPDATE users SET referred_by = $1 WHERE user_id = $2",
                        referrer_id, user_id)

    await try_reward_referrer(user_id)

    if await check_subscription(user_id):
        await message.answer(
            f"👋 Привет, <b>{username}</b>!\n\nДобро пожаловать в <b>WxSub</b>.",
            reply_markup=main_menu_kb(user_id), parse_mode="HTML"
        )
    else:
        await message.answer(
            "🔒 Для использования бота подпишись на наш канал.\n\n"
            "После подписки нажми <b>«Проверить»</b>.",
            reply_markup=subscribe_kb(), parse_mode="HTML"
        )


# ================== ✅ Проверка подписки ==================
@dp.callback_query(F.data == "check_sub")
async def check_sub_cb(callback: CallbackQuery):
    uid = callback.from_user.id
    if await check_subscription(uid):
        async with pool.acquire() as conn:
            await conn.execute("UPDATE users SET subscribed = TRUE WHERE user_id = $1", uid)
        await try_reward_referrer(uid)

        # 🔹 Приветствие после подписки + рекомендация про privacy
        await callback.message.edit_text(
            "✅ <b>Подписка подтверждена!</b>\n\n"
            "Спасибо за подписку и использование бота <b>WxSub</b>! 🎉\n\n"
            "📜 <b>Рекомендуем ознакомиться с Политикой конфиденциальности бота:</b>\n\n"
            "👉 Нажми на <b>бота</b> в Telegram (в шапке чата), затем на "
            "<b>три точки</b> справа сверху и выбери пункт "
            "<b>«Политика конфиденциальности»</b>.\n\n"
            "Либо просто отправь команду /privacy\n\n"
            "Приятного пользования! 💚",
            reply_markup=welcome_done_kb(),
            parse_mode="HTML"
        )
    else:
        await callback.answer("❌ Ты ещё не подписался на канал!", show_alert=True)


@dp.callback_query(F.data == "main_menu")
async def main_menu_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("🏠 <b>Главное меню</b>\n\nВыбери действие:",
                                     reply_markup=main_menu_kb(callback.from_user.id),
                                     parse_mode="HTML")


# ================== 📜 PRIVACY POLICY ==================
@dp.message(Command("privacy"))
async def privacy_cmd(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Открыть правила", url=PRIVACY_URL)],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")]
    ])
    await message.answer(
        "📜 <b>Политика конфиденциальности и правила WxSub</b>\n\n"
        "Ознакомиться с правилами использования бота можно по кнопке ниже:",
        reply_markup=kb, parse_mode="HTML"
    )


@dp.callback_query(F.data == "privacy")
async def privacy_cb(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Открыть правила", url=PRIVACY_URL)],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")]
    ])
    await callback.message.edit_text(
        "📜 <b>Политика конфиденциальности и правила WxSub</b>\n\n"
        "Ознакомиться с правилами использования бота можно по кнопке ниже:",
        reply_markup=kb, parse_mode="HTML"
    )


# ================== 💰 ЗАРАБОТАТЬ ==================
@dp.callback_query(F.data == "earn")
async def earn_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.* FROM channels c
            WHERE c.active = TRUE AND c.price > 0
              AND (c.limit_total = 0 OR c.done_count < c.limit_total)
              AND NOT EXISTS (
                  SELECT 1 FROM tasks t
                  WHERE t.user_id = $1 AND t.channel_id = c.id AND t.status = 'active'
              )
            ORDER BY c.price DESC LIMIT 20
        """, user_id)

    if not rows:
        await callback.message.edit_text(
            "💰 <b>Заработать</b>\n\nПока нет доступных заданий. Заходи позже!",
            reply_markup=back_kb(), parse_mode="HTML")
        return

    lines = ["💰 <b>Доступные задания</b>\n"]
    kb_rows = []
    for r in rows:
        left = "∞" if r["limit_total"] == 0 else str(r["limit_total"] - r["done_count"])
        lines.append(f"• <b>{r['title']}</b> — {r['price']} {CURRENCY} (осталось: {left})")
        kb_rows.append([
            InlineKeyboardButton(text=f"{r['title']} | {r['price']} {CURRENCY}", url=r["link"]),
            InlineKeyboardButton(text="✅ Проверить", callback_data=f"check_task_{r['id']}")
        ])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])

    await callback.message.edit_text("\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML", disable_web_page_preview=True)


@dp.callback_query(F.data.startswith("check_task_"))
async def check_task_cb(callback: CallbackQuery):
    ch_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id

    async with pool.acquire() as conn:
        ch = await conn.fetchrow("SELECT * FROM channels WHERE id = $1", ch_id)
        if not ch:
            await callback.answer("Канал не найден", show_alert=True); return
        existing = await conn.fetchrow(
            "SELECT * FROM tasks WHERE user_id=$1 AND channel_id=$2 AND status='active'",
            user_id, ch_id)
        if existing:
            await callback.answer("Ты уже выполняешь это задание", show_alert=True); return

    try:
        m = await bot.get_chat_member(chat_id=ch["chat_id"], user_id=user_id)
        ok = m.status in ("member", "administrator", "creator")
    except Exception:
        ok = False

    if not ok:
        await callback.answer("❌ Ты ещё не подписался на канал!", show_alert=True); return

    until = datetime.now() + timedelta(days=HOLD_DAYS)
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO tasks (user_id, channel_id, price, status, done_at, check_until)
               VALUES ($1,$2,$3,'active',$4,$5)""",
            user_id, ch_id, ch["price"], datetime.now(), until)
        await conn.execute("UPDATE channels SET done_count = done_count + 1 WHERE id = $1", ch_id)
        await conn.execute(
            "UPDATE users SET balance = balance + $1, tasks_done = tasks_done + 1 WHERE user_id = $2",
            ch["price"], user_id)
        await conn.execute(
            "UPDATE promotions SET done_count = done_count + 1 WHERE channel_id = $1 AND status='active'",
            ch["chat_id"])

    await callback.message.answer(
        f"✅ <b>Задание выполнено!</b>\n\n"
        f"Вам начислено <b>{ch['price']} {CURRENCY}</b>.\n\n"
        f"⚠️ Не отписывайтесь от канала в течение <b>{HOLD_DAYS} дней</b> — "
        f"иначе сумма будет списана.",
        parse_mode="HTML")


# ================== 📈 ПРОДВИЖЕНИЕ ==================
@dp.callback_query(F.data == "promote")
async def promote_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text = (
        "📈 <b>Продвижение</b>\n\n"
        "1️⃣ Добавь нашего бота в <b>администраторы</b> своего канала/группы.\n"
        "2️⃣ Нажми «Выбрать канал» и отправь ссылку.\n"
        f"3️⃣ Укажи цену за 1 подписчика (от <b>{MIN_PRICE}</b> до <b>{MAX_PRICE}</b> "
        f"{CURRENCY}) и количество."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить бота в админы",
            url=f"https://t.me/{BOT_USERNAME}?startchannel=1&admin=invite_users+manage_chat")],
        [InlineKeyboardButton(text="📢 Выбрать канал", callback_data="promo_pick")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "promo_pick")
async def promo_pick(callback: CallbackQuery):
    dp["waiting_channel"] = callback.from_user.id
    await callback.message.edit_text(
        "📢 Отправь ссылку на свой канал или группу.\n\n"
        "Формат: <code>@username</code> или <code>https://t.me/username</code>",
        reply_markup=cancel_kb(), parse_mode="HTML")


@dp.message(F.text & ~F.text.startswith("/"))
async def on_channel_link(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return  # FSM-сценарий активен — не вмешиваемся

    waiting = dp.get("waiting_channel")
    if waiting != message.from_user.id:
        return
    dp["waiting_channel"] = None

    link = message.text.strip()
    username = link.split("/")[-1].replace("@", "")
    chat_id_str = f"@{username}"

    try:
        chat = await bot.get_chat(chat_id_str)
    except Exception:
        await message.answer("❌ Не могу получить доступ к каналу.", reply_markup=back_kb())
        return

    if not await bot_is_admin(chat.id):
        await message.answer(
            "❌ <b>Бот не является администратором</b> этого канала.",
            reply_markup=back_kb(), parse_mode="HTML")
        return

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO channels (owner_id, chat_id, title, link, price, limit_total, active)
            VALUES ($1,$2,$3,$4,0,0,FALSE) ON CONFLICT DO NOTHING
        """, message.from_user.id, chat.id, chat.title, f"https://t.me/{username}")

    await state.update_data(promo_chat_id=chat.id, promo_title=chat.title)
    await state.set_state(Promo.waiting_price)
    await message.answer(
        f"✅ Канал <b>{chat.title}</b> подтверждён.\n\n"
        f"💵 Введи <b>цену за 1 подписчика</b> в {CURRENCY}\n"
        f"(от <b>{MIN_PRICE}</b> до <b>{MAX_PRICE}</b>):",
        reply_markup=cancel_kb(), parse_mode="HTML")


@dp.message(Promo.waiting_price)
async def promo_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer(
            f"❌ Введи число от <b>{MIN_PRICE}</b> до <b>{MAX_PRICE}</b> {CURRENCY}",
            parse_mode="HTML"
        )
        return

    if price < MIN_PRICE or price > MAX_PRICE:
        await message.answer(
            f"❌ Цена за 1 выполнение должна быть от <b>{MIN_PRICE}</b> "
            f"до <b>{MAX_PRICE}</b> {CURRENCY}.\n\n"
            f"Попробуй снова:",
            reply_markup=cancel_kb(), parse_mode="HTML"
        )
        return

    await state.update_data(price=price)
    await state.set_state(Promo.waiting_count)
    await message.answer(
        f"💵 Цена: <b>{price} {CURRENCY}</b>\n\nСколько подписчиков?",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


@dp.message(Promo.waiting_count)
async def promo_count(message: Message, state: FSMContext):
    try:
        cnt = int(message.text)
        if cnt < 1: raise ValueError
    except ValueError:
        await message.answer("❌ Введи целое число ≥ 1"); return

    data = await state.get_data()
    price = data["price"]
    total = round(price * cnt, 2)
    user = await get_user(message.from_user.id)

    await state.update_data(count=cnt, total=total)
    await state.set_state(Promo.confirm)
    text = (
        "📋 <b>Проверь заказ:</b>\n\n"
        f"📢 Канал: <b>{data['promo_title']}</b>\n"
        f"💵 Цена за 1: <b>{price} {CURRENCY}</b>\n"
        f"👥 Количество: <b>{cnt}</b>\n"
        f"💰 <b>Итого: {total} {CURRENCY}</b>\n\n"
        f"Твой баланс: <b>{round(user['balance'],2)} {CURRENCY}</b>"
    )
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="promo_confirm")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")]
    ]), parse_mode="HTML")


@dp.callback_query(F.data == "promo_confirm")
async def promo_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = callback.from_user.id
    user = await get_user(user_id)

    if not data.get("total"):
        await callback.answer("Сессия истекла", show_alert=True); return
    if user["balance"] < data["total"]:
        await callback.answer(f"❌ Нужно {data['total']} {CURRENCY}, "
                              f"у тебя {round(user['balance'],2)}", show_alert=True); return

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2",
                           data["total"], user_id)
        await conn.execute("""
            INSERT INTO promotions (user_id, channel_id, channel_title, price_per, count, total, status, active)
            VALUES ($1,$2,$3,$4,$5,$6,'active',TRUE)
        """, user_id, data["promo_chat_id"], data["promo_title"],
             data["price"], data["count"], data["total"])
        await conn.execute("""
            UPDATE channels SET price = $1, limit_total = $2, active = TRUE
            WHERE chat_id = $3 AND owner_id = $4
        """, data["price"], data["count"], data["promo_chat_id"], user_id)

    await state.clear()
    await callback.message.edit_text(
        f"✅ <b>Заказ оформлен!</b>\n\n"
        f"📢 {data['promo_title']}\n"
        f"👥 {data['count']} × {data['price']} {CURRENCY}\n"
        f"💰 Оплачено: <b>{data['total']} {CURRENCY}</b>",
        reply_markup=main_menu_kb(user_id), parse_mode="HTML")


# ================== 🎁 ЕЖЕДНЕВНЫЙ БОНУС ==================
def bonus_for_streak(streak: int) -> float:
    if streak <= 0: streak = 1
    amount = BONUS_BASE + (streak - 1) * BONUS_STEP
    return float(min(amount, BONUS_MAX))


@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    if not user:
        await callback.answer("Напиши /start", show_alert=True); return

    now = datetime.now()
    last = user["last_bonus"]
    streak = user["bonus_streak"] or 0

    if last:
        diff = now - last
        if diff < timedelta(hours=BONUS_COOLDOWN_HOURS):
            left = timedelta(hours=BONUS_COOLDOWN_HOURS) - diff
            await callback.answer(
                f"⏳ Следующий бонус через {left.seconds//3600} ч {(left.seconds%3600)//60} мин",
                show_alert=True)
            return
        if diff >= timedelta(hours=BONUS_COOLDOWN_HOURS * 2):
            streak = 0

    new_streak = streak + 1
    amount = bonus_for_streak(new_streak)

    async with pool.acquire() as conn:
        await conn.execute("""
            UPDATE users SET balance = balance + $1, bonus_streak = $2, last_bonus = $3
            WHERE user_id = $4
        """, amount, new_streak, now, user_id)

    next_amount = bonus_for_streak(new_streak + 1)
    is_max = amount >= BONUS_MAX

    text = (
        "🎁 <b>Ежедневный бонус получен!</b>\n\n"
        f"🔥 Серия: <b>{new_streak}</b> дн.\n"
        f"💰 Начислено: <b>{int(amount)} {CURRENCY}</b>\n\n"
    )
    if is_max:
        text += f"🏆 Максимум — <b>{int(BONUS_MAX)} {CURRENCY}</b> каждый день!\n"
    else:
        text += f"📅 Завтра: <b>{int(next_amount)} {CURRENCY}</b>\n"
    text += "\n⚠️ Пропустишь день — серия сбросится."

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")]
    ]), parse_mode="HTML")


# ================== 👤 ПРОФИЛЬ ==================
@dp.callback_query(F.data == "profile")
async def profile_cb(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Напиши /start", show_alert=True); return

    text = (
        "👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📛 Ник: <b>{user['username'] or '—'}</b>\n"
        f"💵 Баланс: <b>{round(user['balance'],2)} {CURRENCY}</b>\n"
        f"📅 Дата старта: <b>{user['start_date'].strftime('%d.%m.%Y %H:%M')}</b>\n"
        f"✅ Выполнено заданий: <b>{user['tasks_done']}</b>\n\n"
        f"👥 Рефералов: <b>{user['ref_count'] or 0}</b>\n"
        f"💸 Заработано с рефералов: <b>{round(user['ref_earned'] or 0, 2)} {CURRENCY}</b>"
    )
    await callback.message.edit_text(text, reply_markup=profile_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "ref_link")
async def ref_link_cb(callback: CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await callback.answer("Напиши /start", show_alert=True); return

    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user['user_id']}"
    text = (
        "🔗 <b>Твоя реферальная ссылка</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"🎁 За каждого друга, который <b>подпишется на канал проекта</b>, "
        f"ты получаешь <b>{int(REF_REWARD)} {CURRENCY}</b>.\n\n"
        f"👥 Приглашено: <b>{user['ref_count'] or 0}</b>\n"
        f"💰 Заработано: <b>{round(user['ref_earned'] or 0, 2)} {CURRENCY}</b>"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться",
            url=f"https://t.me/share/url?url={link}&text=Зарабатывай+в+WxSub!")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile")]
    ]), parse_mode="HTML", disable_web_page_preview=True)


# ================== 📋 МОИ ЗАДАНИЯ ==================
@dp.callback_query(F.data == "my_tasks")
async def my_tasks_cb(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM promotions
            WHERE user_id = $1 AND status IN ('active','paused')
            ORDER BY created_at DESC
        """, user_id)

    if not rows:
        await callback.message.edit_text(
            "📋 <b>Мои задания</b>\n\nНет активных заданий.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="profile")]
            ]), parse_mode="HTML")
        return

    lines = ["📋 <b>Мои задания</b>\n"]
    kb_rows = []
    for r in rows:
        left = r["count"] - r["done_count"]
        icon = "▶️" if r["active"] else "⏸"
        lines.append(
            f"{icon} <b>{r['channel_title']}</b>\n"
            f"   💵 {r['price_per']} {CURRENCY} × {r['count']} "
            f"(выполнено {r['done_count']}, осталось {left})\n"
            f"   💰 Оплачено: {r['total']} {CURRENCY}")
        kb_rows.append([
            InlineKeyboardButton(
                text=("⏸ Остановить" if r["active"] else "▶️ Продолжить"),
                callback_data=f"task_toggle_{r['id']}"),
            InlineKeyboardButton(text="➕ Добавить",
                                 callback_data=f"task_add_{r['id']}")
        ])
        kb_rows.append([
            InlineKeyboardButton(text="🗑 Удалить (возврат -5%)",
                                 callback_data=f"task_del_{r['id']}")
        ])
    kb_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="profile")])

    await callback.message.edit_text("\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows), parse_mode="HTML")


@dp.callback_query(F.data.startswith("task_toggle_"))
async def task_toggle_cb(callback: CallbackQuery):
    pid = int(callback.data.split("_")[2])
    async with pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id = $1", pid)
    if not promo or promo["user_id"] != callback.from_user.id:
        await callback.answer("Не найдено", show_alert=True); return

    if promo["active"]:
        async with pool.acquire() as conn:
            await conn.execute("UPDATE promotions SET active=FALSE, status='paused' WHERE id=$1", pid)
            await conn.execute("UPDATE channels SET active=FALSE WHERE chat_id=$1 AND owner_id=$2",
                               promo["channel_id"], callback.from_user.id)
        await callback.answer("⏸ Остановлено")
    else:
        if not await bot_is_admin(promo["channel_id"]):
            await callback.answer("❌ Бот не админ канала. Добавь в админы.",
                                  show_alert=True); return
        async with pool.acquire() as conn:
            await conn.execute("UPDATE promotions SET active=TRUE, status='active' WHERE id=$1", pid)
            await conn.execute("UPDATE channels SET active=TRUE WHERE chat_id=$1 AND owner_id=$2",
                               promo["channel_id"], callback.from_user.id)
        await callback.answer("▶️ Активировано")
    await my_tasks_cb(callback)


@dp.callback_query(F.data.startswith("task_add_"))
async def task_add_cb(callback: CallbackQuery, state: FSMContext):
    pid = int(callback.data.split("_")[2])
    async with pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id=$1", pid)
    if not promo or promo["user_id"] != callback.from_user.id:
        await callback.answer("Не найдено", show_alert=True); return

    await state.update_data(add_pid=pid, add_price=promo["price_per"])
    await state.set_state(AddMore.waiting_count)
    await callback.message.edit_text(
        f"➕ <b>Добавление выполнений</b>\n\n"
        f"📢 {promo['channel_title']}\n"
        f"💵 Цена за 1: <b>{promo['price_per']} {CURRENCY}</b>\n\n"
        f"Сколько добавить?",
        reply_markup=cancel_kb("my_tasks"), parse_mode="HTML")


@dp.message(AddMore.waiting_count)
async def task_add_count(message: Message, state: FSMContext):
    try:
        cnt = int(message.text)
        if cnt < 1: raise ValueError
    except ValueError:
        await message.answer("❌ Введи целое число ≥ 1"); return

    data = await state.get_data()
    pid, price = data["add_pid"], data["add_price"]

    if price < MIN_PRICE or price > MAX_PRICE:
        await message.answer(
            f"❌ Цена этого задания (<b>{price} {CURRENCY}</b>) вне допустимого "
            f"диапазона <b>{MIN_PRICE}–{MAX_PRICE}</b> {CURRENCY}.\n"
            f"Обратись к администратору.",
            reply_markup=back_kb("my_tasks"), parse_mode="HTML"
        )
        await state.clear()
        return

    total = round(price * cnt, 2)
    user = await get_user(message.from_user.id)

    if user["balance"] < total:
        await message.answer(f"❌ Нужно {total} {CURRENCY}, "
                             f"у тебя {round(user['balance'],2)}",
                             reply_markup=back_kb("my_tasks"))
        await state.clear(); return

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2",
                           total, message.from_user.id)
        await conn.execute("UPDATE promotions SET count = count + $1, total = total + $2 WHERE id = $3",
                           cnt, total, pid)
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id=$1", pid)
        await conn.execute("UPDATE channels SET limit_total = $1 WHERE chat_id=$2",
                           promo["count"], promo["channel_id"])

    await state.clear()
    await message.answer(f"✅ Добавлено <b>{cnt}</b>.\nСписано: <b>{total} {CURRENCY}</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои задания", callback_data="my_tasks")]
        ]), parse_mode="HTML")


@dp.callback_query(F.data.startswith("task_del_") & ~F.data.startswith("task_del_ok_"))
async def task_del_cb(callback: CallbackQuery):
    pid = int(callback.data.split("_")[2])
    async with pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id=$1", pid)
    if not promo or promo["user_id"] != callback.from_user.id:
        await callback.answer("Не найдено", show_alert=True); return

    left = max(promo["count"] - promo["done_count"], 0)
    refund = round(left * promo["price_per"] * (1 - REFUND_FEE), 2)
    fee = round(left * promo["price_per"] * REFUND_FEE, 2)

    await callback.message.edit_text(
        f"🗑 <b>Удаление задания</b>\n\n"
        f"📢 {promo['channel_title']}\n"
        f"Не использовано: <b>{left}</b>\n"
        f"Возврат: <b>{refund} {CURRENCY}</b>\n"
        f"Комиссия (5%): <b>{fee} {CURRENCY}</b>\n\n"
        f"Подтверди:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"task_del_ok_{pid}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="my_tasks")]
        ]), parse_mode="HTML")


@dp.callback_query(F.data.startswith("task_del_ok_"))
async def task_del_ok_cb(callback: CallbackQuery):
    pid = int(callback.data.split("_")[3])
    async with pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id=$1", pid)
    if not promo or promo["user_id"] != callback.from_user.id:
        await callback.answer("Не найдено", show_alert=True); return

    left = max(promo["count"] - promo["done_count"], 0)
    refund = round(left * promo["price_per"] * (1 - REFUND_FEE), 2)

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                           refund, callback.from_user.id)
        await conn.execute("UPDATE promotions SET status='deleted', active=FALSE WHERE id=$1", pid)
        await conn.execute("UPDATE channels SET active=FALSE WHERE chat_id=$1 AND owner_id=$2",
                           promo["channel_id"], callback.from_user.id)

    await callback.message.edit_text(
        f"✅ Удалено.\n\n💰 Возвращено: <b>{refund} {CURRENCY}</b> (с учётом 5%)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Мои задания", callback_data="my_tasks")]
        ]), parse_mode="HTML")


# ================== 👑 АДМИН-ПАНЕЛЬ ==================
@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("👑 <b>Админ-панель</b>\n\nВыбери действие:",
                         reply_markup=admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_cb(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True); return
    await callback.message.edit_text("👑 <b>Админ-панель</b>\n\nВыбери действие:",
                                     reply_markup=admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "adm_stats")
async def adm_stats_cb(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Нет доступа", show_alert=True); return

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        new_today = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE start_date >= $1", today_start)
        banned = await conn.fetchval("SELECT COUNT(*) FROM users WHERE banned = TRUE")
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance),0) FROM users") or 0
        total_ref = await conn.fetchval("SELECT COUNT(*) FROM referrals")
        ref_paid = await conn.fetchval("SELECT COALESCE(SUM(reward),0) FROM referrals") or 0
        total_tasks = await conn.fetchval("SELECT COUNT(*) FROM tasks")
        tasks_today = await conn.fetchval(
            "SELECT COUNT(*) FROM tasks WHERE done_at >= $1", today_start)
        tasks_paid = await conn.fetchval(
            "SELECT COALESCE(SUM(price),0) FROM tasks WHERE status IN ('active','payout_done')") or 0
        total_promos = await conn.fetchval("SELECT COUNT(*) FROM promotions")
        promos_sum = await conn.fetchval(
            "SELECT COALESCE(SUM(total),0) FROM promotions WHERE status != 'deleted'") or 0
        active_promos = await conn.fetchval(
            "SELECT COUNT(*) FROM promotions WHERE status='active' AND active=TRUE")

    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 Всего юзеров: <b>{total_users}</b>\n"
        f"🆕 Новых за сегодня: <b>{new_today}</b>\n"
        f"🚫 Забанено: <b>{banned}</b>\n\n"
        f"💰 Сумма балансов: <b>{round(total_balance,2)} {CURRENCY}</b>\n"
        f"🔄 Оборот заданий (выдано): <b>{round(tasks_paid,2)} {CURRENCY}</b>\n"
        f"🔄 Оборот продвижений: <b>{round(promos_sum,2)} {CURRENCY}</b>\n\n"
        f"✅ Заданий выполнено всего: <b>{total_tasks}</b>\n"
        f"✅ Заданий за сегодня: <b>{tasks_today}</b>\n"
        f"📈 Активных заданий на продвижение: <b>{active_promos}</b>\n"
        f"📈 Заказов на продвижение всего: <b>{total_promos}</b>\n\n"
        f"🔗 Рефералов всего: <b>{total_ref}</b>\n"
        f"💸 Выплачено за рефералов: <b>{round(ref_paid,2)} {CURRENCY}</b>"
    )
    await callback.message.edit_text(text, reply_markup=back_kb("admin_panel"), parse_mode="HTML")


@dp.callback_query(F.data == "adm_give")
async def adm_give_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌", show_alert=True); return
    await state.set_state(AdminStates.give_id)
    await callback.message.edit_text("💰 <b>Выдать баланс</b>\n\nВведи <b>ID пользователя</b>:",
                                     reply_markup=cancel_kb("admin_panel"), parse_mode="HTML")


@dp.message(AdminStates.give_id)
async def adm_give_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число (ID)"); return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Юзер не найден"); return

    await state.update_data(uid=uid)
    await state.set_state(AdminStates.give_amount)
    await message.answer(
        f"👤 Юзер: <b>{user['username'] or '—'}</b> (<code>{uid}</code>)\n"
        f"💵 Баланс: <b>{round(user['balance'],2)} {CURRENCY}</b>\n\n"
        f"Введи <b>сумму для выдачи</b>:",
        reply_markup=cancel_kb("admin_panel"), parse_mode="HTML")


@dp.message(AdminStates.give_amount)
async def adm_give_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("❌ Введи положительное число"); return

    data = await state.get_data()
    uid = data["uid"]
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                           amount, uid)

    await state.clear()
    await message.answer(f"✅ Выдано <b>{amount} {CURRENCY}</b> юзеру <code>{uid}</code>",
                         reply_markup=back_kb("admin_panel"), parse_mode="HTML")
    try:
        await bot.send_message(uid, f"💰 Тебе начислено <b>{amount} {CURRENCY}</b> от администратора.",
                               parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data == "adm_take")
async def adm_take_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌", show_alert=True); return
    await state.set_state(AdminStates.take_id)
    await callback.message.edit_text("💸 <b>Забрать баланс</b>\n\nВведи <b>ID пользователя</b>:",
                                     reply_markup=cancel_kb("admin_panel"), parse_mode="HTML")


@dp.message(AdminStates.take_id)
async def adm_take_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число"); return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Не найден"); return
    await state.update_data(uid=uid)
    await state.set_state(AdminStates.take_amount)
    await message.answer(
        f"👤 <b>{user['username'] or '—'}</b> (<code>{uid}</code>)\n"
        f"💵 Баланс: <b>{round(user['balance'],2)} {CURRENCY}</b>\n\n"
        f"Сколько забрать?",
        reply_markup=cancel_kb("admin_panel"), parse_mode="HTML")


@dp.message(AdminStates.take_amount)
async def adm_take_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("❌ Введи положительное число"); return

    data = await state.get_data()
    uid = data["uid"]
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2",
                           amount, uid)

    await state.clear()
    await message.answer(f"✅ Списано <b>{amount} {CURRENCY}</b> у <code>{uid}</code>",
                         reply_markup=back_kb("admin_panel"), parse_mode="HTML")
    try:
        await bot.send_message(uid, f"💸 С тебя списано <b>{amount} {CURRENCY}</b> администратором.",
                               parse_mode="HTML")
    except Exception:
        pass


@dp.callback_query(F.data == "adm_ban")
async def adm_ban_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌", show_alert=True); return
    await state.set_state(AdminStates.ban_id)
    await callback.message.edit_text("🚫 <b>Бан</b>\n\nВведи ID пользователя:",
                                     reply_markup=cancel_kb("admin_panel"), parse_mode="HTML")


@dp.message(AdminStates.ban_id)
async def adm_ban_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число"); return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Не найден"); return

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET banned=TRUE, banned_at=$1 WHERE user_id=$2",
                           datetime.now(), uid)

    await state.clear()
    await message.answer(f"🚫 Юзер <code>{uid}</code> забанен.",
                         reply_markup=back_kb("admin_panel"), parse_mode="HTML")
    try:
        await bot.send_message(uid, "🚫 Ты забанен в боте.")
    except Exception:
        pass


@dp.callback_query(F.data == "adm_unban")
async def adm_unban_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌", show_alert=True); return
    await state.set_state(AdminStates.unban_id)
    await callback.message.edit_text("✅ <b>Разбан</b>\n\nВведи ID пользователя:",
                                     reply_markup=cancel_kb("admin_panel"), parse_mode="HTML")


@dp.message(AdminStates.unban_id)
async def adm_unban_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число"); return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Не найден"); return

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET banned=FALSE, banned_at=NULL WHERE user_id=$1", uid)

    await state.clear()
    await message.answer(f"✅ Юзер <code>{uid}</code> разбанен.",
                         reply_markup=back_kb("admin_panel"), parse_mode="HTML")
    try:
        await bot.send_message(uid, "✅ Ты разбанен. Добро пожаловать обратно!")
    except Exception:
        pass


@dp.callback_query(F.data == "adm_info")
async def adm_info_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌", show_alert=True); return
    await state.set_state(AdminStates.info_id)
    await callback.message.edit_text("👤 <b>Инфо о юзере</b>\n\nВведи ID:",
                                     reply_markup=cancel_kb("admin_panel"), parse_mode="HTML")


@dp.message(AdminStates.info_id)
async def adm_info_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число"); return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Не найден"); return

    async with pool.acquire() as conn:
        tasks_cnt = await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE user_id=$1", uid) or 0
        promos_cnt = await conn.fetchval("SELECT COUNT(*) FROM promotions WHERE user_id=$1", uid) or 0

    text = (
        f"👤 <b>Инфо о юзере</b>\n\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📛 Ник: <b>{user['username'] or '—'}</b>\n"
        f"💵 Баланс: <b>{round(user['balance'],2)} {CURRENCY}</b>\n"
        f"📅 Старт: <b>{user['start_date'].strftime('%d.%m.%Y %H:%M')}</b>\n"
        f"✅ Заданий: <b>{tasks_cnt}</b>\n"
        f"📈 Продвижений: <b>{promos_cnt}</b>\n"
        f"👥 Рефералов: <b>{user['ref_count'] or 0}</b>\n"
        f"💸 С рефералов: <b>{round(user['ref_earned'] or 0,2)} {CURRENCY}</b>\n"
        f"🚫 Бан: <b>{'ДА' if user['banned'] else 'нет'}</b>"
    )
    await state.clear()
    await message.answer(text, reply_markup=back_kb("admin_panel"), parse_mode="HTML")


@dp.callback_query(F.data == "adm_broadcast")
async def adm_broadcast_cb(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌", show_alert=True); return
    await state.set_state(AdminStates.broadcast_text)
    await callback.message.edit_text(
        "📢 <b>Рассылка</b>\n\n"
        "Отправь текст сообщения. Поддерживается HTML (<b>, <i>, <code>).",
        reply_markup=cancel_kb("admin_panel"), parse_mode="HTML")


@dp.message(AdminStates.broadcast_text)
async def adm_broadcast_send(message: Message, state: FSMContext):
    text = message.html_text
    await state.clear()

    async with pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users WHERE banned = FALSE")

    await message.answer(f"📢 Начинаю рассылку на <b>{len(users)}</b> юзеров...",
                         parse_mode="HTML")

    ok, fail = 0, 0
    for row in users:
        try:
            await bot.send_message(row["user_id"], text, parse_mode="HTML")
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)

    await message.answer(
        f"✅ <b>Готово!</b>\n\n"
        f"Доставлено: <b>{ok}</b>\n"
        f"Ошибок: <b>{fail}</b>",
        reply_markup=back_kb("admin_panel"), parse_mode="HTML")


# ================== 🔍 ФОНОВАЯ ПРОВЕРКА ==================
async def check_unsubscribes():
    while True:
        try:
            async with pool.acquire() as conn:
                tasks = await conn.fetch("""
                    SELECT t.*, c.chat_id, c.title FROM tasks t
                    JOIN channels c ON c.id = t.channel_id
                    WHERE t.status = 'active' AND t.check_until > NOW()
                """)
            for t in tasks:
                try:
                    m = await bot.get_chat_member(chat_id=t["chat_id"], user_id=t["user_id"])
                    still = m.status in ("member", "administrator", "creator")
                except Exception:
                    still = True

                if not still:
                    async with pool.acquire() as conn:
                        await conn.execute("UPDATE tasks SET status='revoked' WHERE id=$1", t["id"])
                        await conn.execute(
                            "UPDATE users SET balance = balance - $1 WHERE user_id=$2",
                            t["price"], t["user_id"])
                    try:
                        await bot.send_message(
                            t["user_id"],
                            f"⚠️ Ты отписался от <b>{t['title']}</b>.\n"
                            f"Списано <b>{t['price']} {CURRENCY}</b>.",
                            parse_mode="HTML")
                    except Exception:
                        pass
                elif t["check_until"] <= datetime.now():
                    async with pool.acquire() as conn:
                        await conn.execute("UPDATE tasks SET status='payout_done' WHERE id=$1",
                                           t["id"])

            async with pool.acquire() as conn:
                promos = await conn.fetch(
                    "SELECT * FROM promotions WHERE status='active' AND active=TRUE")
            for p in promos:
                if not await bot_is_admin(p["channel_id"]):
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "UPDATE promotions SET active=FALSE, status='paused' WHERE id=$1",
                            p["id"])
                        await conn.execute(
                            "UPDATE channels SET active=FALSE WHERE chat_id=$1",
                            p["channel_id"])
                    try:
                        await bot.send_message(
                            p["user_id"],
                            f"⚠️ Бот удалён из админов <b>{p['channel_title']}</b>.\n"
                            f"Задание остановлено.",
                            parse_mode="HTML")
                    except Exception:
                        pass

            async with pool.acquire() as conn:
                pending = await conn.fetch("""
                    SELECT user_id FROM users
                    WHERE referred_by IS NOT NULL AND ref_rewarded = FALSE
                """)
            for row in pending:
                await try_reward_referrer(row["user_id"])

        except Exception as e:
            print("check_unsubscribes error:", e)

        await asyncio.sleep(6 * 3600)


# ================== ЗАПУСК ==================
async def main():
    await init_db()
    asyncio.create_task(check_unsubscribes())
    print("✅ Бот запущен")
    await dp.start_polling(bot, handle_signals=False)


if __name__ == "__main__":
    asyncio.run(main())
