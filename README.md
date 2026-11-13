# wekon

Библиотека для ботов Wekon на Python 3.10+.

## Установка

```bash
pip install wekon
```

## Первый бот

Создайте бота у `@wekon_bot`, получите ключ и запишите его в переменную `WEKON_BOT_API_KEY`.

```python
from wekon import Bot

bot = Bot("echo_bot")  # ключ из WEKON_BOT_API_KEY

@bot.command("start")
def start(msg):
    msg.reply("Привет! Как вас зовут?", buttons=[("Сайт", "https://wekon.ru")])

@bot.message()
def echo(msg):
    msg.reply(msg.text)

bot.run()
```

Библиотека сама получает и обновляет токен, повторяет запросы при лимитах и сбоях, не шлёт
дубли при обрыве связи, держит порядок событий внутри чата и отвечает на нажатия кнопок.

Обычные функции работают параллельно по чатам. Для тысяч одновременных пользователей есть
асинхронный вариант с теми же возможностями: `from wekon.aio import Bot` и `async def`.

## Примеры

- [`examples/game_2048.py`](examples/game_2048.py) — игра: сетка кнопок, размеры, цвета, удержание.
- [`examples/shop_bot.py`](examples/shop_bot.py) — магазин: карточка, форма, оплата.

## Чистый экран

В личном чате с ботом можно удалять и сообщения человека, например команду или ответ на вопрос:

```python
@bot.message(state="name")
def save_name(msg):
    msg.delete()  # ответ человека исчезает, на экране остаётся только карточка бота
```

В группе чужие сообщения удаляет только бот с правом модератора, а в подключённом чате двух людей чужие удалять нельзя.

## Файлы от людей

Фото, видео, голосовые, кружки, аудио и документы приходят во вложениях:

```python
@bot.message(types=["photo", "voice", "document"])
def save(msg):
    file = msg.message.file                 # media_type, name, mime_type, size, duration
    data = msg.download()                   # байты
    msg.download(path=file.name)            # или сразу на диск
```

## Адрес

По умолчанию `https://prod.wekon.ru/api/v1`. Другой адрес: `Bot("my_bot", base_url="...")`.

## Диалоги и счета в проде

`MemoryStorage` держит шаги диалогов и связь «заказ — счёт» в памяти: при перезапуске они
теряются. Для работы в проде передайте свой класс с методами `get(key)` и `set(key, state, data)`:
`Bot("my_bot", storage=MyStorage())`.

## Оплата

Ключи кассы остаются в вашей программе, на сервер Wekon они не попадают. Варианты на выбор:

1. Готовая касса: `bot.send_payment(chat_id, YooKassa(...), order_id, amount, title)`.
2. Своя интеграция и заготовка карточки: `send_invoice` и `set_invoice_status`.
3. Полностью своя оплата: обычные карточки, кнопки и формы.

ЮKassa и CloudPayments написаны по их документации. Т-Банк и Robokassa перед запуском сверьте
с документацией кассы. Ни одна касса пока не проверена настоящим платежом.

Пример: [`examples/shop_bot.py`](examples/shop_bot.py).
