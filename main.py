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
    """Загружает конфигурацию из файла или возвращает дефолтную."""
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

                # --- НОВОЕ: Гарантируем наличие поля message у каждого аккаунта ---
                for account_name, account_data in config.get("accounts", {}).items():
                    if "message" not in account_data:
                         account_data["message"] = "" # Добавляем пустое сообщение по умолчанию
                # -------------------------------------------------------------

                return config
        else:
            # Если файла нет, создаем и для дефолтных аккаунтов (если они есть)
            for account_name, account_data in default_config.get("accounts", {}).items():
                 if "message" not in account_data:
                     account_data["message"] = ""
            return default_config
    except (json.JSONDecodeError, IOError, TypeError) as e:
        messagebox.showerror("Ошибка загрузки конфигурации", f"Не удалось загрузить {CONFIG_FILE}:\n{e}\nБудет использована конфигурация по умолчанию.")
        if os.path.exists(CONFIG_FILE):
            try:
                os.remove(CONFIG_FILE)
                messagebox.showinfo("Конфигурация", f"Поврежденный файл {CONFIG_FILE} удален.")
            except OSError as remove_err:
                 messagebox.showerror("Ошибка", f"Не удалось удалить поврежденный файл {CONFIG_FILE}: {remove_err}")
        # Убедимся, что и в этом случае у аккаунтов есть поле message
        for account_name, account_data in default_config.get("accounts", {}).items():
            if "message" not in account_data:
                 account_data["message"] = ""
        return default_config

def save_config(config):
    """Сохраняет конфигурацию в файл."""
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
        self.root.title("Telegram Рассыльщик v2.1 (Исправлено)")
        self.root.geometry("700x650") # Немного увеличим окно
        # Создаем директорию сессий при инициализации, если ее нет
        if not os.path.exists(SESSION_DIR):
            try:
                os.makedirs(SESSION_DIR)
            except OSError as e:
                messagebox.showerror("Ошибка создания папки", f"Не удалось создать папку '{SESSION_DIR}': {e}")
                root.destroy() # Выход, если папка не создается
                return

        self.config = load_config() # Загружаем конфиг после проверки папки

        style = ttk.Style()
        style.theme_use('clam')
        self.notebook = ttk.Notebook(root)

        # --- Вкладки ---
        self.tab_accounts = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_accounts, text='Аккаунты')
        self.create_accounts_tab()

        self.tab_groups = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_groups, text='Группы/Каналы')
        self.create_groups_tab()

        self.tab_sender = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_sender, text='Рассылка')
        self.create_sender_tab() # Создаем новую вкладку рассылки

        self.notebook.pack(expand=True, fill='both')

        # --- Инициализация списков и комбобокса ---
        self.load_accounts_list() # Загружает список аккаунтов в Listbox
        self.load_groups_list()   # Загружает список групп в Listbox
        self.update_sender_account_selector() # Заполняет Combobox на вкладке Рассылка
        self.load_message_for_selected_account() # Загружает сообщение для выбранного аккаунта

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # --- Вспомогательная функция для simpledialog ---
    def ask_string_threadsafe(self, title, prompt, show=''):
        result = None
        event = threading.Event()
        def ask():
            nonlocal result
            try:
                # Убедимся, что окно еще существует
                if self.root.winfo_exists():
                    result = simpledialog.askstring(title, prompt, show=show, parent=self.root)
                else:
                    result = None # Окно закрыто, отмена
            finally:
                event.set()
        # Вызываем в главном потоке через after
        if self.root.winfo_exists():
            self.root.after(0, ask)
            event.wait() # Ждем завершения диалога
        else:
             result = None # Окно закрыто до вызова
        return result

    # --- Создание элементов вкладок ---
    def create_accounts_tab(self):
        # (Код этой функции остался без изменений)
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
        self.accounts_listbox = tk.Listbox(list_frame, height=10) # Увеличил высоту
        self.accounts_listbox.pack(side='left', fill='both', expand=True, padx=(0, 5))
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.accounts_listbox.yview)
        scrollbar.pack(side='right', fill='y')
        self.accounts_listbox.config(yscrollcommand=scrollbar.set)
        remove_button = ttk.Button(frame, text="Удалить выбранный аккаунт (сессию и запись)", command=self.remove_account)
        remove_button.pack(pady=5)
        self.account_status_label = ttk.Label(frame, text="", foreground="blue")
        self.account_status_label.pack(pady=5)

    def create_groups_tab(self):
        # (Код этой функции остался без изменений)
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
        self.groups_listbox = tk.Listbox(list_frame, height=12) # Увеличил высоту
        self.groups_listbox.pack(side='left', fill='both', expand=True, padx=(0, 5))
        scrollbar = ttk.Scrollbar(list_frame, orient='vertical', command=self.groups_listbox.yview)
        scrollbar.pack(side='right', fill='y')
        self.groups_listbox.config(yscrollcommand=scrollbar.set)
        remove_button = ttk.Button(frame, text="Удалить выбранную группу", command=self.remove_group)
        remove_button.pack(pady=5)

    def create_sender_tab(self):
        """Создает элементы на вкладке 'Рассылка' с выбором аккаунта."""
        frame = self.tab_sender

        # --- Фрейм выбора аккаунта и редактирования сообщения ---
        account_msg_frame = ttk.Frame(frame, padding="5")
        account_msg_frame.pack(fill='x', pady=5)

        ttk.Label(account_msg_frame, text="Выберите аккаунт для настройки сообщения:").grid(row=0, column=0, padx=5, pady=(0, 5), sticky='w')

        # Выпадающий список для выбора аккаунта
        self.sender_account_combobox = ttk.Combobox(account_msg_frame, width=35, state="readonly")
        self.sender_account_combobox.grid(row=1, column=0, padx=5, pady=5, sticky='ew')
        self.sender_account_combobox.bind("<<ComboboxSelected>>", self.on_sender_account_selected) # Событие выбора

        # Кнопка сохранения сообщения для выбранного аккаунта
        self.save_message_button = ttk.Button(account_msg_frame, text="Сохранить сообщение для этого аккаунта", command=self.save_account_message)
        self.save_message_button.grid(row=1, column=1, padx=10, pady=5)

        # Поле для ввода/редактирования сообщения
        msg_edit_frame = ttk.LabelFrame(frame, text="Сообщение для выбранного аккаунта", padding="10")
        msg_edit_frame.pack(fill='both', expand=True, pady=5)

        self.sender_message_text = scrolledtext.ScrolledText(msg_edit_frame, height=8, width=60, wrap=tk.WORD)
        self.sender_message_text.pack(fill='both', expand=True)
        self.sender_message_status_label = ttk.Label(frame, text="", foreground="green")
        self.sender_message_status_label.pack(pady=(0, 5))

        # --- Остальные элементы (таймер, кнопки старт/стоп, лог) ---
        timer_frame = ttk.LabelFrame(frame, text="Настройки отправки", padding="10")
        timer_frame.pack(fill='x', pady=5)
        ttk.Label(timer_frame, text="Интервал между полными циклами рассылки (секунды):").grid(row=0, column=0, padx=5, pady=5, sticky='w')
        self.interval_spinbox = ttk.Spinbox(timer_frame, from_=10, to=86400, width=8)
        self.interval_spinbox.set(60) # Значение по умолчанию
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
        """Загружает список аккаунтов в Listbox на вкладке Аккаунты."""
        self.accounts_listbox.delete(0, tk.END)
        account_names = list(self.config.get("accounts", {}).keys())
        account_names.sort() # Сортируем для единообразия
        for session_name in account_names:
            self.accounts_listbox.insert(tk.END, session_name)
        # Обновляем и комбобокс на вкладке Рассылка
        self.update_sender_account_selector()

    def update_sender_account_selector(self):
        """Обновляет выпадающий список аккаунтов на вкладке Рассылка."""
        account_names = list(self.config.get("accounts", {}).keys())
        account_names.sort()
        current_selection = self.sender_account_combobox.get()

        if not account_names:
            self.sender_account_combobox.set('')
            self.sender_account_combobox['values'] = []
            self.sender_account_combobox.config(state=tk.DISABLED)
            if self.sender_message_text.winfo_exists(): # Проверка существования виджета
                self.sender_message_text.delete("1.0", tk.END)
                self.sender_message_text.config(state=tk.DISABLED)
            if self.save_message_button.winfo_exists():
                self.save_message_button.config(state=tk.DISABLED)
        else:
            self.sender_account_combobox.config(state="readonly")
            self.sender_account_combobox['values'] = account_names
            # Пытаемся сохранить выбор, если он все еще валиден
            if current_selection in account_names:
                 self.sender_account_combobox.set(current_selection)
            elif account_names: # Если старого выбора нет, выбираем первый
                 self.sender_account_combobox.current(0)
            else: # Если список стал пустым
                 self.sender_account_combobox.set('')

            if self.sender_message_text.winfo_exists():
                self.sender_message_text.config(state=tk.NORMAL)
            if self.save_message_button.winfo_exists():
                self.save_message_button.config(state=tk.NORMAL)
            # Загружаем сообщение для нового/текущего выбора
            self.load_message_for_selected_account()


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

        # Проверяем, существует ли уже аккаунт с таким телефоном (по имени сессии)
        session_file_name = f"{phone}.session"
        if session_file_name in self.config.get("accounts", {}):
             if not messagebox.askyesno("Аккаунт существует", f"Аккаунт с номером {phone} ({session_file_name}) уже добавлен.\nХотите переавторизовать его (проверить)?"):
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

            # Удаляем старый файл сессии перед попыткой, чтобы обеспечить чистую авторизацию
            if os.path.exists(session_path):
                try:
                    os.remove(session_path)
                    self.update_account_status(f"Старый файл сессии {session_file_name} удален.", "blue")
                except OSError as e:
                    self.update_account_status(f"Не удалось удалить старый файл сессии {session_file_name}: {e}", "orange")
                    # Не критично, можно продолжить

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
                    if code is None: # Проверяем отмену
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
                        if password is None: # Проверяем отмену
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
                # --- НОВОЕ: Добавляем поле message при сохранении ---
                # Если аккаунт уже был, сохраняем старое сообщение, иначе - пустое
                existing_message = self.config.get("accounts", {}).get(session_file_name, {}).get("message", "")
                self.config["accounts"][session_file_name] = {
                    "api_id": api_id,
                    "api_hash": api_hash,
                    "phone": phone,
                    "message": existing_message # Сохраняем старое или пустое сообщение
                }
                save_config(self.config)

                self.root.after(0, self.load_accounts_list) # Обновляем списки в UI
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
        except Exception as e:
             self.update_account_status(f"Критическая ошибка потока авторизации: {e}", "red")
             import traceback
             print("--- Critical Thread Error (do_add_account) ---")
             traceback.print_exc()
             print("--- End Critical ---")
        finally:
            # Гарантируем, что кнопка снова активна
            if self.root.winfo_exists():
                 self.root.after(0, lambda: self.add_account_button.config(state=tk.NORMAL))
                 # Обновим статус, если он не был успехом или ошибкой
                 if self.account_status_label['foreground'] not in ("green", "red", "orange"):
                      self.update_account_status("Процесс авторизации завершен.", "blue")


    def update_account_status(self, message, color):
        def update():
             if self.root.winfo_exists() and self.account_status_label.winfo_exists(): # Проверка, что окно и виджет существуют
                self.account_status_label.config(text=message, foreground=color)
        if self.root.winfo_exists():
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

            # Удаляем из конфига
            if "accounts" in self.config and selected_session_name in self.config["accounts"]:
                try:
                    del self.config["accounts"][selected_session_name]
                    save_config(self.config)
                    self.log_message(f"Запись аккаунта '{selected_session_name}' удалена из конфигурации.", "info")
                    removed_config = True
                except Exception as e:
                    self.log_message(f"Ошибка удаления записи '{selected_session_name}' из конфига: {e}", "error")
                    messagebox.showerror("Ошибка конфигурации", f"Не удалось удалить запись из {CONFIG_FILE}:\n{e}")

            # Удаляем файл сессии
            try:
                if os.path.exists(session_path):
                    os.remove(session_path)
                    self.log_message(f"Файл сессии '{selected_session_name}' удален.", "info")
                    removed_file = True
                else:
                     self.log_message(f"Файл сессии '{selected_session_name}' не найден для удаления.", "warning")
                     # Если запись из конфига удалили, считаем удаление успешным, даже если файла не было
                     if removed_config: removed_file = True

            except OSError as e:
                self.log_message(f"Ошибка удаления файла сессии '{selected_session_name}': {e}", "error")
                messagebox.showerror("Ошибка файла", f"Не удалось удалить файл сессии '{session_path}':\n{e}")

            # Обновляем UI, если что-то было удалено
            if removed_config or removed_file:
                 # Не используем delete, а полностью перезагружаем списки
                 # чтобы гарантировать синхронизацию с combobox
                 self.load_accounts_list()
                 self.update_account_status(f"Аккаунт '{selected_session_name}' удален.", "blue")
            else:
                 self.update_account_status(f"Не удалось полностью удалить '{selected_session_name}'. См. лог.", "orange")
                 self.load_accounts_list() # Перезагружаем на случай ошибки

    # --- Логика вкладки "Группы" ---
    def load_groups_list(self):
        # (Код этой функции остался без изменений)
        self.groups_listbox.delete(0, tk.END)
        groups = self.config.get("groups", [])
        groups.sort() # Сортируем для порядка
        for group in groups:
            self.groups_listbox.insert(tk.END, group)

    def add_group(self):
        # (Код этой функции остался без изменений)
        group_id = self.group_entry.get().strip()
        if not group_id:
            messagebox.showwarning("Пустое поле", "Введите ID, username или ссылку на группу/канал.")
            return

        if "groups" not in self.config or not isinstance(self.config["groups"], list):
            self.config["groups"] = []

        if group_id not in self.config["groups"]:
            self.config["groups"].append(group_id)
            save_config(self.config)
            self.load_groups_list() # Перезагружаем список для сортировки
            self.group_entry.delete(0, tk.END)
            self.log_message(f"Группа/канал '{group_id}' добавлена в список.", "info")
        else:
            messagebox.showinfo("Уже существует", f"Группа/канал '{group_id}' уже есть в списке.")
            self.group_entry.delete(0, tk.END)

    def remove_group(self):
        # (Код этой функции остался без изменений)
        selected_indices = self.groups_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("Не выбрано", "Выберите группу/канал из списка для удаления.")
            return

        selected_group = self.groups_listbox.get(selected_indices[0])

        if "groups" in self.config and isinstance(self.config["groups"], list) and selected_group in self.config["groups"]:
            try:
                self.config["groups"].remove(selected_group)
                save_config(self.config)
                self.load_groups_list() # Перезагружаем список
                self.log_message(f"Группа/канал '{selected_group}' удалена из списка.", "info")
            except Exception as e:
                 messagebox.showerror("Ошибка", f"Не удалось удалить '{selected_group}': {e}")
                 self.log_message(f"Ошибка удаления группы '{selected_group}': {e}", "error")
                 self.load_groups_list() # Перезагружаем на случай ошибки
        else:
            # Эта ситуация маловероятна при использовании load_groups_list, но оставим проверку
            messagebox.showerror("Ошибка синхронизации", f"Группа/канал '{selected_group}' не найдена в конфигурации. Список будет перезагружен.")
            self.log_message(f"Попытка удалить '{selected_group}', но не найдено в config['groups'].", "warning")
            self.load_groups_list()

    # --- Логика вкладки "Рассылка" (Новые и измененные методы) ---

    def on_sender_account_selected(self, event=None):
        """Загружает сообщение в текстовое поле при выборе аккаунта в Combobox."""
        self.load_message_for_selected_account()
        self.clear_sender_status_label() # Очищаем статус при смене аккаунта

    def load_message_for_selected_account(self):
        """Загружает сообщение для аккаунта, выбранного в Combobox."""
        selected_account = self.sender_account_combobox.get()
        if not self.sender_message_text.winfo_exists(): return # Доп проверка

        if not selected_account:
            self.sender_message_text.delete("1.0", tk.END)
            self.sender_message_text.config(state=tk.DISABLED) # Блокируем, если нет аккаунта
            return

        # Разблокируем поле перед вставкой
        self.sender_message_text.config(state=tk.NORMAL)
        self.sender_message_text.delete("1.0", tk.END)

        account_data = self.config.get("accounts", {}).get(selected_account, {})
        message = account_data.get("message", "") # Получаем сообщение, дефолт - пустая строка
        self.sender_message_text.insert("1.0", message)

    def save_account_message(self):
        """Сохраняет текст из поля sender_message_text для выбранного аккаунта."""
        selected_account = self.sender_account_combobox.get()
        if not selected_account:
            messagebox.showwarning("Аккаунт не выбран", "Выберите аккаунт из списка, чтобы сохранить для него сообщение.")
            return
        if not self.sender_message_text.winfo_exists(): return # Доп проверка

        message = self.sender_message_text.get("1.0", tk.END).strip()

        # Обновляем сообщение в конфиге
        if "accounts" in self.config and selected_account in self.config["accounts"]:
            self.config["accounts"][selected_account]["message"] = message
            save_config(self.config)
            self.show_sender_status_label(f"Сообщение для '{selected_account}' сохранено.", "green")
            self.log_message(f"Сообщение для аккаунта '{selected_account}' обновлено.", "info")
        else:
            # Этого не должно произойти, если combobox синхронизирован
            messagebox.showerror("Ошибка", f"Не удалось найти данные для аккаунта '{selected_account}' в конфигурации.")
            self.show_sender_status_label("Ошибка сохранения сообщения.", "red")
            self.log_message(f"Ошибка сохранения сообщения: аккаунт '{selected_account}' не найден в config.", "error")

    def show_sender_status_label(self, text, color):
        """Показывает сообщение под полем ввода сообщения."""
        if self.root.winfo_exists() and self.sender_message_status_label.winfo_exists():
            self.sender_message_status_label.config(text=text, foreground=color)
            # Очистка сообщения через 3 секунды
            self.root.after(3000, self.clear_sender_status_label)

    def clear_sender_status_label(self):
         """Очищает статусное сообщение."""
         if self.root.winfo_exists() and self.sender_message_status_label.winfo_exists():
             self.sender_message_status_label.config(text="")

    def log_message(self, message, level="info"):
        # (Код этой функции остался без изменений)
        def update():
            try:
                if self.log_text.winfo_exists(): # Проверяем виджет перед модификацией
                    self.log_text.config(state=tk.NORMAL)
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    tag = level
                    self.log_text.insert(tk.END, f"[{timestamp}] {message}\n", tag)
                    self.log_text.config(state=tk.DISABLED)
                    self.log_text.see(tk.END) # Автопрокрутка вниз
            except tk.TclError as e:
                 # Ошибка может возникнуть, если виджет уже уничтожен при закрытии окна
                 print(f"Ошибка логгирования (возможно, виджет уничтожен): {e}")
        # Проверяем, существует ли окно перед вызовом after
        if self.root.winfo_exists():
            self.root.after(0, update)
        else:
             # Если окно закрыто, просто печатаем в консоль
             print(f"[LOG (window closed)] {message}")

    def start_sending_thread(self):
        global sending_thread, stop_sending_flag

        # --- Проверка наличия аккаунтов и групп ---
        account_session_names = list(self.config.get("accounts", {}).keys())
        if not account_session_names:
            messagebox.showwarning("Нет аккаунтов", "Добавьте и авторизуйте хотя бы один аккаунт на вкладке 'Аккаунты'.")
            return

        groups = self.config.get("groups", [])
        if not groups:
            messagebox.showwarning("Нет групп", "Добавьте хотя бы одну группу или канал для рассылки на вкладке 'Группы/Каналы'.")
            return

        # --- Проверка, что хотя бы у одного аккаунта есть сообщение ---
        has_message = False
        active_accounts_with_messages = []
        for name in account_session_names:
             account_data = self.config.get("accounts", {}).get(name, {})
             if account_data.get("message", "").strip(): # Проверяем непустое сообщение
                 has_message = True
                 active_accounts_with_messages.append(name)

        if not has_message:
             messagebox.showwarning("Нет сообщений", "Ни для одного аккаунта не задано сообщение для рассылки. Задайте сообщение на вкладке 'Рассылка'.")
             return

        # --- Получение интервала ---
        try:
            interval_str = self.interval_spinbox.get()
            interval = int(interval_str)
            if interval < 5:
                self.log_message(f"Внимание: установлен малый интервал ({interval} сек). Рекомендуется >= 10 сек.", "warning")
        except ValueError:
            messagebox.showerror("Неверный интервал", f"Значение интервала '{interval_str}' не является целым числом.")
            return

        # --- Проверка и запуск потока ---
        if sending_thread and sending_thread.is_alive():
            messagebox.showwarning("Уже запущено", "Рассылка уже выполняется. Остановите текущую перед запуском новой.")
            return

        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.log_message("--- Запуск сеанса рассылки ---", "info")
        stop_sending_flag.clear() # Сбрасываем флаг перед стартом

        # Передаем только те аккаунты, у которых есть сообщения
        sending_thread = threading.Thread(
            target=self.do_sending_loop,
            args=(list(active_accounts_with_messages), list(groups), interval),
            daemon=True)
        sending_thread.start()

    def stop_sending(self):
        global stop_sending_flag, sending_thread
        if sending_thread and sending_thread.is_alive():
            self.log_message("--- Получен запрос на остановку рассылки... ---", "warning")
            stop_sending_flag.set() # Устанавливаем флаг
            # Блокируем кнопку Стоп, чтобы избежать повторных нажатий
            self.stop_button.config(text="Остановка...", state=tk.DISABLED)
        else:
             self.log_message("Рассылка не была запущена.", "info")
             # Сбрасываем состояние кнопок на случай, если поток завершился некорректно
             if self.root.winfo_exists(): # Проверка окна
                 self.start_button.config(state=tk.NORMAL)
                 self.stop_button.config(text="СТОП Рассылки", state=tk.DISABLED)

    def do_sending_loop(self, account_session_names, groups, interval):
        """Основной цикл рассылки (выполняется в отдельном потоке)."""
        global stop_sending_flag

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Создаем Future для возможности отмены задач при остановке
        main_task = None

        async def async_sending_logic():
            nonlocal main_task
            main_task = asyncio.current_task() # Сохраняем текущую задачу

            current_account_index = 0
            active_session_names = list(account_session_names)
            client = None

            self.log_message(f"Начинаем цикл рассылки по {len(active_session_names)} аккаунтам.", "info")

            while not stop_sending_flag.is_set():
                try:
                    self.log_message(f"--- Начало итерации цикла ---", "info")

                    if not active_session_names:
                        self.log_message("Нет активных аккаунтов для рассылки. Рассылка остановлена.", "error")
                        break

                    if current_account_index >= len(active_session_names):
                        current_account_index = 0 # Сбрасываем индекс для следующего круга
                        self.log_message(f"Все {len(active_session_names)} аккаунта(ов) прошли круг.", "info")

                        # --- ИСПРАВЛЕННЫЙ БЛОК ОЖИДАНИЯ ОСНОВНОГО ИНТЕРВАЛА ---
                        if not stop_sending_flag.is_set():
                            self.log_message(f"Начинаем ожидание основного интервала: {interval} секунд...")
                            if interval > 0:
                                start_wait_time = time.monotonic()
                                while time.monotonic() - start_wait_time < interval:
                                    if stop_sending_flag.is_set():
                                        self.log_message("Остановка обнаружена во время основного интервала.", "warning")
                                        break # Прерываем внутренний цикл ожидания
                                    try:
                                        # Короткий сон, чтобы не загружать процессор и реагировать на флаг
                                        await asyncio.sleep(0.5)
                                    except asyncio.CancelledError:
                                        self.log_message("Ожидание основного интервала прервано отменой.", "warning")
                                        stop_sending_flag.set() # Установим флаг для согласованности
                                        break # Прерываем внутренний цикл ожидания
                                else: # Этот блок выполняется, если внутренний while завершился нормально (не через break)
                                    self.log_message("Основной интервал ожидания завершен. Начинаем новый круг.", "info")

                            if stop_sending_flag.is_set(): # Проверяем флаг еще раз после внутреннего цикла или если интервал был <= 0
                                break # Прерываем внешний цикл рассылки
                        else:
                             break # Если флаг установлен, выходим из внешнего цикла
                        # --- КОНЕЦ ИСПРАВЛЕННОГО БЛОКА ОЖИДАНИЯ ---

                    # Получаем имя сессии текущего аккаунта
                    session_file_name = active_session_names[current_account_index]
                    session_path = os.path.join(SESSION_DIR, session_file_name)
                    self.log_message(f"Выбран аккаунт [{current_account_index+1}/{len(active_session_names)}]: {session_file_name}", "info")

                    account_info = self.config.get("accounts", {}).get(session_file_name)
                    if not account_info or "api_id" not in account_info or "api_hash" not in account_info:
                        self.log_message(f"Ошибка: Отсутствуют данные API ID/Hash для {session_file_name} в конфигурации. Аккаунт пропущен и удален из текущей сессии.", "error")
                        active_session_names.pop(current_account_index)
                        await asyncio.sleep(0.1) # Краткая пауза
                        continue

                    api_id = account_info["api_id"]
                    api_hash = account_info["api_hash"]
                    message_to_send = account_info.get("message", "").strip()

                    if not message_to_send:
                        self.log_message(f"Предупреждение: Сообщение для аккаунта {session_file_name} пустое. Аккаунт пропущен в этой итерации.", "warning")
                        current_account_index += 1
                        await asyncio.sleep(0.1) # Краткая пауза
                        continue

                    client = None # Сбрасываем клиент перед новой попыткой
                    try:
                        client = TelegramClient(session_path, api_id, api_hash, loop=loop)
                        self.log_message(f"Подключение аккаунта {session_file_name}...", "info")
                        # Увеличим таймаут подключения
                        await asyncio.wait_for(client.connect(), timeout=30.0)

                        if not await client.is_user_authorized():
                            self.log_message(f"Аккаунт {session_file_name} не авторизован или сессия истекла. Требуется повторная авторизация. Аккаунт пропущен и удален из текущей сессии.", "error")
                            if client.is_connected(): await client.disconnect()
                            client = None
                            active_session_names.pop(current_account_index)
                            continue

                        me = await client.get_me()
                        user_display_name = f"{me.first_name} ({session_file_name})" if me and me.first_name else session_file_name
                        self.log_message(f"Аккаунт {user_display_name} успешно подключен.", "success")

                        groups_sent_count_this_account = 0
                        connection_error_break = False

                        for group_index, group_identifier in enumerate(groups):
                            if stop_sending_flag.is_set():
                                self.log_message("Обнаружен флаг остановки во время перебора групп.", "warning")
                                break

                            self.log_message(f"[{user_display_name}] Попытка отправки в [{group_index+1}/{len(groups)}]: {group_identifier}", "info")

                            entity = None
                            pause_after_attempt = True

                            try:
                                try:
                                    entity = await asyncio.wait_for(client.get_entity(group_identifier), timeout=20.0)
                                except asyncio.TimeoutError:
                                     self.log_message(f"[{user_display_name}] -> {group_identifier}: Ошибка: Таймаут при поиске entity. Пропускаем.", "error")
                                     continue
                                except (ValueError, TypeError) as entity_err: # Ловим ошибки поиска entity
                                     self.log_message(f"[{user_display_name}] Ошибка поиска entity '{group_identifier}': {entity_err}. Пропускаем.", "error")
                                     continue

                                # Отправляем сообщение
                                await client.send_message(entity, message_to_send)
                                self.log_message(f"[{user_display_name}] -> {group_identifier}: Сообщение успешно отправлено.", "success")
                                groups_sent_count_this_account += 1
                                pause_after_attempt = False

                                # --- ИСПРАВЛЕННАЯ Пауза между группами ---
                                pause_between_groups = max(1, min(5, interval // max(1, len(groups)*2)))
                                if pause_between_groups > 0 and not stop_sending_flag.is_set():
                                    self.log_message(f"Пауза {pause_between_groups} сек перед следующей группой...", "info")
                                    try:
                                        await asyncio.sleep(pause_between_groups)
                                    except asyncio.CancelledError:
                                        self.log_message("Пауза между группами прервана отменой.", "warning")
                                        stop_sending_flag.set()
                                        break

                                # Проверяем флаг еще раз ПОСЛЕ паузы
                                if stop_sending_flag.is_set():
                                    self.log_message("Остановка обнаружена после паузы между группами.", "warning")
                                    break
                                # --- КОНЕЦ ИСПРАВЛЕННОЙ Паузы между группами ---

                            except PeerIdInvalidError:
                                self.log_message(f"[{user_display_name}] Ошибка: Неверный ID/username/ссылка '{group_identifier}'.", "error")
                            except (ChatWriteForbiddenError, UserNotParticipantError):
                                self.log_message(f"[{user_display_name}] Ошибка: Нет прав на отправку в '{group_identifier}' или не участник.", "error")
                            except FloodWaitError as flood_err:
                                pause_after_attempt = False
                                wait_time = flood_err.seconds + 5
                                self.log_message(f"[{user_display_name}] FloodWait! Ждем {wait_time} секунд...", "warning")
                                # --- ИСПРАВЛЕННАЯ Пауза при FloodWait ---
                                if wait_time > 0 and not stop_sending_flag.is_set():
                                    try:
                                        await asyncio.sleep(wait_time)
                                    except asyncio.CancelledError:
                                        self.log_message("Ожидание FloodWait прервано отменой.", "warning")
                                        stop_sending_flag.set()
                                        break
                                # Проверяем флаг ПОСЛЕ паузы
                                if stop_sending_flag.is_set():
                                    self.log_message("Остановка обнаружена после ожидания FloodWait.", "warning")
                                    break
                                self.log_message("Время FloodWait истекло, пробуем следующую группу.", "info")
                                # --- КОНЕЦ ИСПРАВЛЕННОЙ Паузы FloodWait ---
                            except ConnectionError as conn_err:
                                connection_error_break = True
                                pause_after_attempt = False
                                self.log_message(f"[{user_display_name}] Ошибка соединения во время отправки: {conn_err}. Прерываем для этого аккаунта.", "error")
                                break
                            except Exception as general_err:
                                self.log_message(f"[{user_display_name}] -> {group_identifier}: Неизвестная ошибка отправки: {type(general_err).__name__}: {general_err}", "error")
                                import traceback
                                print(f"--- Send Error Details ({user_display_name} -> {group_identifier}) ---")
                                traceback.print_exc()
                                print("--- End Send Error Details ---")

                            if pause_after_attempt and not stop_sending_flag.is_set():
                                 try:
                                     await asyncio.sleep(1) # Пауза после неудачной попытки
                                 except asyncio.CancelledError:
                                     stop_sending_flag.set()
                                     break

                        if stop_sending_flag.is_set() or connection_error_break:
                            break

                        self.log_message(f"Аккаунт {user_display_name} завершил проход (отправлено: {groups_sent_count_this_account}).", "info")
                        current_account_index += 1

                        # --- ИСПРАВЛЕННАЯ Пауза между аккаунтами ---
                        if not stop_sending_flag.is_set() and current_account_index < len(active_session_names):
                            short_pause = 2
                            self.log_message(f"Пауза {short_pause} сек перед следующим аккаунтом...", "info")
                            if short_pause > 0:
                                try:
                                    await asyncio.sleep(short_pause)
                                except asyncio.CancelledError:
                                    self.log_message("Пауза между аккаунтами прервана отменой.", "warning")
                                    stop_sending_flag.set()
                                    break

                            if stop_sending_flag.is_set():
                                self.log_message("Остановка обнаружена после паузы между аккаунтами.", "warning")
                                break
                        # --- КОНЕЦ ИСПРАВЛЕННОЙ Паузы между аккаунтами ---

                    except asyncio.TimeoutError:
                        self.log_message(f"Таймаут при подключении аккаунта {session_file_name}. Аккаунт пропущен.", "error")
                        active_session_names.pop(current_account_index)
                        if client and client.is_connected(): await client.disconnect()
                        client = None
                        if not stop_sending_flag.is_set(): await asyncio.sleep(1)
                    except ConnectionError as e:
                        self.log_message(f"Критическая ошибка ПОДКЛЮЧЕНИЯ {session_file_name}: {e}. Аккаунт пропущен.", "error")
                        active_session_names.pop(current_account_index)
                        if client and client.is_connected(): await client.disconnect()
                        client = None
                        if not stop_sending_flag.is_set(): await asyncio.sleep(5)
                    except Exception as e:
                        self.log_message(f"Критическая ошибка аккаунта {session_file_name}: {type(e).__name__}: {e}. Аккаунт пропущен.", "error")
                        import traceback
                        print(f"--- Account Error ({session_file_name}) ---")
                        traceback.print_exc()
                        print("--- End Account Error ---")
                        active_session_names.pop(current_account_index)
                        if client and client.is_connected(): await client.disconnect()
                        client = None
                        if not stop_sending_flag.is_set(): await asyncio.sleep(5)
                    finally:
                        if client and client.is_connected():
                            await client.disconnect()
                            self.log_message(f"Клиент для {session_file_name} отключен.", "info")
                        client = None

                except asyncio.CancelledError:
                     self.log_message("Основная задача рассылки была отменена.", "warning")
                     stop_sending_flag.set() # Убедимся, что флаг установлен
                     break # Выходим из цикла while

            # --- Конец цикла while ---
            self.log_message("Выход из основного цикла рассылки.", "info")
            if client and client.is_connected(): # Финальная проверка
                 await client.disconnect()
                 self.log_message("Финальное отключение клиента.", "info")

        # --- Запуск и обработка цикла asyncio ---
        try:
            loop.run_until_complete(async_sending_logic())
        except Exception as e:
             self.log_message(f"Критическая ошибка в потоке рассылки (event loop): {e}", "error")
             import traceback
             print(f"--- Critical Sending Thread Error (event loop level) ---")
             traceback.print_exc()
             print("--- End Critical ---")
        finally:
            # Отменяем все оставшиеся задачи в цикле при завершении
            if main_task and not main_task.done():
                 main_task.cancel()
                 try:
                     # Даем возможность обработать отмену
                     loop.run_until_complete(main_task)
                 except asyncio.CancelledError:
                     pass # Ожидаемая ошибка при отмене
                 except Exception as e_cancel: # Ловим другие возможные ошибки при отмене
                     print(f"Ошибка при отмене main_task: {e_cancel}")
            loop.close()
            self.log_message("--- Сеанс рассылки завершен ---", "info")
            if self.root.winfo_exists():
                 self.root.after(0, self.on_sending_stopped)

    def on_sending_stopped(self):
        """Вызывается в основном потоке после завершения потока рассылки."""
        global sending_thread
        sending_thread = None
        if self.root.winfo_exists():
            try: # Добавим try-except на случай уничтожения виджетов
                self.start_button.config(state=tk.NORMAL)
                self.stop_button.config(text="СТОП Рассылки", state=tk.DISABLED)
                self.log_message("Интерфейс обновлен после остановки рассылки.", "info")
            except tk.TclError as e:
                print(f"Ошибка обновления UI после остановки: {e}")

    def on_closing(self):
        """Обработчик закрытия окна."""
        global stop_sending_flag, sending_thread
        save_config(self.config) # Сохраняем конфиг при закрытии

        if sending_thread and sending_thread.is_alive():
             if not stop_sending_flag.is_set():
                 self.log_message("Окно закрывается. Отправка сигнала остановки рассылке...", "warning")
                 stop_sending_flag.set()
             else:
                 self.log_message("Окно закрывается. Рассылка уже останавливается...", "warning")
             # Дать потоку немного времени на завершение перед уничтожением окна
             # Это не гарантирует чистое завершение, но может помочь
             # sending_thread.join(timeout=1.5)

        self.root.destroy()

# --- Запуск приложения ---
if __name__ == "__main__":
    root = tk.Tk()
    app = TelegramSenderApp(root)
    # Только если app инициализировался успешно
    if app and root.winfo_exists(): # Добавлена проверка существования окна
        root.mainloop()
