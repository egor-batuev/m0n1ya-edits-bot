import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

# Токен и ID админа берём из переменных окружения (не храни токен в коде!)
TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])  # твой Telegram ID (узнать: @userinfobot)

# Услуги: ключ -> (название, цена в Telegram Stars). Меняй под себя.
SERVICES = {
    "short": ("Короткий эдит (до 30 сек)", 100),
    "mid": ("Средний эдит (до 1 мин)", 200),
    "long": ("Длинный эдит (до 3 мин)", 400),
}

dp = Dispatcher(storage=MemoryStorage())


class Order(StatesGroup):
    waiting_details = State()


def menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{name} — {price} ⭐", callback_data=f"buy:{key}")]
        for key, (name, price) in SERVICES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Привет! Выбери, какой эдит тебе нужен:", reply_markup=menu_kb())


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


@dp.message(Order.waiting_details)
async def get_details(message: Message, state: FSMContext):
    data = await state.get_data()
    name, price = SERVICES[data["service"]]
    # запоминаем сообщение с заказом, чтобы потом переслать админу
    await state.update_data(order_chat=message.chat.id, order_msg=message.message_id)

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
        f"💰 Новый оплаченный заказ\nУслуга: {name}\n"
        f"Сумма: {message.successful_payment.total_amount} ⭐\nКлиент: {username}",
    )
    if "order_msg" in data:
        await bot.copy_message(ADMIN_ID, data["order_chat"], data["order_msg"])
    await state.clear()


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(TOKEN)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
