// Telegram WebApp
const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

// Элементы
const accountNameInput = document.getElementById('accountName');
const openOlxBtn = document.getElementById('openOlxBtn');
const saveBtn = document.getElementById('saveBtn');
const statusCard = document.getElementById('statusCard');
const statusContent = document.getElementById('statusContent');
const statusProgress = document.getElementById('statusProgress');
const progressFill = document.getElementById('progressFill');

// Получаем параметры из URL
const urlParams = new URLSearchParams(window.location.search);
const userId = urlParams.get('user_id') || '';
const username = urlParams.get('username') || 'user';

// Состояние
let olxWindow = null;
let checkInterval = null;
let authDetected = false;

// Функции статуса
function showStatus(type, message) {
    statusContent.innerHTML = message;
    statusContent.className = `status-${type}`;
    statusCard.style.display = 'block';
    statusProgress.style.display = 'none';
    
    // Автоскрытие для успеха
    if (type === 'success') {
        setTimeout(() => {
            statusCard.style.display = 'none';
        }, 3000);
    }
}

function showProgress(message, duration = 5000) {
    statusContent.innerHTML = message;
    statusContent.className = 'status-loading';
    statusCard.style.display = 'block';
    statusProgress.style.display = 'block';
    
    // Анимация прогресс-бара
    let width = 0;
    const interval = 50;
    const steps = duration / interval;
    const increment = 100 / steps;
    
    progressFill.style.width = '0%';
    
    const timer = setInterval(() => {
        width += increment;
        progressFill.style.width = width + '%';
        
        if (width >= 100) {
            clearInterval(timer);
        }
    }, interval);
    
    return timer;
}

// Открытие OLX
openOlxBtn.addEventListener('click', () => {
    const accountName = accountNameInput.value.trim();
    
    if (!accountName) {
        showStatus('error', '❌ Введите название аккаунта');
        accountNameInput.focus();
        return;
    }
    
    if (accountName.length < 2) {
        showStatus('error', '❌ Название слишком короткое');
        return;
    }
    
    // Открываем OLX
    olxWindow = window.open(
        'https://www.olx.kz/identity/',
        '_blank',
        'noopener,noreferrer'
    );
    
    if (!olxWindow) {
        showStatus('error', '❌ Не удалось открыть окно. Разрешите всплывающие окна.');
        return;
    }
    
    // Показываем кнопку сохранения
    saveBtn.style.display = 'flex';
    
    // Запускаем демо-процесс авторизации
    startAuthSimulation();
});

// Симуляция процесса авторизации (демо)
function startAuthSimulation() {
    showProgress('⏳ Открываю OLX...', 1000);
    
    setTimeout(() => {
        showProgress('🔐 Ожидаю вход в аккаунт...', 4000);
    }, 1200);
    
    setTimeout(() => {
        showStatus('success', '✅ Готово! Теперь сохраните аккаунт.');
        authDetected = true;
    }, 6000);
}

// Сохранение аккаунта
saveBtn.addEventListener('click', async () => {
    const accountName = accountNameInput.value.trim();
    
    if (!accountName) {
        showStatus('error', '❌ Введите название аккаунта');
        return;
    }
    
    if (!userId) {
        showStatus('error', '❌ Ошибка: не получен user_id');
        return;
    }
    
    // Показываем прогресс сохранения
    const progressTimer = showProgress('💾 Сохраняю аккаунт...', 2000);
    
    try {
        // Имитация задержки
        await new Promise(resolve => setTimeout(resolve, 1500));
        
        // Демо-данные (в реальном приложении здесь будут реальные куки)
        const demoCookies = [
            {
                name: 'session_token',
                value: `demo_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                domain: '.olx.kz',
                path: '/',
                secure: true,
                httpOnly: false,
                sameSite: 'Lax'
            },
            {
                name: 'user_auth',
                value: 'authenticated',
                domain: '.olx.kz',
                path: '/',
                secure: true
            }
        ];
        
        // Формируем данные для отправки
        const accountData = {
            user_id: parseInt(userId),
            account_name: accountName,
            cookies: demoCookies,
            timestamp: new Date().toISOString(),
            platform: 'webapp',
            version: '2.0'
        };
        
        // Отправляем данные в Telegram бота
        tg.sendData(JSON.stringify(accountData));
        
        // Очищаем таймер прогресса
        clearInterval(progressTimer);
        progressFill.style.width = '100%';
        
        // Показываем успех
        showStatus('success', 
            `✅ Аккаунт <strong>"${accountName}"</strong> сохранен!<br>
            Бот начнет работу через 1-2 минуты.`
        );
        
        // Делаем кнопку неактивной
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<span class="btn-icon">✅</span> Аккаунт сохранен';
        saveBtn.classList.remove('btn-secondary');
        saveBtn.classList.add('btn-primary');
        
        // Автозакрытие через 3 секунды
        setTimeout(() => {
            tg.close();
        }, 3000);
        
    } catch (error) {
        console.error('Save error:', error);
        showStatus('error', '❌ Ошибка сохранения: ' + error.message);
    }
});

// Обработка нажатия Enter в поле ввода
accountNameInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        openOlxBtn.click();
    }
});

// Инициализация
document.addEventListener('DOMContentLoaded', () => {
    // Устанавливаем фокус на поле ввода
    accountNameInput.focus();
    
    // Если есть user_id в URL, можно его использовать
    if (userId) {
        console.log('User ID:', userId);
    }
    
    // Настраиваем Telegram WebApp
    tg.setHeaderColor('#1a1a1a');
    tg.setBackgroundColor('#0a0a0a');
});

// Обработчик сообщений (для демо)
window.addEventListener('message', (event) => {
    // В реальном приложении здесь можно принимать сообщения
    // от iframe или других окон
    console.log('Message received:', event.data);
});
