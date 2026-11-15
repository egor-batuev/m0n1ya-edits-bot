import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from decimal import Decimal

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

# ---------- Настройки (секреты берутся из переменных Railway) ----------
TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])  # твой числовой Telegram ID (@userinfobot)

REVIEWS_URL = os.getenv("REVIEWS_URL") or "https://t.me/m0n1ya_edit_reviews"
PORTFOLIO_URL = "https://www.tiktok.com/@moln1ya_3d1ts_1"

# Оплата криптой через 2328.io (если переменных нет, кнопка крипты не показывается)
P_UID = os.getenv("PAY2328_UID", "")      # ID проекта
P_KEY = os.getenv("PAY2328_KEY", "")      # API-ключ для приёма (НЕ ключ для выплат!)
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")  # https://....up.railway.app
P_NETWORK = os.getenv("PAY2328_NETWORK", "TRX-TRC20")
CRYPTO_ON = bool(P_UID and P_KEY and PUBLIC_URL)
API = "https://api.2328.io/api"

# ключ -> (название, цена в Stars, цена в USDT, примерное время).
# На один эдит уходит в среднем ~40 минут.
SERVICES = {
    "e1": ("1 эдит", 100, "1.81", "40 минут"),
    "e2": ("2 эдита", 200, "3.62", "1 час 20 минут"),
    "e3": ("3 эдита", 300, "5.43", "2 часа"),
    "e5": ("5 эдитов", 500, "9.05", "3 часа 20 минут"),
}

PENDING: dict = {}   # ожидающие крипто-платежи (в памяти)
DONE: set = set()    # уже обработанные платежи
ALERTED: set = set()

dp = Dispatcher(storage=MemoryStorage())


class Order(StatesGroup):
    waiting_details = State()
    choosing = State()


class Review(StatesGroup):
    waiting_text = State()


def menu_kb() -> InlineKeyboardMarkup:
    rows = []
    for key, (name, stars, usdt, _t) in SERVICES.items():
        price = f"{stars} ⭐ / {usdt} USDT" if CRYPTO_ON else f"{stars} ⭐"
        rows.append([InlineKeyboardButton(text=f"{name} — {price}", callback_data=f"buy:{key}")])
    rows.append([InlineKeyboardButton(text="🎬 Примеры работ", url=PORTFOLIO_URL)])
    rows.append([InlineKeyboardButton(text="⭐ Отзывы клиентов", url=REVIEWS_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_label(user) -> str:
    return f"@{user.username}" if user.username else f"id {user.id}"


# ---------- 2328.io ----------
def sign(body: str) -> str:
    b64 = base64.b64encode(body.encode("utf-8")).decode()
    return hmac.new(P_KEY.encode(), b64.encode(), hashlib.sha256).hexdigest()


async def api_post(path: str, data: dict) -> dict:
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "EditsBot/1.0",
        "project": P_UID,
        "sign": sign(body),
    }
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{API}{path}", data=body.encode("utf-8"), headers=headers) as r:
            return await r.json(content_type=None)


# ---------- Общие функции заказа ----------
async def notify_admin(bot: Bot, name, amount_text, username, user_id, text):
    await bot.send_message(
        ADMIN_ID,
        f"НОВЫЙ ЗАКАЗ ЭДИТА\n\n{text}\n\n"
        f"Услуга: {name}\n"
        f"Сумма: {amount_text}\n"
        f"Клиент: {username}\n"
        f"ID клиента: {user_id}\n\n"
        f"Чтобы отправить готовый эдит, ответь (Reply) на это сообщение файлом или видео.",
    )


async def finish_order(bot: Bot, order_id: str, via: str):
    """Крипто-платёж подтверждён: сообщаем клиенту и тебе (один раз)."""
    if order_id in DONE:
        return
    DONE.add(order_id)
    info = PENDING.pop(order_id, None)
    user_id = int(order_id.split("-")[0])

    if info:
        await bot.send_message(info["chat_id"], "Оплата прошла ✅ Заказ принят, скоро свяжусь с тобой!")
        await notify_admin(bot, info["name"], f"{info['usdt']} USDT", info["username"], user_id, info["text"])
        if info["has_media"]:
            await bot.copy_message(ADMIN_ID, info["order_chat"], info["order_msg"])
    else:
        await bot.send_message(
            ADMIN_ID,
            f"💰 Крипто-оплата получена ({via}), но данные заказа потерялись после перезапуска бота.\n"
            f"Платёж: {order_id}\nID клиента: {user_id}\n\nНапиши клиенту сам и уточни заказ.",
        )
        try:
            await bot.send_message(user_id, "Оплата прошла ✅ Скоро свяжусь с тобой!")
        except Exception:
            pass


# ---------- Команды ----------
@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    times = "\n".join(
        f"• {name}: будет делаться примерно {t}" for name, _s, _u, t in SERVICES.values()
    )
    await message.answer(
        "Привет! Примеры моих работ есть в TikTok: tiktok.com/@moln1ya_3d1ts_1\n"
        f"Отзывы клиентов: {REVIEWS_URL}\n\n"
        f"Сроки:\n{times}\n\nВыбери, сколько эдитов нужно:",
        reply_markup=menu_kb(),
    )


@dp.message(Command("review"))
async def ask_review(message: Message, state: FSMContext):
    await state.set_state(Review.waiting_text)
    await message.answer("Напиши свой отзыв одним сообщением (можно с фото или скриншотом).")


@dp.message(Command("reviews"))
async def show_reviews(message: Message):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📖 Читать отзывы", url=REVIEWS_URL)]]
    )
    await message.answer("Отзывы наших клиентов:", reply_markup=kb)


@dp.message(Review.waiting_text)
async def get_review(message: Message, state: FSMContext, bot: Bot):
    text = message.text or message.caption or "(без текста, вложение ниже)"
    await bot.send_message(ADMIN_ID, f"НОВЫЙ ОТЗЫВ\n\n{text}\n\nОт: {user_label(message.from_user)}")
    if message.content_type != "text":
        await bot.copy_message(ADMIN_ID, message.chat.id, message.message_id)
    await message.answer("Спасибо за отзыв! 💛", reply_markup=menu_kb())
    await state.clear()


@dp.message(F.reply_to_message, F.from_user.id == ADMIN_ID)
async def deliver(message: Message, bot: Bot):
    """Ответ админа на сообщение о заказе -> уходит клиенту."""
    source = message.reply_to_message.text or ""
    found = re.search(r"ID клиента: (\d+)", source)
    if not found:
        return
    await bot.copy_message(int(found.group(1)), message.chat.id, message.message_id)
    await message.reply("Отправлено клиенту ✅")


# ---------- Оформление заказа ----------
@dp.callback_query(F.data.startswith("buy:"))
async def choose_service(call: CallbackQuery, state: FSMContext):
    await state.update_data(service=call.data.split(":")[1])
    await state.set_state(Order.waiting_details)
    await call.message.answer(
        "Опиши заказ одним сообщением: что нужно, референсы, музыка, сроки.\n"
        "Можно приложить фото или видео."
    )
    await call.answer()


async def send_stars_invoice(target: Message, service: str, bot: Bot):
    name, stars, _usdt, _t = SERVICES[service]
    try:
        await target.answer_invoice(
            title=name,
            description="Оплата заказа. После оплаты я получу твой заказ и свяжусь с тобой.",
            payload=f"order:{service}",
            currency="XTR",  # Telegram Stars
            prices=[LabeledPrice(label=name, amount=stars)],
        )
    except Exception as e:
        logging.exception("Не удалось создать счёт в Stars")
        await target.answer("Не получилось создать счёт, попробуй другой способ или напиши мне напрямую.")
        await bot.send_message(ADMIN_ID, f"⚠️ Ошибка счёта Stars: {e}")


@dp.message(Order.waiting_details)
async def get_details(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.update_data(
        order_chat=message.chat.id,
        order_msg=message.message_id,
        order_text=message.text or message.caption or "(без текста, вложение ниже)",
        has_media=message.content_type != "text",
    )
    await state.set_state(Order.choosing)

    if not CRYPTO_ON:
        await send_stars_invoice(message, data["service"], bot)
        return

    name, stars, usdt, _t = SERVICES[data["service"]]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐ Звёзды Telegram — {stars}", callback_data="pay:stars")],
        [InlineKeyboardButton(text=f"💎 Крипта (USDT) — {usdt}", callback_data="pay:crypto")],
    ])
    await message.answer(f"{name}. Выбери способ оплаты:", reply_markup=kb)


@dp.callback_query(F.data == "pay:stars")
async def pay_stars(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if "service" not in data:
        await call.answer("Сначала выбери эдит: /start", show_alert=True)
        return
    await call.answer()
    await send_stars_invoice(call.message, data["service"], bot)


@dp.callback_query(F.data == "pay:crypto")
async def pay_crypto(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if "service" not in data or not CRYPTO_ON:
        await call.answer("Сначала выбери эдит: /start", show_alert=True)
        return
    await call.answer()

    name, _stars, usdt, _t = SERVICES[data["service"]]
    order_id = f"{call.from_user.id}-{uuid.uuid4().hex[:12]}"
    request = {
        "amount": usdt,
        "currency": "USDT",
        "network": P_NETWORK,
        "order_id": order_id,
        "description": name,
        "url_callback": f"{PUBLIC_URL}/webhook/2328",
        "ttl_seconds": 3600,
    }
    try:
        resp = await api_post("/v1/payment", request)
        url = resp["result"]["url"]
        if resp.get("state") != 0:
            raise RuntimeError(str(resp)[:300])
    except Exception as e:
        logging.exception("Не удалось создать крипто-платёж")
        await call.message.answer("Не получилось создать крипто-счёт, попробуй звёзды или напиши мне.")
        await bot.send_message(ADMIN_ID, f"⚠️ Ошибка крипто-счёта: {e}")
        return

    PENDING[order_id] = {
        "chat_id": call.message.chat.id,
        "username": user_label(call.from_user),
        "name": name,
        "usdt": usdt,
        "text": data.get("order_text", ""),
        "order_chat": data.get("order_chat"),
        "order_msg": data.get("order_msg"),
        "has_media": data.get("has_media", False),
        "created": time.time(),
    }
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 Оплатить", url=url)]])
    await call.message.answer(
        f"К оплате: {usdt} USDT (сеть {P_NETWORK}). Ссылка действует 1 час. "
        "После оплаты бот сам напишет, что заказ принят.",
        reply_markup=kb,
    )


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def paid(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    name = SERVICES.get(data.get("service", ""), ("?",))[0]
    user = message.from_user
    await message.answer("Оплата прошла ✅ Заказ принят, скоро свяжусь с тобой!")
    await notify_admin(
        bot, name, f"{message.successful_payment.total_amount} ⭐",
        user_label(user), user.id, data.get("order_text", ""),
    )
    if data.get("has_media"):
        await bot.copy_message(ADMIN_ID, data["order_chat"], data["order_msg"])
    await state.clear()


# ---------- Вебхук 2328.io и сверка статусов ----------
async def webhook(request: web.Request) -> web.Response:
    bot: Bot = request.app["bot"]
    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400)
    if not isinstance(payload, dict):
        return web.Response(status=400)

    received = str(payload.pop("sign", ""))
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if not received or not hmac.compare_digest(sign(body), received):
        return web.Response(status=401)

    status = payload.get("payment_status")
    order_id = payload.get("order_id")
    if order_id:
        if status in ("paid", "overpaid"):
            info = PENDING.get(order_id)
            try:
                same = info is None or (
                    payload.get("currency") == "USDT"
                    and Decimal(str(payload.get("amount"))) == Decimal(info["usdt"])
                )
            except Exception:
                same = False
            if same:
                asyncio.create_task(finish_order(bot, order_id, "вебхук"))
            else:
                await bot.send_message(ADMIN_ID, f"⚠️ Сумма платежа {order_id} не совпала с заказом, проверь вручную.")
        elif status in ("underpaid", "aml_lock") and (order_id, status) not in ALERTED:
            ALERTED.add((order_id, status))
            await bot.send_message(ADMIN_ID, f"⚠️ Платёж {order_id}: статус {status}. Разберись вручную.")
    return web.Response(text="ok")


async def reconcile(bot: Bot):
    """Запасная проверка: раз в минуту спрашиваем статус ожидающих платежей."""
    while True:
        await asyncio.sleep(60)
        for order_id, info in list(PENDING.items()):
            if time.time() - info["created"] > 2 * 3600:
                PENDING.pop(order_id, None)
                continue
            try:
                resp = await api_post("/v1/payment/info", {"order_id": order_id})
                status = (resp.get("result") or {}).get("payment_status")
                if status in ("paid", "overpaid"):
                    await finish_order(bot, order_id, "сверка")
                elif status == "cancel":
                    PENDING.pop(order_id, None)
                    await bot.send_message(info["chat_id"], "Счёт истёк. Чтобы заказать снова, нажми /start")
            except Exception:
                logging.exception("Ошибка сверки платежа")


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TOKEN)
    await bot.set_my_commands([
        BotCommand(command="start", description="Заказать эдит"),
        BotCommand(command="review", description="Оставить отзыв"),
        BotCommand(command="reviews", description="Читать отзывы"),
    ])
    background = []
    if CRYPTO_ON:
        app = web.Application()
        app["bot"] = bot
        app.router.add_post("/webhook/2328", webhook)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080"))).start()
        background.append(asyncio.create_task(reconcile(bot)))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
