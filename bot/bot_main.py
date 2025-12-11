#!/usr/bin/env python3
"""
OLX Auto-Booster Bot v2.0
Автоматическое поднятие объявлений с Telegram WebApp
"""

import asyncio
import json
import logging
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread, Lock
from typing import Dict, List, Optional, Tuple
import aiosqlite
import aiohttp

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ===== КОНФИГУРАЦИЯ =====
CONFIG = {
    'bot_token': 'ВАШ_ТОКЕН_ОТ_BOTFATHER',
    'admin_ids': [123456789],  # Ваш ID (узнать у @userinfobot)
    'database': 'olx_accounts.db',
    'screenshots_dir': 'screenshots',
    
    # Интервал поднятия: 13-17 минут со случайными секундами
    'min_interval': 13 * 60,      # 780 секунд (13 минут)
    'max_interval': 17 * 60,      # 1020 секунд (17 минут)
    'base_interval': 15 * 60,     # 900 секунд (15 минут)
    
    # WebApp URL (замени на свой)
    'webapp_url': 'https://ваш-проект.vercel.app/',
    
    # Настройки браузера
    'browser_path': None,  # Автопоиск
    'headless': True,      # Скрытый режим
    
    'max_accounts_per_user': 10,
    'retry_attempts': 3
}

# ===== РАНДОМАЙЗЕР ИНТЕРВАЛОВ =====
def generate_random_interval() -> int:
    """
    Генерирует случайный интервал между 13 и 17 минутами
    с добавлением случайных секунд
    """
    # Берем случайное количество минут от 13 до 17
    random_minutes = random.randint(13, 17)
    
    # Добавляем случайные секунды от 0 до 59
    random_seconds = random.randint(0, 59)
    
    # Итоговый интервал в секундах
    total_seconds = (random_minutes * 60) + random_seconds
    
    # Проверяем границы (дополнительная защита)
    min_seconds = CONFIG['min_interval']
    max_seconds = CONFIG['max_interval']
    
    if total_seconds < min_seconds:
        total_seconds = min_seconds + random_seconds
    elif total_seconds > max_seconds:
        total_seconds = max_seconds - random_seconds
    
    # Форматируем для вывода
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    
    logging.info(f"🎲 Сгенерирован интервал: {minutes} мин {seconds} сек ({total_seconds} сек)")
    
    return total_seconds

# ===== БАЗА ДАННЫХ =====
async def init_database():
    """Инициализация базы данных"""
    async with aiosqlite.connect(CONFIG['database']) as db:
        # Таблица аккаунтов
        await db.execute('''CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            account_name TEXT NOT NULL,
            cookies TEXT,
            olx_username TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_boost TIMESTAMP,
            total_boosts INTEGER DEFAULT 0,
            next_boost TIMESTAMP,
            boost_interval INTEGER DEFAULT 900
        )''')
        
        # Таблица логов поднятий
        await db.execute('''CREATE TABLE IF NOT EXISTS boost_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            success BOOLEAN,
            message TEXT,
            screenshot TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (account_id) REFERENCES accounts (id)
        )''')
        
        # Таблица пользователей
        await db.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        await db.commit()

# ===== МЕНЕДЖЕР БРАУЗЕРА =====
class BrowserManager:
    """Управление браузерными сессиями"""
    
    def __init__(self):
        self.drivers: Dict[str, webdriver.Firefox] = {}
        self.lock = Lock()
        
    def get_driver(self, session_id: str = 'default') -> Optional[webdriver.Firefox]:
        """Получить или создать драйвер Firefox"""
        with self.lock:
            if session_id not in self.drivers:
                try:
                    options = Options()
                    
                    # Настройки для маскировки
                    options.set_preference("general.useragent.override", 
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
                    
                    # Отключаем WebDriver признаки
                    options.set_preference("dom.webdriver.enabled", False)
                    options.set_preference("useAutomationExtension", False)
                    
                    # Скрытый режим (если включен в конфиге)
                    if CONFIG['headless']:
                        options.add_argument("--headless")
                    
                    # Автопоиск браузера
                    browser_path = CONFIG['browser_path'] or self._find_browser()
                    if browser_path:
                        options.binary_location = browser_path
                    
                    # Создаем драйвер
                    service = Service()
                    driver = webdriver.Firefox(service=service, options=options)
                    
                    # Дополнительные настройки
                    driver.set_page_load_timeout(30)
                    driver.set_script_timeout(30)
                    
                    # Скрываем WebDriver признаки через JavaScript
                    driver.execute_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                    )
                    
                    self.drivers[session_id] = driver
                    logging.info(f"✅ Создан новый драйвер для сессии {session_id}")
                    
                except Exception as e:
                    logging.error(f"❌ Ошибка создания драйвера: {e}")
                    return None
        
            return self.drivers[session_id]
    
    def _find_browser(self) -> str:
        """Автоматический поиск установленного браузера"""
        # Для Windows
        windows_paths = [
            "C:\\Program Files\\Mozilla Firefox\\firefox.exe",
            "C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe",
            "C:\\Program Files\\ZEN Browser\\zen.exe",
        ]
        
        # Для Linux
        linux_paths = [
            "/usr/bin/firefox",
            "/usr/bin/firefox-esr",
            "/usr/local/bin/firefox",
        ]
        
        all_paths = windows_paths + linux_paths
        
        for path in all_paths:
            if Path(path).exists():
                logging.info(f"📁 Найден браузер: {path}")
                return path
        
        # Если не нашли - пробуем системный firefox
        logging.info("⚠️ Браузер не найден, использую системный")
        return "firefox"
    
    def cleanup(self):
        """Очистка всех драйверов"""
        with self.lock:
            for session_id, driver in self.drivers.items():
                try:
                    driver.quit()
                    logging.info(f"🔒 Закрыт драйвер {session_id}")
                except Exception as e:
                    logging.error(f"Ошибка закрытия драйвера {session_id}: {e}")
            self.drivers.clear()

# ===== ОСНОВНОЙ БОТ =====
class OLXMasterBot:
    def __init__(self):
        self.browser_manager = BrowserManager()
        self.app: Optional[Application] = None
        self.boost_tasks: Dict[str, asyncio.Task] = {}
        self.session = None
        
    async def init_session(self):
        """Инициализация aiohttp сессии"""
        self.session = aiohttp.ClientSession()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        user = update.effective_user
        
        # Регистрируем пользователя
        async with aiosqlite.connect(CONFIG['database']) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
                (user.id, user.username, user.full_name)
            )
            await db.commit()
        
        # Создаем WebApp URL с user_id
        webapp_url = f"{CONFIG['webapp_url']}?user_id={user.id}&username={user.username}"
        
        # Основное меню
        keyboard = [
            [InlineKeyboardButton(
                "➕ Добавить аккаунт OLX", 
                web_app=WebAppInfo(url=webapp_url)
            )],
            [InlineKeyboardButton("📋 Мои аккаунты", callback_data="my_accounts")],
            [InlineKeyboardButton("⚡ Поднять все сейчас", callback_data="boost_all")],
            [InlineKeyboardButton("🎲 Тест рандомайзера", callback_data="test_random")],
            [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
            [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")]
        ]
        
        await update.message.reply_text(
            f"👋 Привет, {user.first_name}!\n\n"
            f"🤖 **OLX Auto-Booster Bot v2.0**\n"
            f"• Автоподнятие каждые 13-17 минут\n"
            f"• Рандомные секунды в интервалах\n"
            f"• Работает на ПК и телефоне\n"
            f"• Безопасное хранение аккаунтов\n\n"
            f"📌 **Как начать:**\n"
            f"1. Нажми '➕ Добавить аккаунт OLX'\n"
            f"2. Войди в свой аккаунт OLX\n"
            f"3. Вернись в бота и сохрани\n"
            f"4. Бот начнет работать автоматически!\n\n"
            f"⏰ **Интервал:** 13-17 минут (случайный)",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def handle_webapp_data(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка данных из WebApp"""
        try:
            data = json.loads(update.effective_message.web_app_data.data)
            user_id = data.get('user_id')
            account_name = data.get('account_name')
            cookies = data.get('cookies')
            
            if not all([user_id, account_name]):
                await update.message.reply_text("❌ Ошибка: неполные данные")
                return
            
            # Проверяем лимит аккаунтов
            async with aiosqlite.connect(CONFIG['database']) as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM accounts WHERE user_id = ?",
                    (user_id,)
                )
                count = (await cursor.fetchone())[0]
                
                if count >= CONFIG['max_accounts_per_user']:
                    await update.message.reply_text(
                        f"❌ Достигнут лимит аккаунтов ({CONFIG['max_accounts_per_user']})\n"
                        f"Удалите ненужные аккаунты через меню 'Мои аккаунты'"
                    )
                    return
                
                # Сохраняем новый аккаунт
                next_boost_time = datetime.now() + timedelta(
                    seconds=generate_random_interval()
                )
                
                await db.execute(
                    """INSERT INTO accounts 
                    (user_id, account_name, cookies, status, next_boost, boost_interval) 
                    VALUES (?, ?, ?, 'active', ?, ?)""",
                    (user_id, account_name, 
                     json.dumps(cookies) if cookies else None,
                     next_boost_time.isoformat(),
                     generate_random_interval())
                )
                await db.commit()
                
                # Получаем ID нового аккаунта
                cursor = await db.execute(
                    "SELECT last_insert_rowid()"
                )
                account_id = (await cursor.fetchone())[0]
            
            # Отправляем подтверждение
            interval_sec = generate_random_interval()
            minutes = interval_sec // 60
            seconds = interval_sec % 60
            
            await update.message.reply_text(
                f"✅ **Аккаунт '{account_name}' успешно добавлен!**\n\n"
                f"📊 **Детали:**\n"
                f"• ID аккаунта: `{account_id}`\n"
                f"• Статус: 🟢 Активный\n"
                f"• Первое поднятие через: {minutes} мин {seconds} сек\n"
                f"• Следующие интервалы: 13-17 минут (рандом)\n\n"
                f"🤖 Бот автоматически начнет работу.",
                parse_mode='Markdown'
            )
            
            # Запускаем задачу автоподнятия
            asyncio.create_task(self.start_auto_boost(user_id, account_id, account_name))
            
        except Exception as e:
            logging.error(f"Ошибка обработки WebApp данных: {e}")
            await update.message.reply_text(f"❌ Ошибка при сохранении: {str(e)}")
    
    async def show_my_accounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать все аккаунты пользователя"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        async with aiosqlite.connect(CONFIG['database']) as db:
            cursor = await db.execute(
                """SELECT id, account_name, status, last_boost, total_boosts, 
                       boost_interval, next_boost 
                FROM accounts 
                WHERE user_id = ? 
                ORDER BY created_at DESC""",
                (user_id,)
            )
            accounts = await cursor.fetchall()
        
        if not accounts:
            await query.edit_message_text(
                "📭 У вас пока нет добавленных аккаунтов.\n\n"
                "Нажмите '➕ Добавить аккаунт OLX' чтобы начать!"
            )
            return
        
        text = "📋 **Ваши аккаунты OLX:**\n\n"
        keyboard = []
        
        for acc in accounts:
            acc_id, name, status, last_boost, total, interval, next_boost = acc
            
            # Иконка статуса
            status_icon = "🟢" if status == 'active' else "🔴"
            
            # Время последнего поднятия
            if last_boost:
                last_time = datetime.fromisoformat(last_boost).strftime("%d.%m %H:%M")
            else:
                last_time = "никогда"
            
            # Следующее поднятие
            if next_boost:
                next_time = datetime.fromisoformat(next_boost)
                now = datetime.now()
                if next_time > now:
                    delta = next_time - now
                    mins = delta.seconds // 60
                    secs = delta.seconds % 60
                    next_str = f"через {mins} мин {secs} сек"
                else:
                    next_str = "скоро"
            else:
                next_str = "не запланировано"
            
            # Форматируем интервал
            int_min = interval // 60
            int_sec = interval % 60
            
            text += f"{status_icon} **{name}**\n"
            text += f"   ├ ID: `{acc_id}`\n"
            text += f"   ├ Поднятий: {total}\n"
            text += f"   ├ Интервал: {int_min} мин {int_sec} сек\n"
            text += f"   ├ Последнее: {last_time}\n"
            text += f"   └ Следующее: {next_str}\n\n"
            
            # Кнопки для аккаунта
            keyboard.append([
                InlineKeyboardButton(f"⚡ {name}", callback_data=f"boost:{acc_id}"),
                InlineKeyboardButton(f"🔄 {name}", callback_data=f"refresh:{acc_id}")
            ])
        
        keyboard.append([
            InlineKeyboardButton("🗑️ Удалить все", callback_data="delete_all"),
            InlineKeyboardButton("◀️ Назад", callback_data="back_main")
        ])
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    async def boost_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Ручное поднятие аккаунта"""
        query = update.callback_query
        await query.answer()
        
        _, acc_id = query.data.split(":")
        acc_id = int(acc_id)
        
        await query.edit_message_text(
            "🔄 **Начинаю поднятие...**\n"
            "Это займет примерно 30-60 секунд."
        )
        
        success, message, screenshot = await self.perform_boost(acc_id)
        
        if success:
            # Обновляем интервал для следующего поднятия
            new_interval = generate_random_interval()
            next_boost = datetime.now() + timedelta(seconds=new_interval)
            
            async with aiosqlite.connect(CONFIG['database']) as db:
                await db.execute(
                    "UPDATE accounts SET next_boost = ?, boost_interval = ? WHERE id = ?",
                    (next_boost.isoformat(), new_interval, acc_id)
                )
                await db.commit()
            
            # Форматируем время следующего поднятия
            mins = new_interval // 60
            secs = new_interval % 60
            
            await query.edit_message_text(
                f"✅ **Успешно!**\n\n"
                f"{message}\n\n"
                f"⏰ Следующее поднятие через: {mins} мин {secs} сек\n"
                f"🕒 Время: {datetime.now().strftime('%H:%M:%S')}"
            )
        else:
            await query.edit_message_text(
                f"❌ **Ошибка!**\n\n"
                f"{message}\n\n"
                f"Попробуйте:\n"
                f"1. Проверить интернет\n"
                f"2. Перезайти в аккаунт\n"
                f"3. Подождать 5 минут"
            )
    
    async def perform_boost(self, account_id: int) -> Tuple[bool, str, Optional[str]]:
        """Основная функция поднятия объявления"""
        screenshot_path = None
        
        try:
            # Получаем данные аккаунта
            async with aiosqlite.connect(CONFIG['database']) as db:
                cursor = await db.execute(
                    """SELECT account_name, cookies, olx_username 
                    FROM accounts WHERE id = ? AND status = 'active'""",
                    (account_id,)
                )
                account = await cursor.fetchone()
                
                if not account:
                    return False, "Аккаунт не найден или неактивен", None
                
                account_name, cookies_json, olx_username = account
            
            # Создаем драйвер
            driver = self.browser_manager.get_driver(f"acc_{account_id}")
            if not driver:
                return False, "Не удалось запустить браузер", None
            
            # Загружаем главную страницу OLX
            logging.info(f"🌐 Загружаем OLX для аккаунта {account_name}")
            driver.get("https://www.olx.kz")
            time.sleep(3)
            
            # Загружаем куки если есть
            if cookies_json:
                try:
                    cookies = json.loads(cookies_json)
                    driver.delete_all_cookies()
                    
                    for cookie in cookies:
                        try:
                            # Убеждаемся, что куки имеют правильный формат
                            if 'sameSite' in cookie and cookie['sameSite'] not in ['Strict', 'Lax', 'None']:
                                cookie['sameSite'] = 'Lax'
                            driver.add_cookie(cookie)
                        except Exception as e:
                            logging.warning(f"Не удалось добавить куки: {e}")
                    
                    driver.refresh()
                    time.sleep(3)
                except Exception as e:
                    logging.error(f"Ошибка загрузки куки: {e}")
            
            # Проверяем авторизацию
            if not self._check_auth(driver):
                return False, "Требуется повторная авторизация в OLX", None
            
            # Идем в "Мои объявления"
            logging.info(f"🔍 Ищем объявления для {account_name}")
            driver.get("https://www.olx.kz/myaccount/adverts/")
            time.sleep(5)
            
            # Ищем кнопку поднятия
            boost_button = self._find_boost_button(driver)
            if not boost_button:
                # Делаем скриншот для отладки
                screenshot_path = self._take_screenshot(driver, account_id, "no_button")
                return False, "Кнопка 'Поднять' не найдена. Возможно нет активных объявлений", screenshot_path
            
            # Нажимаем кнопку
            logging.info(f"🖱️ Нажимаем кнопку поднятия для {account_name}")
            driver.execute_script("arguments[0].click();", boost_button)
            time.sleep(3)
            
            # Подтверждаем поднятие
            if not self._confirm_boost(driver):
                screenshot_path = self._take_screenshot(driver, account_id, "no_confirm")
                return False, "Не удалось подтвердить поднятие", screenshot_path
            
            # Ждем завершения
            time.sleep(5)
            
            # Проверяем успешность
            if self._check_boost_success(driver):
                # Делаем скриншот успеха
                screenshot_path = self._take_screenshot(driver, account_id, "success")
                
                # Обновляем статистику
                async with aiosqlite.connect(CONFIG['database']) as db:
                    await db.execute(
                        """UPDATE accounts 
                        SET last_boost = ?, total_boosts = total_boosts + 1
                        WHERE id = ?""",
                        (datetime.now().isoformat(), account_id)
                    )
                    
                    await db.execute(
                        """INSERT INTO boost_logs 
                        (account_id, success, message, screenshot) 
                        VALUES (?, 1, ?, ?)""",
                        (account_id, "Успешное поднятие", screenshot_path)
                    )
                    await db.commit()
                
                # Генерируем новый интервал
                new_interval = generate_random_interval()
                mins = new_interval // 60
                secs = new_interval % 60
                
                return True, f"✅ Объявление для '{account_name}' успешно поднято!\n🎲 Следующий интервал: {mins} мин {secs} сек", screenshot_path
            else:
                screenshot_path = self._take_screenshot(driver, account_id, "failed")
                return False, "Поднятие возможно не сработало", screenshot_path
            
        except Exception as e:
            logging.error(f"Ошибка при поднятии аккаунта {account_id}: {e}")
            
            # Сохраняем ошибку в логи
            async with aiosqlite.connect(CONFIG['database']) as db:
                await db.execute(
                    """INSERT INTO boost_logs 
                    (account_id, success, message) 
                    VALUES (?, 0, ?)""",
                    (account_id, str(e))
                )
                await db.commit()
            
            return False, f"Ошибка: {str(e)}", None
    
    def _check_auth(self, driver) -> bool:
        """Проверка авторизации на OLX"""
        try:
            # Проверяем наличие элементов авторизованного пользователя
            indicators = [
                "//a[contains(@href, 'myaccount')]",
                "//div[contains(text(), 'Мой профиль')]",
                "//a[contains(text(), 'Выйти')]",
                "//span[contains(text(), 'Мои объявления')]"
            ]
            
            for indicator in indicators:
                try:
                    elements = driver.find_elements(By.XPATH, indicator)
                    if elements and len(elements) > 0:
                        return True
                except:
                    continue
            
            return False
        except:
            return False
    
    def _find_boost_button(self, driver):
        """Поиск кнопки поднятия объявления"""
        # Список всех возможных селекторов
        selectors = [
            # По тексту
            "//button[contains(., 'Поднять')]",
            "//button[contains(., 'Підняти')]",
            "//button[contains(., 'Renew')]",
            "//button[contains(., 'поднять')]",
            "//button[contains(., 'Поднять за')]",
            
            # По data-атрибутам
            "//button[@data-cy='ad-renew-button']",
            "//button[@data-testid='renew-button']",
            "//button[@data-qa='renew-ad-button']",
            
            # По классам
            "//button[contains(@class, 'renew')]",
            "//button[contains(@class, 'boost')]",
            "//button[contains(@class, 'promote')]",
            
            # По span внутри button
            "//button[.//span[contains(., 'Поднять')]]",
            "//button[.//div[contains(., 'Поднять')]]",
        ]
        
        for selector in selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed() and element.is_enabled():
                        # Прокручиваем к элементу
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block: 'center'});", 
                            element
                        )
                        time.sleep(1)
                        return element
            except:
                continue
        
        return None
    
    def _confirm_boost(self, driver) -> bool:
        """Подтверждение поднятия во всплывающем окне"""
        confirm_selectors = [
            "//button[contains(., 'Подтвердить')]",
            "//button[contains(., 'Підтвердити')]",
            "//button[contains(., 'Confirm')]",
            "//button[contains(., 'ОК')]",
            "//button[contains(., 'Да')]",
            
            "//button[@data-cy='confirmation-button']",
            "//button[@data-testid='confirm-button']",
            "//button[@data-qa='confirm-button']",
            
            "//div[contains(@class, 'modal')]//button[contains(., 'Подтвердить')]",
            "//div[@role='dialog']//button[contains(., 'Подтвердить')]",
        ]
        
        for selector in confirm_selectors:
            try:
                element = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                driver.execute_script("arguments[0].click();", element)
                time.sleep(2)
                return True
            except:
                continue
        
        return False
    
    def _check_boost_success(self, driver) -> bool:
        """Проверка успешности поднятия"""
        success_indicators = [
            "Объявление поднято",
            "Объявление обновлено",
            "Успешно",
            "Success",
            "поднято",
            "обновлено",
            "объявление появится",
            "объявление будет выше"
        ]
        
        try:
            page_text = driver.page_source.lower()
            for indicator in success_indicators:
                if indicator.lower() in page_text:
                    return True
        except:
            pass
        
        return False
    
    def _take_screenshot(self, driver, account_id: int, reason: str) -> str:
        """Создание скриншота"""
        try:
            screenshots_dir = Path(CONFIG['screenshots_dir'])
            screenshots_dir.mkdir(exist_ok=True)
            
            filename = f"{account_id}_{reason}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            filepath = screenshots_dir / filename
            
            driver.save_screenshot(str(filepath))
            logging.info(f"📸 Скриншот сохранен: {filepath}")
            
            return str(filepath)
        except Exception as e:
            logging.error(f"Ошибка создания скриншота: {e}")
            return ""
    
    async def start_auto_boost(self, user_id: int, account_id: int, account_name: str):
        """Запуск автоматического поднятия для аккаунта"""
        task_id = f"{user_id}_{account_id}"
        
        if task_id in self.boost_tasks:
            logging.info(f"⚠️ Задача для аккаунта {account_name} уже запущена")
            return
        
        async def boost_loop():
            """Цикл автоматического поднятия"""
            logging.info(f"🚀 Запущен авто-буст для {account_name}")
            
            while True:
                try:
                    # Проверяем статус аккаунта
                    async with aiosqlite.connect(CONFIG['database']) as db:
                        cursor = await db.execute(
                            "SELECT status, next_boost FROM accounts WHERE id = ?",
                            (account_id,)
                        )
                        account_data = await cursor.fetchone()
                        
                        if not account_data or account_data[0] != 'active':
                            logging.info(f"⏹️ Аккаунт {account_name} неактивен, останавливаю")
                            break
                        
                        # Проверяем время следующего поднятия
                        next_boost_str = account_data[1]
                        if next_boost_str:
                            next_boost = datetime.fromisoformat(next_boost_str)
                            now = datetime.now()
                            
                            if next_boost > now:
                                wait_seconds = (next_boost - now).total_seconds()
                                if wait_seconds > 0:
                                    logging.info(f"⏰ Ожидание {wait_seconds:.0f} сек до поднятия {account_name}")
                                    await asyncio.sleep(wait_seconds)
                    
                    # Выполняем поднятие
                    success, message, screenshot = await self.perform_boost(account_id)
                    
                    if success:
                        logging.info(f"✅ Автоподнятие для {account_name}: {message}")
                        
                        # Обновляем время следующего поднятия
                        new_interval = generate_random_interval()
                        next_boost = datetime.now() + timedelta(seconds=new_interval)
                        
                        async with aiosqlite.connect(CONFIG['database']) as db:
                            await db.execute(
                                "UPDATE accounts SET next_boost = ?, boost_interval = ? WHERE id = ?",
                                (next_boost.isoformat(), new_interval, account_id)
                            )
                            await db.commit()
                        
                        # Ждем до следующего поднятия
                        await asyncio.sleep(new_interval)
                    else:
                        logging.error(f"❌ Ошибка автоподнятия для {account_name}: {message}")
                        
                        # Ждем 5 минут перед повторной попыткой
                        await asyncio.sleep(300)
                    
                except asyncio.CancelledError:
                    logging.info(f"🛑 Задача для {account_name} отменена")
                    break
                except Exception as e:
                    logging.error(f"⚠️ Ошибка в цикле для {account_name}: {e}")
                    await asyncio.sleep(60)
        
        # Создаем и запускаем задачу
        task = asyncio.create_task(boost_loop())
        self.boost_tasks[task_id] = task
        
        # Сохраняем информацию о задаче
        logging.info(f"📝 Зарегистрирована задача {task_id} для {account_name}")
    
    async def test_randomizer(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Тест рандомайзера интервалов"""
        query = update.callback_query
        await query.answer()
        
        text = "🎲 **Тест рандомайзера интервалов**\n\n"
        text += "Генерирую 10 случайных интервалов:\n\n"
        
        intervals = []
        for i in range(10):
            interval = generate_random_interval()
            intervals.append(interval)
            
            minutes = interval // 60
            seconds = interval % 60
            
            text += f"{i+1}. {minutes} мин {seconds} сек ({interval} сек)\n"
        
        avg_seconds = sum(intervals) // len(intervals)
        avg_min = avg_seconds // 60
        avg_sec = avg_seconds % 60
        
        text += f"\n📊 **Статистика:**\n"
        text += f"• Минимум: {min(intervals)//60} мин {min(intervals)%60} сек\n"
        text += f"• Максимум: {max(intervals)//60} мин {max(intervals)%60} сек\n"
        text += f"• Среднее: {avg_min} мин {avg_sec} сек\n"
        text += f"• Диапазон: 13-17 минут со случайными секундами"
        
        await query.edit_message_text(text, parse_mode='Markdown')
    
    async def show_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показать статистику"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        
        async with aiosqlite.connect(CONFIG['database']) as db:
            # Статистика пользователя
            cursor = await db.execute(
                """SELECT 
                    COUNT(*) as total_accounts,
                    SUM(total_boosts) as total_boosts,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_accounts
                FROM accounts WHERE user_id = ?""",
                (user_id,)
            )
            user_stats = await cursor.fetchone()
            
            # Последние поднятия
            cursor = await db.execute(
                """SELECT a.account_name, l.message, l.created_at 
                FROM boost_logs l 
                JOIN accounts a ON l.account_id = a.id 
                WHERE a.user_id = ? 
                ORDER BY l.created_at DESC 
                LIMIT 5""",
                (user_id,)
            )
            recent_boosts = await cursor.fetchall()
        
        if not user_stats or user_stats[0] == 0:
            await query.edit_message_text(
                "📊 **Статистика пуста**\n\n"
                "У вас пока нет аккаунтов или поднятий.\n"
                "Добавьте первый аккаунт через меню!"
            )
            return
        
        total_accounts, total_boosts, active_accounts = user_stats
        
        text = f"📊 **Ваша статистика**\n\n"
        text += f"• Аккаунтов всего: {total_accounts}\n"
        text += f"• Активных: {active_accounts}\n"
        text += f"• Всего поднятий: {total_boosts or 0}\n"
        text += f"• Активных задач: {len([t for t in self.boost_tasks.values() if not t.done()])}\n\n"
        
        if recent_boosts:
            text += "🕒 **Последние поднятия:**\n"
            for acc_name, message, created_at in recent_boosts:
                time_str = datetime.fromisoformat(created_at).strftime("%H:%M")
                icon = "✅" if "Успеш" in str(message) else "❌"
                text += f"{icon} {acc_name} - {time_str}\n"
        
        text += f"\n⚙️ **Настройки бота:**\n"
        text += f"• Интервал: 13-17 минут\n"
        text += f"• Случайные секунды: Да\n"
        text += f"• WebApp URL: {CONFIG['webapp_url']}"
        
        await query.edit_message_text(text, parse_mode='Markdown')
    
    async def cleanup(self):
        """Очистка ресурсов"""
        logging.info("🧹 Очистка ресурсов бота...")
        
        # Отменяем все задачи
        for task_id, task in self.boost_tasks.items():
            if not task.done():
                task.cancel()
                logging.info(f"❌ Отменена задача {task_id}")
        
        # Очищаем браузеры
        self.browser_manager.cleanup()
        
        # Закрываем сессию
        if self.session:
            await self.session.close()
        
        logging.info("✅ Бот остановлен")

# ===== ЗАПУСК БОТА =====
async def main():
    """Основная функция запуска"""
    # Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('olx_bot.log'),
            logging.StreamHandler()
        ]
    )
    
    # Создаем необходимые папки
    Path(CONFIG['screenshots_dir']).mkdir(exist_ok=True)
    
    # Инициализация базы данных
    await init_database()
    
    # Создаем бота
    bot = OLXMasterBot()
    
    try:
        # Инициализация сессии
        await bot.init_session()
        
        # Создаем приложение Telegram
        app = Application.builder().token(CONFIG['bot_token']).build()
        bot.app = app
        
        # Регистрация обработчиков
        app.add_handler(CommandHandler("start", bot.start))
        app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, bot.handle_webapp_data))
        app.add_handler(CallbackQueryHandler(bot.show_my_accounts, pattern="^my_accounts$"))
        app.add_handler(CallbackQueryHandler(bot.boost_account, pattern="^boost:"))
        app.add_handler(CallbackQueryHandler(bot.test_randomizer, pattern="^test_random$"))
        app.add_handler(CallbackQueryHandler(bot.show_stats, pattern="^stats$"))
        app.add_handler(CallbackQueryHandler(bot.start, pattern="^back_main$"))
        
        # Запускаем все активные аккаунты
        await bot.start_all_accounts()
        
        # Запуск бота
        logging.info("🤖 OLX Auto-Booster Bot запущен!")
        logging.info(f"🌐 WebApp URL: {CONFIG['webapp_url']}")
        logging.info("🎲 Интервал: 13-17 минут со случайными секундами")
        
        await app.run_polling()
        
    except KeyboardInterrupt:
        logging.info("\n🛑 Получен сигнал остановки")
    except Exception as e:
        logging.error(f"❌ Критическая ошибка: {e}")
    finally:
        await bot.cleanup()

if __name__ == "__main__":
    # Проверка зависимостей
    import subprocess
    import sys
    
    print("=" * 60)
    print("🤖 OLX Auto-Booster Bot v2.0")
    print("=" * 60)
    print("🎲 Особенности:")
    print("• Автоподнятие каждые 13-17 минут")
    print("• Случайные секунды в интервалах")
    print("• Telegram WebApp для авторизации")
    print("• Работает на бесплатном хостинге")
    print("=" * 60)
    
    # Запрос токена если не указан
    if CONFIG['bot_token'] == 'ВАШ_ТОКЕН_ОТ_BOTFATHER':
        token = input("Введите токен бота от @BotFather: ")
        CONFIG['bot_token'] = token.strip()
    
    # Запрос WebApp URL
    if CONFIG['webapp_url'] == 'https://ваш-проект.vercel.app/':
        url = input("Введите URL вашего WebApp (Vercel/GitHub Pages): ")
        CONFIG['webapp_url'] = url.strip()
    
    print("\n🚀 Запуск бота...")
    print("Для остановки нажмите Ctrl+C\n")
    
    # Запуск
    asyncio.run(main())
