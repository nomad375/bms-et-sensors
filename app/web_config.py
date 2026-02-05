from flask import Flask, render_template_string, request, jsonify
import sys
import glob
import time

# Подключаем MSCL
mscl_path = '/usr/lib/python3.12/dist-packages'
if mscl_path not in sys.path: sys.path.append(mscl_path)
import MSCL as mscl

app = Flask(__name__)

# Глобальные переменные для соединения
BASE_STATION = None
CONNECTION = None

#HTML ШАБЛОН (Интерфейс)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>MSCL Node Configurator</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding: 20px; background-color: #f8f9fa; }
        .card { margin-bottom: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .status-ok { color: green; font-weight: bold; }
        .status-err { color: red; font-weight: bold; }
    </style>
</head>
<body>
<div class="container">
    <h1 class="mb-4">🎛️ Настройка Ноды (Web Interface)</h1>
    
    <div class="card">
        <div class="card-header">1. Подключение к Базовой Станции</div>
        <div class="card-body">
            <button onclick="connectBase()" class="btn btn-primary">🔌 Найти станцию и подключиться</button>
            <button onclick="disconnectBase()" class="btn btn-danger">❌ Отключиться</button>
            <span id="connStatus" class="ms-3">Не подключено</span>
        </div>
    </div>

    <div class="card">
        <div class="card-header">2. Поиск Ноды</div>
        <div class="card-body">
            <div class="input-group mb-3">
                <span class="input-group-text">Node ID</span>
                <input type="number" id="nodeId" class="form-control" value="16907">
                <button onclick="readConfig()" class="btn btn-success">🔍 Считать настройки</button>
            </div>
            <div id="readStatus" class="form-text">Нажмите, чтобы прочитать текущую конфигурацию из EEPROM.</div>
        </div>
    </div>

    <div class="card" id="configCard" style="display:none; border: 2px solid #0d6efd;">
        <div class="card-header bg-primary text-white">3. Настройки Ноды</div>
        <div class="card-body">
            <form id="configForm">
                <div class="mb-3">
                    <label class="form-label">Модель:</label>
                    <input type="text" id="modelName" class="form-control" disabled>
                </div>
                
                <div class="mb-3">
                    <label class="form-label">Частота опроса (Sample Rate):</label>
                    <select id="sampleRate" class="form-select">
                        </select>
                </div>

                <div class="mb-3">
                    <label class="form-label">Активные каналы (Mask):</label>
                    <div id="channelsArea"></div>
                </div>

                <button type="button" onclick="applyConfig()" class="btn btn-warning w-100">💾 ЗАПИСАТЬ В НОДУ</button>
            </form>
            <div id="writeStatus" class="mt-3 text-center font-weight-bold"></div>
        </div>
    </div>
</div>

<script>
    async function connectBase() {
        document.getElementById('connStatus').innerText = "Поиск...";
        const res = await fetch('/api/connect', {method:'POST'});
        const data = await res.json();
        if(data.success) {
            document.getElementById('connStatus').innerHTML = `<span class="status-ok">OK: ${data.port}</span>`;
        } else {
            document.getElementById('connStatus').innerHTML = `<span class="status-err">Ошибка: ${data.error}</span>`;
        }
    }

    async function disconnectBase() {
        await fetch('/api/disconnect', {method:'POST'});
        document.getElementById('connStatus').innerText = "Отключено";
        document.getElementById('configCard').style.display = "none";
    }

    async function readConfig() {
        const id = document.getElementById('nodeId').value;
        document.getElementById('readStatus').innerText = "Чтение... (Может занять 5-10 сек)";
        
        const res = await fetch(`/api/read/${id}`);
        const data = await res.json();
        
        if(data.success) {
            document.getElementById('readStatus').innerHTML = "<span class='status-ok'>Успешно прочитано!</span>";
            document.getElementById('configCard').style.display = "block";
            
            // Заполняем форму
            document.getElementById('modelName').value = data.model;
            
            // Sample Rates
            const sel = document.getElementById('sampleRate');
            sel.innerHTML = "";
            data.supported_rates.forEach(r => {
                let opt = document.createElement('option');
                opt.value = r.enum_val;
                opt.text = r.str_val;
                if(r.enum_val == data.current_rate) opt.selected = true;
                sel.add(opt);
            });

            // Channels
            const chDiv = document.getElementById('channelsArea');
            chDiv.innerHTML = "";
            data.channels.forEach(ch => {
                let checked = ch.enabled ? "checked" : "";
                chDiv.innerHTML += `
                    <div class="form-check form-switch">
                        <input class="form-check-input" type="checkbox" id="ch_${ch.id}" ${checked}>
                        <label class="form-check-label" for="ch_${ch.id}">${ch.name}</label>
                    </div>`;
            });

        } else {
            document.getElementById('readStatus').innerHTML = `<span class="status-err">Ошибка: ${data.error}</span>`;
        }
    }

    async function applyConfig() {
        const id = document.getElementById('nodeId').value;
        const rate = document.getElementById('sampleRate').value;
        
        // Собираем каналы
        let activeChs = [];
        document.querySelectorAll('input[type=checkbox]').forEach(cb => {
            if(cb.checked) {
                activeChs.push(parseInt(cb.id.replace('ch_', '')));
            }
        });

        document.getElementById('writeStatus').innerText = "Запись... Не выключайте питание!";
        
        const res = await fetch('/api/write', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                node_id: id,
                sample_rate: parseInt(rate),
                channels: activeChs
            })
        });
        
        const data = await res.json();
        if(data.success) {
            document.getElementById('writeStatus').innerHTML = "<span class='status-ok'>✅ НАСТРОЙКИ СОХРАНЕНЫ!</span>";
        } else {
            document.getElementById('writeStatus').innerHTML = `<span class='status-err'>❌ Ошибка: ${data.error}</span>`;
        }
    }
</script>
</body>
</html>
"""

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def find_port():
    ports = glob.glob('/dev/ttyACM*') + glob.glob('/dev/ttyUSB*')
    return ports[0] if ports else None

# --- РОУТЫ ---

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/connect', methods=['POST'])
def api_connect():
    global BASE_STATION, CONNECTION
    try:
        if BASE_STATION: return jsonify(success=True, port="Already Connected")
        
        port = find_port()
        if not port: return jsonify(success=False, error="No USB Found")
        
        CONNECTION = mscl.Connection.Serial(port)
        BASE_STATION = mscl.BaseStation(CONNECTION)
        BASE_STATION.readWriteRetries(3) # Важно для стабильности!
        
        if BASE_STATION.ping():
            return jsonify(success=True, port=port)
        else:
            return jsonify(success=False, error="Ping Failed")
    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/api/disconnect', methods=['POST'])
def api_disconnect():
    global BASE_STATION, CONNECTION
    try:
        if CONNECTION: CONNECTION.disconnect()
    except: pass
    BASE_STATION = None
    CONNECTION = None
    return jsonify(success=True)

@app.route('/api/read/<int:node_id>')
def api_read(node_id):
    global BASE_STATION
    if not BASE_STATION: return jsonify(success=False, error="Not Connected")
    
    try:
        node = mscl.WirelessNode(node_id, BASE_STATION)
        node.readWriteRetries(3) # Повышаем надежность
        
        # 1. Будим ноду (Важно!)
        node.setToIdle()
        
        # 2. Читаем модель
        model = node.model()
        
        # 3. Читаем текущие настройки
        # Sample Rate
        current_rate_enum = node.getSampleRate().rate()
        
        # Supported Rates (для выпадающего списка)
        features = node.features()
        supported_rates = []
        for rate in features.sampleRates():
            supported_rates.append({
                "enum_val": int(rate), 
                "str_val": str(rate)
            })
            
        # Channels
        active_mask = node.getActiveChannels() # ChannelMask object
        channels = []
        # FlexiForce обычно имеет до 8 каналов, проверим первые 4
        for i in range(1, 9):
            if features.supportsChannel(i):
                channels.append({
                    "id": i,
                    "name": f"Channel {i}",
                    "enabled": active_mask.enabled(i)
                })
        
        return jsonify(
            success=True,
            model=model,
            current_rate=int(current_rate_enum),
            supported_rates=supported_rates,
            channels=channels
        )

    except Exception as e:
        return jsonify(success=False, error=str(e))

@app.route('/api/write', methods=['POST'])
def api_write():
    global BASE_STATION
    if not BASE_STATION: return jsonify(success=False, error="Not Connected")
    
    data = request.json
    node_id = int(data['node_id'])
    
    try:
        node = mscl.WirelessNode(node_id, BASE_STATION)
        
        # 1. Готовим конфиг
        config = mscl.WirelessNodeConfig()
        config.defaultMode(mscl.WirelessTypes.defaultMode_sync)
        
        # Sample Rate
        sample_rate_enum = mscl.WirelessTypes.WirelessSampleRate(data['sample_rate'])
        config.sampleRate(sample_rate_enum)
        
        # Channels
        mask = mscl.ChannelMask(0)
        for ch in data['channels']:
            mask.enable(ch)
        config.activeChannels(mask)
        
        # 2. Будим и пишем
        node.setToIdle()
        node.applyConfig(config)
        
        return jsonify(success=True)
    except Exception as e:
        return jsonify(success=False, error=str(e))

if __name__ == '__main__':
    # Запускаем на всех интерфейсах (0.0.0.0), чтобы видеть извне
    app.run(host='0.0.0.0', port=5000, debug=True)