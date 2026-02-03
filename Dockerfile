# Этап 1: Сборка (Builder)
FROM python:3.12-slim AS builder

# Установка расширенного набора инструментов для сборки
# Добавлены: autoconf-archive, libtool, curl, zip, unzip, tar
RUN apt-get update && apt-get install -y --no-install-recommends \
    git build-essential cmake swig libusb-1.0-0-dev \
    automake autoconf autoconf-archive libtool pkg-config libboost-all-dev \
    ca-certificates curl zip unzip tar \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

# Сборка uldaq (RedLab/MCC) — это база, она должна работать всегда
RUN git clone --depth 1 https://github.com/mccdaq/uldaq.git && \
    cd uldaq && autoreconf -ivf && ./configure && \
    make -j$(nproc) && make install

# Этап 2: Финальный образ
FROM python:3.12-slim

# Копируем скомпилированную библиотеку uldaq
COPY --from=builder /usr/local/lib/libuldaq* /usr/local/lib/
COPY --from=builder /usr/local/include/uldaq.h /usr/local/include/
RUN ldconfig

# Установка системных библиотек для работы USB и Boost
RUN apt-get update && apt-get install -y --no-install-recommends \
    libusb-1.0-0 libboost-system-dev libboost-filesystem-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]