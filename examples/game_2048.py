"""Игра 2048: поле-сетка, большая крестовина и удержание кнопки.

Сетка кнопок на 4 колонки: стрелки влево и вправо высотой в два ряда, вверх и вниз шириной
в две колонки. Сервер не считает ход сам: бот хранит поле и правит одну карточку.
"""
import random

from wekon import Bot, Card, Keyboard, button, cell

bot = Bot("game_bot")
games = {}  # чат -> игра; для прода храните в базе

COLORS = {2: "#EEE4DA", 4: "#EDE0C8", 8: "#F2B179", 16: "#F59563", 32: "#F67C5F", 64: "#F65E3B",
          128: "#EDCF72", 256: "#EDCC61", 512: "#EDC850", 1024: "#EDC53F", 2048: "#EDC22E"}
LINES = {"l": [[r * 4 + c for c in range(4)] for r in range(4)],
         "r": [[r * 4 + 3 - c for c in range(4)] for r in range(4)],
         "u": [[c * 4 + r for c in range(4)] for r in range(4)],
         "d": [[(3 - c) * 4 + r for c in range(4)] for r in range(4)]}


def spawn(game):
    empty = [i for i, value in enumerate(game["board"]) if not value]
    if empty:
        game["board"][random.choice(empty)] = 4 if random.random() < 0.1 else 2


def new_game():
    game = {"board": [0] * 16, "score": 0}
    spawn(game)
    spawn(game)
    return game


def move(game, direction):
    """Сдвигает плитки; False — ход ничего не изменил."""
    moved = False
    for line in LINES[direction]:
        values = [game["board"][i] for i in line if game["board"][i]]
        merged = []
        while values:
            value = values.pop(0)
            if values and values[0] == value:
                values.pop(0)
                value *= 2
                game["score"] += value
            merged.append(value)
        merged += [0] * (4 - len(merged))
        for index, value in zip(line, merged):
            moved = moved or game["board"][index] != value
            game["board"][index] = value
    return moved


def arrow(text, direction, **size):
    return button(text, f"move:{direction}", loading="none", haptic="light", hint="Сдвинуть плитки", **size)


def screen(game):
    cells = [cell(str(value), color=COLORS.get(value, "#3C3A32")) if value else cell() for value in game["board"]]
    card = (Card(f"2048 · счёт {game['score']}")
            .header("2048", "Складывайте одинаковые плитки", icon="🎮", status="Ваш ход", tone="accent")
            .stats(("Счёт", game["score"], "accent"), ("Лучшая", max(game["board"])))
            .grid(4, cells))
    keyboard = (Keyboard(columns=4)
                .row(arrow("⬅️", "l", height=2), arrow("⬆️", "u", width=2), arrow("➡️", "r", height=2))
                .row(arrow("⬇️", "d", width=2))
                # Нажатие спрашивает «Точно?», удержание начинает сразу.
                .row(button("🔄 Заново", "restart", width=4, long_data="restart_now", confirm="Начать заново?")))
    return card, keyboard


@bot.command("start")
def start(msg):
    games[msg.chat_id] = new_game()
    card, keyboard = screen(games[msg.chat_id])
    msg.reply(card=card, keyboard=keyboard)


@bot.callback("move:{direction}")
def play(msg):
    game = games.setdefault(msg.chat_id, new_game())
    if move(game, msg.params["direction"]):
        spawn(game)
        card, keyboard = screen(game)
        msg.edit(card=card, keyboard=keyboard)


@bot.callback("restart", "restart_now")
def restart(msg):
    games[msg.chat_id] = new_game()
    card, keyboard = screen(games[msg.chat_id])
    msg.edit(card=card, keyboard=keyboard)


if __name__ == "__main__":
    bot.run()
