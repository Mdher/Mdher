import logging
import sqlite3
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from datetime import datetime, timedelta
import asyncio

# إعدادات التصحيح
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# إعداد قاعدة البيانات
def setup_database():
    conn = sqlite3.connect("subscribers.db")
    cursor = conn.cursor()

    # إنشاء جدول المشتركين
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subscribers (
        user_id INTEGER PRIMARY KEY,
        username TEXT NOT NULL,
        activation_code TEXT,
        activation_date DATE,
        expiry_date DATE,
        subscription_status TEXT
    );
    """)

    # إنشاء جدول أكواد التفعيل
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS activation_codes (
        code TEXT PRIMARY KEY,
        is_used INTEGER DEFAULT 0
    );
    """)

    conn.commit()
    conn.close()

# تحميل أكواد التفعيل من ملف Excel
def load_activation_codes_from_excel(file_path):
    conn = sqlite3.connect("subscribers.db")
    cursor = conn.cursor()

    # تحميل الملف واستعراض الأعمدة الصحيحة
    df = pd.read_excel(file_path)
    codes = df[['subscription_numbers', 'Status']].values.tolist()

    for code, status in codes:
        if status == 'unused':
            cursor.execute("INSERT OR IGNORE INTO activation_codes (code, is_used) VALUES (?, 0)")

    conn.commit()
    conn.close()

# دالة للتحقق من صلاحية الكود
def is_valid_code(code):
    conn = sqlite3.connect("subscribers.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM activation_codes WHERE code = ? AND is_used = 0", (code,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

# تحديث حالة الكود المستخدم
def mark_code_as_used(code):
    conn = sqlite3.connect("subscribers.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE activation_codes SET is_used = 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()

# إضافة أو تمديد اشتراك المستخدم
def add_or_extend_subscription(user_id, username, code):
    conn = sqlite3.connect("subscribers.db")
    cursor = conn.cursor()

    cursor.execute("SELECT expiry_date FROM subscribers WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if result:
        expiry_date = datetime.strptime(result[0], '%Y-%m-%d')
        if expiry_date > datetime.now():
            new_expiry = expiry_date + timedelta(days=30)
        else:
            new_expiry = datetime.now() + timedelta(days=30)
        cursor.execute("UPDATE subscribers SET expiry_date = ?, activation_code = ? WHERE user_id = ?",
                       (new_expiry.strftime('%Y-%m-%d'), code, user_id))
    else:
        expiry_date = datetime.now() + timedelta(days=30)
        cursor.execute("INSERT INTO subscribers (user_id, username, activation_code, activation_date, expiry_date, subscription_status) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, username, code, datetime.now().strftime('%Y-%m-%d'), expiry_date.strftime('%Y-%m-%d'), "active"))

    conn.commit()
    conn.close()
    return expiry_date

# التحقق مما إذا كان المستخدم مشتركًا
def is_subscribed(user_id):
    conn = sqlite3.connect("subscribers.db")
    cursor = conn.cursor()
    cursor.execute("SELECT expiry_date FROM subscribers WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    if result:
        expiry_date = datetime.strptime(result[0], '%Y-%m-%d')
        if expiry_date > datetime.now():
            return True, expiry_date
    return False, None

# إرسال رسالة للمالك
async def notify_owner(context, message):
    owner_id = 5278280995  # معرف مالك البوت
    await context.bot.send_message(chat_id=owner_id, text=message)

# وظائف البوت
async def start(update: Update, context):
    logger.info(f"Received /start command from {update.message.from_user.username}")
    keyboard = [
        [InlineKeyboardButton("تفعيل الاشتراك", callback_data='activate_subscription')],
        [InlineKeyboardButton("الحصول على بطاقة اشتراك", callback_data='get_subscription_card')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("مرحبًا! اختر إحدى الخيارات:", reply_markup=reply_markup)

async def button_handler(update: Update, context):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    username = query.from_user.username

    if query.data == 'activate_subscription':
        subscribed, expiry_date = is_subscribed(user_id)
        if subscribed:
            await query.edit_message_text(f"أنت مشترك بالفعل. تاريخ انتهاء الاشتراك: {expiry_date.strftime('%Y-%m-%d')}")
        else:
            await query.edit_message_text("أدخل كود التفعيل:")
            context.user_data['awaiting_code'] = True

    elif query.data == 'get_subscription_card':
        await query.edit_message_text("احصل على بطاقة الاشتراك من مكتبة ألف باء.")

async def activate_code(update: Update, context):
    if context.user_data.get('awaiting_code', False):
        user_id = update.message.from_user.id
        username = update.message.from_user.username
        code = update.message.text

        if is_valid_code(code):
            expiry_date = add_or_extend_subscription(user_id, username, code)
            mark_code_as_used(code)
            await update.message.reply_text(f"تم تفعيل الاشتراك بنجاح! تاريخ انتهاء الاشتراك: {expiry_date.strftime('%Y-%m-%d')}")
            await notify_owner(context, f"المستخدم {username} قام بتفعيل الاشتراك. تاريخ انتهاء الاشتراك: {expiry_date.strftime('%Y-%m-%d')}")
            context.user_data['awaiting_code'] = False
        else:
            await update.message.reply_text("الكود غير صحيح أو مستخدم. احصل على كود جديد من مكتبة ألف باء.")
    else:
        username = update.message.text
        conn = sqlite3.connect("subscribers.db")
        cursor = conn.cursor()
        cursor.execute("SELECT expiry_date FROM subscribers WHERE username = ?", (username,))
        result = cursor.fetchone()
        conn.close()

        if result:
            expiry_date = datetime.strptime(result[0], '%Y-%m-%d')
            if expiry_date > datetime.now():
                await update.message.reply_text(f"المستخدم {username} مشترك. تاريخ انتهاء الاشتراك: {expiry_date.strftime('%Y-%m-%d')}")
            else:
                await update.message.reply_text(f"المستخدم {username} انتهى اشتراكه.")
        else:
            await update.message.reply_text(f"المستخدم {username} غير مشترك.")

# التحقق من انتهاء الاشتراك كل يوم
async def check_subscriptions(context):
    conn = sqlite3.connect("subscribers.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, expiry_date FROM subscribers WHERE subscription_status = 'active'")
    users = cursor.fetchall()

    for user in users:
        user_id, username, expiry_date = user
        expiry_date = datetime.strptime(expiry_date, '%Y-%m-%d')

        if expiry_date <= datetime.now():
            cursor.execute("UPDATE subscribers SET subscription_status = 'inactive' WHERE user_id = ?", (user_id,))
            await context.bot.send_message(chat_id=user_id, text="انتهت صلاحية اشتراكك.")
            await notify_owner(context, f"انتهت صلاحية اشتراك المستخدم {username}.")
    
    conn.commit()
    conn.close()

# إعداد البوت
async def main():
    logger.info("Starting the bot...")
    app = ApplicationBuilder().token('7306640917:AAGB_Tebf5XE6804r_Ao-0dHm_xB3LnsGHo').build()

    # إضافة معالجات الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, activate_code))

    # إعداد قاعدة البيانات وتحميل أكواد التفعيل
    setup_database()
    load_activation_codes_from_excel('ActivationNumbers.xlsx')

    # بدء البوت
    await app.initialize()
    await app.start()

    # تشغيل التحقق اليومي من الاشتراكات
    job_queue = app.job_queue
    job_queue.run_daily(check_subscriptions, time=datetime.now().time())

    logger.info("Bot has started and is polling...")
    await app.updater.start_polling()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
