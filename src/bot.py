from __future__ import annotations

import asyncio
import os

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiohttp import web
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import get_session, engine, Base
from src.models import User, Checkin, Reminder
from src.i18n import t
from src.llm import analyze_checkin, detect_crisis, chat
from src.utils import parse_time_hhmm, today_start_in_tz


# ===== FSM =====

class ConsentStates(StatesGroup):
    waiting = State()


class RemindersStates(StatesGroup):
    waiting = State()


class CheckinStates(StatesGroup):
    mood = State()
    stress = State()
    energy = State()
    emotions = State()
    sleep = State()
    notes = State()


class ChatStates(StatesGroup):
    active = State()


# ===== Keyboards =====

def kb_consent():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Согласен", callback_data="consent:yes")
    kb.button(text="❌ Не согласен", callback_data="consent:no")
    kb.adjust(2)
    return kb.as_markup()

def kb_scale(field: str):
    kb = InlineKeyboardBuilder()
    for i in range(1, 11):
        kb.button(text=str(i), callback_data=f"scale:{field}:{i}")
    kb.adjust(5, 5)
    kb.button(text="Пропустить", callback_data=f"skip:{field}")
    kb.adjust(5, 5, 1)
    return kb.as_markup()

def kb_skip(field: str):
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить", callback_data=f"skip:{field}")
    return kb.as_markup()

def kb_chat_controls():
    kb = InlineKeyboardBuilder()
    kb.button(text="Подытожь мой день", callback_data="coach:prompt:summary")
    kb.button(text="План на завтра", callback_data="coach:prompt:plan")
    kb.button(text="Снизить стресс", callback_data="coach:prompt:stress")
    kb.button(text="Завершить", callback_data="coach:end")
    kb.adjust(2, 2)
    return kb.as_markup()


# ===== Helpers =====

async def ask_scale(message_or_query, locale: str, field: str, prompt_key: str):
    text = f"{t(prompt_key, locale)}\nВыберите по шкале 1–10:"
    if isinstance(message_or_query, Message):
        await message_or_query.answer(text, reply_markup=kb_scale(field))
    else:
        await message_or_query.message.edit_text(text, reply_markup=kb_scale(field))

async def ask_free_text(message: Message | CallbackQuery, locale: str, prompt_key: str, field: str, hint: str = ""):
    msg = f"{t(prompt_key, locale)}"
    if hint:
        msg += f"\n{hint}"
    target = message if isinstance(message, Message) else message.message
    await target.answer(msg, reply_markup=kb_skip(field))

async def finalize_checkin(message: Message, state: FSMContext, session: AsyncSession, user: User, locale: str, data: dict):
    # дата "сегодня" по таймзоне пользователя — делаем naive под TIMESTAMP WITHOUT TIME ZONE
    date_local = today_start_in_tz(user.timezone)      # aware
    date_naive = date_local.replace(tzinfo=None)       # naive

    # найти чек-ин на сегодня; если нет — создать
    q = await session.execute(
        select(Checkin).where(Checkin.user_id == user.id, Checkin.date == date_naive)
    )
    checkin = q.scalar_one_or_none()
    if checkin is None:
        checkin = Checkin(user_id=user.id, date=date_naive)
        session.add(checkin)

    # наивный парсер чисел из строк
    def to_int_or_none(v):
        try:
            return int(v)
        except Exception:
            return None

    checkin.mood_score = to_int_or_none(data.get('mood'))
    checkin.stress_score = to_int_or_none(data.get('stress'))
    checkin.energy_score = to_int_or_none(data.get('energy'))
    checkin.emotions = data.get('emotions') or None

    # sleep (может прийти как "7", "7.5", "7 ч")
    try:
        sh_raw = (data.get('sleep') or "").replace(",", ".")
        sh_tok = [x for x in sh_raw.split() if x.replace('.', '', 1).isdigit()]
        checkin.sleep_hours = int(float(sh_tok[0])) if sh_tok else None
    except Exception:
        checkin.sleep_hours = None

    checkin.notes = data.get('notes') or None

    # сохранить базовые данные
    await session.commit()
    await message.answer(t('checkin_saved', locale))

    # кризис
    full_text = "\n".join(filter(None, [
        str(data.get('mood', '')), str(data.get('stress','')), str(data.get('energy','')),
        data.get('emotions',''), data.get('sleep',''), data.get('notes','')
    ]))
    if detect_crisis(full_text):
        await message.answer(t('crisis_detected', locale))
        await message.answer("Если вы в опасности — звоните 112. Линия доверия: 8-800-2000-122.")

    # LLM-анализ
    analysis = await analyze_checkin(
        f"User locale={locale}, timezone={user.timezone}. Daily check-in raw data: {data}.\n"
        "Provide: 1) brief empathetic summary; 2) 2–4 actionable, low-risk recommendations aligned with CBT/ACT/mindfulness; 3) encourage self-reflection; 4) no diagnoses.",
        locale=locale,
    )
    checkin.analysis_summary = analysis
    checkin.recommendations = analysis
    await session.commit()

    await message.answer(t('analysis_ready', locale) + "\n\n" + analysis)


# ===== Handlers =====

async def cmd_start(message: Message, state: FSMContext, session: AsyncSession):
    # Upsert user
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(tg_user_id=message.from_user.id, language_code=message.from_user.language_code or 'ru')
        session.add(user)
        await session.commit()
    locale = user.language_code or 'ru'

    await message.answer(t('start_welcome', locale))
    await message.answer(t('disclaimer', locale))
    await state.set_state(ConsentStates.waiting)
    await message.answer(t('consent_request', locale), reply_markup=kb_consent())


async def consent_handler(message: Message, state: FSMContext, session: AsyncSession):
    text = (message.text or '').strip().lower()
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    if text in {"да", "согласен", "согласна", "yes", "agree"}:
        user.consent_given = True
        await session.commit()
        await state.clear()
        await message.answer(t('consent_yes', locale))
    else:
        await state.clear()
        await message.answer(t('consent_no', locale))


async def cb_consent(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == query.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    _, value = (query.data or "consent:no").split(":")
    if value == "yes":
        user.consent_given = True
        await session.commit()
        await state.clear()
        await query.message.edit_text(t('consent_yes', locale))
    else:
        await state.clear()
        await query.message.edit_text(t('consent_no', locale))


async def cmd_help(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await message.answer(t('help', locale))


async def cmd_lang(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    user.language_code = 'en' if (user.language_code or 'ru') == 'ru' else 'ru'
    await session.commit()
    await message.answer(t('language_set', user.language_code))


async def cmd_settings(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await message.answer(
        f"{t('settings_saved', locale)}\n"
        f"TZ: {user.timezone}, check-in: {user.checkin_time}"
    )


async def cmd_reminders(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.set_state(RemindersStates.waiting)
    await message.answer(
        t('reminder_set', locale) + "\n" +
        "Отправьте список времени (HH:MM, через запятую) и при необходимости укажите часовой пояс (например, Europe/Moscow)."
    )


async def reminders_text(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    text = (message.text or '').strip()
    parts = [p.strip() for p in text.split()]
    tz = user.timezone
    times_part = parts
    if len(parts) >= 2 and "/" in parts[-1]:
        tz = parts[-1]
        times_part = parts[:-1]
    times_raw = " ".join(times_part).replace(" ", "")
    times = [t for t in times_raw.split(',') if parse_time_hhmm(t)]
    times_str = ",".join(times) if times else user.checkin_time

    result = await session.execute(select(Reminder).where(Reminder.user_id == user.id))
    rem = result.scalar_one_or_none()
    if not rem:
        rem = Reminder(user_id=user.id, enabled=True, times=times_str)
        session.add(rem)
    else:
        rem.times = times_str
        rem.enabled = True
    user.timezone = tz
    await session.commit()
    await state.clear()
    await message.answer(t('reminder_set', locale))


# === Check-in with inline ===

async def cmd_checkin(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.clear()
    await state.set_state(CheckinStates.mood)
    await message.answer(t('checkin_intro', locale))
    await ask_scale(message, locale, field="mood", prompt_key="ask_mood")


async def cb_scale(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    # data = "scale:mood:7"
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        await query.answer()
        return
    _, field, value = parts[0], parts[1], parts[2]

    result = await session.execute(select(User).where(User.tg_user_id == query.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'

    await state.update_data(**{field: value})

    # переходы
    if field == "mood":
        await state.set_state(CheckinStates.stress)
        await ask_scale(query, locale, field="stress", prompt_key="ask_stress")
    elif field == "stress":
        await state.set_state(CheckinStates.energy)
        await ask_scale(query, locale, field="energy", prompt_key="ask_energy")
    elif field == "energy":
        await state.set_state(CheckinStates.emotions)
        await ask_free_text(query, locale, prompt_key="ask_emotions", field="emotions", hint="(можно словами через запятую)")
    else:
        await query.answer()


async def cb_skip(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    # data = "skip:emotions" | "skip:sleep" | "skip:notes" | also can be skip:mood/stress/energy
    parts = (query.data or "").split(":")
    if len(parts) < 2:
        await query.answer()
        return
    field = parts[1]

    result = await session.execute(select(User).where(User.tg_user_id == query.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'

    await state.update_data(**{field: None})

    if field == "mood":
        await state.set_state(CheckinStates.stress)
        await ask_scale(query, locale, field="stress", prompt_key="ask_stress")
    elif field == "stress":
        await state.set_state(CheckinStates.energy)
        await ask_scale(query, locale, field="energy", prompt_key="ask_energy")
    elif field == "energy":
        await state.set_state(CheckinStates.emotions)
        await ask_free_text(query, locale, "ask_emotions", "emotions", "(можно словами через запятую)")
    elif field == "emotions":
        await state.set_state(CheckinStates.sleep)
        await ask_free_text(query, locale, "ask_sleep", "sleep", "Например: 7")
    elif field == "sleep":
        await state.set_state(CheckinStates.notes)
        await ask_free_text(query, locale, "ask_notes", "notes")
    elif field == "notes":
        # финализация без заметок
        data = await state.get_data()
        await state.clear()
        # нужен Message для отправки — берем исходное сообщение
        await finalize_checkin(query.message, state, session, user, locale, data)
        await query.answer()
    else:
        await query.answer()


async def mood_handler(message: Message, state: FSMContext, session: AsyncSession):
    # если кто-то всё же пишет текстом на шаге шкалы — сохраним и продолжим
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.update_data(mood=(message.text or '').strip())
    await state.set_state(CheckinStates.stress)
    await ask_scale(message, locale, "stress", "ask_stress")


async def stress_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.update_data(stress=(message.text or '').strip())
    await state.set_state(CheckinStates.energy)
    await ask_scale(message, locale, "energy", "ask_energy")


async def energy_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.update_data(energy=(message.text or '').strip())
    await state.set_state(CheckinStates.emotions)
    await ask_free_text(message, locale, "ask_emotions", "emotions", "(можно словами через запятую)")


async def emotions_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.update_data(emotions=message.text or '')
    await state.set_state(CheckinStates.sleep)
    await ask_free_text(message, locale, "ask_sleep", "sleep", "Например: 7")


async def sleep_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.update_data(sleep=message.text or '')
    await state.set_state(CheckinStates.notes)
    await ask_free_text(message, locale, "ask_notes", "notes")


async def notes_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'

    await state.update_data(notes=message.text or '')
    data = await state.get_data()
    await state.clear()

    await finalize_checkin(message, state, session, user, locale, data)


# === Stats & export ===

async def cmd_stats(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'

    q = await session.execute(select(Checkin).where(Checkin.user_id == user.id).order_by(Checkin.date.desc()).limit(7))
    rows = q.scalars().all()
    if not rows:
        await message.answer(t('stats_title', locale) + "\nНет данных пока.")
        return
    lines = []
    for r in rows:
        lines.append(
            f"{r.date.date()}: mood={r.mood_score}, stress={r.stress_score}, energy={r.energy_score}; sleep={r.sleep_hours}; notes={(r.notes or '')[:50]}")
    await message.answer(t('stats_title', locale) + "\n" + "\n".join(lines))


async def cmd_export(message: Message, state: FSMContext, session: AsyncSession):
    import orjson
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    q = await session.execute(select(Checkin).where(Checkin.user_id == user.id).order_by(Checkin.date.asc()))
    rows = q.scalars().all()
    payload = [
        {
            "date": r.date.isoformat(),
            "mood": r.mood_score,
            "stress": r.stress_score,
            "energy": r.energy_score,
            "emotions": r.emotions,
            "sleep_hours": r.sleep_hours,
            "notes": r.notes,
            "analysis": r.analysis_summary,
            "recs": r.recommendations,
        } for r in rows
    ]
    data = orjson.dumps(payload)
    await message.answer_document(document=BufferedInputFile(data, filename="export.json"))


async def cmd_delete_me(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    await session.execute(delete(Checkin).where(Checkin.user_id == user.id))
    await session.execute(delete(Reminder).where(Reminder.user_id == user.id))
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()
    await message.answer("Данные удалены.")


# === Coach chat ===

async def cmd_coach(message: Message, state: FSMContext, session: AsyncSession):
    # старт чата — подтягиваем последний чек-ин как контекст
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'

    last = await session.execute(
        select(Checkin).where(Checkin.user_id == user.id).order_by(Checkin.date.desc()).limit(1)
    )
    last = last.scalar_one_or_none()

    ctx = ""
    if last:
        ctx = (
            f"Контекст последнего чек-ина ({last.date.date()}): "
            f"mood={last.mood_score}, stress={last.stress_score}, energy={last.energy_score}, "
            f"sleep={last.sleep_hours}, emotions={last.emotions or ''}, notes={(last.notes or '')[:200]}"
        )

    await state.set_state(ChatStates.active)
    await state.update_data(history=[{"role": "user", "content": f"{ctx}\nКоротко: поможешь обсудить мой день?"}])

    intro = (
        "Режим беседы с коучем включён. Пиши сообщение — отвечу. "
        "Есть быстрые кнопки ниже."
    )
    await message.answer(intro, reply_markup=kb_chat_controls())


async def cb_coach_prompt(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    # быстрые подсказки
    _, _, kind = (query.data or "coach:prompt:summary").split(":")
    prompt_map = {
        "summary": "Подытожь мой день коротко и доброжелательно. Дай 2–3 мягких шага.",
        "plan": "Помоги составить простой план на завтра из 3 шагов (сон/учёба/отдых).",
        "stress": "Что могу сделать сегодня и завтра, чтобы снизить стресс без риска?",
    }
    user_prompt = prompt_map.get(kind, "Подытожь и дай 2–3 шага.")

    data = await state.get_data()
    history = data.get("history", [])
    history.append({"role": "user", "content": user_prompt})
    await state.update_data(history=history)

    # ответ модели
    result = await session.execute(select(User).where(User.tg_user_id == query.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    reply = await chat(history, locale=locale)
    history.append({"role": "assistant", "content": reply})
    await state.update_data(history=history)

    await query.message.answer(reply, reply_markup=kb_chat_controls())
    await query.answer()


async def cb_coach_end(query: CallbackQuery, state: FSMContext, session: AsyncSession):
    await state.clear()
    await query.message.edit_text("Беседа завершена.")
    await query.answer()


async def chat_message_handler(message: Message, state: FSMContext, session: AsyncSession):
    # любые сообщения, пока ChatStates.active
    data = await state.get_data()
    history = data.get("history", [])
    history.append({"role": "user", "content": message.text or ""})

    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'

    reply = await chat(history, locale=locale)
    history.append({"role": "assistant", "content": reply})
    await state.update_data(history=history)

    await message.answer(reply, reply_markup=kb_chat_controls())


# ===== Infra =====

def setup_routes(dp: Dispatcher):
    dp.message.register(cmd_start, Command(commands=["start"]))
    dp.message.register(cmd_help, Command(commands=["help"]))
    dp.message.register(cmd_lang, Command(commands=["lang"]))
    dp.message.register(cmd_settings, Command(commands=["settings"]))

    dp.message.register(consent_handler, ConsentStates.waiting)
    dp.callback_query.register(cb_consent, F.data.startswith("consent:"))

    dp.message.register(cmd_reminders, Command(commands=["reminders"]))
    dp.message.register(reminders_text, RemindersStates.waiting)

    dp.message.register(cmd_checkin, Command(commands=["checkin"]))
    dp.callback_query.register(cb_scale, F.data.startswith("scale:"))
    dp.callback_query.register(cb_skip, F.data.startswith("skip:"))

    dp.message.register(mood_handler, CheckinStates.mood)
    dp.message.register(stress_handler, CheckinStates.stress)
    dp.message.register(energy_handler, CheckinStates.energy)
    dp.message.register(emotions_handler, CheckinStates.emotions)
    dp.message.register(sleep_handler, CheckinStates.sleep)
    dp.message.register(notes_handler, CheckinStates.notes)

    dp.message.register(cmd_stats, Command(commands=["stats"]))
    dp.message.register(cmd_export, Command(commands=["export"]))
    dp.message.register(cmd_delete_me, Command(commands=["delete_me"]))

    # coach
    dp.message.register(cmd_coach, Command(commands=["coach"]))
    dp.callback_query.register(cb_coach_prompt, F.data.startswith("coach:prompt:"))
    dp.callback_query.register(cb_coach_end, F.data == "coach:end")
    dp.message.register(chat_message_handler, ChatStates.active)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def health(request):
    return web.Response(text="ok")


async def index(request):
    return web.Response(text="MindCheck bot running")


async def run_http_server():
    app = web.Application()
    app.add_routes([web.get('/', index), web.get('/healthz', health)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', '10000'))
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    await site.start()
    while True:
        await asyncio.sleep(3600)


async def main():
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    dp = Dispatcher()
    await init_db()

    async def db_session_mw(handler, event, data):
        async for session in get_session():
            data["session"] = session
            return await handler(event, data)

    dp.update.outer_middleware(db_session_mw)

    setup_routes(dp)

    await asyncio.gather(
        dp.start_polling(bot),
        run_http_server(),
    )


if __name__ == "__main__":
    asyncio.run(main())
