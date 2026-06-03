# -*- coding: utf-8 -*-
"""
ProfitCalculator.py — плагин калькулятора прибыли для FunPay Cardinal.

Установка:
1) /menu -> Плагины -> Добавить плагин
2) Отправить этот .py файл боту
3) Перезапустить Cardinal, если бот попросит
4) Открыть /calc

Плагин ничего не меняет на FunPay. Он только считает примерную цену и хранит список товаров локально.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from typing import TYPE_CHECKING, Any

from telebot.types import InlineKeyboardMarkup as K, InlineKeyboardButton as B, Message, CallbackQuery

if TYPE_CHECKING:
    from cardinal import Cardinal


NAME = "Калькулятор прибыли"
VERSION = "1.0.0"
DESCRIPTION = "Считает цену товара с наценкой, комиссией FunPay и хранит список рассчитанных товаров."
CREDITS = "@plugins_shopbot"
UUID = "9e1c9b4b-7466-4e12-94c7-5e224313f5b8"
SETTINGS_PAGE = False
BIND_TO_DELETE = None

logger = logging.getLogger("FPC.ProfitCalculator")

CB_PREFIX = "pcalc"
STATE_PREFIX = "pcalc"
DATA_FILE = os.path.join("plugins", "ProfitCalculator_data.json")

DEFAULT_DATA = {
    "settings": {
        "currency": "RUB",
        "funpay_fee": 9.0,
        "withdraw_fee": 0.0,
    },
    "products": []
}

CURRENCIES = {
    "RUB": {"symbol": "₽", "step": 10.0},
    "UAH": {"symbol": "₴", "step": 10.0},
    "USD": {"symbol": "$", "step": 0.1},
    "EUR": {"symbol": "€", "step": 0.1},
}

# ---------- storage ----------

def ensure_storage() -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    if not os.path.exists(DATA_FILE):
        save_data(DEFAULT_DATA.copy())


def load_data() -> dict[str, Any]:
    ensure_storage()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        logger.warning("Не удалось прочитать ProfitCalculator_data.json. Создаю новый файл.")
        data = DEFAULT_DATA.copy()

    if not isinstance(data, dict):
        data = DEFAULT_DATA.copy()
    data.setdefault("settings", DEFAULT_DATA["settings"].copy())
    data.setdefault("products", [])
    data["settings"].setdefault("currency", "RUB")
    data["settings"].setdefault("funpay_fee", 9.0)
    data["settings"].setdefault("withdraw_fee", 0.0)
    return data


def save_data(data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- formatting / math ----------

def esc(text: Any) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_num(value: float) -> str:
    value = float(value)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return (f"{value:.2f}" if value >= 10 else f"{value:.3f}").rstrip("0").rstrip(".")


def money(value: float, currency: str) -> str:
    cur = CURRENCIES.get(currency, CURRENCIES["RUB"])
    return f"{fmt_num(value)} {cur['symbol']}"


def round_up(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.ceil((value - 1e-9) / step) * step


def parse_number(text: str) -> float:
    clean = text.strip().replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", clean):
        raise ValueError("bad_number")
    value = float(clean)
    if value < 0:
        raise ValueError("negative")
    return value


def parse_markup(text: str) -> tuple[str, float, str]:
    clean = text.strip().replace(" ", "").replace(",", ".")
    if clean.endswith("%"):
        number = parse_number(clean[:-1])
        return "percent", number, f"{fmt_num(number)}%"
    number = parse_number(clean)
    return "fixed", number, fmt_num(number)


def calculate_product(name: str, buy_price: float, markup_raw: str, settings: dict[str, Any]) -> dict[str, Any]:
    currency = settings.get("currency", "RUB")
    cur = CURRENCIES.get(currency, CURRENCIES["RUB"])
    step = float(cur["step"])
    funpay_fee = float(settings.get("funpay_fee", 9.0))
    withdraw_fee = float(settings.get("withdraw_fee", 0.0))

    markup_type, markup_value, markup_text = parse_markup(markup_raw)

    if markup_type == "percent":
        markup_amount_raw = buy_price * markup_value / 100
        markup_amount = round_up(markup_amount_raw, step)
    else:
        markup_amount_raw = markup_value
        markup_amount = markup_value

    seller_target = buy_price + markup_amount

    multiplier = (1 - funpay_fee / 100) * (1 - withdraw_fee / 100)
    if multiplier <= 0:
        raise ValueError("bad_fee")

    buyer_price_raw = seller_target / multiplier
    buyer_price = round_up(buyer_price_raw, step)

    funpay_commission = buyer_price * funpay_fee / 100
    after_funpay = buyer_price - funpay_commission
    withdraw_commission = after_funpay * withdraw_fee / 100
    net_after_all = after_funpay - withdraw_commission
    profit = net_after_all - buy_price

    return {
        "id": int(time.time() * 1000),
        "name": name.strip(),
        "buy_price": round(buy_price, 4),
        "markup_raw": markup_raw.strip(),
        "markup_type": markup_type,
        "markup_value": markup_value,
        "markup_text": markup_text,
        "markup_amount_raw": round(markup_amount_raw, 4),
        "markup_amount": round(markup_amount, 4),
        "seller_target": round(seller_target, 4),
        "buyer_price_raw": round(buyer_price_raw, 4),
        "buyer_price": round(buyer_price, 4),
        "funpay_fee": funpay_fee,
        "funpay_commission": round(funpay_commission, 4),
        "withdraw_fee": withdraw_fee,
        "withdraw_commission": round(withdraw_commission, 4),
        "net_after_all": round(net_after_all, 4),
        "profit": round(profit, 4),
        "currency": currency,
        "created_at": int(time.time()),
    }


def product_text(product: dict[str, Any], preview: bool = False) -> str:
    currency = product.get("currency", "RUB")
    title = "🧮 <b>Расчёт товара</b>" if preview else "📦 <b>Товар</b>"
    withdraw_line = ""
    if float(product.get("withdraw_fee", 0.0)) > 0:
        withdraw_line = (
            f"\n💳 Комиссия вывода: <b>{fmt_num(product.get('withdraw_fee', 0))}%</b>"
            f"\n💸 Комиссия вывода примерно: <b>{money(product.get('withdraw_commission', 0), currency)}</b>"
        )

    return (
        f"{title}\n\n"
        f"📦 Название: <b>{esc(product.get('name', 'Без названия'))}</b>\n"
        f"💱 Валюта: <b>{esc(currency)}</b>\n\n"
        f"💸 Закупочная цена: <b>{money(product.get('buy_price', 0), currency)}</b>\n"
        f"📈 Наценка: <b>{esc(product.get('markup_text', product.get('markup_raw', '0')))}</b>\n"
        f"➕ Сумма наценки: <b>{money(product.get('markup_amount', 0), currency)}</b>\n"
        f"💰 Цена после наценки: <b>{money(product.get('seller_target', 0), currency)}</b>\n\n"
        f"🏦 Комиссия FunPay: <b>{fmt_num(product.get('funpay_fee', 0))}%</b>\n"
        f"🧾 Комиссия FunPay примерно: <b>{money(product.get('funpay_commission', 0), currency)}</b>"
        f"{withdraw_line}\n\n"
        f"🛒 Примерная цена для покупателя: <b>{money(product.get('buyer_price', 0), currency)}</b>\n"
        f"✅ Примерная цена для продавца: <b>{money(product.get('net_after_all', 0), currency)}</b>\n"
        f"📊 Примерная прибыль: <b>{money(product.get('profit', 0), currency)}</b>"
    )


# ---------- keyboards ----------

def kb_main() -> K:
    kb = K(row_width=1)
    kb.add(B("🧮 Рассчитать стоимость", callback_data=f"{CB_PREFIX}:calc"))
    kb.add(B("📦 Список товаров", callback_data=f"{CB_PREFIX}:list"))
    kb.add(B("💱 Изменить валюту", callback_data=f"{CB_PREFIX}:currency"))
    kb.add(B("⚙️ Изменить комиссию FunPay", callback_data=f"{CB_PREFIX}:fee"))
    kb.add(B("💳 Комиссия вывода", callback_data=f"{CB_PREFIX}:withdraw"))
    kb.add(B("ℹ️ Настройки", callback_data=f"{CB_PREFIX}:settings"))
    return kb


def kb_cancel() -> K:
    return K().add(B("❌ Отмена", callback_data=f"{CB_PREFIX}:cancel"))


def kb_confirm() -> K:
    kb = K(row_width=2)
    kb.add(
        B("✅ Да, добавить", callback_data=f"{CB_PREFIX}:save_calc"),
        B("❌ Нет", callback_data=f"{CB_PREFIX}:cancel")
    )
    return kb


def kb_back() -> K:
    return K().add(B("⬅️ Назад", callback_data=f"{CB_PREFIX}:menu"))


def kb_currency(current: str) -> K:
    kb = K(row_width=2)
    buttons = []
    for code in CURRENCIES:
        mark = "✅ " if code == current else ""
        buttons.append(B(f"{mark}{code}", callback_data=f"{CB_PREFIX}:set_currency:{code}"))
    kb.add(*buttons)
    kb.add(B("⬅️ Назад", callback_data=f"{CB_PREFIX}:menu"))
    return kb


def kb_products(products: list[dict[str, Any]]) -> K:
    kb = K(row_width=1)
    for p in products[-20:]:
        pid = p.get("id")
        name = str(p.get("name", "Без названия"))[:40]
        kb.add(B(f"📦 {name}", callback_data=f"{CB_PREFIX}:view:{pid}"))
    kb.add(B("🧹 Очистить весь список", callback_data=f"{CB_PREFIX}:ask_clear"))
    kb.add(B("⬅️ Назад", callback_data=f"{CB_PREFIX}:menu"))
    return kb


def kb_product_detail(pid: int) -> K:
    kb = K(row_width=1)
    kb.add(B("🗑 Удалить этот товар", callback_data=f"{CB_PREFIX}:del:{pid}"))
    kb.add(B("⬅️ К списку товаров", callback_data=f"{CB_PREFIX}:list"))
    return kb


def kb_ask_clear() -> K:
    kb = K(row_width=2)
    kb.add(B("✅ Очистить", callback_data=f"{CB_PREFIX}:clear_all"), B("❌ Нет", callback_data=f"{CB_PREFIX}:list"))
    return kb


# ---------- handlers ----------

def init_plugin(cardinal: "Cardinal", *args) -> None:
    ensure_storage()
    tg = cardinal.telegram
    bot = tg.bot

    try:
        cardinal.add_telegram_commands(UUID, [
            ("calc", "Калькулятор прибыли", True),
        ])
    except Exception:
        logger.debug("Не удалось добавить команды ProfitCalculator в меню.", exc_info=True)

    def open_menu(obj: Message | CallbackQuery) -> None:
        data = load_data()
        s = data["settings"]
        text = (
            "💰 <b>Калькулятор прибыли FunPay</b>\n\n"
            "Здесь можно посчитать цену товара с наценкой и комиссией, а потом сохранить товар в список.\n\n"
            f"💱 Валюта: <b>{esc(s.get('currency', 'RUB'))}</b>\n"
            f"🏦 Комиссия FunPay: <b>{fmt_num(s.get('funpay_fee', 9))}%</b>\n"
            f"💳 Комиссия вывода: <b>{fmt_num(s.get('withdraw_fee', 0))}%</b>\n"
            f"📦 Товаров в списке: <b>{len(data.get('products', []))}</b>"
        )
        if isinstance(obj, CallbackQuery):
            bot.edit_message_text(text, obj.message.chat.id, obj.message.id, reply_markup=kb_main())
            bot.answer_callback_query(obj.id)
        else:
            bot.send_message(obj.chat.id, text, reply_markup=kb_main())

    def cmd_profit_calc(m: Message) -> None:
        open_menu(m)

    def cb_menu(c: CallbackQuery) -> None:
        open_menu(c)

    def cb_calc(c: CallbackQuery) -> None:
        msg = bot.edit_message_text(
            "📦 Введите <b>название товара</b>.\n\nНапример: <code>Discord Nitro 1 месяц</code>",
            c.message.chat.id,
            c.message.id,
            reply_markup=kb_cancel(),
        )
        tg.set_state(c.message.chat.id, msg.id, c.from_user.id, f"{STATE_PREFIX}:name", {})
        bot.answer_callback_query(c.id)

    def cb_cancel(c: CallbackQuery) -> None:
        tg.clear_state(c.message.chat.id, c.from_user.id)
        bot.edit_message_text("❌ Действие отменено.", c.message.chat.id, c.message.id, reply_markup=kb_back())
        bot.answer_callback_query(c.id)

    def cb_currency(c: CallbackQuery) -> None:
        data = load_data()
        current = data["settings"].get("currency", "RUB")
        bot.edit_message_text(
            f"💱 <b>Выберите валюту калькулятора</b>\n\nСейчас: <b>{esc(current)}</b>",
            c.message.chat.id,
            c.message.id,
            reply_markup=kb_currency(current),
        )
        bot.answer_callback_query(c.id)

    def cb_set_currency(c: CallbackQuery) -> None:
        code = c.data.split(":")[-1]
        if code not in CURRENCIES:
            bot.answer_callback_query(c.id, "Неизвестная валюта.", show_alert=True)
            return
        data = load_data()
        data["settings"]["currency"] = code
        save_data(data)
        bot.answer_callback_query(c.id, f"Валюта изменена на {code}")
        open_menu(c)

    def cb_fee(c: CallbackQuery) -> None:
        data = load_data()
        current = data["settings"].get("funpay_fee", 9.0)
        msg = bot.edit_message_text(
            "🏦 Введите комиссию FunPay в процентах.\n\n"
            f"Сейчас: <b>{fmt_num(current)}%</b>\n"
            "Например: <code>9</code> или <code>3.5</code>",
            c.message.chat.id,
            c.message.id,
            reply_markup=kb_cancel(),
        )
        tg.set_state(c.message.chat.id, msg.id, c.from_user.id, f"{STATE_PREFIX}:fee", {})
        bot.answer_callback_query(c.id)

    def cb_withdraw(c: CallbackQuery) -> None:
        data = load_data()
        current = data["settings"].get("withdraw_fee", 0.0)
        msg = bot.edit_message_text(
            "💳 Введите комиссию вывода в процентах.\n\n"
            f"Сейчас: <b>{fmt_num(current)}%</b>\n"
            "Если не хочешь учитывать вывод — введи <code>0</code>.",
            c.message.chat.id,
            c.message.id,
            reply_markup=kb_cancel(),
        )
        tg.set_state(c.message.chat.id, msg.id, c.from_user.id, f"{STATE_PREFIX}:withdraw", {})
        bot.answer_callback_query(c.id)

    def cb_settings(c: CallbackQuery) -> None:
        data = load_data()
        s = data["settings"]
        currency = s.get("currency", "RUB")
        step = CURRENCIES.get(currency, CURRENCIES["RUB"])["step"]
        text = (
            "ℹ️ <b>Настройки калькулятора</b>\n\n"
            f"💱 Валюта: <b>{esc(currency)}</b>\n"
            f"🔢 Округление вверх: <b>до {fmt_num(step)} {CURRENCIES.get(currency, CURRENCIES['RUB'])['symbol']}</b>\n"
            f"🏦 Комиссия FunPay: <b>{fmt_num(s.get('funpay_fee', 9))}%</b>\n"
            f"💳 Комиссия вывода: <b>{fmt_num(s.get('withdraw_fee', 0))}%</b>\n\n"
            "Формула цены покупателя:\n"
            "<code>цена = цена_после_наценки / ((1 - комиссия_FunPay) * (1 - комиссия_вывода))</code>\n\n"
            "Комиссия вывода по умолчанию 0%, потому что она зависит от способа вывода."
        )
        bot.edit_message_text(text, c.message.chat.id, c.message.id, reply_markup=kb_back())
        bot.answer_callback_query(c.id)

    def cb_list(c: CallbackQuery) -> None:
        data = load_data()
        products = data.get("products", [])
        if not products:
            bot.edit_message_text("📦 Список товаров пока пуст.", c.message.chat.id, c.message.id, reply_markup=kb_back())
            bot.answer_callback_query(c.id)
            return

        total_profit = sum(float(p.get("profit", 0)) for p in products)
        currency = data["settings"].get("currency", "RUB")
        visible_products = products[-20:]
        lines = ["📦 <b>Список товаров</b>\n"]
        for i, p in enumerate(visible_products, start=1):
            lines.append(f"{i}. <b>{esc(p.get('name', 'Без названия'))}</b>")

        lines.append(f"\n📊 Суммарная примерная прибыль: <b>{money(total_profit, currency)}</b>")
        lines.append("\nВыбери товар кнопкой ниже, чтобы посмотреть полный расчёт или удалить его.")
        bot.edit_message_text("\n".join(lines), c.message.chat.id, c.message.id, reply_markup=kb_products(products))
        bot.answer_callback_query(c.id)

    def cb_view_product(c: CallbackQuery) -> None:
        try:
            pid = int(c.data.split(":")[-1])
        except Exception:
            bot.answer_callback_query(c.id, "Ошибка ID товара.", show_alert=True)
            return

        data = load_data()
        product = None
        for p in data.get("products", []):
            if int(p.get("id", 0)) == pid:
                product = p
                break

        if not product:
            bot.answer_callback_query(c.id, "Товар не найден.", show_alert=True)
            return

        bot.edit_message_text(product_text(product), c.message.chat.id, c.message.id, reply_markup=kb_product_detail(pid))
        bot.answer_callback_query(c.id)

    def cb_del(c: CallbackQuery) -> None:
        try:
            pid = int(c.data.split(":")[-1])
        except Exception:
            bot.answer_callback_query(c.id, "Ошибка ID товара.", show_alert=True)
            return
        data = load_data()
        before = len(data.get("products", []))
        data["products"] = [p for p in data.get("products", []) if int(p.get("id", 0)) != pid]
        save_data(data)
        bot.answer_callback_query(c.id, "Товар удалён." if len(data["products"]) < before else "Товар не найден.")
        c.data = f"{CB_PREFIX}:list"
        cb_list(c)

    def cb_ask_clear(c: CallbackQuery) -> None:
        bot.edit_message_text("🧹 Точно очистить весь список товаров?", c.message.chat.id, c.message.id, reply_markup=kb_ask_clear())
        bot.answer_callback_query(c.id)

    def cb_clear_all(c: CallbackQuery) -> None:
        data = load_data()
        data["products"] = []
        save_data(data)
        bot.edit_message_text("✅ Список товаров очищен.", c.message.chat.id, c.message.id, reply_markup=kb_back())
        bot.answer_callback_query(c.id)

    def send_step_message(chat_id: int, old_mid: int, user_id: int, text: str, reply_markup: K, data_state: dict[str, Any]) -> Message:
        """
        Обычно плагин редактирует одно и то же сообщение, чтобы не засорять чат.
        Но если перед этим была ошибка ввода, следующее нормальное сообщение отправляем новым,
        чтобы оно появилось ниже ошибки, а не меняло старое сообщение сверху.
        """
        if data_state.pop("_had_error", False):
            return bot.send_message(chat_id, text, reply_markup=reply_markup)
        return bot.edit_message_text(text, chat_id, old_mid, reply_markup=reply_markup)

    def cb_save_calc(c: CallbackQuery) -> None:
        state = tg.get_state(c.message.chat.id, c.from_user.id)
        product = None
        if state and state.get("state") == f"{STATE_PREFIX}:confirm":
            product = state.get("data", {}).get("product")
        if not product:
            bot.answer_callback_query(c.id, "Расчёт не найден. Попробуй заново.", show_alert=True)
            return
        data = load_data()
        product["id"] = int(time.time() * 1000)
        data.setdefault("products", []).append(product)
        save_data(data)
        tg.clear_state(c.message.chat.id, c.from_user.id)
        bot.edit_message_text("✅ Товар добавлен в список.\n\n" + product_text(product), c.message.chat.id, c.message.id, reply_markup=kb_back())
        bot.answer_callback_query(c.id)

    def process_state_message(m: Message) -> None:
        if not m.text:
            return
        state = tg.get_state(m.chat.id, m.from_user.id)
        if not state or not str(state.get("state", "")).startswith(f"{STATE_PREFIX}:"):
            return

        st = state["state"]
        data_state = state.get("data", {}) or {}
        text = m.text.strip()

        try:
            bot.delete_message(m.chat.id, m.id)
        except Exception:
            pass

        try:
            if st == f"{STATE_PREFIX}:name":
                if len(text) < 2:
                    data_state["_had_error"] = True
                    tg.set_state(m.chat.id, state["mid"], m.from_user.id, st, data_state)
                    bot.send_message(m.chat.id, "⚠️ Название слишком короткое. Введите нормальное название товара.", reply_markup=kb_cancel())
                    return
                data_state["name"] = text[:120]
                msg = send_step_message(
                    m.chat.id,
                    state["mid"],
                    m.from_user.id,
                    "💸 Введите <b>закупочную цену</b>.\n\nНапример: <code>1000</code> или <code>5.2</code>",
                    kb_cancel(),
                    data_state,
                )
                tg.set_state(m.chat.id, msg.id, m.from_user.id, f"{STATE_PREFIX}:buy", data_state)
                return

            if st == f"{STATE_PREFIX}:buy":
                buy = parse_number(text)
                if buy <= 0:
                    raise ValueError("zero")
                data_state["buy_price"] = buy
                msg = send_step_message(
                    m.chat.id,
                    state["mid"],
                    m.from_user.id,
                    "📈 Введите <b>наценку</b>.\n\n"
                    "Можно процентом или числом:\n"
                    "• <code>20%</code> — процент от закупки\n"
                    "• <code>150</code> — фиксированная сумма сверху",
                    kb_cancel(),
                    data_state,
                )
                tg.set_state(m.chat.id, msg.id, m.from_user.id, f"{STATE_PREFIX}:markup", data_state)
                return

            if st == f"{STATE_PREFIX}:markup":
                product = calculate_product(data_state["name"], float(data_state["buy_price"]), text, load_data()["settings"])
                msg = send_step_message(
                    m.chat.id,
                    state["mid"],
                    m.from_user.id,
                    product_text(product, preview=True) + "\n\nДобавить товар в список?",
                    kb_confirm(),
                    data_state,
                )
                tg.set_state(m.chat.id, msg.id, m.from_user.id, f"{STATE_PREFIX}:confirm", {"product": product})
                return

            if st == f"{STATE_PREFIX}:fee":
                fee = parse_number(text)
                if fee >= 80:
                    data_state["_had_error"] = True
                    tg.set_state(m.chat.id, state["mid"], m.from_user.id, st, data_state)
                    bot.send_message(m.chat.id, "⚠️ Комиссия выглядит слишком большой. Введите число меньше 80.", reply_markup=kb_cancel())
                    return
                data = load_data()
                data["settings"]["funpay_fee"] = fee
                save_data(data)
                tg.clear_state(m.chat.id, m.from_user.id)
                bot.edit_message_text(f"✅ Комиссия FunPay изменена на <b>{fmt_num(fee)}%</b>.", m.chat.id, state["mid"], reply_markup=kb_back())
                return

            if st == f"{STATE_PREFIX}:withdraw":
                fee = parse_number(text)
                if fee >= 80:
                    data_state["_had_error"] = True
                    tg.set_state(m.chat.id, state["mid"], m.from_user.id, st, data_state)
                    bot.send_message(m.chat.id, "⚠️ Комиссия вывода выглядит слишком большой. Введите число меньше 80.", reply_markup=kb_cancel())
                    return
                data = load_data()
                data["settings"]["withdraw_fee"] = fee
                save_data(data)
                tg.clear_state(m.chat.id, m.from_user.id)
                bot.edit_message_text(f"✅ Комиссия вывода изменена на <b>{fmt_num(fee)}%</b>.", m.chat.id, state["mid"], reply_markup=kb_back())
                return

        except ValueError as e:
            if str(e) == "bad_fee":
                error_text = "Комиссия слишком большая. Проверь настройки комиссии FunPay и вывода."
            else:
                error_text = "Не понял число. Введите, например: <code>1000</code>, <code>5.2</code> или <code>20%</code>."
            data_state["_had_error"] = True
            tg.set_state(m.chat.id, state["mid"], m.from_user.id, st, data_state)
            bot.send_message(m.chat.id, "⚠️ " + error_text, reply_markup=kb_cancel())
        except Exception:
            logger.error("Ошибка в калькуляторе прибыли.", exc_info=True)
            bot.send_message(m.chat.id, "⚠️ Произошла ошибка при расчёте. Попробуй ещё раз.", reply_markup=kb_back())
            tg.clear_state(m.chat.id, m.from_user.id)

    def is_pcalc_state_message(m: Message) -> bool:
        """Не даём обработчику плагина перехватывать все текстовые сообщения Cardinal.
        Он должен срабатывать только когда пользователь уже находится в состоянии калькулятора.
        """
        try:
            state = tg.get_state(m.chat.id, m.from_user.id)
            return bool(state and str(state.get("state", "")).startswith(f"{STATE_PREFIX}:"))
        except Exception:
            return False

    tg.msg_handler(cmd_profit_calc, commands=["calc"])
    tg.msg_handler(process_state_message, func=is_pcalc_state_message, content_types=["text"])

    tg.cbq_handler(cb_menu, lambda c: c.data == f"{CB_PREFIX}:menu")
    tg.cbq_handler(cb_calc, lambda c: c.data == f"{CB_PREFIX}:calc")
    tg.cbq_handler(cb_cancel, lambda c: c.data == f"{CB_PREFIX}:cancel")
    tg.cbq_handler(cb_currency, lambda c: c.data == f"{CB_PREFIX}:currency")
    tg.cbq_handler(cb_set_currency, lambda c: c.data.startswith(f"{CB_PREFIX}:set_currency:"))
    tg.cbq_handler(cb_fee, lambda c: c.data == f"{CB_PREFIX}:fee")
    tg.cbq_handler(cb_withdraw, lambda c: c.data == f"{CB_PREFIX}:withdraw")
    tg.cbq_handler(cb_settings, lambda c: c.data == f"{CB_PREFIX}:settings")
    tg.cbq_handler(cb_list, lambda c: c.data == f"{CB_PREFIX}:list")
    tg.cbq_handler(cb_view_product, lambda c: c.data.startswith(f"{CB_PREFIX}:view:"))
    tg.cbq_handler(cb_del, lambda c: c.data.startswith(f"{CB_PREFIX}:del:"))
    tg.cbq_handler(cb_ask_clear, lambda c: c.data == f"{CB_PREFIX}:ask_clear")
    tg.cbq_handler(cb_clear_all, lambda c: c.data == f"{CB_PREFIX}:clear_all")
    tg.cbq_handler(cb_save_calc, lambda c: c.data == f"{CB_PREFIX}:save_calc")


BIND_TO_PRE_INIT = [init_plugin]
