# pip install "aiogram>=3.19" aiohttp
import asyncio, json, time, hmac, hashlib, sqlite3, datetime, html, os
from pathlib import Path
from urllib.parse import parse_qsl
from aiohttp import web
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (Message, CallbackQuery, PreCheckoutQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton as B, WebAppInfo, LabeledPrice,
    InlineQueryResultArticle, InputTextMessageContent)

# ================= НАСТРОЙКИ =================
TOKEN = "BOT_TOKEN"                                 # вставь токен от @BotFather
BOT_USERNAME = "Brainclick_bot"                     # без @
ADMIN_ID = 5948504163                               # твой Telegram ID
# index.html отдаётся НЕ этим сервером, а отдельно с GitHub Pages, поэтому сюда — адрес GitHub Pages:
WEBAPP_URL = "https://dsadasdasdasdasd23e.github.io/clicker/"
# а сюда — ТОЛЬКО источник (протокол+домен, без пути и без / на конце), ровно как его шлёт браузер в заголовке Origin:
ALLOWED_ORIGIN = "https://dsadasdasdasdasd23e.github.io"
CHANNELS = {"sub1": "@Brainclik"}   # бот должен быть АДМИНОМ этого канала; второй канал можно добавить так: "sub2": "@другой_канал"
REWARD = {"sub1": 1, "sub2": 1, "repost": 1, "ref": 1}
STARS_TO_COINS = 1.5        # 1 звезда = 1.5 монеты (показывается игроку в окне депозита)
AUTO_CREDIT_STARS = False   # False: монеты за Stars выдаёшь ТЫ кнопкой в админ-панели; True: сразу автоматически
MIN_WD = 100                # минимум на вывод
PORT = int(os.environ.get("PORT", 8080))   # хостинги сами задают порт через переменную PORT
BASE = Path(__file__).parent                # папка, где лежат bot.py, index.html и img/
BRAINROTS = ["Кеш оф карт", "Гарама", "Ла секрет", "Бургуро", "Фоксини", "Венуспино", "Ла каса", "Дракон"]
# цена в монетах за 1 штуку каждого брейнрота при пополнении (Касса → «Пополнить брейнротом»).
# ЗАПОЛНИ РЕАЛЬНЫМИ ЦЕНАМИ, когда они будут готовы — больше ничего менять не нужно.
BRAINROT_PRICES = {b: 0 for b in BRAINROTS}
TOP_REWARDS = [50, 30, 30, 15, 15, 15, 15, 15, 15, 15]   # 1-е, 2-3-е, 4-10-е места недельного топа по кликам
# ==============================================

bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
admin = Router()
admin.message.filter(F.from_user.id == ADMIN_ID)
admin.callback_query.filter(F.from_user.id == ADMIN_ID)

db = sqlite3.connect("game.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, ref INTEGER, created INTEGER, name TEXT, username TEXT, banned INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS claims(user_id INTEGER, key TEXT, ts INTEGER, PRIMARY KEY(user_id,key));
CREATE TABLE IF NOT EXISTS grants(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, reason TEXT, delivered INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, amount REAL, details TEXT, status TEXT DEFAULT 'new', ts INTEGER);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, stars INTEGER, coins REAL, charge_id TEXT UNIQUE, status TEXT DEFAULT 'paid', ts INTEGER);
CREATE TABLE IF NOT EXISTS inventory(user_id INTEGER, brainrot TEXT, qty INTEGER DEFAULT 0, PRIMARY KEY(user_id,brainrot));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
""")
for col in ("name TEXT", "username TEXT", "banned INTEGER DEFAULT 0",
            "balance REAL DEFAULT 0", "clicks INTEGER DEFAULT 0", "week_clicks INTEGER DEFAULT 0"):   # миграция старой базы
    try: db.execute(f"ALTER TABLE users ADD COLUMN {col}")
    except sqlite3.OperationalError: pass
db.commit()
cur_week = lambda: "%d-W%02d" % datetime.date.today().isocalendar()[:2]

q1 = lambda sql, a=(): db.execute(sql, a).fetchone()
qa = lambda sql, a=(): db.execute(sql, a).fetchall()
def ex(sql, a=()): db.execute(sql, a); db.commit()
def grant(uid, amount, reason, delivered=0):
    ex("INSERT INTO grants(user_id,amount,reason,delivered) VALUES(?,?,?,?)", (uid, amount, reason, delivered))
def upsert(uid, name, username, ref=None):
    if q1("SELECT 1 FROM users WHERE id=?", (uid,)):
        ex("UPDATE users SET name=?,username=? WHERE id=?", (name, username, uid)); return False
    ex("INSERT INTO users(id,ref,created,name,username) VALUES(?,?,?,?,?)", (uid, ref, int(time.time()), name, username)); return True
def uline(uid):
    """Кто это: имя (нажимается и открывает профиль) · @username · ID"""
    r = q1("SELECT name,username FROM users WHERE id=?", (uid,))
    name = html.escape((r[0] if r else None) or "Игрок")
    un = f"@{r[1]}" if r and r[1] else "без username"
    return f'<a href="tg://user?id={uid}">{name}</a> · {un} · <code>{uid}</code>'
now = lambda: datetime.datetime.now().strftime("%d.%m %H:%M")
f2 = lambda x: f"{x:.2f}".rstrip("0").rstrip(".")

def check_init(init_data: str):
    try:
        d = dict(parse_qsl(init_data, strict_parsing=True)); h = d.pop("hash")
        s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(hmac.new(key, s.encode(), hashlib.sha256).hexdigest(), h): return None
        if time.time() - int(d["auth_date"]) > 86400: return None
        u = json.loads(d["user"])
        r = q1("SELECT banned FROM users WHERE id=?", (u["id"],))
        if r and r[0]: return None                       # забаненным API не отвечает
        return u
    except Exception:
        return None

# ======================= ОБЫЧНЫЙ БОТ =======================
@dp.message(CommandStart())
async def start(m: Message, command: CommandObject):
    uid = m.from_user.id
    ref = None; a = command.args or ""
    if a.startswith("ref_") and a[4:].isdigit():
        r = int(a[4:])
        if r != uid and q1("SELECT 1 FROM users WHERE id=?", (r,)): ref = r
    if upsert(uid, m.from_user.first_name, m.from_user.username, ref) and ref:
        grant(ref, REWARD["ref"], f"ref:{uid}")
        try: await bot.send_message(ref, f"👥 По твоей ссылке пришёл друг! +{REWARD['ref']} монета (зачислится в игре)")
        except Exception: pass
    await m.answer("Добро пожаловать в Brainrot Clicker!", reply_markup=ReplyKeyboardRemove())
    kb = InlineKeyboardMarkup(inline_keyboard=[[B(text="🎮 Играть", web_app=WebAppInfo(url=WEBAPP_URL))]])
    await m.answer("Жми «Играть» 👇", reply_markup=kb)

# ---- Telegram Stars ----
@dp.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@dp.message(F.successful_payment)
async def paid(m: Message):
    sp = m.successful_payment
    stars = sp.total_amount; uid = m.from_user.id
    if q1("SELECT 1 FROM payments WHERE charge_id=?", (sp.telegram_payment_charge_id,)): return
    coins = stars * STARS_TO_COINS
    ex("INSERT INTO payments(user_id,stars,coins,charge_id,status,ts) VALUES(?,?,?,?,?,?)",
       (uid, stars, coins, sp.telegram_payment_charge_id, "paid", int(time.time())))
    pid = q1("SELECT last_insert_rowid()")[0]
    if AUTO_CREDIT_STARS:
        grant(uid, coins, f"stars:{pid}"); ex("UPDATE payments SET status='given' WHERE id=?", (pid,))
        await m.answer(f"✅ Оплата получена: {stars} ⭐. Начислено {f2(coins)} монет — открой игру.")
    else:
        await m.answer(f"✅ Оплата получена: {stars} ⭐. Администратор скоро выдаст {f2(coins)} монет.")
    await bot.send_message(ADMIN_ID, f"⭐ <b>Новый платёж Stars</b>  🕒 {now()}\n{uline(uid)}\nСумма: <b>{stars} ⭐</b> → {f2(coins)} монет\nСтатус: {'выдано авто' if AUTO_CREDIT_STARS else 'ждёт выдачи'}",
        reply_markup=None if AUTO_CREDIT_STARS else pay_kb(pid, uid))

def pay_kb(pid, uid):
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="✅ Выдать монеты", callback_data=f"pay:give:{pid}"),
                                                  B(text="↩️ Вернуть звёзды", callback_data=f"pay:ref:{pid}")],
                                                 [B(text="👤 Игрок", callback_data=f"u:{uid}")]])
def req_kb(rid, uid):
    return InlineKeyboardMarkup(inline_keyboard=[[B(text="✅ Одобрить", callback_data=f"req:ok:{rid}"), B(text="❌ Отклонить", callback_data=f"req:no:{rid}")],
                                                 [B(text="👤 Игрок", callback_data=f"u:{uid}")]])

# ======================= АДМИН-ПАНЕЛЬ =======================
class F_(StatesGroup):
    give = State(); take = State(); find = State(); ban = State(); cast = State(); give_to = State(); take_from = State()

def panel_kb():
    r = lambda t, d: B(text=t, callback_data=d)
    return InlineKeyboardMarkup(inline_keyboard=[
        [r("📊 Статистика", "adm:stat"), r("📥 Заявки", "adm:reqs")],
        [r("👥 Пользователи", "adm:users:0"), r("🔎 Найти игрока", "adm:find")],
        [r("⭐ Платежи Stars", "adm:pays")],
        [r("🎁 Выдать монеты", "adm:give"), r("➖ Списать монеты", "adm:take")],
        [r("📣 Рассылка", "adm:cast"), r("🚫 Бан / разбан", "adm:ban")]])

@admin.message(Command("admin"))
async def adm(m: Message, state: FSMContext):
    await state.clear(); await m.answer("🛠 <b>Админ-панель</b>", reply_markup=panel_kb())

@admin.callback_query(F.data == "adm:menu")
async def adm_menu(c: CallbackQuery, state: FSMContext):
    await state.clear(); await c.message.answer("🛠 <b>Админ-панель</b>", reply_markup=panel_kb()); await c.answer()

@admin.callback_query(F.data == "adm:stat")
async def stat(c: CallbackQuery):
    day = int(time.time()) - 86400
    users = q1("SELECT COUNT(*) FROM users")[0]; new = q1("SELECT COUNT(*) FROM users WHERE created>?", (day,))[0]
    banned = q1("SELECT COUNT(*) FROM users WHERE banned=1")[0]
    stars = q1("SELECT COALESCE(SUM(stars),0) FROM payments WHERE status!='refunded'")[0]
    waiting = q1("SELECT COUNT(*) FROM payments WHERE status='paid'")[0]
    rq = q1("SELECT COUNT(*) FROM requests WHERE status='new'")[0]
    refs = q1("SELECT COUNT(*) FROM users WHERE ref IS NOT NULL")[0]
    await c.message.answer(f"📊 <b>Статистика</b>\nИгроков: {users} (за 24ч +{new}), забанено: {banned}\nРефералов: {refs}\n"
        f"Stars получено: {stars} ⭐, ждут выдачи: {waiting}\nНовых заявок: {rq}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text="‹ Панель", callback_data="adm:menu")]]))
    await c.answer()

def req_label(t, a, d):
    if t == "deposit": return f"💰 ПОПОЛНЕНИЕ · Сумма: <b>{f2(a)}</b> монет\nРеквизиты: {html.escape(d or '—')}"
    if t == "withdraw": return f"💸 ВЫВОД · Сумма: <b>{f2(a)}</b> монет\nРеквизиты: {html.escape(d or '—')}"
    try: j = json.loads(d or "{}")
    except Exception: j = {}
    br = html.escape(j.get("brainrot") or "?"); tgn = html.escape(j.get("tg") or "—"); rbx = html.escape(j.get("roblox") or "—")
    if t == "deposit_brainrot": return f"📦 ПОПОЛНЕНИЕ БРЕЙНРОТОМ · {br} ×{int(a)}\nRoblox ник: {rbx}"
    return f"📤 ВЫВОД БРЕЙНРОТА · {br} ×{int(a)}\nTelegram ник: {tgn}\nRoblox ник: {rbx}"

@admin.callback_query(F.data == "adm:reqs")
async def reqs(c: CallbackQuery):
    rows = qa("SELECT id,user_id,type,amount,details FROM requests WHERE status='new' ORDER BY id LIMIT 10")
    if not rows: await c.message.answer("Новых заявок нет ✅")
    for rid, uid, t, a, d in rows:
        await c.message.answer(f"#{rid}\n{uline(uid)}\n{req_label(t, a, d)}", reply_markup=req_kb(rid, uid))
    await c.answer()

@admin.callback_query(F.data.startswith("req:"))
async def req_decide(c: CallbackQuery):
    _, act, rid = c.data.split(":"); rid = int(rid)
    r = q1("SELECT user_id,type,amount,details,status FROM requests WHERE id=?", (rid,))
    if not r or r[4] != "new": return await c.answer("Уже обработано", show_alert=True)
    uid, t, a, d, _ = r
    try: j = json.loads(d or "{}")
    except Exception: j = {}
    br = j.get("brainrot")
    if act == "ok":
        if t == "deposit":
            grant(uid, a, f"deposit:{rid}"); msg = f"Пополнение на {f2(a)} монет одобрено! Открой игру — монеты зачислятся за 30 секунд."
        elif t == "withdraw":
            msg = f"Вывод {f2(a)} монет одобрен, выплата скоро придёт ✅"
        elif t == "deposit_brainrot":
            coins = BRAINROT_PRICES.get(br, 0) * int(a)
            grant(uid, coins, f"deposit_brainrot:{rid}")
            msg = f"Пополнение «{br}» ×{int(a)} одобрено! Начислено {f2(coins)} монет."
        else:
            msg = f"Вывод «{br}» ×{int(a)} одобрен, выплата в Roblox скоро придёт ✅"
        ex("UPDATE requests SET status='ok' WHERE id=?", (rid,))
    else:
        if t == "withdraw":
            grant(uid, a, f"refund:{rid}"); msg = f"Вывод {f2(a)} монет отклонён. Монеты вернутся на баланс в игре."
        elif t == "withdraw_brainrot":
            ex("INSERT INTO inventory(user_id,brainrot,qty) VALUES(?,?,?) ON CONFLICT(user_id,brainrot) DO UPDATE SET qty=qty+excluded.qty", (uid, br, int(a)))
            msg = f"Вывод «{br}» ×{int(a)} отклонён, он вернулся в твой инвентарь в игре."
        elif t == "deposit_brainrot":
            msg = f"Пополнение «{br}» ×{int(a)} отклонено."
        else:
            msg = f"Пополнение на {f2(a)} монет отклонено."
        ex("UPDATE requests SET status='no' WHERE id=?", (rid,))
    try: await bot.send_message(uid, msg)
    except Exception: pass
    await c.message.edit_text(c.message.html_text + ("\n\n✅ ОДОБРЕНО" if act == "ok" else "\n\n❌ ОТКЛОНЕНО")); await c.answer()

@admin.callback_query(F.data == "adm:pays")
async def pays(c: CallbackQuery):
    rows = qa("SELECT id,user_id,stars,coins,status FROM payments ORDER BY id DESC LIMIT 10")
    if not rows: await c.message.answer("Платежей пока нет")
    for pid, uid, s, co, st in rows:
        lab = {"paid": "ждёт выдачи", "given": "выдано", "refunded": "возвращено"}[st]
        await c.message.answer(f"⭐ Платёж #{pid}\n{uline(uid)}\n{s} ⭐ → {f2(co)} монет\nСтатус: <b>{lab}</b>", reply_markup=pay_kb(pid, uid) if st == "paid" else None)
    await c.answer()

@admin.callback_query(F.data.startswith("pay:"))
async def pay_act(c: CallbackQuery):
    _, act, pid = c.data.split(":"); pid = int(pid)
    r = q1("SELECT user_id,stars,coins,status,charge_id FROM payments WHERE id=?", (pid,))
    if not r or r[3] != "paid": return await c.answer("Уже обработано", show_alert=True)
    uid, stars, coins, _, charge = r
    if act == "give":
        grant(uid, coins, f"stars:{pid}"); ex("UPDATE payments SET status='given' WHERE id=?", (pid,))
        try: await bot.send_message(uid, f"✅ Тебе выдано {f2(coins)} монет за {stars} ⭐. Открой игру!")
        except Exception: pass
        tag = "✅ ВЫДАНО"
    else:
        try:
            await bot.refund_star_payment(user_id=uid, telegram_payment_charge_id=charge)
            ex("UPDATE payments SET status='refunded' WHERE id=?", (pid,)); tag = "↩️ ЗВЁЗДЫ ВОЗВРАЩЕНЫ"
            try: await bot.send_message(uid, f"↩️ Тебе возвращено {stars} ⭐.")
            except Exception: pass
        except Exception as e:
            return await c.answer(f"Ошибка возврата: {e}", show_alert=True)
    await c.message.edit_text(c.message.html_text + f"\n\n{tag}"); await c.answer()

async def ask(c: CallbackQuery, state: FSMContext, st, text):
    await state.set_state(st); await c.message.answer(text); await c.answer()

@admin.callback_query(F.data == "adm:give")
async def a_give(c: CallbackQuery, state: FSMContext): await ask(c, state, F_.give, "Пришли <b>ID сумма</b>, например:\n<code>123456789 50</code>")
@admin.callback_query(F.data == "adm:take")
async def a_take(c: CallbackQuery, state: FSMContext): await ask(c, state, F_.take, "Пришли <b>ID сумма</b> для списания, например:\n<code>123456789 50</code>")
@admin.callback_query(F.data == "adm:find")
async def a_find(c: CallbackQuery, state: FSMContext): await ask(c, state, F_.find, "Пришли @username, имя или ID игрока")
@admin.callback_query(F.data == "adm:ban")
async def a_ban(c: CallbackQuery, state: FSMContext): await ask(c, state, F_.ban, "Пришли ID — переключу бан / разбан")
@admin.callback_query(F.data == "adm:cast")
async def a_cast(c: CallbackQuery, state: FSMContext): await ask(c, state, F_.cast, "Пришли текст рассылки (уйдёт всем игрокам). Отмена: /admin")

async def money(m: Message, state: FSMContext, sign):
    try:
        uid, amt = m.text.split(); uid = int(uid); amt = float(amt.replace(",", ".")) * sign
        assert amt != 0 and q1("SELECT 1 FROM users WHERE id=?", (uid,))
    except Exception:
        return await m.answer("Не понял. Формат: <code>ID сумма</code>. Такой игрок должен был запускать бота.")
    grant(uid, amt, "admin"); await state.clear()
    try: await bot.send_message(uid, f"🎁 Админ выдал тебе {f2(amt)} монет! Открой игру." if amt > 0 else f"Админ списал {f2(-amt)} монет.")
    except Exception: pass
    await m.answer(f"Готово: {uline(uid)} → {'+' if amt>0 else ''}{f2(amt)} монет (зачислятся в игре за ~30 сек)", reply_markup=panel_kb())
@admin.message(F_.give)
async def do_give(m: Message, state: FSMContext): await money(m, state, 1)
@admin.message(F_.take)
async def do_take(m: Message, state: FSMContext): await money(m, state, -1)

PAGE = 8
def short(n, u): return (html.escape((n or "Игрок")[:18]) + (f" @{u}" if u else ""))
def users_view(p):
    total = q1("SELECT COUNT(*) FROM users")[0]
    pages = max(1, (total + PAGE - 1) // PAGE); p = max(0, min(p, pages - 1))
    rows = qa("SELECT id,name,username FROM users ORDER BY created DESC LIMIT ? OFFSET ?", (PAGE, p * PAGE))
    text = f"👥 <b>Пользователи</b> ({total}) · стр. {p+1}/{pages}\n\n" + ("\n".join(
        f"{p*PAGE+i+1}. {html.escape(n or 'Игрок')} · {'@'+u if u else 'без username'} · <code>{i_}</code>" for i, (i_, n, u) in enumerate(rows)) or "Пока никого нет")
    kb = [[B(text=f"{short(n,u)}", callback_data=f"u:{i_}")] for i_, n, u in rows]
    nav = []
    if p > 0: nav.append(B(text="◀", callback_data=f"adm:users:{p-1}"))
    if p < pages - 1: nav.append(B(text="▶", callback_data=f"adm:users:{p+1}"))
    if nav: kb.append(nav)
    kb.append([B(text="🔎 Поиск", callback_data="adm:find"), B(text="‹ Панель", callback_data="adm:menu")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)

@admin.callback_query(F.data.startswith("adm:users:"))
async def users_list(c: CallbackQuery):
    text, kb = users_view(int(c.data.split(":")[2]))
    try: await c.message.edit_text(text, reply_markup=kb)
    except Exception: await c.message.answer(text, reply_markup=kb)
    await c.answer()

def card(uid):
    r = q1("SELECT created,ref,banned FROM users WHERE id=?", (uid,))
    if not r: return None, None
    refs = q1("SELECT COUNT(*) FROM users WHERE ref=?", (uid,))[0]
    stars = q1("SELECT COALESCE(SUM(stars),0) FROM payments WHERE user_id=? AND status!='refunded'", (uid,))[0]
    gr = q1("SELECT COALESCE(SUM(amount),0) FROM grants WHERE user_id=?", (uid,))[0]
    wd = q1("SELECT COALESCE(SUM(amount),0) FROM requests WHERE user_id=? AND type='withdraw' AND status='ok'", (uid,))[0]
    text = (f"👤 {uline(uid)}\nС нами с: {datetime.datetime.fromtimestamp(r[0]):%d.%m.%Y}\nПригласил: {refs}"
            f"{'; пришёл от ' + str(r[1]) if r[1] else ''}\nStars оплачено: {stars} ⭐\nВыдано/начислено монет: {f2(gr)}\nВыведено: {f2(wd)}\nБан: {'да' if r[2] else 'нет'}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [B(text="🎁 Выдать монеты", callback_data=f"ug:{uid}"), B(text="➖ Списать монеты", callback_data=f"ut:{uid}")],
        [B(text="✅ Разбанить" if r[2] else "🚫 Забанить", callback_data=f"ub:{uid}")],
        [B(text="👥 Список", callback_data="adm:users:0"), B(text="‹ Панель", callback_data="adm:menu")]])
    return text, kb

@admin.callback_query(F.data.regexp(r"^u:\d+$"))
async def open_card(c: CallbackQuery):
    text, kb = card(int(c.data.split(":")[1]))
    if not text: return await c.answer("Игрок не найден", show_alert=True)
    await c.message.answer(text, reply_markup=kb); await c.answer()

@admin.message(F_.find)
async def do_find(m: Message, state: FSMContext):
    q = (m.text or "").strip().lstrip("@").lower()
    if not q: return await m.answer("Пришли @username, имя или ID")
    if q.isdigit(): rows = qa("SELECT id,name,username FROM users WHERE id=?", (int(q),))
    else: rows = [r for r in qa("SELECT id,name,username FROM users") if q in (r[1] or "").lower() or q in (r[2] or "").lower()][:10]
    if not rows: return await m.answer("Никого не нашёл. Попробуй другое имя или @username (игрок должен был запускать бота).")
    await state.clear()
    if len(rows) == 1:
        text, kb = card(rows[0][0]); return await m.answer(text, reply_markup=kb)
    text = "🔎 <b>Найдено:</b>\n\n" + "\n".join(f"{html.escape(n or 'Игрок')} · {'@'+u if u else 'без username'} · <code>{i_}</code>" for i_, n, u in rows)
    await m.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text=short(n, u), callback_data=f"u:{i_}")] for i_, n, u in rows]))

@admin.callback_query(F.data.regexp(r"^(ug|ut):\d+$"))
async def ask_amount(c: CallbackQuery, state: FSMContext):
    kind, uid = c.data.split(":")
    await state.set_state(F_.give_to if kind == "ug" else F_.take_from); await state.update_data(uid=int(uid))
    await c.message.answer(f"Сколько монет {'выдать' if kind=='ug' else 'списать'} игроку {uline(int(uid))}? Пришли число."); await c.answer()

async def money_to(m: Message, state: FSMContext, sign):
    uid = (await state.get_data()).get("uid")
    try: amt = float((m.text or "").replace(",", ".")) * sign; assert amt != 0 and uid
    except Exception: return await m.answer("Нужно число, например: 50")
    grant(uid, amt, "admin"); await state.clear()
    try: await bot.send_message(uid, f"🎁 Админ выдал тебе {f2(amt)} монет! Открой игру." if amt > 0 else f"Админ списал {f2(-amt)} монет.")
    except Exception: pass
    text, kb = card(uid)
    await m.answer(f"✅ {uline(uid)}: {'+' if amt>0 else ''}{f2(amt)} монет (зачислятся в игре за ~30 сек)\n\n{text}", reply_markup=kb)
@admin.message(F_.give_to)
async def do_give_to(m: Message, state: FSMContext): await money_to(m, state, 1)
@admin.message(F_.take_from)
async def do_take_from(m: Message, state: FSMContext): await money_to(m, state, -1)

@admin.callback_query(F.data.regexp(r"^ub:\d+$"))
async def toggle_ban(c: CallbackQuery):
    uid = int(c.data.split(":")[1]); r = q1("SELECT banned FROM users WHERE id=?", (uid,))
    if not r: return await c.answer("Не найден", show_alert=True)
    ex("UPDATE users SET banned=? WHERE id=?", (0 if r[0] else 1, uid))
    text, kb = card(uid)
    try: await c.message.edit_text(text, reply_markup=kb)
    except Exception: pass
    await c.answer("Разбанен" if r[0] else "Забанен")

@admin.message(F_.ban)
async def do_ban(m: Message, state: FSMContext):
    if not (m.text or "").strip().isdigit(): return await m.answer("Нужен числовой ID")
    uid = int(m.text); r = q1("SELECT banned FROM users WHERE id=?", (uid,))
    if not r: return await m.answer("Игрок не найден")
    ex("UPDATE users SET banned=? WHERE id=?", (0 if r[0] else 1, uid)); await state.clear()
    await m.answer(f"{uline(uid)} — {'РАЗБАНЕН' if r[0] else 'ЗАБАНЕН'}", reply_markup=panel_kb())

@admin.message(F_.cast)
async def do_cast(m: Message, state: FSMContext):
    await state.clear(); ok = bad = 0
    for (uid,) in qa("SELECT id FROM users WHERE banned=0"):
        try: await bot.send_message(uid, m.html_text); ok += 1
        except Exception: bad += 1
        await asyncio.sleep(0.05)
    await m.answer(f"📣 Рассылка завершена: доставлено {ok}, не доставлено {bad}", reply_markup=panel_kb())

# ======================= API ДЛЯ МИНИ-АППА =======================
@web.middleware
async def cors(req, handler):
    resp = web.Response() if req.method == "OPTIONS" else await handler(req)
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "POST,OPTIONS"
    return resp

async def auth(req):
    body = await req.json()
    return check_init(body.get("initData", "")), body
J = web.json_response

async def api_me(req):
    u, _ = await auth(req)
    if not u: return J({"ok": False, "error": "auth"})
    uid = u["id"]; upsert(uid, u.get("first_name"), u.get("username"))
    rows = qa("SELECT id,amount FROM grants WHERE user_id=? AND delivered=0", (uid,))
    total = sum(r[1] for r in rows)
    if rows: ex(f"UPDATE grants SET delivered=1 WHERE id IN ({','.join('?'*len(rows))})", [r[0] for r in rows])
    if total: ex("UPDATE users SET balance=balance+? WHERE id=?", (total, uid))
    claimed = [r[0] for r in qa("SELECT key FROM claims WHERE user_id=?", (uid,))]
    refs = [{"name": r[0] or "Игрок", "ts": r[1]} for r in qa("SELECT name,created FROM users WHERE ref=? ORDER BY created DESC LIMIT 50", (uid,))]
    row = q1("SELECT balance,clicks FROM users WHERE id=?", (uid,)) or (0, 0)
    inv = {r[0]: r[1] for r in qa("SELECT brainrot,qty FROM inventory WHERE user_id=? AND qty>0", (uid,))}
    return J({"ok": True, "grant": total, "claimed": claimed, "refs": refs, "star_rate": STARS_TO_COINS, "ref_reward": REWARD["ref"],
              "balance": row[0] or 0, "clicks": row[1] or 0, "inventory": inv, "brainrots": BRAINROTS})

async def api_sync(req):
    u, body = await auth(req)
    if not u: return J({"ok": False, "error": "auth"})
    uid = u["id"]; upsert(uid, u.get("first_name"), u.get("username"))
    try:
        coins = max(-1e9, min(1e9, float(body.get("coins") or 0)))
        clicks = max(0, min(100000, int(body.get("clicks") or 0)))
    except Exception:
        return J({"ok": False, "error": "bad"})
    ex("UPDATE users SET balance=MAX(0,balance+?), clicks=clicks+?, week_clicks=week_clicks+? WHERE id=?", (coins, clicks, clicks, uid))
    bal = q1("SELECT balance FROM users WHERE id=?", (uid,))[0] or 0
    return J({"ok": True, "balance": bal})

async def api_add_inventory(req):
    u, body = await auth(req)
    if not u: return J({"ok": False, "error": "auth"})
    name = str(body.get("brainrot") or ""); qty = max(1, min(1000, int(body.get("qty") or 1)))
    if name not in BRAINROTS: return J({"ok": False, "error": "bad"})
    ex("INSERT INTO inventory(user_id,brainrot,qty) VALUES(?,?,?) ON CONFLICT(user_id,brainrot) DO UPDATE SET qty=qty+excluded.qty", (u["id"], name, qty))
    return J({"ok": True})

async def api_leaderboard(req):
    all_top = [{"name": r[0] or "Игрок", "clicks": r[1]} for r in qa("SELECT name,clicks FROM users WHERE clicks>0 ORDER BY clicks DESC LIMIT 10")]
    week_top = [{"name": r[0] or "Игрок", "clicks": r[1]} for r in qa("SELECT name,week_clicks FROM users WHERE week_clicks>0 ORDER BY week_clicks DESC LIMIT 10")]
    return J({"ok": True, "all": all_top, "week": week_top})

async def api_claim(req):
    u, body = await auth(req)
    if not u: return J({"ok": False, "error": "auth"})
    uid, kind = u["id"], body.get("kind")
    if kind not in REWARD or kind == "ref": return J({"ok": False, "error": "bad"})
    key = f"repost:{datetime.datetime.utcnow():%Y-%m-%d}" if kind == "repost" else kind
    if q1("SELECT 1 FROM claims WHERE user_id=? AND key=?", (uid, key)): return J({"ok": False, "error": "already"})
    if kind in CHANNELS:
        try:
            mem = await bot.get_chat_member(CHANNELS[kind], uid)
            ok = mem.status in ("member", "administrator", "creator") or (mem.status == "restricted" and getattr(mem, "is_member", False))
        except Exception as e:
            print("get_chat_member error:", e); return J({"ok": False, "error": "bot_error"})
        if not ok: return J({"ok": False, "error": "not_subscribed"})
    ex("INSERT INTO claims(user_id,key,ts) VALUES(?,?,?)", (uid, key, int(time.time())))
    grant(uid, REWARD[kind], key, delivered=1)
    return J({"ok": True, "amount": REWARD[kind]})

async def api_prepare_share(req):
    u, _ = await auth(req)
    if not u: return J({"ok": False, "error": "auth"})
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{u['id']}"
    msg = await bot.save_prepared_inline_message(user_id=u["id"], result=InlineQueryResultArticle(
        id="share", title="Brainrot Clicker", input_message_content=InputTextMessageContent(message_text="🔥 Играй в Brainrot Clicker и фарми монеты!"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text="Играть", url=link)]])),
        allow_user_chats=True, allow_group_chats=True, allow_channel_chats=True, allow_bot_chats=False)
    return J({"ok": True, "id": msg.id})

async def api_stars_invoice(req):
    u, body = await auth(req)
    if not u: return J({"ok": False, "error": "auth"})
    try: stars = int(body.get("stars"))
    except Exception: return J({"ok": False, "error": "limit"})
    if not 1 <= stars <= 50000: return J({"ok": False, "error": "limit"})
    link = await bot.create_invoice_link(title="Пополнение монет", description=f"{stars} ⭐ → {f2(stars*STARS_TO_COINS)} монет",
        payload=f"dep:{u['id']}:{stars}", provider_token="", currency="XTR", prices=[LabeledPrice(label="Монеты", amount=stars)])
    return J({"ok": True, "link": link})

async def api_request(req):
    u, body = await auth(req)
    if not u: return J({"ok": False, "error": "auth"})
    uid = u["id"]; t = body.get("type")
    if t in ("deposit", "withdraw"):
        try: amount = float(body.get("amount"))
        except Exception: return J({"ok": False, "error": "bad"})
        if not 0 < amount < 1e9: return J({"ok": False, "error": "bad"})
        if t == "withdraw" and amount < MIN_WD: return J({"ok": False, "error": "min"})
        details = str(body.get("details") or "")[:200]
    elif t in ("deposit_brainrot", "withdraw_brainrot"):
        br = str(body.get("brainrot") or "")
        if br not in BRAINROTS: return J({"ok": False, "error": "bad"})
        try: amount = max(1, min(1000, int(body.get("qty") or 1)))
        except Exception: amount = 1
        if t == "withdraw_brainrot":
            have = q1("SELECT qty FROM inventory WHERE user_id=? AND brainrot=?", (uid, br))
            if not have or have[0] < amount: return J({"ok": False, "error": "no_item"})
            ex("UPDATE inventory SET qty=qty-? WHERE user_id=? AND brainrot=?", (amount, uid, br))
        details = json.dumps({"brainrot": br, "tg": str(body.get("tg") or ""), "roblox": str(body.get("roblox") or "")[:40]}, ensure_ascii=False)[:300]
    else:
        return J({"ok": False, "error": "bad"})
    ex("INSERT INTO requests(user_id,type,amount,details,ts) VALUES(?,?,?,?,?)", (uid, t, amount, details, int(time.time())))
    rid = q1("SELECT last_insert_rowid()")[0]
    await bot.send_message(ADMIN_ID, f"#{rid}  🕒 {now()}\n{uline(uid)}\n{req_label(t, amount, details)}", reply_markup=req_kb(rid, uid))
    return J({"ok": True})

async def page_index(req):
    r = web.FileResponse(BASE / "index.html"); r.headers["Cache-Control"] = "no-cache"; return r

async def page_img(req):                     # картинки отдаём без учёта регистра: 0.PNG = 0.png
    name = req.match_info["name"].lower(); d = BASE / "img"
    if d.is_dir():
        for f in d.iterdir():
            if f.name.lower() == name:
                r = web.FileResponse(f); r.headers["Cache-Control"] = "public, max-age=86400"; return r
    raise web.HTTPNotFound()

async def weekly_payout_loop():
    while True:
        try:
            wk = cur_week()
            last = q1("SELECT value FROM meta WHERE key='payout_week'")
            if not last or last[0] != wk:
                rows = qa("SELECT id,week_clicks FROM users WHERE week_clicks>0 ORDER BY week_clicks DESC LIMIT 10")
                for i, (uid, wc) in enumerate(rows):
                    r = TOP_REWARDS[i] if i < len(TOP_REWARDS) else 0
                    if r:
                        grant(uid, r, f"top_week:{wk}")
                        try: await bot.send_message(uid, f"🏆 ТОП недели по кликам! Ты на {i+1}-м месте — начислено {r} монет.")
                        except Exception: pass
                ex("UPDATE users SET week_clicks=0")
                ex("INSERT INTO meta(key,value) VALUES('payout_week',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (wk,))
        except Exception as e:
            print("weekly payout error:", e)
        await asyncio.sleep(3600)

async def main():
    dp.include_router(admin)
    app = web.Application(middlewares=[cors])
    app.add_routes([web.get("/", page_index), web.get("/index.html", page_index), web.get("/img/{name}", page_img),
                    web.post("/api/me", api_me), web.post("/api/claim", api_claim), web.post("/api/prepare_share", api_prepare_share),
                    web.post("/api/stars_invoice", api_stars_invoice), web.post("/api/request", api_request),
                    web.post("/api/sync", api_sync), web.post("/api/add_inventory", api_add_inventory), web.post("/api/leaderboard", api_leaderboard),
                    web.options("/{tail:.*}", lambda r: web.Response())])
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    asyncio.create_task(weekly_payout_loop())
    await dp.start_polling(bot, allowed_updates=["message", "callback_query", "pre_checkout_query"])

if __name__ == "__main__":
    asyncio.run(main())
