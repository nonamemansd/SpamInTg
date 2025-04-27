import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
import threading
import time
import json
import os
import asyncio
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError, PhoneCodeInvalidError, ApiIdInvalidError,
    ApiIdPublishedFloodError, FloodWaitError
)
from telethon.errors.rpcerrorlist import (
    PeerIdInvalidError, UserNotParticipantError, ChatWriteForbiddenError
)
from telethon.tl.types import PeerUser, PeerChat, PeerChannel
# Опционально, для явного указания типа сессии
# from telethon.sessions import StringSession, SQLiteSession

# --- Константы ---
CONFIG_FILE = "telegram_sender_config.json"
SESSION_DIR = "sessions"

# --- Глобальные переменные ---
sending_thread = None
stop_sending_flag = threading.Event()

# --- Функции конфигурации ---
def load_config():
    default_config = {"accounts": {}, "groups": []}
    try:
        if not os.path.exists(SESSION_DIR):
            os.makedirs(SESSION_DIR)
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # Гарантируем наличие ключей и правильные типы
                if "accounts" not in config or not isinstance(config.get("accounts"), dict):
                     config["accounts"] = {}
                if "groups" not in config or not isinstance(config.get("groups"), list):
                     config["groups"] = []
                return config
        else:
             return default_config
    except (json.JSONDecodeError, IOError, TypeError) as e:
        messagebox.showerror("Ошибка загрузки конфигурации", f"Не удалось загрузить {CONFIG_FILE}:\n{e}\nБудет использована конфигурация по умолчанию.")
        if os.path.exists(CONFIG_FILE):
            try:
                os.remove(CONFIG_FILE)
                messagebox.showinfo("Конфигурация", f"Поврежденный файл {CONFIG_FILE} удален.")
            except OSError as remove_err:
                 messagebox.showerror("Ошибка", f"Не удалось удалить поврежденный файл {CONFIG_FILE}: {remove_err}")
        return default_config

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except IOError as e:
        messagebox.showerror("Ошибка сохранения конфигурации", f"Не удалось сохранить {CONFIG_FILE}:\n{e}")
    except TypeError as e:
         messagebox.showerror("Ошибка сохранения конфигурации", f"Ошибка данных при сохранении:\n{e}")

# --- Класс приложения ---
class TelegramSenderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Telegram Рассыльщик")
        self.root.geometry("650x550")
        # Создаем директорию сессий при инициализации, если ее нет
        if not os.path.exists(SESSION_DIR):
            try:
                os.makedirs(SESSION_DIR)
            except OSError as e:
                messagebox.showerror("Ошибка создания папки", f"Не удалось создать папку '{SESSION_DIR}': {e}")
        self.config = load_config() # Загружаем конфиг после проверки папки

        style = ttk.Style()
        style.theme_use('clam')
        self.notebook = ttk.Notebook(root)

        self.tab_accounts = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_accounts, text='Аккаунты')
        self.create_accounts_tab()

        self.tab_groups = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_groups, text='Группы/Каналы')
        self.create_groups_tab()

        self.tab_sender = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_sender, text='Рассылка')
        self.create_sender_tab()

        self.notebook.pack(expand=True, fill='both')
        self.load_accounts_list()
        self.load_groups_list()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # --- Вспомогательная функция для simpledialog ---
    def ask_string_threadsafe(self, title, prompt, show=''):
        result = None
        event = threading.Event()
        def ask():
            nonlocal result
            try:
                result = simpledialog.askstring(title, prompt, show=show, parent=self.root)
            finally:
                event.set()
        self.root.after(0, ask)
        event.wait()
        return result

    # --- Создание элементов вкладок ---
    def create_accounts_tab(self):
        frame = self.tab_accounts
        add_frame = ttk.LabelFrame(frame, text="Добавить/Авторизовать аккаунт", padding="10")
        add_frame.pack(fill='x', pady=5)
        ttk.Label(add_frame, text="API ID:").grid(row=0, column=0, padx=5, pady=2, sticky='w')
        self.api_id_entry = ttk.Entry(add_frame, width=30)
        self.api_id_entry.grid(row=0, column=1, padx=5, pady=2, sticky='ew')
        ttk.Label(add_frame, text="API Hash:").grid(row=1, column=0, padx=5, pady=2, sticky='w')
        self.api_hash_entry = ttk.Entry(add_frame, width=30)
        self.api_hash_entry.grid(row=1, column=1, padx=5, pady=2, sticky='ew')
        ttk.Label(add_frame, text="Номер телефона (+код страны):").grid(row=2, column=0, padx=5, pady=2, sticky='w')
        self.phone_entry = ttk.Entry(add_frame, width=30)
        self.phone_entry.grid(row=2, column=1, padx=5, pady=2, sticky='ew')
        self.add_account_button = ttk.Button(add_frame, text="Добавить/Проверить", command=self.add_account_thread)
        self.add_account_button.grid(row=3, column=0, columnspan=2, pady=10)
        list_frame = ttk.LabelFrame(frame, text="Привязанные аккаунты (файлы сессий)", padding="10")
        list_frame.pack(fill='both', expand=True, pady=5)
        self.accounts_listbox = tk.Listbox(list_frame, height=8)
        self.accounts_listbox.pack(side='left', fill='both', expand=True, padx=(0, 5))
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.accounts_listbox.yview)
        scrollbar.pack(side='right', fill='y')
        self.accounts_listbox.config(yscrollcommand=scrollbar.set)
        remove_button = ttk.Button(frame, text="Удалить выбранный аккаунт (сессию и запись)", command=self.remove_account)
        remove_button.pack(pady=5)
        self.account_status_label = ttk.Label(frame, text="", foreground="blue")
        self.account_status_label.pack(pady=5)

    def create_groups_tab(self):
        frame = self.tab_groups
        add_frame = ttk.LabelFrame(frame, text="Добавить группу/канал", padding="10")
        add_frame.pack(fill='x', pady=5)
        ttk.Label(add_frame, text="ID, username (@...) или ссылка (https://t.me/...):").pack(anchor='w', padx=5, pady=2)
        self.group_entry = ttk.Entry(add_frame, width=40)
        self.group_entry.pack(fill='x', padx=5, pady=2)
        add_button = ttk.Button(add_frame, text="Добавить в список", command=self.add_group)
        add_button.pack(pady=10)
        list_frame = ttk.LabelFrame(frame, text="Список групп/каналов для рассылки", padding="10")
        list_frame.pack(fill='both', expand=True, pady=5)
        self.groups_listbox = tk.Listbox(list_frame, height=10)
        self.groups_listbox.pack(side='left', fill='both', expand=True, padx=(0, 5))
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.groups_listbox.yview)
        scrollbar.pack(side='right', fill='y')
        self.groups_listbox.config(yscrollcommand=scrollbar.set)
        remove_button = ttk.Button(frame, text="Удалить выбранную группу", command=self.remove_group)
        remove_button.pack(pady=5)

    def create_sender_tab(self):
        frame = self.tab_sender
        msg_frame = ttk.LabelFrame(frame, text="Сообщение для рассылки", padding="10")
        msg_frame.pack(fill='x', pady=5)
        self.message_text = scrolledtext.ScrolledText(msg_frame, height=8, width=60, wrap=tk.WORD)
        self.message_text.pack(fill='both', expand=True)
        timer_frame = ttk.LabelFrame(frame, text="Настройки отправки", padding="10")
        timer_frame.pack(fill='x', pady=5)
        ttk.Label(timer_frame, text="Интервал между циклами рассылки (секунды):").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.interval_spinbox = ttk.Spinbox(timer_frame, from_=10, to=86400, width=8)
        self.interval_spinbox.set(60)
        self.interval_spinbox.grid(row=0, column=1, padx=5, pady=5, sticky='w')
        control_frame = ttk.Frame(frame, padding="10")
        control_frame.pack(pady=10)
        self.start_button = ttk.Button(control_frame, text="СТАРТ Рассылки", command=self.start_sending_thread)
        self.start_button.pack(side='left', padx=10)
        self.stop_button = ttk.Button(control_frame, text="СТОП Рассылки", command=self.stop_sending, state=tk.DISABLED)
        self.stop_button.pack(side='left', padx=10)
        log_frame = ttk.LabelFrame(frame, text="Лог рассылки", padding="10")
        log_frame.pack(fill='both', expand=True, pady=5)
        self.log_text = scrolledtext.ScrolledText(log_frame, height=10, width=70, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(fill='both', expand=True)
        self.log_text.tag_config("error", foreground="red")
        self.log_text.tag_config("warning", foreground="orange")
        self.log_text.tag_config("success", foreground="green")
        self.log_text.tag_config("info", foreground="black")

    # --- Логика вкладки "Аккаунты" ---
    def load_accounts_list(self):
        self.accounts_listbox.delete(0, tk.END)
        for session_name in self.config.get("accounts", {}).keys():
            self.accounts_listbox.insert(tk.END, session_name)

    def add_account_thread(self):
        api_id = self.api_id_entry.get().strip()
        api_hash = self.api_hash_entry.get().strip()
        phone = self.phone_entry.get().strip()
        if not api_id or not api_hash or not phone:
            messagebox.showerror("Ошибка", "Заполните все поля (API ID, API Hash, Телефон).")
            return
        try:
            int(api_id) # Проверяем, что API ID - число
        except ValueError:
            messagebox.showerror("Ошибка", "API ID должен быть числом.")
            return
        if not phone.startswith('+'):
            messagebox.showerror("Ошибка", "Номер телефона должен начинаться с '+' и содержать код страны (например, +71234567890).")
            return

        self.add_account_button.config(state=tk.DISABLED)
        self.update_account_status("Запуск процесса авторизации...", "blue")
        thread = threading.Thread(target=self.do_add_account, args=(api_id, api_hash, phone), daemon=True)
        thread.start()

    def do_add_account(self, api_id_str, api_hash, phone):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = None

        async def async_auth_logic():
            nonlocal client
            api_id = int(api_id_str)
            session_file_name = f"{phone}.session"
            session_path = os.path.join(SESSION_DIR, session_file_name)

            client = TelegramClient(session_path, api_id, api_hash, loop=loop)
            try:
                self.update_account_status(f"Подключение к Telegram для {phone}...", "blue")
                await client.connect()

                if not await client.is_user_authorized():
                    self.update_account_status(f"Отправка кода на {phone}...", "blue")
                    try:
                        await client.send_code_request(phone)
                    except FloodWaitError as e:
                        self.update_account_status(f"FloodWait: дождитесь {e.seconds} сек.", "red")
                        return False
                    except Exception as e:
                        self.update_account_status(f"Ошибка отправки кода: {e}", "red")
                        return False

                    code = self.ask_string_threadsafe("Код подтверждения", f"Введите код для {phone}:")
                    if not code:
                        self.update_account_status("Ввод кода отменен пользователем.", "orange")
                        return False

                    try:
                        self.update_account_status("Вход в аккаунт...", "blue")
                        await client.sign_in(phone, code)
                    except PhoneCodeInvalidError:
                        self.update_account_status("Неверный код подтверждения.", "red")
                        return False
                    except SessionPasswordNeededError:
                        self.update_account_status("Требуется пароль двухфакторной аутентификации (2FA)...", "blue")
                        password = self.ask_string_threadsafe("Пароль 2FA", f"Введите пароль 2FA для {phone}:", show='*')
                        if not password:
                            self.update_account_status("Ввод пароля 2FA отменен.", "orange")
                            return False
                        try:
                            await client.sign_in(password=password)
                        except Exception as e:
                            self.update_account_status(f"Ошибка входа с паролем 2FA: {e}", "red")
                            return False
                    except Exception as e:
                        self.update_account_status(f"Ошибка входа: {e}", "red")
                        return False

                me = await client.get_me()
                user_info = f"{me.first_name} {me.last_name or ''} (@{me.username})" if me else phone
                success_msg = f"Аккаунт {user_info} ({phone}) успешно авторизован!"
                self.update_account_status(success_msg, "green")
                self.log_message(success_msg, "success")

                if "accounts" not in self.config: self.config["accounts"] = {}
                self.config["accounts"][session_file_name] = {"api_id": api_id, "api_hash": api_hash, "phone": phone}
                save_config(self.config)

                self.root.after(0, self.load_accounts_list)
                return True

            except (ApiIdInvalidError, ApiIdPublishedFloodError):
                self.update_account_status("Ошибка: Неверный API ID или API Hash.", "red")
                return False
            except ConnectionError as e:
                self.update_account_status(f"Ошибка подключения к Telegram: {e}", "red")
                return False
            except Exception as e:
                self.update_account_status(f"Неожиданная ошибка авторизации: {e}", "red")
                import traceback
                print(f"--- Traceback auth ({phone}) ---")
                traceback.print_exc()
                print("--- End Traceback ---")
                return False
            finally:
                if client and client.is_connected():
                    await client.disconnect()
                    self.log_message(f"Клиент для {phone} отключен.", "info")

        try:
            success = loop.run_until_complete(async_auth_logic())
            if not success:
                pass
        except Exception as e:
             self.update_account_status(f"Критическая ошибка потока авторизации: {e}", "red")
             import traceback
             print("--- Critical Thread Error (do_add_account) ---")
             traceback.print_exc()
             print("--- End Critical ---")
        finally:
            self.root.after(0, lambda: self.add_account_button.config(state=tk.NORMAL))

    def update_account_status(self, message, color):
        def update():
            self.account_status_label.config(text=message, foreground=color)
        self.root.after(0, update)

    def remove_account(self):
        selected_indices = self.accounts_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Аккаунт не выбран", "Пожалуйста, выберите аккаунт из списка для удаления.")
            return

        selected_session_name = self.accounts_listbox.get(selected_indices[0])
        session_path = os.path.join(SESSION_DIR, selected_session_name)

        if messagebox.askyesno("Подтверждение удаления", f"Вы уверены, что хотите удалить аккаунт '{selected_session_name}'?\nБудет удалена запись из конфигурации и файл сессии ({selected_session_name})."):
            removed_config = False
            removed_file = False

            if "accounts" in self.config and selected_session_name in self.config["accounts"]:
                try:
                    del self.config["accounts"][selected_session_name]
                    save_config(self.config)
                    self.log_message(f"Запись аккаунта '{selected_session_name}' удалена из конфигурации.", "info")
                    removed_config = True
                except Exception as e:
                    self.log_message(f"Ошибка удаления записи '{selected_session_name}' из конфига: {e}", "error")
                    messagebox.showerror("Ошибка конфигурации", f"Не удалось удалить запись из {CONFIG_FILE}:\n{e}")

            try:
                if os.path.exists(session_path):
                    os.remove(session_path)
                    self.log_message(f"Файл сессии '{selected_session_name}' удален.", "info")
                    removed_file = True
                else:
                     self.log_message(f"Файл сессии '{selected_session_name}' не найден для удаления.", "warning")
                     if removed_config: removed_file = True

            except OSError as e:
                self.log_message(f"Ошибка удаления файла сессии '{selected_session_name}': {e}", "error")
                messagebox.showerror("Ошибка файла", f"Не удалось удалить файл сессии '{session_path}':\n{e}")

            if removed_config or removed_file:
                 self.accounts_listbox.delete(selected_indices[0])
                 self.update_account_status(f"Аккаунт '{selected_session_name}' удален.", "blue")
            else:
                 self.update_account_status(f"Не удалось полностью удалить '{selected_session_name}'. См. лог.", "orange")
                 self.load_accounts_list()

    # --- Логика вкладки "Группы" ---
    def load_groups_list(self):
        self.groups_listbox.delete(0, tk.END)
        for group in self.config.get("groups", []):
            self.groups_listbox.insert(tk.END, group)

    def add_group(self):
        group_id = self.group_entry.get().strip()
        if not group_id:
            messagebox.showwarning("Пустое поле", "Введите ID, username или ссылку на группу/канал.")
            return

        if "groups" not in self.config or not isinstance(self.config["groups"], list):
            self.config["groups"] = []

        if group_id not in self.config["groups"]:
            self.config["groups"].append(group_id)
            self.groups_listbox.insert(tk.END, group_id)
            save_config(self.config)
            self.group_entry.delete(0, tk.END)
            self.log_message(f"Группа/канал '{group_id}' добавлена в список.", "info")
        else:
            messagebox.showinfo("Уже существует", f"Группа/канал '{group_id}' уже есть в списке.")
            self.group_entry.delete(0, tk.END)

    def remove_group(self):
        selected_indices = self.groups_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Не выбрано", "Выберите группу/канал из списка для удаления.")
            return

        selected_group = self.groups_listbox.get(selected_indices[0])

        if "groups" in self.config and isinstance(self.config["groups"], list) and selected_group in self.config["groups"]:
            try:
                self.config["groups"].remove(selected_group)
                self.groups_listbox.delete(selected_indices[0])
                save_config(self.config)
                self.log_message(f"Группа/канал '{selected_group}' удалена из списка.", "info")
            except Exception as e:
                 messagebox.showerror("Ошибка", f"Не удалось удалить '{selected_group}': {e}")
                 self.log_message(f"Ошибка удаления группы '{selected_group}': {e}", "error")
                 self.load_groups_list()
        else:
            messagebox.showerror("Ошибка синхронизации", f"Группа/канал '{selected_group}' не найдена в конфигурации. Список будет перезагружен.")
            self.log_message(f"Попытка удалить '{selected_group}', но не найдено в config['groups'].", "warning")
            self.load_groups_list()

    # --- Логика вкладки "Рассылка" ---
    def log_message(self, message, level="info"):
        def update():
            try:
                self.log_text.config(state=tk.NORMAL)
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                tag = level
                self.log_text.insert(tk.END, f"[{timestamp}] {message}\n", tag)
                self.log_text.config(state=tk.DISABLED)
                self.log_text.see(tk.END)
            except tk.TclError as e:
                 print(f"Ошибка логгирования (возможно, окно закрыто): {e}")
        if self.root.winfo_exists():
            self.root.after(0, update)
        else:
             print(f"[LOG (window closed)] {message}")

    def start_sending_thread(self):
        global sending_thread, stop_sending_flag

        message = self.message_text.get("1.0", tk.END).strip()
        if not message:
            messagebox.showwarning("Нет сообщения", "Введите текст сообщения для рассылки.")
            return

        account_session_names = list(self.config.get("accounts", {}).keys())
        if not account_session_names:
            messagebox.showwarning("Нет аккаунтов", "Добавьте и авторизуйте хотя бы один аккаунт на вкладке 'Аккаунты'.")
            return

        groups = self.config.get("groups", [])
        if not groups:
            messagebox.showwarning("Нет групп", "Добавьте хотя бы одну группу или канал для рассылки на вкладке 'Группы/Каналы'.")
            return

        try:
            interval_str = self.interval_spinbox.get()
            interval = int(interval_str)
            if interval < 5:
                messagebox.showwarning("Малый интервал", "Интервал слишком мал. Рекомендуется установить хотя бы 5 секунд, чтобы избежать ограничений Telegram.")
                interval = 5
                self.interval_spinbox.set(5)
        except ValueError:
            messagebox.showerror("Неверный интервал", f"Значение интервала '{interval_str}' не является целым числом.")
            return

        if sending_thread and sending_thread.is_alive():
            messagebox.showwarning("Уже запущено", "Рассылка уже выполняется. Остановите текущую перед запуском новой.")
            return

        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.log_message("--- Запуск сеанса рассылки ---", "info")
        stop_sending_flag.clear()

        sending_thread = threading.Thread(
            target=self.do_sending_loop,
            args=(list(account_session_names), list(groups), message, interval),
            daemon=True)
        sending_thread.start()

    def stop_sending(self):
        global stop_sending_flag, sending_thread
        if sending_thread and sending_thread.is_alive():
            self.log_message("--- Получен запрос на остановку рассылки... ---", "warning")
            stop_sending_flag.set()
            self.stop_button.config(text="Остановка...", state=tk.DISABLED)
        else:
             self.log_message("Рассылка не была запущена.", "info")
             self.start_button.config(state=tk.NORMAL)
             self.stop_button.config(text="СТОП Рассылки", state=tk.DISABLED)

    def do_sending_loop(self, account_session_names, groups, message, interval):
        """Основной цикл рассылки (выполняется в отдельном потоке)."""
        global stop_sending_flag

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def async_sending_logic():
            current_account_index = 0
            active_session_names = list(account_session_names)
            client = None

            self.log_message("Начинаем цикл рассылки.", "info")

            while not stop_sending_flag.is_set():
                self.log_message(f"--- Начало итерации цикла ---", "info")

                if not active_session_names:
                    self.log_message("Нет доступных для работы аккаунтов. Рассылка остановлена.", "error")
                    break

                if current_account_index >= len(active_session_names):
                    current_account_index = 0
                    self.log_message(f"Все аккаунты ({len(active_session_names)}) прошли круг. Начинаем заново.", "info")

                session_file_name = active_session_names[current_account_index]
                session_path = os.path.join(SESSION_DIR, session_file_name)
                self.log_message(f"Выбран аккаунт [{current_account_index+1}/{len(active_session_names)}]: {session_file_name}", "info")

                account_info = self.config.get("accounts", {}).get(session_file_name)
                if not account_info or "api_id" not in account_info or "api_hash" not in account_info:
                    self.log_message(f"Ошибка: Отсутствуют данные API ID/Hash для {session_file_name} в конфигурации. Аккаунт пропущен и удален из текущей сессии.", "error")
                    active_session_names.pop(current_account_index)
                    await asyncio.sleep(1)
                    continue

                api_id = account_info["api_id"]
                api_hash = account_info["api_hash"]

                try:
                    client = TelegramClient(session_path, api_id, api_hash, loop=loop)
                    self.log_message(f"Подключение аккаунта {session_file_name}...", "info")
                    await client.connect()

                    if not await client.is_user_authorized():
                        self.log_message(f"Аккаунт {session_file_name} не авторизован или сессия истекла. Требуется повторная авторизация. Аккаунт пропущен и удален из текущей сессии.", "error")
                        await client.disconnect()
                        client = None
                        active_session_names.pop(current_account_index)
                        continue

                    me = await client.get_me()
                    self.log_message(f"Аккаунт {session_file_name} ({me.first_name if me else 'N/A'}) успешно подключен.", "success")

                    groups_sent_count_this_account = 0
                    connection_error_break = False
                    for group_index, group_identifier in enumerate(groups):
                        if stop_sending_flag.is_set():
                            self.log_message("Обнаружен флаг остановки во время перебора групп.", "warning")
                            break

                        self.log_message(f"[{session_file_name}] Попытка отправки в [{group_index+1}/{len(groups)}]: {group_identifier}", "info")

                        pause_after_group = True

                        try:
                            entity = await client.get_entity(group_identifier)
                            await client.send_message(entity, message)
                            self.log_message(f"[{session_file_name}] -> {group_identifier}: Сообщение успешно отправлено.", "success")
                            groups_sent_count_this_account += 1

                            pause_between_groups = max(1, min(5, interval // max(1, len(groups)) // 2))
                            self.log_message(f"Пауза {pause_between_groups} сек перед следующей группой...", "info")
                            await asyncio.sleep(pause_between_groups)
                            pause_after_group = False

                        except PeerIdInvalidError:
                            self.log_message(f"[{session_file_name}] Ошибка: Неверный ID, username или ссылка '{group_identifier}'. Проверьте правильность.", "error")
                        except (ValueError, TypeError) as err_val_type:
                            self.log_message(f"[{session_file_name}] Ошибка: Не удалось найти группу/канал '{group_identifier}'. Возможно, опечатка или аккаунт не имеет доступа. ({err_val_type})", "error")
                        except (ChatWriteForbiddenError, UserNotParticipantError):
                            self.log_message(f"[{session_file_name}] Ошибка: Нет прав на отправку сообщений в '{group_identifier}' или аккаунт не участник.", "error")
                        except FloodWaitError as flood_err:
                            pause_after_group = False
                            wait_time = flood_err.seconds + 5
                            self.log_message(f"[{session_file_name}] FloodWait! Получено ограничение от Telegram. Ждем {wait_time} секунд...", "warning")
                            try:
                                await asyncio.wait_for(stop_sending_flag.wait(), timeout=wait_time)
                                self.log_message("Остановка во время ожидания FloodWait.", "warning")
                                break
                            except asyncio.TimeoutError:
                                self.log_message("Время FloodWait истекло, продолжаем со следующей группой.", "info")
                                continue
                        except ConnectionError as conn_err:
                            connection_error_break = True
                            pause_after_group = False
                            self.log_message(f"[{session_file_name}] Ошибка соединения во время отправки: {conn_err}. Прерываем отправку для этого аккаунта.", "error")
                            break
                        except Exception as general_err:
                            self.log_message(f"[{session_file_name}] -> {group_identifier}: Неизвестная ошибка при отправке: {type(general_err).__name__}: {general_err}", "error")
                            import traceback
                            print(f"--- Send Error Details ({session_file_name} -> {group_identifier}) ---")
                            traceback.print_exc()
                            print("--- End Send Error Details ---")

                        if pause_after_group:
                             await asyncio.sleep(0.5)

                    if stop_sending_flag.is_set() or connection_error_break:
                         break

                    self.log_message(f"Аккаунт {session_file_name} завершил проход по группам (отправлено: {groups_sent_count_this_account}).", "info")
                    current_account_index += 1

                    # --- ИЗМЕНЕННЫЙ БЛОК ОЖИДАНИЯ ИНТЕРВАЛА ---
                    if current_account_index >= len(active_session_names):
                        # Wait for the main interval (using custom loop)
                        if not stop_sending_flag.is_set():
                            self.log_message(f"Все аккаунты прошли круг. Начинаем ожидание основного интервала: {interval} секунд...")
                            start_wait_time = time.monotonic()
                            interval_completed = False
                            while time.monotonic() - start_wait_time < interval:
                                if stop_sending_flag.is_set():
                                    self.log_message("Остановка обнаружена во время основного интервала.", "warning")
                                    break # Break inner wait loop
                                # Sleep for a short duration (e.g., 0.5 seconds) before checking again
                                await asyncio.sleep(0.5)
                            else: # This else block executes if the inner while loop finished normally (not via break)
                                interval_completed = True

                            if stop_sending_flag.is_set(): # Check flag again after inner loop (in case break happened)
                                break # Break outer while loop

                            if interval_completed:
                                self.log_message("Основной интервал ожидания завершен. Начинаем новый круг.", "info")
                                # The outer while loop will continue naturally
                    # --- КОНЕЦ ИЗМЕНЕННОГО БЛОКА ОЖИДАНИЯ ---
                    else: # Если это был НЕ последний аккаунт
                         if not stop_sending_flag.is_set():
                             short_pause = 2
                             self.log_message(f"Пауза {short_pause} сек перед следующим аккаунтом...", "info")
                             await asyncio.sleep(short_pause)

                except ConnectionError as e:
                    self.log_message(f"Критическая ошибка ПОДКЛЮЧЕНИЯ для аккаунта {session_file_name}: {e}. Аккаунт пропущен и удален из текущей сессии.", "error")
                    active_session_names.pop(current_account_index)
                    await asyncio.sleep(5)
                    continue
                except Exception as e:
                    self.log_message(f"Критическая НЕПРЕДВИДЕННАЯ ошибка с аккаунтом {session_file_name}: {type(e).__name__}: {e}. Аккаунт пропущен и удален из текущей сессии.", "error")
                    import traceback
                    print(f"--- Account Error ({session_file_name}) ---")
                    traceback.print_exc()
                    print("--- End Account Error ---")
                    active_session_names.pop(current_account_index)
                    await asyncio.sleep(5)
                    continue
                finally:
                    if client and client.is_connected():
                        await client.disconnect()
                        self.log_message(f"Клиент для {session_file_name} отключен после использования.", "info")
                    client = None

            self.log_message("Выход из основного цикла рассылки.", "info")
            if client and client.is_connected():
                 await client.disconnect()
                 self.log_message("Финальное отключение клиента (на всякий случай).", "info")

        try:
            loop.run_until_complete(async_sending_logic())
        except Exception as e:
             self.log_message(f"Критическая ошибка в потоке рассылки (уровень event loop): {e}", "error")
             import traceback
             print(f"--- Critical Sending Thread Error (event loop level) ---")
             traceback.print_exc()
             print("--- End Critical ---")
        finally:
            self.log_message("--- Сеанс рассылки завершен ---", "info")
            if self.root.winfo_exists():
                 self.root.after(0, self.on_sending_stopped)

    def on_sending_stopped(self):
        """Вызывается в основном потоке после завершения потока рассылки."""
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(text="СТОП Рассылки", state=tk.DISABLED)
        global sending_thread
        sending_thread = None
        self.log_message("Интерфейс обновлен после остановки рассылки.", "info")

    def on_closing(self):
        """Обработчик закрытия окна."""
        global stop_sending_flag, sending_thread
        if sending_thread and sending_thread.is_alive():
             if not stop_sending_flag.is_set():
                 self.log_message("Окно закрывается. Отправка сигнала остановки рассылке...", "warning")
                 stop_sending_flag.set()
             else:
                 self.log_message("Окно закрывается. Рассылка уже останавливается...", "warning")
        save_config(self.config)
        self.root.destroy()

# --- Запуск приложения ---
if __name__ == "__main__":
    root = tk.Tk()
    app = TelegramSenderApp(root)
    root.mainloop()