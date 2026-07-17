# Common: navigation, settings, main menu

TEXTS = {
    "ua": {
        "ask_extend_days": "На скільки днів продовжити курс?",
        "ask_new_name": "📝 Введіть ваше нове ПІБ:",
        "ask_new_tz": "🌍 Напишіть місто і країну, де ви зараз перебуваєте\n\nНаприклад: <code>Варшава, Польща</code>",
        "ask_feedback": "💬 Опишіть проблему або пропозицію одним повідомленням — я передам це розробнику.",
        "btn_add": "➕ Додати",
        "btn_back": "⬅️ Назад",
        "btn_change_name": "✏️ Змінити ПІБ",
        "btn_change_tz": "🌍 Змінити часовий пояс",
        "btn_feedback": "💬 Зворотній зв'язок",
        "btn_help": "ℹ️ Допомога",
        "btn_lang": "🌐 Мова",
        "btn_list": "📋 Список",
        "btn_medicines": "💊 Ліки",
        "btn_no": "❌ Ні",
        "btn_placeholder": "Оберіть дію...",
        "btn_report": "📤 Звіти",
        "btn_settings": "👤 Налаштування",
        "btn_stats": "📊 Статистика",
        "btn_yes": "✅ Так",
        "edit_enter_new": "✏️ Введіть нове значення для поля <b>{field}</b>:",
        "edit_success": "✅ Поле успішно оновлено!",
        "edit_what": "✏️ <b>Що змінити?</b>",
        "err_invalid_number": "❌ Будь ласка, введіть коректне число.",
        "err_stock": "❌ Введіть ціле додатне число (наприклад: 20):",
        "err_timezone_place": "❌ Не вдалося визначити місто. Спробуйте ще раз у форматі: <b>Місто, Країна</b> (наприклад: <code>Варшава, Польща</code>).",
        "err_feedback_unavailable": "⚠️ Наразі не вдалося передати повідомлення розробнику. Спробуйте пізніше.",
        "fallback_text": "👋 Натисніть /start щоб розпочати або оберіть дію з меню.",
        "feedback_admin_header": "📩 <b>Нове звернення</b>\nВід: {name} (@{username}, id: <code>{user_id}</code>)\n\n{text}",
        "feedback_sent": "✅ Дякуємо! Ваше повідомлення передано розробнику.",
        "help_text": (
            "<b>📖 Доступні команди:</b>\n\n"
            "/start – перезапустити бота\n"
            "/help – ця довідка\n\n"
            "<b>💊 Ліки:</b>\n"
            "• Додавання препаратів з нагадуваннями (розклад, дозування, залишок в аптечці)\n"
            "• Підтвердження або пропуск прийому прямо з нагадування\n"
            "• Редагування, продовження курсу та видалення ліків\n\n"
            "<b>📝 Рецепти:</b>\n"
            "• Облік рецептів з терміном дії та дозволеною кількістю\n"
            "• Нагадування про закінчення терміну дії рецепту\n"
            "• Відмітка купівлі з автоматичним поповненням аптечки\n\n"
            "<b>🤖 AI-асистент:</b>\n"
            "• Можу сам додати, змінити чи видалити препарат або рецепт прямо з розмови – просто попросіть\n"
            "• Можна навіть надіслати голосове повідомлення замість тексту\n\n"
            "<b>📤 Звіти:</b> Excel/CSV з повною історією прийому препаратів\n"
            "<b>🌐 Мова:</b> Українська, English, Русский\n\n"
        ),
        "lang_changed": "✅ Мову змінено на українську.",
        "lang_choose": "🌐 Оберіть мову / Choose language:",
        "med_not_found": "❌ Препарат не знайдено",
        "name_updated": "✅ ПІБ успішно оновлено!",
        "not_set": "Не вказано",
        "settings_title": "👤 <b>Ваш профіль</b>\n\n📝 ПІБ: <b>{name}</b>\n🌍 Часовий пояс: <b>{tz}</b>\n",
        "start_text": (
            "👋 Привіт, <b>{name}</b>!\n\n"
            "Я твій медичний асистент. Ось що я вмію:\n\n"
            "💊 <b>Ліки</b> – додавати, переглядати, редагувати та видаляти нагадування, стежити за залишком в аптечці\n"
            "📝 <b>Рецепти</b> – зберігати рецепти, термін дії та кількість, яку ще можна купити\n"
            "📤 <b>Звіти</b> – отримати Excel/CSV-звіт про прийом препаратів\n"
            "👤 <b>Налаштування</b> – змінити ПІБ, мову, часовий пояс, зворотній зв'язок\n\n"
            "💬 Просто напишіть (або надішліть голосове) з метою щось додати/змінити/видалити – я відповім та зроблю це як AI-асистент прямо тут, у чаті.\n\n"
            "ℹ️ <b>/help</b> – детальніше про можливості бота\n\n"
            "⚠️ Якщо помітили помилку в роботі бота або виникли питання, пишіть у <b>👤 Налаштування</b> -> <b>💬 Зворотній зв'язок</b>\n\n"
            "Оберіть дію з меню нижче 👇"
        ),
        "tz_updated": "✅ Часовий пояс успішно оновлено!\n⏰ <i>Усі ваші нагадування автоматично переведені на новий час.</i>",
        "tz_updated_with_name": "✅ Часовий пояс оновлено: <b>{tz}</b>\n⏰ <i>Усі ваші нагадування автоматично переведені на новий час.</i>",
        "generic_error": "⚠️ Сталася помилка під час обробки вашого запиту.",
    },
    "en": {
        "ask_extend_days": "How many days to extend the course?",
        "ask_new_name": "📝 Enter your new Full Name:",
        "ask_new_tz": "🌍 Tell me the city and country you're currently in\n\nFor example: <code>Warsaw, Poland</code>",
        "ask_feedback": "💬 Describe the problem or suggestion in one message — I'll pass it on to the developer.",
        "btn_add": "➕ Add",
        "btn_back": "⬅️ Back",
        "btn_change_name": "✏️ Change Name",
        "btn_change_tz": "🌍 Change Timezone",
        "btn_feedback": "💬 Feedback",
        "btn_help": "ℹ️ Help",
        "btn_lang": "🌐 Language",
        "btn_list": "📋 List",
        "btn_medicines": "💊 Medicines",
        "btn_no": "❌ No",
        "btn_placeholder": "Choose an action...",
        "btn_report": "📤 Reports",
        "btn_settings": "👤 Settings",
        "btn_stats": "📊 Statistics",
        "btn_yes": "✅ Yes",
        "edit_enter_new": "✏️ Enter new value for <b>{field}</b>:",
        "edit_success": "✅ Field successfully updated!",
        "edit_what": "✏️ <b>What to change?</b>",
        "err_invalid_number": "❌ Please enter a valid number.",
        "err_stock": "❌ Enter a positive integer (e.g., 20):",
        "err_timezone_place": "❌ Could not find that place. Please try again in the format: <b>City, Country</b> (e.g. <code>Warsaw, Poland</code>).",
        "err_feedback_unavailable": "⚠️ Could not deliver your message to the developer right now. Please try again later.",
        "fallback_text": "👋 Press /start or choose an action from the menu.",
        "feedback_admin_header": "📩 <b>New feedback</b>\nFrom: {name} (@{username}, id: <code>{user_id}</code>)\n\n{text}",
        "feedback_sent": "✅ Thank you! Your message has been passed on to the developer.",
        "help_text": (
            "<b>📖 Available commands:</b>\n\n"
            "/start – restart the bot\n"
            "/help – show this help\n\n"
            "<b>💊 Medicines:</b>\n"
            "• Add medicines with reminders (schedule, dosage, remaining stock)\n"
            "• Confirm or skip a dose right from the reminder\n"
            "• Edit, extend the course, or delete medicines\n\n"
            "<b>📝 Prescriptions:</b>\n"
            "• Track prescriptions with expiration date and allowed quantity\n"
            "• Get reminded before a prescription expires\n"
            "• Mark purchases with automatic stock top-up\n\n"
            "<b>🤖 AI Assistant:</b>\n"
            "• I can add, update, or remove a medicine or prescription for you right from the conversation – just ask\n"
            "• You can even send a voice message instead of typing\n\n"
            "<b>📤 Reports:</b> Excel/CSV with your full medication history\n"
            "<b>🌐 Language:</b> Ukrainian, English, Russian\n\n"
        ),
        "lang_changed": "✅ Language changed to English.",
        "lang_choose": "🌐 Choose language / Оберіть мову:",
        "med_not_found": "❌ Medicine not found",
        "name_updated": "✅ Name successfully updated!",
        "not_set": "Not set",
        "settings_title": "👤 <b>Your Profile</b>\n\n📝 Name: <b>{name}</b>\n🌍 Timezone: <b>{tz}</b>",
        "start_text": (
            "👋 Hi, <b>{name}</b>!\n\n"
            "I am your medical assistant. I can help with:\n\n"
            "💊 <b>Medicines</b> – add, view, edit and delete reminders, track remaining stock\n"
            "📝 <b>Prescriptions</b> – keep track of prescriptions, expiration dates, and remaining allowed quantity\n"
            "📤 <b>Reports</b> – get an Excel/CSV report about your medication intake\n"
            "👤 <b>Settings</b> – change your name, language, timezone, feedback\n\n"
            "💬 Just send me (typed or as a voice message) with aim to add/change/remove something – I will answer and take care of it as an AI assistant right here in the chat.\n\n"
            "ℹ️ <b>/help</b> – more details about the bot's features\n\n"
            "⚠️ If you notice an error in the bot's operation or have any questions, write to <b>👤 Settings</b> -> <b>💬 Feedback</b>\n\n"
            "Choose an action from the menu below 👇"
        ),
        "tz_updated": "✅ Timezone successfully updated!\n⏰ <i>All your reminders have been automatically adjusted to the new time.</i>",
        "tz_updated_with_name": "✅ Timezone updated: <b>{tz}</b>\n⏰ <i>All your reminders have been automatically adjusted to the new time.</i>",
        "generic_error": "⚠️ An error occurred while processing your request.",
    },
    "ru": {
        "ask_extend_days": "На сколько дней продлить курс?",
        "ask_new_name": "📝 Введите ваше новое ФИО:",
        "ask_new_tz": "🌍 Напишите город и страну, где вы сейчас находитесь\n\nНапример: <code>Варшава, Польша</code>",
        "ask_feedback": "💬 Опишите проблему или предложение одним сообщением — я передам это разработчику.",
        "btn_add": "➕ Добавить",
        "btn_back": "⬅️ Назад",
        "btn_change_name": "✏️ Изменить ФИО",
        "btn_change_tz": "🌍 Изменить часовой пояс",
        "btn_feedback": "💬 Обратная связь",
        "btn_help": "ℹ️ Помощь",
        "btn_lang": "🌐 Язык",
        "btn_list": "📋 Список",
        "btn_medicines": "💊 Лекарства",
        "btn_no": "❌ Нет",
        "btn_placeholder": "Выберите действие...",
        "btn_report": "📤 Отчёты",
        "btn_settings": "👤 Настройки",
        "btn_stats": "📊 Статистика",
        "btn_yes": "✅ Да",
        "edit_enter_new": "✏️ Введите новое значение для поля <b>{field}</b>:",
        "edit_success": "✅ Поле успешно обновлено!",
        "edit_what": "✏️ <b>Что изменить?</b>",
        "err_invalid_number": "❌ Пожалуйста, введите корректное число.",
        "err_stock": "❌ Введите целое положительное число (например: 20):",
        "err_timezone_place": "❌ Не удалось определить город. Попробуйте ещё раз в формате: <b>Город, Страна</b> (например: <code>Варшава, Польша</code>).",
        "err_feedback_unavailable": "⚠️ Не удалось передать сообщение разработчику сейчас. Попробуйте позже.",
        "fallback_text": "👋 Нажмите /start чтобы начать или выберите действие из меню.",
        "feedback_admin_header": "📩 <b>Новое обращение</b>\nОт: {name} (@{username}, id: <code>{user_id}</code>)\n\n{text}",
        "feedback_sent": "✅ Спасибо! Ваше сообщение передано разработчику.",
        "help_text": (
            "<b>📖 Доступные команды:</b>\n\n"
            "/start – перезапустить бота\n"
            "/help – эта справка\n\n"
            "<b>💊 Лекарства:</b>\n"
            "• Добавление препаратов с напоминаниями (расписание, дозировка, остаток в аптечке)\n"
            "• Подтверждение или пропуск приёма прямо из напоминания\n"
            "• Редактирование, продление курса и удаление лекарств\n\n"
            "<b>📝 Рецепты:</b>\n"
            "• Учёт рецептов со сроком действия и разрешённым количеством\n"
            "• Напоминание об истечении срока действия рецепта\n"
            "• Отметка покупки с автоматическим пополнением аптечки\n\n"
            "<b>🤖 AI-ассистент:</b>\n"
            "• Могу сам добавить, изменить или удалить препарат либо рецепт прямо из разговора – просто попросите\n"
            "• Можно даже отправить голосовое сообщение вместо текста\n\n"
            "<b>📤 Отчёты:</b> Excel/CSV с полной историей приёма препаратов\n"
            "<b>🌐 Язык:</b> Украинский, English, Русский\n\n"
        ),
        "lang_changed": "✅ Язык изменён на русский.",
        "lang_choose": "🌐 Оберіть мову / Choose language / Выберите язык:",
        "med_not_found": "❌ Препарат не найден",
        "name_updated": "✅ ФИО успешно обновлено!",
        "not_set": "Не указано",
        "settings_title": "👤 <b>Ваш профиль</b>\n\n📝 ФИО: <b>{name}</b>\n🌍 Часовой пояс: <b>{tz}</b>\n",
        "start_text": (
            "👋 Привет, <b>{name}</b>!\n\n"
            "Я твой медицинский ассистент. Вот что я умею:\n\n"
            "💊 <b>Лекарства</b> – добавлять, просматривать, редактировать и удалять напоминания, следить за остатком в аптечке\n"
            "📝 <b>Рецепты</b> – хранить рецепты, срок действия и оставшееся разрешённое количество\n"
            "📤 <b>Отчёты</b> – получить Excel/CSV-отчёт о приёме препаратов\n"
            "👤 <b>Настройки</b> – изменить ФИО, язык, часовой пояс, обратная связь\n\n"
            "💬 Просто напишите (или отправьте голосовое) с целью что-то добавить/изменить/удалить – я отвечу и сделаю это как AI-ассистент прямо здесь, в чате.\n\n"
            "ℹ️ <b>/help</b> – детальнее о возможностях бота\n\n"
            "⚠️ Если заметили ошибку в работе бота/возникли вопросы, пишите в <b>👤 Настройки</b> -> <b>💬 Обратная связь</b>\n\n"
            "Выберите действие из меню ниже 👇"
        ),
        "tz_updated": "✅ Часовой пояс успешно обновлён!\n⏰ <i>Все ваши напоминания автоматически переведены на новое время.</i>",
        "tz_updated_with_name": "✅ Часовой пояс обновлён: <b>{tz}</b>\n⏰ <i>Все ваши напоминания автоматически переведены на новое время.</i>",
        "generic_error": "⚠️ Произошла ошибка при обработке вашего запроса.",
    },
}
