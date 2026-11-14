import asyncio
import asyncpg
import aiohttp
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
)
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = "9023924024:AAHsVjuygA6foum-kHATKp8TKmGp2Xf2lXI"

DATABASE_URL = "postgresql://neondb_owner:npg_hRwYWpb5C0ry@ep-fancy-shadow-bescuz4s-pooler.c-9.us-east-2.aws.neon.tech/neondb?sslmode=require"

CHANNEL_URL = "https://t.me/WxSubNews"
CHANNEL_ID = -1003725062404
CURRENCY = "WxSub"
HOLD_DAYS = 3
BOT_USERNAME = "WxSubBot"
REFUND_FEE = 0.05
PRIVACY_URL = "https://telegra.ph/WxSub-11-12"

# ================== PIARFLOW ==================
PIARFLOW_API_KEY = "-C1I-pABEtPXlOkp_HnMSSczwbme2e-j"
PIARFLOW_API_URL = "https://piarflow.com/v1"
PIARFLOW_MAX_SPONSORS = 5
PIARFLOW_REWARD = 3000  # 🔥 награда за подписку на спонсора
# ================================================

# ================== БОНУС ==================
BONUS_BASE = 500
BONUS_STEP = 500
BONUS_MAX = 5000
BONUS_COOLDOWN_HOURS = 24

# ================== РЕФЕРАЛКА ==================
REF_REWARD = 10000

# ================== ЛИМИТЫ ==================
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


# ================== PIARFLOW API ==================
async def piarflow_request(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"{PIARFLOW_API_URL}{path}"
    headers = {"Authorization": f"Bearer {PIARFLOW_API_KEY}", "Content-Type": "application/json"}
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, json=payload, headers=headers) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                raise RuntimeError(data.get("message") or f"PiarFlow error {resp.status}")
            return data


async def piarflow_get_sponsors(user_id: int, chat_id: int) -> list[dict]:
    try:
        data = await piarflow_request("POST", "/sponsors", {
            "user_id": user_id, "chat_id": chat_id,
            "max_sponsors": PIARFLOW_MAX_SPONSORS,
        })
        return data.get("sponsors") or []
    except Exception as e:
        print(f"⚠️ PiarFlow /sponsors error: {e}")
        return []


async def piarflow_check_sponsors(user_id: int, links: list[str]) -> list[dict]:
    if not links:
        return []
    try:
        data = await piarflow_request("POST", "/sponsors/check", {
            "user_id": user_id, "links": links
        })
        return data.get("sponsors") or []
    except Exception as e:
        print(f"⚠️ PiarFlow /sponsors/check error: {e}")
        return []


# ================== КЛАВИАТУРЫ ==================
def piarflow_kb(sponsors: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, s in enumerate(sponsors, 1):
        rows.append([InlineKeyboardButton(
            text=f"📢 Спонсор {i}",
            url=s["link"]
        )])
    rows.append([InlineKeyboardButton(text="✅ Проверить", callback_data="piarflow_check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def earn_sponsors_kb(sponsors: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, s in enumerate(sponsors, 1):
        rows.append([InlineKeyboardButton(
            text=f"📢 Спонсор {i} · +{PIARFLOW_REWARD} {CURRENCY}",
            url=s["link"]
        )])
    rows.append([InlineKeyboardButton(text="✅ Проверить", callback_data="earn_piarflow_check")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
        CREATE TABLE IF NOT EXISTS piarflow_rewards (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            link TEXT NOT NULL,
            amount DOUBLE PRECISION NOT NULL,
            rewarded_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, link)
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
        try:
            await conn.execute("DELETE FROM tasks a USING tasks b WHERE a.id > b.id AND a.user_id = b.user_id AND a.channel_id = b.channel_id;")
            await conn.execute("DELETE FROM channels a USING channels b WHERE a.id > b.id AND a.chat_id = b.chat_id;")
            print("🧹 Дубли очищены")
        except Exception as e:
            print(f"⚠️ Очистка: {e}")
        try:
            await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS channels_chat_id_unique ON channels (chat_id);")
            print("✅ Индекс channels")
        except Exception as e:
            print(f"⚠️ Индекс channels: {e}")
        try:
            await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS tasks_user_channel_unique ON tasks (user_id, channel_id);")
            print("✅ Индекс tasks")
        except Exception as e:
            print(f"⚠️ Индекс tasks: {e}")


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


async def give_piarflow_reward(user_id: int, link: str) -> bool:
    """Начисляет 3000 WxSub за выполнение PiarFlow-задания (один раз)."""
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """INSERT INTO piarflow_rewards (user_id, link, amount)
                   VALUES ($1, $2, $3)""",
                user_id, link, PIARFLOW_REWARD
            )
        except asyncpg.exceptions.UniqueViolationError:
            return False  # уже награждали
        await conn.execute(
            "UPDATE users SET balance = balance + $1, tasks_done = tasks_done + 1 WHERE user_id = $2",
            PIARFLOW_REWARD, user_id
        )
    return True


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
        u = await conn.fetchrow("SELECT referred_by, ref_rewarded FROM users WHERE user_id = $1", referred_id)
    if not u or not u["referred_by"] or u["ref_rewarded"]:
        return
    if not await check_subscription(referred_id):
        return
    referrer_id = u["referred_by"]
    async with pool.acquire() as conn:
        updated = await conn.execute("UPDATE users SET ref_rewarded = TRUE WHERE user_id = $1 AND ref_rewarded = FALSE", referred_id)
        if updated == "UPDATE 0":
            return
        await conn.execute("UPDATE users SET balance = balance + $1, ref_count = ref_count + 1, ref_earned = ref_earned + $1 WHERE user_id = $2", REF_REWARD, referrer_id)
        await conn.execute("INSERT INTO referrals (referrer_id, referred_id, reward) VALUES ($1, $2, $3) ON CONFLICT (referred_id) DO NOTHING", referrer_id, referred_id, REF_REWARD)
    try:
        await bot.send_message(referrer_id, f"🎉 <b>Новый реферал!</b>\n\nТвой друг подписался на канал.\n💰 Начислено: <b>{int(REF_REWARD)} {CURRENCY}</b>", parse_mode="HTML")
    except Exception:
        pass


# ================== REPLY-КЛАВИАТУРЫ ==================
def main_menu_kb(user_id: int):
    rows = [
        [KeyboardButton(text="💰 Заработать"), KeyboardButton(text="📈 Продвижение")],
        [KeyboardButton(text="🎁 Ежедневный бонус"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="📜 Политика")]
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton(text="👑 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def earn_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои задания"), KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )


def profile_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои задания"), KeyboardButton(text="🔗 Реф. ссылка")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )


def promote_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Добавить бота в админы")],
            [KeyboardButton(text="📢 Выбрать канал")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )


def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    )


def admin_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="💰 Выдать"), KeyboardButton(text="💸 Забрать")],
            [KeyboardButton(text="🚫 Забанить"), KeyboardButton(text="✅ Разбанить")],
            [KeyboardButton(text="👤 Инфо"), KeyboardButton(text="📢 Рассылка")],
            [KeyboardButton(text="🔙 Назад")]
        ],
        resize_keyboard=True
    )


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


class PiarFlow(StatesGroup):
    waiting_check = State()


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
                ref_exists = await conn.fetchval("SELECT 1 FROM users WHERE user_id = $1", referrer_id)
                if ref_exists:
                    await conn.execute("UPDATE users SET referred_by = $1 WHERE user_id = $2", referrer_id, user_id)
    await try_reward_referrer(user_id)

    # 1. Проверка нашего канала
    if not await check_subscription(user_id):
        await message.answer(
            "🔒 <b>Для использования бота подпишись на наш канал.</b>\n\n"
            "Нажми на кнопку ниже, подпишись и нажми <b>«✅ Проверить»</b>.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Наш канал", url=CHANNEL_URL)],
                [InlineKeyboardButton(text="✅ Проверить", callback_data="check_all_sub")]
            ]),
            parse_mode="HTML"
        )
        return

    # 2. PiarFlow-спонсоры (обязательные)
    sponsors = await piarflow_get_sponsors(user_id, message.chat.id)
    if sponsors:
        await state.set_state(PiarFlow.waiting_check)
        await state.update_data(piarflow_links=[s["link"] for s in sponsors])
        await message.answer(
            "📢 <b>Обязательные задания</b>\n\n"
            "Подпишись на спонсоров и нажми <b>«✅ Проверить»</b> — "
            "иначе бот не откроется.\n\n"
            f"🎁 За каждого спонсора +<b>{PIARFLOW_REWARD} {CURRENCY}</b>",
            reply_markup=piarflow_kb(sponsors),
            parse_mode="HTML"
        )
        return

    # 3. Меню
    await message.answer(
        f"👋 Привет, <b>{username}</b>!\n\nДобро пожаловать в <b>WxSub</b>.\nВыбери действие 👇",
        reply_markup=main_menu_kb(user_id), parse_mode="HTML"
    )


# ================== ✅ Проверка нашего канала ==================
@dp.callback_query(F.data == "check_all_sub")
async def check_all_sub_cb(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id

    if not await check_subscription(uid):
        await callback.answer("❌ Ты ещё не подписался на наш канал!", show_alert=True)
        return

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET subscribed = TRUE WHERE user_id = $1", uid)
    await try_reward_referrer(uid)

    sponsors = await piarflow_get_sponsors(uid, callback.message.chat.id)
    if sponsors:
        await state.set_state(PiarFlow.waiting_check)
        await state.update_data(piarflow_links=[s["link"] for s in sponsors])
        await callback.message.edit_text(
            "📢 <b>Обязательные задания</b>\n\n"
            "Подпишись на спонсоров и нажми <b>«✅ Проверить»</b> — "
            "иначе бот не откроется.\n\n"
            f"🎁 За каждого спонсора +<b>{PIARFLOW_REWARD} {CURRENCY}</b>",
            reply_markup=piarflow_kb(sponsors),
            parse_mode="HTML"
        )
        return

    await state.clear()
    await callback.message.edit_text(
        "✅ <b>Подписка подтверждена!</b>\n\n"
        "Спасибо за подписку и использование бота <b>WxSub</b>! 🎉\n\n"
        "📜 <b>Рекомендуем ознакомиться с Политикой конфиденциальности:</b>\n\n"
        "👉 Нажми на <b>бота</b> → <b>три точки</b> → <b>«Политика конфиденциальности»</b>.\n\n"
        "Либо отправь /privacy\n\n"
        "Приятного пользования! 💚",
        parse_mode="HTML"
    )
    await callback.message.answer("🏠 Главное меню:", reply_markup=main_menu_kb(uid))


# ================== ✅ Проверка PiarFlow (обязательные при старте) ==================
@dp.callback_query(F.data == "piarflow_check")
async def piarflow_check_cb(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()
    links = data.get("piarflow_links") or []

    if not links:
        await callback.answer("Сессия истекла. Напиши /start", show_alert=True)
        return

    sponsors = await piarflow_check_sponsors(user_id, links)
    not_subscribed = [s for s in sponsors if s.get("status") != "subscribed"]
    subscribed = [s for s in sponsors if s.get("status") == "subscribed"]

    # 🔥 начисляем награду за каждого выполненного
    rewarded = 0
    for s in subscribed:
        if await give_piarflow_reward(user_id, s["link"]):
            rewarded += 1

    if not not_subscribed:
        await state.clear()
        text = "✅ <b>Все задания выполнены!</b>\n\n"
        if rewarded:
            text += f"🎁 Начислено: <b>{rewarded * PIARFLOW_REWARD} {CURRENCY}</b>\n\n"
        text += "Добро пожаловать в главное меню."
        await callback.message.edit_text(text, parse_mode="HTML")
        await callback.message.answer("🏠 Главное меню:", reply_markup=main_menu_kb(user_id))
    else:
        await state.update_data(piarflow_links=[s["link"] for s in not_subscribed])
        text = "❌ <b>Не все задания выполнены</b>\n\n"
        if rewarded:
            text += f"🎁 Начислено за выполненных: <b>{rewarded * PIARFLOW_REWARD} {CURRENCY}</b>\n\n"
        text += "Подпишись на оставшихся спонсоров и снова нажми «✅ Проверить»."
        await callback.message.edit_text(
            text,
            reply_markup=piarflow_kb(not_subscribed),
            parse_mode="HTML"
        )
    await callback.answer()


# ================== 🔙 Назад ==================
@dp.message(F.text == "🔙 Назад")
async def back_handler(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await message.answer("🏠 <b>Главное меню</b>", reply_markup=main_menu_kb(user_id), parse_mode="HTML")


@dp.message(F.text == "❌ Отмена")
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    await message.answer("❌ Отменено. Главное меню:", reply_markup=main_menu_kb(user_id))


@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer("🏠 <b>Главное меню</b>",
                                   reply_markup=main_menu_kb(callback.from_user.id), parse_mode="HTML")
    await callback.answer()


# ================== 📜 Политика ==================
@dp.message(F.text == "📜 Политика")
@dp.message(Command("privacy"))
async def privacy_handler(message: Message):
    await message.answer(
        "📜 <b>Политика конфиденциальности и правила WxSub</b>\n\n"
        f"📖 {PRIVACY_URL}\n\n"
        "Нажми на ссылку, чтобы открыть правила.",
        parse_mode="HTML", disable_web_page_preview=False
    )


# ================== 💰 Заработать ==================
@dp.message(F.text == "💰 Заработать")
async def earn_handler(message: Message, state: FSMContext):
    user_id = message.from_user.id

    # 🔥 PiarFlow-спонсоры как задания
    sponsors = await piarflow_get_sponsors(user_id, message.chat.id)

    # Внутренние задания (твои каналы)
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT c.* FROM channels c
            WHERE c.active = TRUE AND c.price > 0
              AND (c.limit_total = 0 OR c.done_count < c.limit_total)
              AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.user_id = $1 AND t.channel_id = c.id)
            ORDER BY c.price DESC LIMIT 20
        """, user_id)

    if not sponsors and not rows:
        await message.answer("💰 <b>Заработать</b>\n\nПока нет доступных заданий. Заходи позже!",
                             reply_markup=earn_kb(), parse_mode="HTML")
        return

    text = "💰 <b>Доступные задания</b>\n\n"

    # PiarFlow-блок
    if sponsors:
        text += "🔥 <b>Спонсоры PiarFlow:</b>\n"
        text += f"За каждого спонсора +<b>{PIARFLOW_REWARD} {CURRENCY}</b>\n"
        for i, s in enumerate(sponsors, 1):
            text += f"• Спонсор {i}\n"
        text += "\n👉 Подпишись и нажми «✅ Проверить»\n\n"

    # Внутренние задания
    if rows:
        text += "💎 <b>Задания WxSub:</b>\n"
        for r in rows:
            left = "∞" if r["limit_total"] == 0 else str(r["limit_total"] - r["done_count"])
            text += f"• <b>{r['title']}</b> — {r['price']} {CURRENCY} (осталось: {left})\n"

    # Кнопки
    kb_rows = []
    if sponsors:
        for i, s in enumerate(sponsors, 1):
            kb_rows.append([InlineKeyboardButton(
                text=f"📢 Спонсор {i} · +{PIARFLOW_REWARD} {CURRENCY}",
                url=s["link"]
            )])
        kb_rows.append([InlineKeyboardButton(text="✅ Проверить спонсоров", callback_data="earn_piarflow_check")])

    for r in rows:
        kb_rows.append([
            InlineKeyboardButton(text=f"{r['title']} | {r['price']} {CURRENCY}", url=r["link"]),
            InlineKeyboardButton(text="✅ Проверить", callback_data=f"check_task_{r['id']}")
        ])

    kb_rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")])

    await message.answer(text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML", disable_web_page_preview=True)


# ================== ✅ Проверка PiarFlow в «Заработать» (с наградой) ==================
@dp.callback_query(F.data == "earn_piarflow_check")
async def earn_piarflow_check_cb(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    # получаем заново актуальных спонсоров
    sponsors = await piarflow_get_sponsors(user_id, callback.message.chat.id)
    if not sponsors:
        await callback.answer("Нет активных спонсоров", show_alert=True)
        return

    links = [s["link"] for s in sponsors]
    result = await piarflow_check_sponsors(user_id, links)
    subscribed = [s for s in result if s.get("status") == "subscribed"]
    not_subscribed = [s for s in result if s.get("status") != "subscribed"]

    # 🔥 Начисляем награду за каждого выполненного
    rewarded = 0
    total_reward = 0
    for s in subscribed:
        if await give_piarflow_reward(user_id, s["link"]):
            rewarded += 1
            total_reward += PIARFLOW_REWARD

    if rewarded:
        text = f"✅ <b>Задания выполнены!</b>\n\n"
        text += f"🎁 Начислено: <b>{total_reward} {CURRENCY}</b>\n"
        text += f"📊 Выполнено спонсоров: <b>{rewarded}</b>\n\n"
    else:
        text = "ℹ️ <b>Новых выполненных заданий нет</b>\n\n"

    if not_subscribed:
        text += "❌ Осталось подписаться:\n"
        for i, s in enumerate(not_subscribed, 1):
            text += f"• Спонсор {i}\n"
        text += "\nПодпишись и снова нажми «✅ Проверить»."

        kb_rows = []
        for i, s in enumerate(not_subscribed, 1):
            kb_rows.append([InlineKeyboardButton(
                text=f"📢 Спонсор {i} · +{PIARFLOW_REWARD} {CURRENCY}",
                url=s["link"]
            )])
        kb_rows.append([InlineKeyboardButton(text="✅ Проверить", callback_data="earn_piarflow_check")])
        kb_rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")])

        await callback.message.answer(text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
            parse_mode="HTML")
    else:
        text += "🏆 Все спонсоры выполнены!\n\nВозвращайся позже за новыми."
        await callback.message.answer(text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ В меню", callback_data="main_menu")]
            ]),
            parse_mode="HTML")

    await callback.answer()


# ================== ✅ Проверка внутренних заданий ==================
@dp.callback_query(F.data.startswith("check_task_"))
async def check_task_cb(callback: CallbackQuery):
    ch_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id

    async with pool.acquire() as conn:
        ch = await conn.fetchrow("SELECT * FROM channels WHERE id = $1", ch_id)
        if not ch:
            await callback.answer("Канал не найден", show_alert=True); return

        existing = await conn.fetchrow(
            "SELECT * FROM tasks WHERE user_id=$1 AND channel_id=$2",
            user_id, ch_id)
        if existing:
            await callback.answer("❌ Ты уже выполнял это задание раньше", show_alert=True); return

        if ch["limit_total"] > 0 and ch["done_count"] >= ch["limit_total"]:
            await callback.answer("❌ Задание уже выполнено другими", show_alert=True); return

    try:
        m = await bot.get_chat_member(chat_id=ch["chat_id"], user_id=user_id)
        ok = m.status in ("member", "administrator", "creator")
    except Exception:
        ok = False

    if not ok:
        await callback.answer("❌ Ты ещё не подписался на канал!", show_alert=True); return

    until = datetime.now() + timedelta(days=HOLD_DAYS)
    try:
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
    except asyncpg.exceptions.UniqueViolationError:
        await callback.answer("❌ Ты уже выполнял это задание раньше", show_alert=True)
        return

    await callback.message.answer(
        f"✅ <b>Задание выполнено!</b>\n\n"
        f"Вам начислено <b>{ch['price']} {CURRENCY}</b>.\n\n"
        f"⚠️ Не отписывайтесь от канала в течение <b>{HOLD_DAYS} дней</b>.",
        parse_mode="HTML")


# ================== 📈 Продвижение ==================
@dp.message(F.text == "📈 Продвижение")
async def promote_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "📈 <b>Продвижение</b>\n\n"
        f"1️⃣ Добавь бота в админы: @{BOT_USERNAME}\n"
        "2️⃣ Нажми «📢 Выбрать канал»\n"
        f"3️⃣ Цена: {MIN_PRICE}–{MAX_PRICE} {CURRENCY}",
        reply_markup=promote_kb(), parse_mode="HTML"
    )


@dp.message(F.text == "➕ Добавить бота в админы")
async def add_bot_admin_handler(message: Message):
    await message.answer(
        f"➕ Открой свой канал → Управление → Администраторы → Добавить\n\n"
        f"Username бота: @{BOT_USERNAME}\n\n"
        "Дай права: «Добавлять участников» + «Пригласительные ссылки»",
        reply_markup=promote_kb()
    )


@dp.message(F.text == "📢 Выбрать канал")
async def promo_pick_handler(message: Message, state: FSMContext):
    await state.update_data(promo_step="waiting_link")
    await message.answer(
        "📢 Отправь ссылку на канал:\n\n"
        "Формат: <code>@username</code> или <code>https://t.me/username</code>",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


@dp.message(
    F.text.startswith("@") |
    F.text.startswith("https://t.me/") |
    F.text.startswith("t.me/")
)
async def on_channel_link(message: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("promo_step") != "waiting_link":
        return
    await state.update_data(promo_step=None)

    link = message.text.strip()
    username = link.split("/")[-1].replace("@", "")
    chat_id_str = f"@{username}"

    try:
        chat = await bot.get_chat(chat_id_str)
    except Exception:
        await message.answer("❌ Не могу получить доступ к каналу.", reply_markup=promote_kb())
        return

    if not await bot_is_admin(chat.id):
        await message.answer(
            f"❌ <b>Бот не админ канала</b>\n\nДобавь @{BOT_USERNAME} в админы.",
            reply_markup=promote_kb(), parse_mode="HTML")
        return

    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO channels (owner_id, chat_id, title, link, price, limit_total, active)
            VALUES ($1,$2,$3,$4,0,0,FALSE)
            ON CONFLICT (chat_id) DO UPDATE
            SET owner_id = EXCLUDED.owner_id, title = EXCLUDED.title, link = EXCLUDED.link
        """, message.from_user.id, chat.id, chat.title, f"https://t.me/{username}")

    await state.update_data(promo_chat_id=chat.id, promo_title=chat.title)
    await state.set_state(Promo.waiting_price)
    await message.answer(
        f"✅ Канал <b>{chat.title}</b> подтверждён.\n\n"
        f"💵 Введи цену за 1 подписчика ({MIN_PRICE}–{MAX_PRICE}):",
        reply_markup=cancel_kb(), parse_mode="HTML"
    )


@dp.message(Promo.waiting_price)
async def promo_price(message: Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer(f"❌ Введи число от {MIN_PRICE} до {MAX_PRICE}")
        return

    if price < MIN_PRICE or price > MAX_PRICE:
        await message.answer(f"❌ Цена от {MIN_PRICE} до {MAX_PRICE}. Попробуй снова:")
        return

    await state.update_data(price=price)
    await state.set_state(Promo.waiting_count)
    await message.answer(f"💵 Цена: <b>{price} {CURRENCY}</b>\n\nСколько подписчиков?",
                         reply_markup=cancel_kb(), parse_mode="HTML")


@dp.message(Promo.waiting_count)
async def promo_count(message: Message, state: FSMContext):
    try:
        cnt = int(message.text)
        if cnt < 1: raise ValueError
    except ValueError:
        await message.answer("❌ Введи целое число ≥ 1")
        return

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
        f"Твой баланс: <b>{round(user['balance'],2)} {CURRENCY}</b>\n\n"
        "Напиши «✅ Подтвердить» или «❌ Отмена»"
    )
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Подтвердить"), KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True
    ), parse_mode="HTML")


@dp.message(F.text == "✅ Подтвердить")
async def promo_confirm_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    user = await get_user(user_id)

    if not data.get("total"):
        await message.answer("Сессия истекла", reply_markup=main_menu_kb(user_id))
        await state.clear()
        return
    if user["balance"] < data["total"]:
        await message.answer(
            f"❌ Нужно {data['total']} {CURRENCY}, у тебя {round(user['balance'],2)}",
            reply_markup=main_menu_kb(user_id))
        await state.clear()
        return

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", data["total"], user_id)
        await conn.execute("""
            INSERT INTO promotions (user_id, channel_id, channel_title, price_per, count, total, status, active)
            VALUES ($1,$2,$3,$4,$5,$6,'active',TRUE)
        """, user_id, data["promo_chat_id"], data["promo_title"], data["price"], data["count"], data["total"])
        await conn.execute("""
            UPDATE channels SET price = $1, limit_total = $2, done_count = 0, active = TRUE WHERE chat_id = $3
        """, data["price"], data["count"], data["promo_chat_id"])

    await state.clear()
    await message.answer(
        f"✅ <b>Заказ оформлен!</b>\n\n"
        f"📢 {data['promo_title']}\n"
        f"👥 {data['count']} × {data['price']} {CURRENCY}\n"
        f"💰 Оплачено: <b>{data['total']} {CURRENCY}</b>",
        reply_markup=main_menu_kb(user_id), parse_mode="HTML"
    )


# ================== 🎁 Бонус ==================
def bonus_for_streak(streak: int) -> float:
    if streak <= 0: streak = 1
    return float(min(BONUS_BASE + (streak - 1) * BONUS_STEP, BONUS_MAX))


@dp.message(F.text == "🎁 Ежедневный бонус")
async def daily_bonus_handler(message: Message):
    user_id = message.from_user.id
    user = await get_user(user_id)
    if not user:
        await message.answer("Напиши /start", reply_markup=main_menu_kb(user_id))
        return

    now = datetime.now()
    last = user["last_bonus"]
    streak = user["bonus_streak"] or 0

    if last:
        diff = now - last
        if diff < timedelta(hours=BONUS_COOLDOWN_HOURS):
            left = timedelta(hours=BONUS_COOLDOWN_HOURS) - diff
            await message.answer(
                f"⏳ Следующий бонус через {left.seconds//3600} ч {(left.seconds%3600)//60} мин",
                reply_markup=main_menu_kb(user_id))
            return
        if diff >= timedelta(hours=BONUS_COOLDOWN_HOURS * 2):
            streak = 0

    new_streak = streak + 1
    amount = bonus_for_streak(new_streak)

    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1, bonus_streak = $2, last_bonus = $3 WHERE user_id = $4",
                           amount, new_streak, now, user_id)

    next_amount = bonus_for_streak(new_streak + 1)
    is_max = amount >= BONUS_MAX

    text = (
        "🎁 <b>Ежедневный бонус получен!</b>\n\n"
        f"🔥 Серия: <b>{new_streak}</b> дн.\n"
        f"💰 Начислено: <b>{int(amount)} {CURRENCY}</b>\n\n"
    )
    if is_max:
        text += f"🏆 Максимум — <b>{int(BONUS_MAX)} {CURRENCY}</b> каждый день!"
    else:
        text += f"📅 Завтра: <b>{int(next_amount)} {CURRENCY}</b>"

    await message.answer(text, reply_markup=main_menu_kb(user_id), parse_mode="HTML")


# ================== 👤 Профиль ==================
@dp.message(F.text == "👤 Профиль")
async def profile_handler(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Напиши /start", reply_markup=main_menu_kb(message.from_user.id))
        return

    text = (
        "👤 <b>Профиль</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📛 Ник: <b>{user['username'] or '—'}</b>\n"
        f"💵 Баланс: <b>{round(user['balance'],2)} {CURRENCY}</b>\n"
        f"📅 Дата старта: <b>{user['start_date'].strftime('%d.%m.%Y %H:%M')}</b>\n"
        f"✅ Заданий: <b>{user['tasks_done']}</b>\n\n"
        f"👥 Рефералов: <b>{user['ref_count'] or 0}</b>\n"
        f"💸 С рефералов: <b>{round(user['ref_earned'] or 0, 2)} {CURRENCY}</b>"
    )
    await message.answer(text, reply_markup=profile_kb(), parse_mode="HTML")


@dp.message(F.text == "🔗 Реф. ссылка")
async def ref_link_handler(message: Message):
    user = await get_user(message.from_user.id)
    if not user:
        await message.answer("Напиши /start")
        return
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user['user_id']}"
    await message.answer(
        f"🔗 <b>Твоя реферальная ссылка:</b>\n\n<code>{link}</code>\n\n"
        f"🎁 За каждого друга — <b>{int(REF_REWARD)} {CURRENCY}</b>\n\n"
        f"👥 Приглашено: <b>{user['ref_count'] or 0}</b>\n"
        f"💰 Заработано: <b>{round(user['ref_earned'] or 0, 2)} {CURRENCY}</b>",
        reply_markup=profile_kb(), parse_mode="HTML"
    )


# ================== 📋 Мои задания ==================
@dp.message(F.text == "📋 Мои задания")
async def my_tasks_handler(message: Message):
    user_id = message.from_user.id
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT * FROM promotions WHERE user_id = $1 AND status IN ('active','paused')
            ORDER BY created_at DESC
        """, user_id)

    if not rows:
        await message.answer("📋 <b>Мои задания</b>\n\nНет активных заданий.",
                             reply_markup=profile_kb(), parse_mode="HTML")
        return

    text = "📋 <b>Мои задания</b>\n\n"
    for r in rows:
        left = r["count"] - r["done_count"]
        icon = "▶️" if r["active"] else "⏸"
        text += (
            f"{icon} <b>{r['channel_title']}</b>\n"
            f"   💵 {r['price_per']} × {r['count']} "
            f"(выполнено {r['done_count']}, осталось {left})\n"
            f"   💰 Оплачено: {r['total']} {CURRENCY}\n"
            f"   ID: <code>{r['id']}</code>\n\n"
        )
    text += (
        "Команды:\n"
        "• <code>/pause ID</code> — остановить\n"
        "• <code>/resume ID</code> — продолжить\n"
        "• <code>/delete ID</code> — удалить (возврат -5%)\n"
        "• <code>/add ID КОЛ-ВО</code> — добавить выполнений"
    )
    await message.answer(text, reply_markup=profile_kb(), parse_mode="HTML")


@dp.message(Command("pause"))
async def pause_task(message: Message, command: CommandObject):
    try:
        pid = int(command.args)
    except (ValueError, TypeError):
        await message.answer("Формат: /pause ID", reply_markup=profile_kb())
        return
    async with pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id = $1", pid)
    if not promo or promo["user_id"] != message.from_user.id:
        await message.answer("❌ Не найдено", reply_markup=profile_kb())
        return
    async with pool.acquire() as conn:
        await conn.execute("UPDATE promotions SET active=FALSE, status='paused' WHERE id=$1", pid)
        await conn.execute("UPDATE channels SET active=FALSE WHERE chat_id=$1 AND owner_id=$2",
                           promo["channel_id"], message.from_user.id)
    await message.answer("⏸ Остановлено", reply_markup=profile_kb())


@dp.message(Command("resume"))
async def resume_task(message: Message, command: CommandObject):
    try:
        pid = int(command.args)
    except (ValueError, TypeError):
        await message.answer("Формат: /resume ID", reply_markup=profile_kb())
        return
    async with pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id = $1", pid)
    if not promo or promo["user_id"] != message.from_user.id:
        await message.answer("❌ Не найдено", reply_markup=profile_kb())
        return
    if not await bot_is_admin(promo["channel_id"]):
        await message.answer("❌ Бот не админ канала. Добавь в админы.", reply_markup=profile_kb())
        return
    async with pool.acquire() as conn:
        await conn.execute("UPDATE promotions SET active=TRUE, status='active' WHERE id=$1", pid)
        await conn.execute("UPDATE channels SET active=TRUE WHERE chat_id=$1 AND owner_id=$2",
                           promo["channel_id"], message.from_user.id)
    await message.answer("▶️ Активировано", reply_markup=profile_kb())


@dp.message(Command("delete"))
async def delete_task(message: Message, command: CommandObject):
    try:
        pid = int(command.args)
    except (ValueError, TypeError):
        await message.answer("Формат: /delete ID", reply_markup=profile_kb())
        return
    async with pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id = $1", pid)
    if not promo or promo["user_id"] != message.from_user.id:
        await message.answer("❌ Не найдено", reply_markup=profile_kb())
        return
    left = max(promo["count"] - promo["done_count"], 0)
    refund = round(left * promo["price_per"] * (1 - REFUND_FEE), 2)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                           refund, message.from_user.id)
        await conn.execute("UPDATE promotions SET status='deleted', active=FALSE WHERE id=$1", pid)
        await conn.execute("UPDATE channels SET active=FALSE WHERE chat_id=$1 AND owner_id=$2",
                           promo["channel_id"], message.from_user.id)
    await message.answer(
        f"✅ Удалено.\n💰 Возвращено: <b>{refund} {CURRENCY}</b> (с учётом 5%)",
        reply_markup=profile_kb(), parse_mode="HTML")


@dp.message(Command("add"))
async def add_task_count(message: Message, command: CommandObject):
    try:
        parts = command.args.split()
        pid, cnt = int(parts[0]), int(parts[1])
        if cnt < 1: raise ValueError
    except (ValueError, TypeError, IndexError):
        await message.answer("Формат: /add ID КОЛ-ВО", reply_markup=profile_kb())
        return
    async with pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id=$1", pid)
    if not promo or promo["user_id"] != message.from_user.id:
        await message.answer("❌ Не найдено", reply_markup=profile_kb())
        return
    price = promo["price_per"]
    if price < MIN_PRICE or price > MAX_PRICE:
        await message.answer(f"❌ Цена задания вне {MIN_PRICE}–{MAX_PRICE}", reply_markup=profile_kb())
        return
    total = round(price * cnt, 2)
    user = await get_user(message.from_user.id)
    if user["balance"] < total:
        await message.answer(f"❌ Нужно {total} {CURRENCY}, у тебя {round(user['balance'],2)}",
                             reply_markup=profile_kb())
        return
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2",
                           total, message.from_user.id)
        await conn.execute("UPDATE promotions SET count = count + $1, total = total + $2 WHERE id = $3",
                           cnt, total, pid)
        promo = await conn.fetchrow("SELECT * FROM promotions WHERE id=$1", pid)
        await conn.execute("UPDATE channels SET limit_total = $1 WHERE chat_id=$2",
                           promo["count"], promo["channel_id"])
    await message.answer(f"✅ Добавлено <b>{cnt}</b>.\nСписано: <b>{total} {CURRENCY}</b>",
                         reply_markup=profile_kb(), parse_mode="HTML")


# ================== 👑 Админка ==================
@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_kb(), parse_mode="HTML")


@dp.message(F.text == "👑 Админ-панель")
async def admin_panel_handler(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=admin_kb(), parse_mode="HTML")


@dp.message(F.text == "📊 Статистика")
async def adm_stats_handler(message: Message):
    if not is_admin(message.from_user.id):
        return
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    async with pool.acquire() as conn:
        total_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        new_today = await conn.fetchval("SELECT COUNT(*) FROM users WHERE start_date >= $1", today)
        banned = await conn.fetchval("SELECT COUNT(*) FROM users WHERE banned = TRUE")
        total_balance = await conn.fetchval("SELECT COALESCE(SUM(balance),0) FROM users") or 0
        total_ref = await conn.fetchval("SELECT COUNT(*) FROM referrals")
        ref_paid = await conn.fetchval("SELECT COALESCE(SUM(reward),0) FROM referrals") or 0
        total_tasks = await conn.fetchval("SELECT COUNT(*) FROM tasks")
        tasks_today = await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE done_at >= $1", today)
        tasks_paid = await conn.fetchval("SELECT COALESCE(SUM(price),0) FROM tasks WHERE status IN ('active','payout_done')") or 0
        total_promos = await conn.fetchval("SELECT COUNT(*) FROM promotions")
        promos_sum = await conn.fetchval("SELECT COALESCE(SUM(total),0) FROM promotions WHERE status != 'deleted'") or 0
        piarflow_rewards = await conn.fetchval("SELECT COUNT(*) FROM piarflow_rewards")
        piarflow_sum = await conn.fetchval("SELECT COALESCE(SUM(amount),0) FROM piarflow_rewards") or 0

    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Всего: <b>{total_users}</b>\n"
        f"🆕 Сегодня: <b>{new_today}</b>\n"
        f"🚫 Бан: <b>{banned}</b>\n\n"
        f"💰 Балансы: <b>{round(total_balance,2)} {CURRENCY}</b>\n"
        f"🔄 Заданий выдано: <b>{round(tasks_paid,2)} {CURRENCY}</b>\n"
        f"🔄 Продвижений: <b>{round(promos_sum,2)} {CURRENCY}</b>\n"
        f"🔥 PiarFlow наград: <b>{piarflow_rewards}</b> на <b>{round(piarflow_sum,2)} {CURRENCY}</b>\n\n"
        f"✅ Заданий всего: <b>{total_tasks}</b>\n"
        f"✅ Сегодня: <b>{tasks_today}</b>\n"
        f"📈 Продвижений: <b>{total_promos}</b>\n\n"
        f"🔗 Рефералов: <b>{total_ref}</b>\n"
        f"💸 Выплачено: <b>{round(ref_paid,2)} {CURRENCY}</b>"
    )
    await message.answer(text, reply_markup=admin_kb(), parse_mode="HTML")


@dp.message(F.text == "💰 Выдать")
async def adm_give_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.give_id)
    await message.answer("💰 Введи ID пользователя:", reply_markup=cancel_kb())


@dp.message(AdminStates.give_id)
async def adm_give_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число")
        return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Не найден")
        return
    await state.update_data(uid=uid)
    await state.set_state(AdminStates.give_amount)
    await message.answer(
        f"👤 <b>{user['username'] or '—'}</b> (<code>{uid}</code>)\n"
        f"💵 {round(user['balance'],2)} {CURRENCY}\n\nСумма?",
        reply_markup=cancel_kb(), parse_mode="HTML")


@dp.message(AdminStates.give_amount)
async def adm_give_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("❌ Введи положительное число")
        return
    data = await state.get_data()
    uid = data["uid"]
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", amount, uid)
    await state.clear()
    await message.answer(f"✅ Выдано {amount} {CURRENCY} → <code>{uid}</code>",
                         reply_markup=admin_kb(), parse_mode="HTML")
    try:
        await bot.send_message(uid, f"💰 Тебе начислено <b>{amount} {CURRENCY}</b>", parse_mode="HTML")
    except Exception:
        pass


@dp.message(F.text == "💸 Забрать")
async def adm_take_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.take_id)
    await message.answer("💸 Введи ID пользователя:", reply_markup=cancel_kb())


@dp.message(AdminStates.take_id)
async def adm_take_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число")
        return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Не найден")
        return
    await state.update_data(uid=uid)
    await state.set_state(AdminStates.take_amount)
    await message.answer(
        f"👤 <b>{user['username'] or '—'}</b> (<code>{uid}</code>)\n"
        f"💵 {round(user['balance'],2)} {CURRENCY}\n\nСколько забрать?",
        reply_markup=cancel_kb(), parse_mode="HTML")


@dp.message(AdminStates.take_amount)
async def adm_take_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("❌ Введи положительное число")
        return
    data = await state.get_data()
    uid = data["uid"]
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", amount, uid)
    await state.clear()
    await message.answer(f"✅ Списано {amount} {CURRENCY} у <code>{uid}</code>",
                         reply_markup=admin_kb(), parse_mode="HTML")
    try:
        await bot.send_message(uid, f"💸 С тебя списано <b>{amount} {CURRENCY}</b>", parse_mode="HTML")
    except Exception:
        pass


@dp.message(F.text == "🚫 Забанить")
async def adm_ban_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.ban_id)
    await message.answer("🚫 ID пользователя:", reply_markup=cancel_kb())


@dp.message(AdminStates.ban_id)
async def adm_ban_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число")
        return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Не найден")
        return
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET banned=TRUE, banned_at=$1 WHERE user_id=$2",
                           datetime.now(), uid)
    await state.clear()
    await message.answer(f"🚫 Забанен <code>{uid}</code>", reply_markup=admin_kb(), parse_mode="HTML")
    try:
        await bot.send_message(uid, "🚫 Ты забанен в боте.")
    except Exception:
        pass


@dp.message(F.text == "✅ Разбанить")
async def adm_unban_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.unban_id)
    await message.answer("✅ ID пользователя:", reply_markup=cancel_kb())


@dp.message(AdminStates.unban_id)
async def adm_unban_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число")
        return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Не найден")
        return
    async with pool.acquire() as conn:
        await conn.execute("UPDATE users SET banned=FALSE, banned_at=NULL WHERE user_id=$1", uid)
    await state.clear()
    await message.answer(f"✅ Разбанен <code>{uid}</code>", reply_markup=admin_kb(), parse_mode="HTML")


@dp.message(F.text == "👤 Инфо")
async def adm_info_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.info_id)
    await message.answer("👤 ID пользователя:", reply_markup=cancel_kb())


@dp.message(AdminStates.info_id)
async def adm_info_id(message: Message, state: FSMContext):
    try:
        uid = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи число")
        return
    user = await get_user(uid)
    if not user:
        await message.answer("❌ Не найден")
        return
    async with pool.acquire() as conn:
        tasks_cnt = await conn.fetchval("SELECT COUNT(*) FROM tasks WHERE user_id=$1", uid) or 0
        promos_cnt = await conn.fetchval("SELECT COUNT(*) FROM promotions WHERE user_id=$1", uid) or 0
        pf_cnt = await conn.fetchval("SELECT COUNT(*) FROM piarflow_rewards WHERE user_id=$1", uid) or 0
    text = (
        f"👤 <b>Инфо</b>\n\n"
        f"🆔 <code>{uid}</code>\n"
        f"📛 <b>{user['username'] or '—'}</b>\n"
        f"💵 {round(user['balance'],2)} {CURRENCY}\n"
        f"📅 {user['start_date'].strftime('%d.%m.%Y %H:%M')}\n"
        f"✅ Заданий: {tasks_cnt}\n"
        f"📈 Продвижений: {promos_cnt}\n"
        f"🔥 PiarFlow: {pf_cnt}\n"
        f"👥 Рефералов: {user['ref_count'] or 0}\n"
        f"🚫 Бан: {'ДА' if user['banned'] else 'нет'}"
    )
    await state.clear()
    await message.answer(text, reply_markup=admin_kb(), parse_mode="HTML")


@dp.message(F.text == "📢 Рассылка")
async def adm_broadcast_handler(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AdminStates.broadcast_text)
    await message.answer("📢 Отправь текст для рассылки:", reply_markup=cancel_kb())


@dp.message(AdminStates.broadcast_text)
async def adm_broadcast_send(message: Message, state: FSMContext):
    text = message.html_text
    await state.clear()
    async with pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users WHERE banned = FALSE")
    await message.answer(f"📢 Рассылка на {len(users)} юзеров...", reply_markup=admin_kb())
    ok, fail = 0, 0
    for row in users:
        try:
            await bot.send_message(row["user_id"], text, parse_mode="HTML")
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Готово!\nДоставлено: {ok}\nОшибок: {fail}", reply_markup=admin_kb())


# ================== 🔍 Фоновая проверка ==================
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
                        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id=$2",
                                           t["price"], t["user_id"])
                    try:
                        await bot.send_message(t["user_id"],
                            f"⚠️ Ты отписался от <b>{t['title']}</b>.\nСписано <b>{t['price']} {CURRENCY}</b>.",
                            parse_mode="HTML")
                    except Exception:
                        pass
                elif t["check_until"] <= datetime.now():
                    async with pool.acquire() as conn:
                        await conn.execute("UPDATE tasks SET status='payout_done' WHERE id=$1", t["id"])

            async with pool.acquire() as conn:
                promos = await conn.fetch("SELECT * FROM promotions WHERE status='active' AND active=TRUE")
            for p in promos:
                if not await bot_is_admin(p["channel_id"]):
                    async with pool.acquire() as conn:
                        await conn.execute("UPDATE promotions SET active=FALSE, status='paused' WHERE id=$1", p["id"])
                        await conn.execute("UPDATE channels SET active=FALSE WHERE chat_id=$1", p["channel_id"])
                    try:
                        await bot.send_message(p["user_id"],
                            f"⚠️ Бот удалён из админов <b>{p['channel_title']}</b>.\nЗадание остановлено.",
                            parse_mode="HTML")
                    except Exception:
                        pass

            async with pool.acquire() as conn:
                pending = await conn.fetch("SELECT user_id FROM users WHERE referred_by IS NOT NULL AND ref_rewarded = FALSE")
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
