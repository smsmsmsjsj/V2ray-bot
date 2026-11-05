import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# تنظیمات لاگ‌گیری
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('سلام! ربات V2Ray شما با موفقیت روی Render روشن شد و آماده به کار است.')

def main():
    # دریافت توکن از متغیرهای محیطی Render
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("خطا: توکن ربات (BOT_TOKEN) تنظیم نشده است.")
        return

    application = ApplicationBuilder().token(token).build()

    # دستور استارت
    application.add_handler(CommandHandler("start", start))

    print("ربات در حال اجراست...")
    application.run_polling()

if __name__ == '__main__':
    main()
