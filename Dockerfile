# Этап 1: Сборка библиотеки uldaq
FROM python:3.10-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential automake autoconf libtool \
    libusb-1.0-0-dev swig pkg-config ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
RUN git clone --depth 1 https://github.com/mccdaq/uldaq.git /tmp/uldaq_repo && \
    cd /tmp/uldaq_repo && \
    autoreconf -ivf && \
    ./configure && \
    make -j$(nproc) && \
    make install

# Этап 2: Финальный образ для запуска
FROM python:3.10-slim

# Копируем только скомпилированные библиотеки из первого этапа
COPY --from=builder /usr/local/lib/libuldaq* /usr/local/lib/
COPY --from=builder /usr/local/include/uldaq.h /usr/local/include/
RUN ldconfig

# Установка только необходимых системных библиотек (libusb)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libusb-1.0-0 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]