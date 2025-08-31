from __future__ import annotations
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from .config import settings
from .db import get_session, engine
from .models import User, Checkin, Reminder
from .i18n import t
from .llm import analyze_checkin, detect_crisis
from .utils import parse_time_hhmm, today_start_in_tz, to_utc


class CheckinStates(StatesGroup):
    mood = State()
    stress = State()
    energy = State()
    emotions = State()
    sleep = State()
    notes = State()


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
    await message.answer(t('consent_request', locale))


async def consent_handler(message: Message, state: FSMContext, session: AsyncSession):
    text = (message.text or '').strip().lower()
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    if text in {"да", "согласен", "согласна", "yes", "agree"}:
        user.consent_given = True
        await session.commit()
        await message.answer(t('consent_yes', locale))
    else:
        await message.answer(t('consent_no', locale))


async def cmd_help(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await message.answer(t('help', locale))


async def cmd_lang(message: Message, state: FSMContext, session: AsyncSession):
    # Toggle ru/en for simplicity
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
    await message.answer(
        t('reminder_set', locale) + "\n" +
        "Отправьте список времени (HH:MM, через запятую) и при необходимости укажите часовой пояс (например, Europe/Moscow)."
    )


async def reminders_text(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    text = (message.text or '').strip()
    # crude parse: "09:00, 18:00 Europe/Moscow"
    parts = [p.strip() for p in text.split()]
    tz = user.timezone
    times_part = parts
    if len(parts) >= 2 and "/" in parts[-1]:
        tz = parts[-1]
        times_part = parts[:-1]
    times_raw = " ".join(times_part).replace(" ", "")
    times = [t for t in times_raw.split(',') if parse_time_hhmm(t)]
    times_str = ",".join(times) if times else user.checkin_time

    # upsert reminder
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
    await message.answer(t('reminder_set', locale))


async def cmd_checkin(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.clear()
    await state.set_state(CheckinStates.mood)
    await message.answer(t('checkin_intro', locale))
    await message.answer(t('ask_mood', locale) + "\n" + t('prompt_skip_hint', locale))


async def mood_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    text = message.text or ''
    await state.update_data(mood=text)
    await state.set_state(CheckinStates.stress)
    await message.answer(t('ask_stress', locale) + "\n" + t('prompt_skip_hint', locale))


async def stress_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.update_data(stress=message.text or '')
    await state.set_state(CheckinStates.energy)
    await message.answer(t('ask_energy', locale) + "\n" + t('prompt_skip_hint', locale))


async def energy_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.update_data(energy=message.text or '')
    await state.set_state(CheckinStates.emotions)
    await message.answer(t('ask_emotions', locale) + "\n" + t('prompt_skip_hint', locale))


async def emotions_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.update_data(emotions=message.text or '')
    await state.set_state(CheckinStates.sleep)
    await message.answer(t('ask_sleep', locale) + "\n" + t('prompt_skip_hint', locale))


async def sleep_handler(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'
    await state.update_data(sleep=message.text or '')
    await state.set_state(CheckinStates.notes)
    await message.answer(t('ask_notes', locale) + "\n" + t('prompt_skip_hint', locale))


async def notes_handler(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'

    await state.update_data(notes=message.text or '')
    data = await state.get_data()
    await state.clear()

    # Save checkin
    date_local = today_start_in_tz(user.timezone)
    checkin = Checkin(
        user_id=user.id,
        date=date_local,
        notes=data.get('notes'),
    )
    # naive parse for numbers
    def extract_score(s: str) -> int | None:
        try:
            nums = [int(x) for x in s.split() if x.isdigit()]
            return nums[0] if nums else None
        except Exception:
            return None
    checkin.mood_score = extract_score(data.get('mood', ''))
    checkin.stress_score = extract_score(data.get('stress', ''))
    checkin.energy_score = extract_score(data.get('energy', ''))
    checkin.emotions = data.get('emotions')

    # sleep hours
    try:
        sh = [x for x in (data.get('sleep') or '').replace(',', '.').split() if x.replace('.', '', 1).isdigit()]
        checkin.sleep_hours = int(float(sh[0])) if sh else None
    except Exception:
        checkin.sleep_hours = None

    session.add(checkin)
    await session.commit()

    await message.answer(t('checkin_saved', locale))

    # Crisis detection quick path
    full_text = "\n".join([data.get('mood',''), data.get('stress',''), data.get('energy',''), data.get('emotions',''), data.get('sleep',''), data.get('notes','')])
    if detect_crisis(full_text):
        await message.answer(t('crisis_detected', locale))
        # Provide minimal resources (RU-focused)
        await message.answer("Если вы в опасности — звоните 112. Линия доверия: 8-800-2000-122. Обратитесь к близким/специалисту.")

    # LLM analysis
    analysis = await analyze_checkin(
        f"User locale={locale}, timezone={user.timezone}. Daily check-in raw data: {data}.\n"
        "Provide: 1) brief empathetic summary; 2) 2–4 actionable, low-risk recommendations aligned with CBT/ACT/mindfulness; 3) encourage self-reflection; 4) no diagnoses.",
        locale=locale,
    )
    checkin.analysis_summary = analysis
    checkin.recommendations = analysis
    await session.commit()

    await message.answer(t('analysis_ready', locale) + "\n\n" + analysis)


async def cmd_stats(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    locale = user.language_code or 'ru'

    # simple last 7 days
    q = await session.execute(select(Checkin).where(Checkin.user_id == user.id).order_by(Checkin.date.desc()).limit(7))
    rows = q.scalars().all()
    if not rows:
        await message.answer(t('stats_title', locale) + "\nНет данных пока.")
        return
    lines = []
    for r in rows:
        lines.append(f"{r.date.date()}: mood={r.mood_score}, stress={r.stress_score}, energy={r.energy_score}; sleep={r.sleep_hours}; notes={(r.notes or '')[:50]}")
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
    data = orjson.dumps(payload).decode()
    await message.answer_document(document=("export.json", data))


async def cmd_delete_me(message: Message, state: FSMContext, session: AsyncSession):
    result = await session.execute(select(User).where(User.tg_user_id == message.from_user.id))
    user = result.scalar_one_or_none()
    await session.execute(delete(Checkin).where(Checkin.user_id == user.id))
    await session.execute(delete(Reminder).where(Reminder.user_id == user.id))
    await session.execute(delete(User).where(User.id == user.id))
    await session.commit()
    await message.answer("Данные удалены.")


def setup_routes(dp: Dispatcher):
    dp.message.register(cmd_start, Command(commands=["start"]))
    dp.message.register(cmd_help, Command(commands=["help"]))
    dp.message.register(cmd_lang, Command(commands=["lang"]))
    dp.message.register(cmd_settings, Command(commands=["settings"]))
    dp.message.register(cmd_reminders, Command(commands=["reminders"]))
    dp.message.register(reminders_text, F.text, Command(commands=[]))

    dp.message.register(cmd_checkin, Command(commands=["checkin"]))
    dp.message.register(mood_handler, CheckinStates.mood)
    dp.message.register(stress_handler, CheckinStates.stress)
    dp.message.register(energy_handler, CheckinStates.energy)
    dp.message.register(emotions_handler, CheckinStates.emotions)
    dp.message.register(sleep_handler, CheckinStates.sleep)
    dp.message.register(notes_handler, CheckinStates.notes)

    dp.message.register(cmd_stats, Command(commands=["stats"]))
    dp.message.register(cmd_export, Command(commands=["export"]))
    dp.message.register(cmd_delete_me, Command(commands=["delete_me"]))


async def main():
    bot = Bot(token=settings.bot_token, parse_mode="HTML")
    dp = Dispatcher()

    # Middleware to inject DB session per message
    @dp.update.outer_middleware()
    async def db_session_mw(handler, event, data):
        async for session in get_session():
            data["session"] = session
            return await handler(event, data)

    setup_routes(dp)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
