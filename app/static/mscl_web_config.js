    const LAST_COMM_WARN_SEC = 30;
    const LAST_COMM_CRITICAL_SEC = 120;
    let idleConfirmed = false;

    function updateReadWriteButtons() {
        const btnRead = document.getElementById('btnRead');
        const btnWrite = document.getElementById('btnWrite');
        if (btnRead) btnRead.disabled = !idleConfirmed;
        if (btnWrite) btnWrite.disabled = !idleConfirmed;
    }

    function markIdleState(isIdle) {
        idleConfirmed = !!isIdle;
        updateReadWriteButtons();
    }

    function updateBaseButtons(connected) {
        const connectBtn = document.getElementById('btnConnect');
        const disconnectBtn = document.getElementById('btnDisconnect');
        if (connectBtn) connectBtn.disabled = connected;
        if (disconnectBtn) disconnectBtn.disabled = !connected;
    }

    async function connectBase() {
        try {
            let res = await fetch('/api/connect', {method:'POST'});
            let data = await res.json();
            if (!data.success) {
                res = await fetch('/api/reconnect', {method:'POST'});
                data = await res.json();
            }
            const statusDiv = document.getElementById('readStatus');
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = "✅ Base connected";
                markIdleState(false);
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `❌ Base connect failed: ${data.message || data.error || 'Unknown error'}`;
                markIdleState(false);
            }
            await refreshBaseStatus();
        } catch (e) { }
        } finally {
            // allow disconnect button toggle after potential connect
            updateBaseButtons(window.currentBaseConnected === true);
        }
    }

    async function disconnectBase() {
        const statusDiv = document.getElementById('readStatus');
        try {
            const res = await fetch('/api/disconnect', {method: 'POST'});
            const data = await res.json();
            if (!data.success) {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `❌ ${data.error || 'Disconnect failed'}`;
            } else {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `✅ ${data.message || 'Disconnected'}`;
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "❌ Disconnect failed";
        } finally {
            await refreshBaseStatus();
        }
    }

    async function readConfig() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btnRead = document.getElementById('btnRead');
        const wasIdle = idleConfirmed;
        btnRead.disabled = true;
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "⌛ Reading...";
        document.getElementById('configCard').style.display = "none";
        
        try {
            const res = await fetch(`/api/read/${id}`);
            const data = await res.json();
            if(data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = "✅ Read complete";
                document.getElementById('configCard').style.display = "block";
                
                document.getElementById('txPower').value = data.current_power;
                const inputSel = document.getElementById('inputRange');
                inputSel.innerHTML = "";
                const inputRanges = Array.isArray(data.supported_input_ranges) ? data.supported_input_ranges : [];
                if (!inputRanges.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    inputSel.add(opt);
                } else {
                    inputRanges.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (r.primary) opt.style.fontWeight = "700";
                        if (String(r.value) === String(data.current_input_range)) opt.selected = true;
                        inputSel.add(opt);
                    });
                }
                const selectedInputRangeOpt = inputSel.options[inputSel.selectedIndex];
                const inputRangeActual = selectedInputRangeOpt ? selectedInputRangeOpt.text : "N/A";

                const lpSel = document.getElementById('lowPassFilter');
                lpSel.innerHTML = "";
                const lpOptions = Array.isArray(data.low_pass_options) ? data.low_pass_options : [];
                if (!lpOptions.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    lpSel.add(opt);
                } else {
                    lpOptions.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (String(r.value) === String(data.current_low_pass)) opt.selected = true;
                        lpSel.add(opt);
                    });
                }

                const smSel = document.getElementById('storageLimitMode');
                smSel.innerHTML = "";
                const smOptions = Array.isArray(data.storage_limit_options) ? data.storage_limit_options : [];
                if (!smOptions.length) {
                    const opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    smSel.add(opt);
                } else {
                    smOptions.forEach(r => {
                        const opt = document.createElement('option');
                        opt.value = r.value;
                        opt.text = r.label || `Value ${r.value}`;
                        if (String(r.value) === String(data.current_storage_limit_mode)) opt.selected = true;
                        smSel.add(opt);
                    });
                }
                document.getElementById('lostBeaconTimeout').value = (data.current_lost_beacon_timeout ?? 2);
                document.getElementById('diagnosticInterval').value = (data.current_diagnostic_interval ?? 60);
                const activeChannelsText = Array.isArray(data.channels)
                    ? data.channels.filter(ch => ch.enabled).map(ch => `ch${ch.id}`).join(", ")
                    : "";
                const stateNonIdle = (data.state !== null && data.state !== undefined) && String(data.state) !== "0";
                const stateText = data.state_text
                    ? `State: ${data.state_text}${(data.state !== null && data.state !== undefined) ? ` (${data.state})` : ''}`
                    : null;
                const lastCommDisplay = formatNodeLastCommLocal(data.last_comm);
                const lastCommAgeSec = parseLastCommAgeSec(data.last_comm);
                const lastCommWarn = Number.isFinite(lastCommAgeSec) && lastCommAgeSec > LAST_COMM_WARN_SEC && lastCommAgeSec <= LAST_COMM_CRITICAL_SEC;
                const lastCommDanger = Number.isFinite(lastCommAgeSec) && lastCommAgeSec > LAST_COMM_CRITICAL_SEC;
                const lastCommText = lastCommDisplay
                    ? (lastCommDanger
                        ? `<span class='status-err'>Last Comm: ${lastCommDisplay} (${Math.round(lastCommAgeSec)}s ago)</span>`
                        : (lastCommWarn
                            ? `<span class='status-warn'>Last Comm: ${lastCommDisplay} (${Math.round(lastCommAgeSec)}s ago)</span>`
                            : `Last Comm: ${lastCommDisplay}`))
                    : null;
                const storagePctNum = (data.storage_pct !== null && data.storage_pct !== undefined)
                    ? Number(data.storage_pct)
                    : null;
                const storageDanger = Number.isFinite(storagePctNum) && storagePctNum > 90;
                const storageWarn = Number.isFinite(storagePctNum) && storagePctNum >= 70 && storagePctNum <= 90;
                const statusParts = [
                    `Model: ${data.model}`,
                    `FW: ${data.fw}`,
                    (data.node_address !== null && data.node_address !== undefined) ? `Node Address: ${data.node_address}` : null,
                    lastCommText,
                    stateText ? (stateNonIdle ? `<span class='status-warn'>${stateText}</span>` : stateText) : null,
                    data.comm_protocol_text ? `Comm Protocol: ${data.comm_protocol_text}${(data.comm_protocol !== null && data.comm_protocol !== undefined) ? ` (${data.comm_protocol})` : ''}` : null,
                    data.region ? `Region: ${data.region}` : null,
                    data.frequency ? `Frequency: ${data.frequency}` : null,
                    (data.current_power !== null && data.current_power !== undefined) ? `Radio Power: ${data.current_power} dBm` : null,
                    inputRangeActual ? `Input Range: ${inputRangeActual}` : null,
                    activeChannelsText ? `Active Channels: ${activeChannelsText}` : null,
                    data.sampling_mode ? `Sampling: ${data.sampling_mode}` : null,
                    (data.data_mode && String(data.data_mode) !== "1") ? `Data Mode: ${data.data_mode}` : null,
                    (storagePctNum !== null)
                        ? (storageDanger
                            ? `<span class='status-err'>Storage: ${storagePctNum}%</span>`
                            : (storageWarn
                                ? `<span class='status-warn'>Storage: ${storagePctNum}%</span>`
                                : `Storage: ${storagePctNum}%`))
                        : null
                ].filter(Boolean);
                document.getElementById('diagArea').innerHTML = statusParts.join(" | ");
                await loadDiagnostics(id);

                const sel = document.getElementById('sampleRate');
                sel.innerHTML = "";
                if (data.current_rate === null || data.current_rate === undefined) {
                    let opt = document.createElement('option');
                    opt.value = "";
                    opt.text = "N/A";
                    opt.selected = true;
                    sel.add(opt);
                }
                data.supported_rates.forEach(r => {
                    let opt = document.createElement('option');
                    opt.value = r.enum_val; opt.text = r.str_val;
                    if(r.enum_val == data.current_rate) opt.selected = true;
                    sel.add(opt);
                });

                const chArea = document.getElementById('channelsArea');
                chArea.innerHTML = "";
                chArea.innerHTML = `
                    <div class="channel-picker" id="channelPicker">
                        <button type="button" class="btn btn-outline-secondary channel-summary-btn" id="channelSummaryBtn" onclick="toggleChannelsMenu(event)">
                            0 active
                        </button>
                        <div class="channel-menu" id="channelMenu"></div>
                    </div>
                `;
                const channelNames = {
                    1: "Raw Data (ch1)",
                    2: "CJC Temperature (ch2)"
                };
                const menu = document.getElementById('channelMenu');
                data.channels.forEach(ch => {
                    let checked = ch.enabled ? "checked" : "";
                    const label = channelNames[ch.id] || `Channel ${ch.id}`;
                    menu.innerHTML += `
                        <label class="channel-row">
                            <input class="form-check-input ch-enable" type="checkbox" id="ch_${ch.id}" ${checked} onchange="updateChannelSummary()">
                            <span>${label}</span>
                        </label>
                    `;
                });
                updateChannelSummary();
                document.getElementById('btnClearStorage').disabled = false;
                markIdleState(String(data.state) === "0");
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = "❌ Error: " + data.error;
                document.getElementById('btnClearStorage').disabled = true;
                markIdleState(false);
            }
        } finally {
            if (wasIdle && idleConfirmed) btnRead.disabled = false;
        }
    }

    function parseLastCommAgeSec(lastCommStr) {
        if (!lastCommStr) return NaN;
        // Node time is treated as UTC from MSCL/WSDA, convert age from UTC.
        const iso = String(lastCommStr).replace(" ", "T") + "Z";
        const dt = new Date(iso);
        if (Number.isNaN(dt.getTime())) return NaN;
        return (Date.now() - dt.getTime()) / 1000;
    }

    function formatNodeLastCommLocal(lastCommStr) {
        if (!lastCommStr) return null;
        const dt = new Date(String(lastCommStr).replace(" ", "T") + "Z");
        if (Number.isNaN(dt.getTime())) return lastCommStr;
        const yyyy = dt.getFullYear();
        const mm = String(dt.getMonth() + 1).padStart(2, "0");
        const dd = String(dt.getDate()).padStart(2, "0");
        const hh = String(dt.getHours()).padStart(2, "0");
        const mi = String(dt.getMinutes()).padStart(2, "0");
        const ss = String(dt.getSeconds()).padStart(2, "0");
        return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
    }

    async function probeNode() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnProbe');
        btn.disabled = true;
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "🛰️ Probing node...";
        try {
            const res = await fetch(`/api/probe/${id}`);
            const data = await res.json();
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `✅ ${data.message || 'Probe OK'}`;
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `❌ ${data.error || 'Probe failed'}`;
            }
            markIdleState(false);
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "❌ Probe failed";
            markIdleState(false);
        } finally {
            btn.disabled = false;
        }
    }

    async function setNodeIdle() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnSetIdle');
        btn.disabled = true;
        try {
            statusDiv.className = "mt-2 text-center text-primary";
            statusDiv.innerHTML = "⏳ Sending Set to Idle...";
            const res = await fetch(`/api/node_idle/${id}`, {method:'POST'});
            const data = await res.json();
            const st = data.idle_status || {};
            const sent = st.command_sent === true ? "sent" : "not-sent";
            const link = st.transport_alive === true ? "alive" : "no-link";
            const confirmed = st.state_confirmed === true ? "confirmed" : "pending";
            if (!data.success) {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `❌ ${data.error || 'Set to Idle failed'} | cmd:${sent} | link:${link} | idle:${confirmed}`;
                markIdleState(false);
            } else if (data.idle_confirmed) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `✅ Idle confirmed | cmd:${sent} | link:${link} | idle:${confirmed}`;
                markIdleState(true);
            } else {
                statusDiv.className = "mt-2 text-center status-warn";
                statusDiv.innerHTML = `⚠️ Idle pending (${data.reason || 'pending'}) | cmd:${sent} | link:${link} | idle:${confirmed}`;
                markIdleState(false);
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "❌ Set to Idle failed";
            markIdleState(false);
        } finally {
            setTimeout(() => { btn.disabled = false; }, 1500);
        }
    }

    async function setNodeSampling() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnSampling');
        const raw = prompt("Sampling duration in seconds (0 = no auto-stop):", "60");
        if (raw === null) return;
        const duration = Math.max(0, parseInt(raw || "0", 10) || 0);
        btn.disabled = true;
        try {
            statusDiv.className = "mt-2 text-center text-primary";
            statusDiv.innerHTML = "⏳ Sending Sampling command...";
            const res = await fetch(`/api/node_sampling/${id}`, {
                method:'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({duration_sec: duration})
            });
            const data = await res.json();
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `✅ ${data.message}; duration=${data.duration_sec}s`;
                markIdleState(false);
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `❌ ${data.error || 'Sampling failed'}`;
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "❌ Sampling failed";
        } finally {
            btn.disabled = false;
        }
    }

    async function setNodeSleep() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnSleep');
        const confirmed = window.confirm(
            "Put node into SLEEP mode?\n\nWake requires physical power cycle."
        );
        if (!confirmed) return;
        btn.disabled = true;
        try {
            statusDiv.className = "mt-2 text-center text-primary";
            statusDiv.innerHTML = "⏳ Sending Sleep command...";
            const res = await fetch(`/api/node_sleep/${id}`, {method:'POST'});
            const data = await res.json();
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `✅ ${data.message}`;
                markIdleState(false);
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `❌ ${data.error || 'Sleep failed'}`;
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "❌ Sleep failed";
        } finally {
            btn.disabled = false;
        }
    }

    async function cycleNodePower() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnCyclePower');
        btn.disabled = true;
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "⏳ Sending Power Cycle...";
        try {
            const res = await fetch(`/api/node_cycle_power/${id}`, {method:'POST'});
            const data = await res.json();
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = "✅ Power cycle command sent";
                markIdleState(false);
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `❌ ${data.error}`;
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "❌ Power cycle failed";
        } finally {
            btn.disabled = false;
        }
    }

    async function clearStorage() {
        const id = document.getElementById('nodeId').value;
        const statusDiv = document.getElementById('readStatus');
        const btn = document.getElementById('btnClearStorage');
        btn.disabled = true;
        statusDiv.className = "mt-2 text-center text-primary";
        statusDiv.innerHTML = "⏳ Clearing storage...";
        try {
            const res = await fetch(`/api/clear_storage/${id}`, {method:'POST'});
            const data = await res.json();
            if (data.success) {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = "✅ Storage cleared";
            } else {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `❌ ${data.error || 'Clear storage failed'}`;
            }
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "❌ Clear storage failed";
        } finally {
            btn.disabled = false;
        }
    }

    async function applyConfig() {
        const id = document.getElementById('nodeId').value;
        const rate = document.getElementById('sampleRate').value;
        const power = document.getElementById('txPower').value;
        const inputRangeRaw = document.getElementById('inputRange').value;
        const lowPassFilterRaw = document.getElementById('lowPassFilter').value;
        const storageLimitModeRaw = document.getElementById('storageLimitMode').value;
        const lostBeaconTimeoutRaw = document.getElementById('lostBeaconTimeout').value;
        const diagnosticIntervalRaw = document.getElementById('diagnosticInterval').value;
        const statusDiv = document.getElementById('writeStatus');
        if (!idleConfirmed) {
            statusDiv.className = "mt-3 text-center status-err";
            statusDiv.innerHTML = "❌ Node is not confirmed in Idle. Press Set to Idle first.";
            return;
        }
        if (!rate || Number.isNaN(parseInt(rate))) {
            statusDiv.className = "mt-3 text-center status-err";
            statusDiv.innerHTML = "❌ Sample Rate is unknown (N/A). Run FULL READ once or configure in SensorConnect.";
            return;
        }

        let activeChs = [];
        document.querySelectorAll('.ch-enable').forEach(cb => {
            if(cb.checked) { activeChs.push(parseInt(cb.id.replace('ch_', ''))); }
        });
        
        statusDiv.className = "mt-3 text-center text-primary";
        statusDiv.innerHTML = "⏳ Writing config...";
        try {
            const res = await fetch('/api/write', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    node_id: id,
                    sample_rate: parseInt(rate),
                    tx_power: parseInt(power),
                    channels: activeChs,
                    input_range: inputRangeRaw === "" ? null : parseInt(inputRangeRaw),
                    low_pass_filter: lowPassFilterRaw === "" ? null : parseInt(lowPassFilterRaw),
                    storage_limit_mode: storageLimitModeRaw === "" ? null : parseInt(storageLimitModeRaw),
                    lost_beacon_timeout: lostBeaconTimeoutRaw === "" ? null : parseInt(lostBeaconTimeoutRaw),
                    diagnostic_interval: diagnosticIntervalRaw === "" ? null : parseInt(diagnosticIntervalRaw)
                })
            });
            const data = await res.json();
            statusDiv.innerHTML = data.success ? "<span class='status-ok'>✅ SAVED SUCCESSFULLY</span>" : `<span class='status-err'>❌ ${data.error}</span>`;
        } catch (e) { }
    }

    function updateChannelSummary() {
        const all = document.querySelectorAll('.ch-enable');
        let active = 0;
        all.forEach(cb => { if (cb.checked) active += 1; });
        const btn = document.getElementById('channelSummaryBtn');
        if (btn) btn.innerText = `${active} active`;
    }

    function toggleChannelsMenu(event) {
        event.preventDefault();
        event.stopPropagation();
        const menu = document.getElementById('channelMenu');
        if (!menu) return;
        menu.classList.toggle('open');
    }

    document.addEventListener('click', function(evt) {
        const picker = document.getElementById('channelPicker');
        const menu = document.getElementById('channelMenu');
        if (!picker || !menu) return;
        if (!picker.contains(evt.target)) {
            menu.classList.remove('open');
        }
    });

    document.getElementById('nodeId').addEventListener('input', function() {
        const btn = document.getElementById('btnClearStorage');
        if (btn) btn.disabled = true;
    });

    async function refreshBaseStatus() {
        try {
            const res = await fetch('/api/status');
            const data = await res.json();
            window.currentBaseConnected = Boolean(data.connected);
            const text = document.getElementById('baseStatusText');
            const pill = document.getElementById('baseStatusPill');
            if (data.connected) {
                pill.className = "status-pill status-ok-pill mb-3";
                pill.innerText = "Connected";
            } else {
                pill.className = "status-pill status-err-pill mb-3";
                pill.innerText = "Disconnected";
            }
            if (data.link_health === "healthy") {
                pill.className = "status-pill status-ok-pill mb-3";
                pill.innerText = "Link: Healthy";
            } else if (data.link_health === "degraded") {
                pill.className = "status-pill status-warn-pill mb-3";
                pill.innerText = "Link: Degraded";
            } else if (data.link_health === "offline") {
                pill.className = "status-pill status-err-pill mb-3";
                pill.innerText = "Link: Offline";
            }
            window.currentBeaconState = data.beacon_state;
            const beaconBtn = document.getElementById('btnBeacon');
            if (data.beacon_state === true) {
                beaconBtn.innerText = "Beacon: ON";
            } else if (data.beacon_state === false) {
                beaconBtn.innerText = "Beacon: OFF";
            } else {
                beaconBtn.innerText = "Beacon: ?";
            }
            const line1 = [
                `Status: ${data.message}`,
                `Port: ${data.port}`,
                `Last: ${data.ts || 'N/A'}`,
                data.base_model ? `Model: ${data.base_model}` : null,
                data.base_fw ? `FW: ${data.base_fw}` : null,
                data.base_serial ? `S/N: ${data.base_serial}` : null,
                data.base_connection ? `Connection: ${data.base_connection}` : null,
                data.base_region ? `Region: ${data.base_region}` : null,
                data.base_radio ? `Radio: ${data.base_radio}` : null
            ].filter(Boolean).join(" | ");
            const line2 = [
                data.base_last_comm ? `Last Comm: ${data.base_last_comm}` : null,
                data.base_link ? `State: ${data.base_link}` : null,
                data.link_health ? `Link Health: ${data.link_health}` : null,
                data.link_health_reason ? `Reason: ${data.link_health_reason}` : null,
                (data.ping_age_sec !== null && data.ping_age_sec !== undefined) ? `Ping Age: ${data.ping_age_sec}s` : null,
                (data.comm_age_sec !== null && data.comm_age_sec !== undefined) ? `Comm Age: ${data.comm_age_sec}s` : null
            ].filter(Boolean).join(" | ");
            text.innerText = line2 ? `${line1}\n${line2}` : line1;
            updateBaseButtons(window.currentBaseConnected);
        } catch (e) { }
    }

    async function toggleBeacon() {
        try {
            const current = window.currentBeaconState;
            const target = (current === true) ? false : true;
            const res = await fetch('/api/beacon', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({enabled: target})
            });
            const data = await res.json();
            const statusDiv = document.getElementById('readStatus');
            if (!data.success) {
                statusDiv.className = "mt-2 text-center status-err";
                statusDiv.innerHTML = `❌ ${data.error}`;
            } else {
                statusDiv.className = "mt-2 text-center status-ok";
                statusDiv.innerHTML = `✅ ${data.message}`;
            }
            await refreshBaseStatus();
        } catch (e) { }
    }

    async function loadDiagnostics(id) {
        try {
            const res = await fetch(`/api/diagnostics/${id}`);
            const data = await res.json();
            const target = document.getElementById('diagFlags');
            if (!data.success) {
                target.innerText = "Diagnostics: " + data.error;
                return;
            }
            const parts = data.flags.map(f => `${f.name}: <span class='fw-bold'>${f.value ? 'YES' : 'NO'}</span>`);
            parts.push("<span class='fst-italic'>Feature flags may be conservative for this firmware/MSCL build.</span>");
            target.innerHTML = parts.join(" | ");
        } catch (e) { }
    }

    async function refreshLogs() {
        try {
            const res = await fetch('/api/logs');
            const data = await res.json();
            const box = document.getElementById('logBox');
            const joined = (data.logs || []).join('\n');
            box.textContent = joined.replace(/\\n/g, '\n');
        } catch (e) { }
    }

    async function copyLogs() {
        const box = document.getElementById('logBox');
        const statusDiv = document.getElementById('readStatus');
        const text = box ? box.textContent || "" : "";
        try {
            await navigator.clipboard.writeText(text);
            statusDiv.className = "mt-2 text-center status-ok";
            statusDiv.innerHTML = "✅ Logs copied";
        } catch (e) {
            statusDiv.className = "mt-2 text-center status-err";
            statusDiv.innerHTML = "❌ Copy failed";
        }
    }

    // Manual refresh only (no auto status refresh)
    window.currentBaseConnected = false;
    updateBaseButtons(false);
    updateReadWriteButtons();
    refreshLogs();
