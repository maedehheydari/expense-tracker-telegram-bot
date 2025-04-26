import os
import logging
import sqlite3
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot import apihelper
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get('BOT_TOKEN')

logging.basicConfig(level=logging.INFO)

bot = telebot.TeleBot(BOT_TOKEN)


# Database setup
conn = sqlite3.connect('expenses.db', check_same_thread=False)
cursor = conn.cursor()

# Create tables if they don't exist
cursor.execute('''
    CREATE TABLE IF NOT EXISTS groups (
        chat_id INTEGER PRIMARY KEY,
        title TEXT
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS members (
        user_id INTEGER,
        chat_id INTEGER,
        username TEXT,
        PRIMARY KEY (user_id, chat_id)
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        name TEXT,
        amount REAL,
        payer_id INTEGER,
        date TEXT
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS expense_members (
        expense_id INTEGER,
        member_id INTEGER,
        PRIMARY KEY (expense_id, member_id)
    )
''')

conn.commit()

user_states = {}
expense_cache = {}

# Start message when bot is added to a group or started
@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    if message.chat.type in ['group', 'supergroup']:
        cursor.execute('SELECT * FROM groups WHERE chat_id = ?', (chat_id,))
        group = cursor.fetchone()
        if not group:
            cursor.execute('INSERT INTO groups (chat_id, title) VALUES (?, ?)', (chat_id, message.chat.title))
            conn.commit()
        bot.send_message(
            chat_id,
            "👋 Hello! I am your group expense manager bot. Use the buttons below to get started.",
            reply_markup=get_main_menu()
        )
    else:
        bot.send_message(
            chat_id,
            "Please add me to a group to start managing expenses!"
        )

# Main menu with buttons
def get_main_menu():
    menu = InlineKeyboardMarkup()
    menu.row_width = 2
    menu.add(
        InlineKeyboardButton("➕ Add Expense", callback_data="add_expense"),
        InlineKeyboardButton("📊 View Balance", callback_data="view_balance"),
        InlineKeyboardButton("📜 Expense History", callback_data="expense_history"),
        InlineKeyboardButton("🧾 Settle Up", callback_data="see_transactions"),
        InlineKeyboardButton("ℹ️ Help", callback_data="help")
    )
    return menu

# Help message
def send_help(chat_id):
    help_text = (
        "💡 *Help Menu*\n\n"
        "Use the buttons or commands below to interact with the bot:\n\n"
        "/addexpense - Add a new expense\n"
        "/viewbalance - View current balances\n"
        "/showtransactions - View required transactions to settle all balances to zero\n"
        "/history - View expense history\n"
        "/help - Show this help message"
    )
    logging.info("yooooo2 Chat ID: %s", chat_id)
    bot.send_message(chat_id, help_text, parse_mode="Markdown", reply_markup=get_main_menu())

# Handle button clicks
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    logging.info("yooooo3 Chat ID: %s", chat_id)
    data = call.data

    if data == "add_expense":
        start_add_expense(call)
    elif data == "view_balance":
        show_balances(chat_id)
    elif data == "expense_history":
        show_expense_history(chat_id)
    elif data == "help":
        send_help(chat_id)
    elif data == "see_transactions":
        show_transactions(chat_id)
    elif data.startswith("select_payer_"):
        handle_payer_selection(call)
    elif data.startswith("select_member_"):
        add_member_to_expense(call)
    elif data == "expense_done":
        finalize_expense(call)
    elif data.startswith("edit_expense_"):
        edit_expense(call)
    elif data.startswith("delete_expense_"):
        delete_expense(call)
    else:
        bot.answer_callback_query(call.id, "Unknown action.")

# Commands
@bot.message_handler(commands=['addexpense'])
def cmd_add_expense(message):
    start_add_expense(message)

@bot.message_handler(commands=['viewbalance'])
def cmd_view_balance(message):
    chat_id = message.chat.id
    show_balances(chat_id)

@bot.message_handler(commands=['history'])
def cmd_expense_history(message):
    chat_id = message.chat.id
    show_expense_history(chat_id)

@bot.message_handler(commands=['help'])
def cmd_help(message):
    chat_id = message.chat.id
    send_help(chat_id)

@bot.message_handler(commands=['showtransactions'])
def cmd_show_transactions(message):
    chat_id = message.chat.id
    show_transactions(chat_id)

# Start adding an expense
def start_add_expense(message_or_call):
    if isinstance(message_or_call, telebot.types.Message):
        message = message_or_call
        user_id = message.from_user.id
    elif isinstance(message_or_call, telebot.types.CallbackQuery):
        call = message_or_call
        message = call.message
        user_id = call.from_user.id
    else:
        return

    chat_id = message.chat.id
    user_states[user_id] = 'awaiting_expense_details'
    expense_cache[user_id] = {'chat_id': chat_id}
    bot.send_message(
        chat_id,
        f"📝 Please enter the expense details in the following format:\n\n"
        f"*Name*, *Amount*\n\n"
        f"Example:\nDinner, 100",
        parse_mode="Markdown"
    )

# Handle user input for expense creation
@bot.message_handler(func=lambda message: user_states.get(message.from_user.id) == 'awaiting_expense_details')
def handle_expense_creation(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip()

    try:
        details = [item.strip() for item in text.split(",")]
        if len(details) != 2:
            raise ValueError("Input does not have exactly two components.")
        name, amount_str = details
        try:
            amount = float(amount_str)
        except ValueError:
            raise ValueError("Amount must be a number.")
        expense_cache[user_id]['name'] = name
        expense_cache[user_id]['amount'] = amount
        user_states[user_id] = 'awaiting_payer_selection'
        bot.send_message(chat_id, "👥 Please select the *payer*:", reply_markup=get_member_keyboard(chat_id, "select_payer_"))
    except ValueError as e:
        bot.send_message(chat_id, f"❌ {e}\n\nPlease enter the expense details in the format:\nName, Amount", parse_mode="Markdown")

# Get keyboard of group members
def get_member_keyboard(chat_id, callback_prefix, selected_members=None):
    if selected_members is None:
        selected_members = []
    
    cursor.execute('SELECT user_id, username FROM members WHERE chat_id = ?', (chat_id,))
    members = cursor.fetchall()
    logging.info("yooooo4 members: %s", members)
    keyboard = InlineKeyboardMarkup()
    
    for user_id, username in members:
        if user_id in selected_members:
            button_text = f"🔘 {username}" 
        else:
            button_text = f"{username}" 
            
        callback_data = f"{callback_prefix}{user_id}"
        keyboard.add(InlineKeyboardButton(button_text, callback_data=callback_data))
    
    if callback_prefix == "select_member_":
        keyboard.add(InlineKeyboardButton("✅ Done", callback_data="expense_done"))
    
    logging.info("yooooo5 keyboard: %s", keyboard)
    return keyboard

# Handle payer selection
def handle_payer_selection(call):
    user_id = call.from_user.id
    selected_user_id = int(call.data.split("_")[-1])
    
    if user_id not in expense_cache:
        expense_cache[user_id] = {'chat_id': call.message.chat.id}
    
    expense_cache[user_id]['payer_id'] = selected_user_id
    
    if 'members' not in expense_cache[user_id]:
        expense_cache[user_id]['members'] = []
    
    user_states[user_id] = 'awaiting_member_selection'
    
    bot.edit_message_text(
        "👥 Payer selected. Now select the *members involved* by clicking on their names. You can select multiple members. Click a member again to deselect. When you're done, click ✅ Done.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=get_member_keyboard(call.message.chat.id, "select_member_", expense_cache[user_id]['members'])
    )

# Handle member selection
def add_member_to_expense(call):
    user_id = call.from_user.id
    selected_member_id = int(call.data.split("_")[-1])
    
    if user_id not in expense_cache:
        expense_cache[user_id] = {'chat_id': call.message.chat.id, 'members': []}
    elif 'members' not in expense_cache[user_id]:
        expense_cache[user_id]['members'] = []
    
    members = expense_cache[user_id]['members']
    
    if selected_member_id in members:
        members.remove(selected_member_id)
        bot.answer_callback_query(call.id, "Member removed")
    else:
        members.append(selected_member_id)
        bot.answer_callback_query(call.id, "Member added")
    
    keyboard = get_member_keyboard(call.message.chat.id, "select_member_", members)
    bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=keyboard
    )

# Finalize the expense
def finalize_expense(call):
    user_id = call.from_user.id
    expense = expense_cache.get(user_id)
    if expense and 'members' in expense and expense['members']:
        cursor.execute('''
            INSERT INTO expenses (chat_id, name, amount, payer_id, date)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            expense['chat_id'],
            expense['name'],
            expense['amount'],
            expense['payer_id'],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))

        expense_id = cursor.lastrowid
        for member_id in expense['members']:
            cursor.execute('''
                INSERT INTO expense_members (expense_id, member_id)
                VALUES (?, ?)
            ''', (expense_id, member_id))
        conn.commit()

        bot.send_message(
            call.message.chat.id,
            f"✅ Expense added:\n"
            f"*Name*: {expense['name']}\n"
            f"*Amount*: {expense['amount']}\n"
            f"*Payer*: {get_username(expense['payer_id'], expense['chat_id'])}\n"
            f"*Members*: {', '.join([get_username(uid, expense['chat_id']) for uid in expense['members']])}",
            parse_mode="Markdown",
            reply_markup=get_main_menu()
        )
    else:
        bot.send_message(call.message.chat.id, "❌ No members selected. Expense not added.")
    user_states.pop(user_id, None)
    expense_cache.pop(user_id, None)

# Show balances
def show_balances(chat_id):
    balances = get_balances(chat_id)
    balance_message = "📊 *Current Balances:*\n"
    for user_id, balance in balances.items():
        username = get_username(user_id, chat_id)
        balance_message += f"{username}: {balance:.2f}\n"
    bot.send_message(chat_id, balance_message, parse_mode="Markdown", reply_markup=get_main_menu())

# Retrieve current balances for each member
def get_balances(chat_id):
    cursor.execute('SELECT user_id, username FROM members WHERE chat_id = ?', (chat_id,))
    members = cursor.fetchall()
    balances = {user_id: 0.0 for user_id, _ in members}

    cursor.execute('SELECT * FROM expenses WHERE chat_id = ?', (chat_id,))
    expenses = cursor.fetchall()
    for expense in expenses:
        expense_id = expense[0]
        amount = expense[3]
        payer_id = expense[4]
        cursor.execute('SELECT member_id FROM expense_members WHERE expense_id = ?', (expense_id,))
        expense_members = cursor.fetchall()
        member_ids = [member_id for (member_id,) in expense_members]
        if member_ids:
            split_amount = amount / len(member_ids)
            for member_id in member_ids:
                balances[member_id] -= split_amount
            balances[payer_id] += amount
    return balances

# Show expense history
def show_expense_history(chat_id):
    cursor.execute('SELECT * FROM expenses WHERE chat_id = ? ORDER BY date DESC LIMIT 10', (chat_id,))
    expenses = cursor.fetchall()
    if expenses:
        message = "📜 *Expense History:*\n"
        for expense in expenses:
            expense_id = expense[0]
            name = expense[2]
            amount = expense[3]
            payer_id = expense[4]
            date = expense[5]
            cursor.execute('SELECT member_id FROM expense_members WHERE expense_id = ?', (expense_id,))
            members = cursor.fetchall()
            member_names = ', '.join([get_username(member_id, chat_id) for (member_id,) in members])
            message += (
                f"\n*{name}* - {amount}\n"
                f"👤 Payer: {get_username(payer_id, chat_id)}\n"
                f"👥 Members: {member_names}\n"
                f"📅 Date: {date}\n"
            )
        bot.send_message(chat_id, message, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=get_main_menu())
    else:
        bot.send_message(chat_id, "No expenses found.", reply_markup=get_main_menu())

# Edit expense (placeholder for functionality)
def edit_expense(call):
    bot.answer_callback_query(call.id, "Edit functionality is under development.")

# Delete expense
def delete_expense(call):
    expense_id = int(call.data.split("_")[-1])
    cursor.execute('DELETE FROM expenses WHERE id = ?', (expense_id,))
    cursor.execute('DELETE FROM expense_members WHERE expense_id = ?', (expense_id,))
    conn.commit()
    bot.answer_callback_query(call.id, "Expense deleted.")
    bot.send_message(call.message.chat.id, "✅ Expense has been deleted.", reply_markup=get_main_menu())

def get_username(user_id, chat_id):
    cursor.execute('SELECT username FROM members WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
    result = cursor.fetchone()
    return result[0] if result else "Unknown"

# When a user sends a message in the group, add them to the members list
@bot.message_handler(func=lambda message: (
    message.chat.type in ['group', 'supergroup'] and
    message.from_user.id not in user_states
))
def add_member_to_group(message):
    chat_id = message.chat.id
    user = message.from_user
    user_id = user.id
    username = user.username or user.first_name
    cursor.execute('SELECT * FROM members WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
    if not cursor.fetchone():
        cursor.execute('INSERT INTO members (user_id, chat_id, username) VALUES (?, ?, ?)', (user_id, chat_id, username))
        conn.commit()
        logging.info(f"Added user {username} to group {chat_id}")

# Show transactions
def show_transactions(chat_id):
    balances = get_balances(chat_id)
    transactions = calculate_optimal_transactions(balances)

    if transactions:
        message = "🧾 *Transactions to Settle Balances:*\n"
        for (from_user, to_user, amount) in transactions:
            message += f"{get_username(from_user, chat_id)} -> {get_username(to_user, chat_id)}: {amount:.2f}\n"
    else:
        message = "✅ All balances are already settled. No transactions needed."

    bot.send_message(chat_id, message, parse_mode="Markdown", reply_markup=get_main_menu())

def calculate_optimal_transactions(balances):
    debtors = []
    creditors = []

    for user_id, balance in balances.items():
        if balance < 0:
            debtors.append((user_id, -balance))  # Store as positive amount owed
        elif balance > 0:
            creditors.append((user_id, balance))

    debtors.sort(key=lambda x: x[1], reverse=True)
    creditors.sort(key=lambda x: x[1], reverse=True)

    transactions = []
    i, j = 0, 0
    while i < len(debtors) and j < len(creditors):
        debtor, debt_amount = debtors[i]
        creditor, credit_amount = creditors[j]

        settled_amount = min(debt_amount, credit_amount)
        transactions.append((debtor, creditor, settled_amount))

        debt_amount -= settled_amount
        credit_amount -= settled_amount

        debtors[i] = (debtor, debt_amount)
        creditors[j] = (creditor, credit_amount)

        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1

    return transactions

@bot.message_handler(func=lambda message: True)
def get_chat_id(message):
    logging.info("Chat ID: %s", message.chat.id)
    
    # Only add users from group or supergroup chats
    if message.chat.type in ['group', 'supergroup']:
        add_member_to_group(message)

# Start the bot with infinity polling
if __name__ == '__main__':
    logging.info("Bot is polling...")
    bot.infinity_polling(timeout=20, long_polling_timeout=30)
