"""Готовые кассы. Ключи остаются в программе разработчика и на сервер Wekon не попадают.

Подключать их необязательно: можно работать со своей кассой напрямую и вызывать только
``send_invoice`` и ``set_invoice_status``, или вообще собрать оплату своими карточками.

Статус проверки: ЮKassa и CloudPayments написаны по их документации; Т-Банк и Robokassa
требуют сверки. Ни одна касса пока не проверена настоящим платежом.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode

import httpx

from .cards import to_unix
from .errors import WekonError


class PaymentError(WekonError):
    """Касса отказала в создании платежа."""


@dataclass
class Payment:
    """Созданный платёж: ``url`` идёт в карточку счёта."""

    id: str
    url: str
    order_id: str
    raw: Any = None


@dataclass
class PaymentEvent:
    """Проверенное уведомление кассы. ``status`` — как в ``set_invoice_status``."""

    order_id: str
    payment_id: str
    status: str
    raw: Any = None


class Provider:
    """Общий вид кассы: создать платёж и разобрать её уведомление."""

    def __init__(self, http: Optional[httpx.AsyncClient] = None) -> None:
        self.http = http or httpx.AsyncClient(timeout=20)

    async def create(self, order_id: str, amount: int, title: str, *, expires_at: Any = None) -> Payment:
        raise NotImplementedError

    async def parse_notification(self, body: bytes, headers: Mapping[str, str]) -> Optional[PaymentEvent]:
        """Проверить подлинность и вернуть событие; ``None`` — чужое или ненужное уведомление."""
        raise NotImplementedError

    def notification_reply(self, event: Optional[PaymentEvent]) -> str:
        """Что ответить кассе."""
        return ""


class YooKassa(Provider):
    """ЮKassa: рубли строкой, уведомления настраиваются в кабинете."""

    API = "https://api.yookassa.ru/v3/payments"
    EVENTS = {"succeeded": "paid", "canceled": "failed", "waiting_for_capture": "authorized"}

    def __init__(self, shop_id: str, secret_key: str, *, return_url: str = "https://web.wekon.ru", http: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(http)
        self.auth = (shop_id, secret_key)
        self.return_url = return_url

    async def create(self, order_id: str, amount: int, title: str, *, expires_at: Any = None) -> Payment:
        response = await self.http.post(self.API, auth=self.auth, headers={"Idempotence-Key": order_id}, json={
            "amount": {"value": f"{amount / 100:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": self.return_url},
            "description": title[:128],
            "metadata": {"order_id": order_id},
        })
        data = response.json()
        if response.status_code >= 400:
            raise PaymentError(str(data.get("description") or data), "yookassa", response.status_code)
        return Payment(data["id"], data["confirmation"]["confirmation_url"], order_id, data)

    async def parse_notification(self, body: bytes, headers: Mapping[str, str]) -> Optional[PaymentEvent]:
        notice = json.loads(body)
        event = notice.get("event", "")
        target = notice.get("object") or {}
        if event == "refund.succeeded":
            payment_id, status = target.get("payment_id", ""), "refunded"
        elif event.startswith("payment."):
            payment_id, status = target.get("id", ""), ""
        else:
            return None
        # Уведомлению не верим: статус спрашиваем у самой кассы.
        response = await self.http.get(f"{self.API}/{payment_id}", auth=self.auth)
        if response.status_code >= 400:
            return None
        payment = response.json()
        status = status or self.EVENTS.get(payment.get("status", ""), "")
        if not status:
            return None
        return PaymentEvent((payment.get("metadata") or {}).get("order_id", ""), payment_id, status, payment)


class TBank(Provider):
    """Т-Банк: копейки, подпись SHA-256. Перед запуском сверьте с документацией банка."""

    API = "https://securepay.tinkoff.ru/v2/Init"
    STATUSES = {"CONFIRMED": "paid", "AUTHORIZED": "authorized", "REJECTED": "failed", "CANCELED": "canceled",
                "REFUNDED": "refunded", "DEADLINE_EXPIRED": "expired"}

    def __init__(self, terminal_key: str, password: str, *, notification_url: Optional[str] = None, http: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(http)
        self.terminal_key = terminal_key
        self.password = password
        self.notification_url = notification_url

    def token(self, data: Mapping[str, Any]) -> str:
        values = {key: value for key, value in data.items() if key != "Token" and not isinstance(value, (dict, list))}
        values["Password"] = self.password
        text = "".join(str(values[key]).lower() if isinstance(values[key], bool) else str(values[key]) for key in sorted(values))
        return hashlib.sha256(text.encode()).hexdigest()

    async def create(self, order_id: str, amount: int, title: str, *, expires_at: Any = None) -> Payment:
        body: dict = {"TerminalKey": self.terminal_key, "Amount": amount, "OrderId": order_id, "Description": title[:140]}
        if self.notification_url:
            body["NotificationURL"] = self.notification_url
        body["Token"] = self.token(body)
        data = (await self.http.post(self.API, json=body)).json()
        if not data.get("Success"):
            raise PaymentError(str(data.get("Message") or data), "tbank")
        return Payment(str(data.get("PaymentId")), data["PaymentURL"], order_id, data)

    async def parse_notification(self, body: bytes, headers: Mapping[str, str]) -> Optional[PaymentEvent]:
        notice = json.loads(body)
        if not hmac.compare_digest(str(notice.get("Token", "")), self.token(notice)):
            return None
        status = self.STATUSES.get(notice.get("Status", ""))
        return PaymentEvent(str(notice.get("OrderId", "")), str(notice.get("PaymentId", "")), status, notice) if status else None

    def notification_reply(self, event: Optional[PaymentEvent]) -> str:
        return "OK"


class Robokassa(Provider):
    """Robokassa: рубли с точкой, ссылка подписывается сама. ``order_id`` — число.

    Перед запуском сверьте открытие ссылкой и ответ Result URL с документацией.
    """

    URL = "https://auth.robokassa.ru/Merchant/Index.aspx"

    def __init__(self, login: str, password1: str, password2: str, *, test: bool = False, http: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(http)
        self.login = login
        self.password1 = password1
        self.password2 = password2
        self.test = test

    async def create(self, order_id: str, amount: int, title: str, *, expires_at: Any = None) -> Payment:
        out_sum = f"{amount / 100:.2f}"
        sign = hashlib.md5(f"{self.login}:{out_sum}:{order_id}:{self.password1}".encode()).hexdigest()
        query = {"MerchantLogin": self.login, "OutSum": out_sum, "InvId": order_id, "Description": title[:100], "SignatureValue": sign}
        if self.test:
            query["IsTest"] = 1
        return Payment(str(order_id), f"{self.URL}?{urlencode(query)}", str(order_id))

    async def parse_notification(self, body: bytes, headers: Mapping[str, str]) -> Optional[PaymentEvent]:
        form = dict(parse_qsl(body.decode()))
        expected = hashlib.md5(f"{form.get('OutSum')}:{form.get('InvId')}:{self.password2}".encode()).hexdigest()
        if not hmac.compare_digest(expected, form.get("SignatureValue", "").lower()):
            return None
        return PaymentEvent(form["InvId"], form["InvId"], "paid", form)

    def notification_reply(self, event: Optional[PaymentEvent]) -> str:
        return f"OK{event.order_id}" if event else "bad sign"


class CloudPayments(Provider):
    """CloudPayments: рубли числом, счёт со ссылкой, подпись HMAC-SHA256."""

    API = "https://api.cloudpayments.ru/orders/create"
    STATUSES = {"Completed": "paid", "Authorized": "authorized"}

    def __init__(self, public_id: str, api_secret: str, *, http: Optional[httpx.AsyncClient] = None) -> None:
        super().__init__(http)
        self.auth = (public_id, api_secret)
        self.secret = api_secret

    async def create(self, order_id: str, amount: int, title: str, *, expires_at: Any = None) -> Payment:
        body = {"Amount": amount / 100, "Currency": "RUB", "Description": title, "InvoiceId": order_id}
        data = (await self.http.post(self.API, auth=self.auth, json=body)).json()
        if not data.get("Success"):
            raise PaymentError(str(data.get("Message") or data), "cloudpayments")
        return Payment(str(data["Model"].get("Id", "")), data["Model"]["Url"], order_id, data)

    async def parse_notification(self, body: bytes, headers: Mapping[str, str]) -> Optional[PaymentEvent]:
        lowered = {key.lower(): value for key, value in headers.items()}
        signature = lowered.get("content-hmac") or lowered.get("x-content-hmac") or ""
        expected = base64.b64encode(hmac.new(self.secret.encode(), body, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(expected, signature):
            return None
        form = dict(parse_qsl(body.decode())) if not body.lstrip().startswith(b"{") else json.loads(body)
        status = self.STATUSES.get(form.get("Status", ""), "failed" if form.get("Reason") else "")
        if form.get("PaymentTransactionId"):
            status = "refunded"
        return PaymentEvent(str(form.get("InvoiceId", "")), str(form.get("TransactionId", "")), status, form) if status else None

    def notification_reply(self, event: Optional[PaymentEvent]) -> str:
        return '{"code": 0}'
