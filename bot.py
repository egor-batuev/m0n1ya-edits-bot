import asyncio
import logging
import os
import re

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

# Токен и ID админа берём из переменных окружения (не храни токен в коде!)
TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])  # твой Telegram ID (узнать: @userinfobot)
# Ссылка на канал/чат с отзывами (необязательно). Если пусто, кнопки не будет.
REVIEWS_URL = os.getenv("REVIEWS_URL", "")

# Услуги: ключ -> (название, цена в Telegram Stars). Меняй под себя.
SERVICES = {
    "short": ("Эдит до 30 секунд", 100),
}

dp = Dispatcher(storage=MemoryStorage())


class Order(StatesGroup):
    waiting_details = State()


class Review(StatesGroup):
    waiting_text = State()


def menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{name} — {price} ⭐", callback_data=f"buy:{key}")]
        for key, (name, price) in SERVICES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Привет! Здесь можно заказать эдит до 30 секунд:", reply_markup=menu_kb())


@dp.callback_query(F.data.startswith("buy:"))
async def choose_service(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":")[1]
    await state.update_data(service=key)
    await state.set_state(Order.waiting_details)
    await call.message.answer(
        "Опиши заказ одним сообщением: что нужно, референсы, музыка, сроки.\n"
        "Можно приложить фото или видео."
    )
    await call.answer()


@dp.message(Command("review"))
async def ask_review(message: Message, state: FSMContext):
    await state.set_state(Review.waiting_text)
    await message.answer("Напиши свой отзыв одним сообщением (можно с фото или скриншотом).")


@dp.message(Command("reviews"))
async def show_reviews(message: Message):
    if REVIEWS_URL:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="📖 Читать отзывы", url=REVIEWS_URL)]]
        )
        await message.answer("Отзывы наших клиентов:", reply_markup=kb)
    else:
        await message.answer("Отзывы пока не добавлены.")


@dp.message(Review.waiting_text)
async def get_review(message: Message, state: FSMContext, bot: Bot):
    user = message.from_user
    username = f"@{user.username}" if user.username else f"id {user.id}"
    text = message.text or message.caption or "(без текста, вложение ниже)"

    await bot.send_message(ADMIN_ID, f"НОВЫЙ ОТЗЫВ\n\n{text}\n\nОт: {username}")
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


@dp.message(Order.waiting_details)
async def get_details(message: Message, state: FSMContext):
    data = await state.get_data()
    name, price = SERVICES[data["service"]]

    text = message.text or message.caption or "(без текста, вложение ниже)"
    await state.update_data(
        order_chat=message.chat.id,
        order_msg=message.message_id,
        order_text=text,
        has_media=message.content_type != "text",
    )

    await message.answer_invoice(
        title=name,
        description="Оплата заказа. После оплаты я получу твой заказ и свяжусь с тобой.",
        payload=f"order:{data['service']}",
        currency="XTR",  # Telegram Stars, провайдер платежей не нужен
        prices=[LabeledPrice(label=name, amount=price)],
    )


@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def paid(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    service = data.get("service", "?")
    name = SERVICES.get(service, ("?", 0))[0]
    user = message.from_user
    username = f"@{user.username}" if user.username else f"id {user.id}"

    await message.answer("Оплата прошла ✅ Заказ принят, скоро свяжусь с тобой!")

    await bot.send_message(
        ADMIN_ID,
        f"НОВЫЙ ЗАКАЗ ЭДИТА\n\n{data.get('order_text', '')}\n\n"
        f"Услуга: {name}\n"
        f"Сумма: {message.successful_payment.total_amount} ⭐\n"
        f"Клиент: {username}\n"
        f"ID клиента: {user.id}\n\n"
        f"Чтобы отправить готовый эдит, ответь (Reply) на это сообщение файлом или видео.",
    )
    if data.get("has_media"):
        await bot.copy_message(ADMIN_ID, data["order_chat"], data["order_msg"])
    await state.clear()


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TOKEN)
    await bot.set_my_commands([
        BotCommand(command="start", description="Заказать эдит"),
        BotCommand(command="review", description="Оставить отзыв"),
        BotCommand(command="reviews", description="Читать отзывы"),
    ])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
