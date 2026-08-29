FROM python:3.12-slim

WORKDIR /app

# Отключаем создание .pyc файлов и буферизацию вывода
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
# Форсируем установку PyAV 17.1.0 в обход ложной блокировки aiortc для исправления утечки памяти
RUN pip install --no-cache-dir --no-deps --ignore-installed av==17.1.0
# Копируем наш патч для lolka._voice_impl, исправляющий блокировку event loop и утечки в очередях
COPY src/patches/_voice_impl.py /usr/local/lib/python3.12/site-packages/lolka/_voice_impl.py

COPY . .

# При старте контейнера сначала накатываем миграции, затем запускаем бота
CMD ["sh", "-c", "alembic upgrade head && python src/bot.py"]
