"""Магазин: сумма считается на месте, адрес спрашивается формой, оплата через ЮKassa.

Запуск: WEKON_BOT_API_KEY=... YOOKASSA_SHOP_ID=... YOOKASSA_SECRET_KEY=... uvicorn shop_bot:app
"""
import os
import uuid

from wekon import Bot, Card, Form
from wekon.payments import YooKassa

bot = Bot("shop_bot")
kassa = YooKassa(os.environ["YOOKASSA_SHOP_ID"], os.environ["YOOKASSA_SECRET_KEY"])

PRICE = 490000  # копейки


def product(quantity):
    return (Card()
            .header("Кроссовки Aero", icon="👟", status="В наличии", tone="success")
            .stepper("quantity", 1, 5, quantity, label="Количество")
            .stats(("Итого", f"{PRICE * quantity // 100} ₽", "accent"))
            .buttons(("Оформить заказ", f"order:{quantity}")))


@bot.command("start")
def start(msg):
    msg.reply(card=product(1))


@bot.callback("quantity")
def change_quantity(msg):
    msg.edit(card=product(int(msg.value)))


@bot.callback("order:{quantity}")
def order(msg):
    form = Form(f"address:{msg.params['quantity']}", "Доставка").text("name", "Имя", required=True).textarea("address", "Адрес", required=True)
    msg.edit("Осталось указать адрес", buttons=[("Заполнить", form)])


@bot.form("address:{quantity}")
def pay(msg):
    bot.send_payment(msg.chat_id, kassa, str(uuid.uuid4()), PRICE * int(msg.params["quantity"]), "Кроссовки Aero")


@bot.on("payment")
def paid(msg):
    if msg.payment.status == "paid":
        msg.reply("Спасибо! Заказ передан в доставку 🚚")


# Уведомления кассы приходят на адрес /pay/yookassa, события бота — на /wekon.
app = bot.asgi(payments={"/pay/yookassa": kassa})
