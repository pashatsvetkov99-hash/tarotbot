import asyncio
import logging
import re
import ssl
import sys
import time
import uuid

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

import config
import db
from keyboards import pay_keyboard, sphere_keyboard, start_keyboard, support_keyboard, ADMIN_USERNAME

SUPPORT_MSG = f"\n\n🆘 Если проблема повторяется — напиши <a href=\"https://t.me/{ADMIN_USERNAME}\">администратору</a>."

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("tarotbot")

bot = Bot(
    token=config.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()
router = Router()

SPHERE_NAMES = {
    "sphere_career": "Карьера",
    "sphere_money": "Деньги",
    "sphere_current": "Нынешний партнёр",
    "sphere_ex": "Бывший партнёр",
    "sphere_general": "Общий",
}

# ─── Rate limiting ──────────────────────────────────────────────────────────

_rate_limit: dict[int, float] = {}
RATE_LIMIT_COOLDOWN = 3.0  # seconds between actions per user

# Store pending situation text in DB (works across Railway instances)
_awaiting_situation: set[int] = set()


def _check_rate(user_id: int) -> bool:
    now = time.monotonic()
    last = _rate_limit.get(user_id, 0)
    if now - last < RATE_LIMIT_COOLDOWN:
        return False
    _rate_limit[user_id] = now
    return True


# ─── GigaChat API ───────────────────────────────────────────────────────────

GIGACHAT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

_token_cache: dict = {"access_token": None, "expires_at": 0}
_token_lock = asyncio.Lock()

# Limit concurrent AI requests to prevent resource exhaustion
_ai_semaphore = asyncio.Semaphore(3)

# Persistent aiohttp session (reuse connections)
_http_session: aiohttp.ClientSession | None = None


async def _get_http() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60),
            connector=aiohttp.TCPConnector(limit=10, ssl=_ssl_ctx),
        )
    return _http_session


async def _fetch_access_token() -> str:
    session = await _get_http()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {config.GIGACHAT_AUTH_KEY}",
    }
    async with session.post(
        GIGACHAT_AUTH_URL,
        headers=headers,
        data=f"scope={config.GIGACHAT_SCOPE}",
    ) as resp:
        resp.raise_for_status()
        result = await resp.json()
        _token_cache["access_token"] = result["access_token"]
        _token_cache["expires_at"] = result.get("expires_at", 0)
        return result["access_token"]


async def _get_token() -> str:
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]
    async with _token_lock:
        # Double-check after acquiring lock
        if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
            return _token_cache["access_token"]
        return await _fetch_access_token()


async def gigachat_chat(prompt: str) -> str:
    async with _ai_semaphore:
        token = await _get_token()
        session = await _get_http()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "model": "GigaChat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.8,
            "max_tokens": 512,
        }
        async with session.post(
            GIGACHAT_CHAT_URL,
            json=payload,
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            result = await resp.json()
            return result["choices"][0]["message"]["content"]


def _clean(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"#{1,6}\s*", "", text)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    return text.strip()


def _sanitize(text: str) -> str:
    """Strip HTML tags and control chars from user input before sending to AI."""
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text.strip()


# ─── Очередь раскладов ──────────────────────────────────────────────────────

MAX_QUEUE_SIZE = 50

_queue_lock = asyncio.Lock()
_waiting_queue: dict[int, dict] = {}
_processing: set[int] = set()


async def do_tarot_reading(user_id: int, data: dict):
    msg: Message = data["message"]
    situation = data["situation"]
    sphere = data["sphere"]

    can_use = await db.can_use_reading(user_id)
    if not can_use:
        _waiting_queue.pop(user_id, None)
        _processing.discard(user_id)
        await msg.answer(
            "😔 Бесплатные расклады закончились.\n"
            f"Купите ещё <b>{config.PAID_CREDITS} раскладов</b> "
            f"за <b>{config.STARS_PRICE} ⭐ Telegram Stars</b>.",
            reply_markup=pay_keyboard(),
        )
        return

    try:
        prompt = (
            "Ты — опытный таролог с многолетним стажем. "
            "Ты видишь будущее через карты и говоришь прямо, как будто сидишь напротив клиента.\n\n"
            f"Ситуация клиента: {situation}\n"
            f"Сфера: {sphere}\n\n"
            "Выложи 3 карты. По каждой:\n"
            "— Название\n"
            "— Что карта говорит о прошлом, настоящем и будущем клиента\n"
            "— Куда ведёт ситуация, к чему готовиться\n\n"
            "Заверши общим прогнозом — что ждёт клиента.\n\n"
            "СТИЛЬ:\n"
            "— Говори как настоящий таролог: уверенно, мистически, с деталями\n"
            "— Не давай советов типа «тебе стоит подумать». Предсказывай\n"
            "— 80-120 слов, без воды\n"
            "— Только обычный текст и эмодзи. Никаких **, *, _, #, `, []"
        )
        reading = _clean(await gigachat_chat(prompt))
    except asyncio.TimeoutError:
        log.warning("GigaChat timeout for user %s", user_id)
        await msg.answer(
            "⏳ Карты не ответили вовремя. Попробуй ещё раз." + SUPPORT_MSG,
            reply_markup=support_keyboard(),
        )
        return
    except aiohttp.ClientResponseError as e:
        log.error("GigaChat HTTP error for user %s: %s %s", user_id, e.status, e.message, exc_info=True)
        await msg.answer(
            f"❌ Ошибка сервера (код {e.status}). Попробуй позже." + SUPPORT_MSG,
            reply_markup=support_keyboard(),
        )
        return
    except aiohttp.ClientError as e:
        log.error("GigaChat connection error for user %s: %s", user_id, str(e), exc_info=True)
        await msg.answer(
            "❌ Не удалось подключиться к серверу. Попробуй позже." + SUPPORT_MSG,
            reply_markup=support_keyboard(),
        )
        return
    except Exception as e:
        log.error("GigaChat API error for user %s: %s - %s", user_id, type(e).__name__, str(e), exc_info=True)
        await msg.answer(
            "❌ Не удалось получить расклад. Попробуй позже." + SUPPORT_MSG,
            reply_markup=support_keyboard(),
        )
        return
    finally:
        _processing.discard(user_id)

    await db.consume_reading(user_id)
    await db.save_session(user_id, situation, sphere)
    await msg.answer(
        f"━━━━━━━━━━━━━━\n🔮 <b>{sphere}</b>\n━━━━━━━━━━━━━━\n\n{reading}",
        reply_markup=start_keyboard(),
    )


async def process_queue():
    if _queue_lock.locked():
        return
    async with _queue_lock:
        while _waiting_queue:
            uid = next(iter(_waiting_queue))
            data = _waiting_queue.pop(uid)
            _processing.add(uid)
            try:
                await data["message"].answer("⏳ Ждёмс...")
            except Exception:
                _processing.discard(uid)
                continue
            await do_tarot_reading(uid, data)


# ─── /support & /test ──────────────────────────────────────────────────────

from aiogram.filters import Command

@router.message(Command("support"))
async def cmd_support(message: Message):
    await message.answer(
        "🆘 <b>Поддержка</b>\n\n"
        f"Если бот не работает или есть вопросы — напиши "
        f"<a href=\"https://t.me/{ADMIN_USERNAME}\">администратору</a>.",
        reply_markup=support_keyboard(),
    )


@router.message(Command("test"))
async def cmd_test(message: Message):
    """Test GigaChat API connection."""
    uid = message.from_user.id
    if uid not in config.ADMIN_IDS:
        return
    await message.answer("🔄 Тестирую подключение к GigaChat...")
    try:
        result = await gigachat_chat("Скажи 'Привет' одним словом.")
        await message.answer(f"✅ GigaChat работает!\n\nОтвет: {result}")
    except Exception as e:
        log.error("GigaChat test failed: %s", e, exc_info=True)
        await message.answer(
            f"❌ GigaChat не работает!\n\nОшибка: {type(e).__name__}: {str(e)}" + SUPPORT_MSG,
            reply_markup=support_keyboard(),
        )


# ─── /start ─────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message):
    await db.ensure_user(message.from_user.id)

    uid = message.from_user.id
    if uid in _processing:
        await message.answer("⏳ Подожди, твой расклад обрабатывается...")
        return
    if uid in _waiting_queue:
        pos = list(_waiting_queue.keys()).index(uid) + 1
        ahead = pos - 1
        await message.answer(
            f"⏳ Ты в очереди. Позиция: <b>{pos}</b>. "
            f"Перед тобой: <b>{ahead}</b>. Ожидай ⏰"
        )
        return

    await message.answer(
        "🔮 <b>AI Таро</b>\n\n"
        "Расклад из трёх карт по твоей ситуации.\n"
        "Опиши что беспокоит — получишь ответ за минуту.\n\n"
        "⚡ На базе нейросети <b>ChatGPT-6 Astra</b>",
        reply_markup=start_keyboard(),
    )


# ─── Начать расклад ────────────────────────────────────────────────────────

@router.callback_query(F.data == "start_reading")
async def cb_start_reading(callback: CallbackQuery):
    uid = callback.from_user.id
    if not _check_rate(uid):
        await callback.answer("⏳ Подожди немного", show_alert=True)
        return
    await callback.answer()

    if uid in _processing:
        await callback.message.answer("⏳ Подожди, твой расклад обрабатывается...")
        return
    if uid in _waiting_queue:
        pos = list(_waiting_queue.keys()).index(uid) + 1
        ahead = pos - 1
        await callback.message.answer(
            f"⏳ Ты уже в очереди. Позиция: <b>{pos}</b>. "
            f"Перед тобой: <b>{ahead}</b>. Ожидай ⏰"
        )
        return

    can_use = await db.can_use_reading(uid)
    if not can_use:
        await callback.message.answer(
            "😔 Бесплатные расклады закончились.\n"
            f"Купите ещё <b>{config.PAID_CREDITS} раскладов</b> "
            f"за <b>{config.STARS_PRICE} ⭐ Telegram Stars</b>.",
            reply_markup=pay_keyboard(),
        )
        return

    await db.clear_pending_situation(uid)
    _awaiting_situation.add(uid)
    await callback.message.answer(
        "✨ <b>Опиши ситуацию</b>\n\n"
        "Что беспокоит? Чем подробнее — тем точнее.\n\n"
        f"До {config.MAX_SITUATION_LEN} символов."
    )


# ─── Ввод ситуации ──────────────────────────────────────────────────────────

@router.message()
async def process_situation(message: Message):
    uid = message.from_user.id
    if uid not in _awaiting_situation:
        return  # not expecting input from this user

    if not message.text:
        await message.answer("❌ Отправь текстовое описание.")
        return
    text = _sanitize(message.text)
    if len(text) > config.MAX_SITUATION_LEN:
        await message.answer(
            f"❌ Слишком длинный текст ({len(text)} символов). "
            f"Максимум {config.MAX_SITUATION_LEN}. Сократи, пожалуйста."
        )
        return
    if len(text) < 10:
        await message.answer("❌ Слишком короткое описание. Напиши хотя бы пару предложений.")
        return
    _awaiting_situation.discard(uid)
    await db.save_pending_situation(uid, text)
    await message.answer(
        "🎴 <b>Выбери сферу:</b>",
        reply_markup=sphere_keyboard(),
    )


# ─── Выбор сферы → очередь ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("sphere_"))
async def process_sphere(callback: CallbackQuery):
    uid = callback.from_user.id
    if not _check_rate(uid):
        await callback.answer("⏳ Подожди немного", show_alert=True)
        return

    if uid in _processing:
        await callback.answer("⏳ Подожди, твой расклад обрабатывается...", show_alert=True)
        return
    if uid in _waiting_queue:
        pos = list(_waiting_queue.keys()).index(uid) + 1
        ahead = pos - 1
        await callback.answer(
            f"⏳ Ты уже в очереди (позиция {pos}, перед тобой {ahead})",
            show_alert=True,
        )
        return

    await callback.answer()
    sphere_key = callback.data
    sphere_name = SPHERE_NAMES.get(sphere_key, "Общий")

    situation_text = await db.get_pending_situation(uid)
    await db.clear_pending_situation(uid)
    _awaiting_situation.discard(uid)
    if not situation_text or len(situation_text) < 10:
        await callback.message.answer("❌ Описание потерялось. Начни заново.", reply_markup=start_keyboard())
        return

    can_use = await db.can_use_reading(uid)
    if not can_use:
        await callback.message.answer(
            "😔 Бесплатные расклады закончились.\n"
            f"Купите ещё <b>{config.PAID_CREDITS} раскладов</b> "
            f"за <b>{config.STARS_PRICE} ⭐ Telegram Stars</b>.",
            reply_markup=pay_keyboard(),
        )
        return

    if len(_waiting_queue) >= MAX_QUEUE_SIZE:
        await callback.message.answer("⚠ Очередь переполнена. Попробуй позже." + SUPPORT_MSG, reply_markup=support_keyboard())
        return

    _waiting_queue[uid] = {
        "message": callback.message,
        "situation": situation_text,
        "sphere": sphere_name,
    }
    pos = list(_waiting_queue.keys()).index(uid) + 1
    ahead = pos - 1

    if ahead > 0:
        await callback.message.answer(
            f"⏳ Ты в очереди. Позиция: <b>{pos}</b>. "
            f"Перед тобой: <b>{ahead}</b>. Ожидай ⏰"
        )

    asyncio.create_task(process_queue())


# ─── Оплата Telegram Stars ──────────────────────────────────────────────────

@router.callback_query(F.data == "buy_credits")
async def cb_buy_credits(callback: CallbackQuery):
    await callback.answer()
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="AI Таро — расклады",
        description=f"{config.PAID_CREDITS} раскладов от AI-таролога",
        payload="tarot_pack",
        currency="XTR",
        prices=[LabeledPrice(label="Расклады", amount=config.STARS_PRICE)],
    )


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    payment = message.successful_payment
    user_id = message.from_user.id
    charge_id = payment.telegram_payment_charge_id

    credited = await db.add_payment(user_id, charge_id, config.PAID_CREDITS)
    if not credited:
        log.warning("Duplicate payment ignored: user=%s charge=%s", user_id, charge_id)
        await message.answer("⚠ Платёж уже был обработан ранее.")
        return
    log.info("Payment received: user=%s charge=%s", user_id, charge_id)
    await message.answer(
        f"✅ Оплата прошла! Начислено <b>{config.PAID_CREDITS} раскладов</b>.\n\n"
        "Нажми кнопку ниже, чтобы начать.",
        reply_markup=start_keyboard(),
    )


# ─── Global error handler ──────────────────────────────────────────────────

@dp.error()
async def global_error_handler(event: ErrorEvent):
    log.error("Unhandled error: %s", event.exception, exc_info=True)
    try:
        update = event.update
        if update.message:
            await update.message.answer(
                "❌ Произошла ошибка. Попробуй позже." + SUPPORT_MSG,
                reply_markup=support_keyboard(),
            )
        elif update.callback_query:
            await update.callback_query.message.answer(
                "❌ Произошла ошибка. Попробуй позже." + SUPPORT_MSG,
                reply_markup=support_keyboard(),
            )
    except Exception:
        log.error("Failed to send error message to user")
    return True


# ─── Cleanup & startup ─────────────────────────────────────────────────────

async def _cleanup_old_sessions():
    """Delete sessions older than 30 days to prevent DB bloat."""
    await db.cleanup_old_sessions(days=30)


async def main():
    await db.init_db()
    dp.include_router(router)
    log.info("Bot starting...")
    try:
        await dp.start_polling(bot)
    finally:
        if _http_session and not _http_session.closed:
            await _http_session.close()
        log.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())