        // UNIVERSAL ASTERDEX PERPETUAL PRICE ALERTS JAVASCRIPT
        // =========================================================================

        // Initialize audio checkbox state
        const soundCb = document.getElementById('soundToggleCheckbox');
        if (soundCb) soundCb.checked = soundAlertsEnabled;

        function toggleSoundAlerts(enabled) {
            soundAlertsEnabled = Boolean(enabled);
            localStorage.setItem('soundAlertsEnabled', String(soundAlertsEnabled));
            if (soundAlertsEnabled) {
                playAlertChime();
            }
        }

        function playAlertChime() {
            try {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                if (!AudioContext) return;
                const ctx = new AudioContext();
                const now = ctx.currentTime;

                const osc1 = ctx.createOscillator();
                const osc2 = ctx.createOscillator();
                const gain = ctx.createGain();

                osc1.type = 'sine';
                osc1.frequency.setValueAtTime(587.33, now); // D5
                osc1.frequency.exponentialRampToValueAtTime(880.00, now + 0.12); // A5

                osc2.type = 'triangle';
                osc2.frequency.setValueAtTime(880.00, now + 0.12); // A5
                osc2.frequency.exponentialRampToValueAtTime(1174.66, now + 0.32); // D6

                gain.gain.setValueAtTime(0.18, now);
                gain.gain.exponentialRampToValueAtTime(0.001, now + 0.42);

                osc1.connect(gain);
                osc2.connect(gain);
                gain.connect(ctx.destination);

                osc1.start(now);
                osc2.start(now + 0.12);
                osc1.stop(now + 0.42);
                osc2.stop(now + 0.42);
            } catch (e) {
                console.warn("Web Audio chime failed:", e);
            }
        }

        function testAlertAudio() {
            playAlertChime();
            showToastNotification({
                symbol: "TESTUSDT",
                direction: "PRICE_ABOVE",
                target_price: 100.0,
                trigger_price: 100.25,
                triggered_at: new Date().toISOString(),
                note: "Audio chime test notification"
            });
        }

        async function testAlertDiscord() {
            try {
                showNotice('Sending test alert to Discord...', 'info');
                const res = await fetch('/api/alerts/discord/test', { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    showNotice(`Discord test notification sent successfully (${data.webhook_masked || 'active'})!`, 'success');
                } else {
                    showNotice(`Discord test failed: ${data.detail || data.message || 'Delivery error'}`, 'error');
                }
            } catch (err) {
                showNotice(`Failed to test Discord webhook: ${err.message}`, 'error');
            }
        }

        function showToastNotification(ev) {
            const container = document.getElementById('alertToastContainer');
            if (!container) return;

            const toast = document.createElement('div');
            toast.className = 'pointer-events-auto card-bg p-4 rounded-xl border border-indigo-500/60 shadow-2xl flex items-start gap-3 transform transition-all duration-300 translate-y-2 opacity-0';
            const isAbove = ev.direction === 'PRICE_ABOVE';
            const dirColor = isAbove ? 'text-emerald-400 bg-emerald-950 border-emerald-800' : 'text-red-400 bg-red-950 border-red-800';
            const dirIcon = isAbove ? '↑' : '↓';

            toast.innerHTML = `
                <div class="w-8 h-8 rounded-lg flex items-center justify-center font-bold text-sm border ${dirColor} shrink-0">
                    ${dirIcon}
                </div>
                <div class="flex-1 min-w-0">
                    <div class="flex items-center justify-between gap-2">
                        <span class="text-sm font-bold text-white tracking-wide">${escapeHtml(ev.symbol)}</span>
                        <span class="text-[10px] font-mono text-slate-400">${new Date(ev.triggered_at).toLocaleTimeString()}</span>
                    </div>
                    <div class="text-xs text-slate-300 mt-0.5">
                        Price ${isAbove ? 'rose above' : 'fell below'} <strong class="text-white font-mono">$${formatPrice(ev.target_price)}</strong>
                    </div>
                    <div class="text-[11px] font-mono text-indigo-300 mt-0.5">
                        Triggered at: <strong>$${formatPrice(ev.trigger_price)}</strong>
                    </div>
                    ${ev.note ? `<div class="text-[11px] text-slate-400 mt-1 italic">"${escapeHtml(ev.note)}"</div>` : ''}
                </div>
                <button onclick="this.parentElement.remove()" class="text-slate-400 hover:text-white text-base leading-none">&times;</button>
            `;

            container.appendChild(toast);
            requestAnimationFrame(() => {
                toast.classList.remove('translate-y-2', 'opacity-0');
            });

            // Auto dismiss after 9 seconds
            setTimeout(() => {
                toast.classList.add('opacity-0', 'translate-y-2');
                setTimeout(() => toast.remove(), 350);
            }, 9000);
        }

        async function fetchAlertSymbols() {
            try {
                const res = await fetch('/api/alerts/symbols');
                if (res.ok) {
                    cachedAlertSymbols = await res.json();
                    const universeCount = document.getElementById('universeSymbolsCount');
                    if (universeCount) universeCount.innerText = `${cachedAlertSymbols.length} Contracts`;
                }
            } catch (err) {
                console.warn("Error fetching alert symbols:", err);
            }
        }

        function handleSymbolFocus() {
            const input = document.getElementById('alertSymbolInput');
            handleSymbolSearch(input.value);
        }

        function handleSymbolSearch(query) {
            const dropdown = document.getElementById('symbolDropdownList');
            if (!dropdown) return;

            const q = (query || '').trim().toUpperCase();
            let matches = cachedAlertSymbols;
            if (q) {
                matches = cachedAlertSymbols.filter(s => s.symbol.includes(q) || s.base_asset.includes(q));
            }
            matches = matches.slice(0, 40); // Top 40 results

            if (matches.length === 0) {
                dropdown.innerHTML = `<div class="p-3 text-slate-500 text-center">No matching perpetual contracts found</div>`;
                dropdown.classList.remove('hidden');
                return;
            }

            dropdown.innerHTML = matches.map(s => `
                <div onclick="selectAlertSymbol('${s.symbol}')" class="p-2 hover:bg-slate-800 cursor-pointer flex items-center justify-between transition">
                    <div>
                        <span class="font-bold text-white">${s.symbol}</span>
                        <span class="text-slate-500 text-[10px] ml-1">(${s.base_asset}/USDT)</span>
                    </div>
                    <div class="text-right font-mono">
                        <div class="text-emerald-400 font-semibold">$${formatPrice(s.mark_price || s.last_price)}</div>
                        <div class="text-[10px] text-slate-500">Tick: ${s.tick_size}</div>
                    </div>
                </div>
            `).join('');
            dropdown.classList.remove('hidden');
        }

        // Close symbol dropdown on click outside
        document.addEventListener('click', (e) => {
            const dropdown = document.getElementById('symbolDropdownList');
            const input = document.getElementById('alertSymbolInput');
            if (dropdown && !dropdown.contains(e.target) && e.target !== input) {
                dropdown.classList.add('hidden');
            }
        });

        function selectAlertSymbol(symbol) {
            const sym = cachedAlertSymbols.find(s => s.symbol === symbol);
            if (!sym) return;

            selectedAlertSymbolItem = sym;
            const input = document.getElementById('alertSymbolInput');
            input.value = sym.symbol;

            const details = document.getElementById('selectedSymbolDetails');
            const markDisplay = document.getElementById('symMarkDisplay');
            const lastDisplay = document.getElementById('symLastDisplay');
            const fillBtn = document.getElementById('fillPriceBtn');

            markDisplay.innerText = `$${formatPrice(sym.mark_price)}`;
            lastDisplay.innerText = `$${formatPrice(sym.last_price)}`;
            details.classList.remove('hidden');
            if (fillBtn) fillBtn.classList.remove('hidden');

            const dropdown = document.getElementById('symbolDropdownList');
            if (dropdown) dropdown.classList.add('hidden');

            // Default target price to current price if empty
            const targetInput = document.getElementById('alertTargetPrice');
            if (!targetInput.value) {
                targetInput.value = (sym.mark_price || sym.last_price || '');
            }
        }

        function fillSelectedPrice() {
            if (!selectedAlertSymbolItem) return;
            const src = document.getElementById('alertPriceSource').value;
            const price = src === 'MARK_PRICE' ? selectedAlertSymbolItem.mark_price : selectedAlertSymbolItem.last_price;
            if (price) {
                document.getElementById('alertTargetPrice').value = price;
            }
        }

        async function handleAlertFormSubmit(event) {
            event.preventDefault();
            const editId = document.getElementById('editingAlertId').value;
            const symbol = document.getElementById('alertSymbolInput').value.trim().toUpperCase();
            const targetPrice = parseFloat(document.getElementById('alertTargetPrice').value);
            const direction = document.getElementById('alertDirection').value;
            const priceSource = document.getElementById('alertPriceSource').value;
            const alertType = document.getElementById('alertRecurrence').value;
            const note = document.getElementById('alertNote').value.trim();

            if (!symbol || isNaN(targetPrice) || targetPrice <= 0) {
                showNotice('Please enter a valid symbol and a positive target price.', 'warn');
                return;
            }

            const submitBtn = document.getElementById('alertSubmitBtn');
            submitBtn.disabled = true;

            try {
                if (editId) {
                    // Update existing alert
                    const res = await fetch(`/api/alerts/${editId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            target_price: targetPrice,
                            direction: direction,
                            price_source: priceSource,
                            alert_type: alertType,
                            note: note
                        })
                    });
                    if (!res.ok) {
                        const errData = await res.json().catch(() => ({}));
                        throw new Error(errData.detail || 'Failed to update alert');
                    }
                    showNotice(`Alert for ${symbol} updated successfully.`);
                } else {
                    // Create new alert
                    const res = await fetch('/api/alerts', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            symbol: symbol,
                            target_price: targetPrice,
                            direction: direction,
                            price_source: priceSource,
                            alert_type: alertType,
                            note: note
                        })
                    });
                    if (!res.ok) {
                        const errData = await res.json().catch(() => ({}));
                        throw new Error(errData.detail || 'Failed to create alert');
                    }
                    showNotice(`Price alert for ${symbol} created.`);
                }
                resetAlertForm();
                await loadAlerts();
            } catch (err) {
                showNotice(err.message, 'error');
            } finally {
                submitBtn.disabled = false;
            }
        }

        function resetAlertForm() {
            document.getElementById('editingAlertId').value = '';
            const symInput = document.getElementById('alertSymbolInput');
            symInput.value = '';
            symInput.disabled = false;
            document.getElementById('alertTargetPrice').value = '';
            document.getElementById('alertNote').value = '';
            document.getElementById('alertFormHeading').innerText = 'Create Price Alert';
            document.getElementById('alertSubmitBtnText').innerText = 'Create Alert';
            document.getElementById('alertCancelBtn').classList.add('hidden');
            document.getElementById('selectedSymbolDetails').classList.add('hidden');
            document.getElementById('fillPriceBtn').classList.add('hidden');
            selectedAlertSymbolItem = null;
        }

        function setAlertsFilter(filter) {
            alertsFilter = filter;
            ['ALL', 'ACTIVE', 'TRIGGERED', 'DISABLED'].forEach(f => {
                const btn = document.getElementById(`alertFilter-${f}`);
                if (btn) {
                    if (f === filter) {
                        btn.className = 'px-2.5 py-1 rounded-md font-semibold bg-indigo-600 text-white';
                    } else {
                        btn.className = 'px-2.5 py-1 rounded-md text-slate-400 hover:text-white';
                    }
                }
            });
            loadAlerts();
        }

        async function loadAlerts(manual = false) {
            try {
                if (cachedAlertSymbols.length === 0) {
                    await fetchAlertSymbols();
                }

                // 1. Fetch alerts and status
                const [alertsRes, statusRes] = await Promise.all([
                    fetch('/api/alerts'),
                    fetch('/api/alerts/status')
                ]);

                if (!alertsRes.ok || !statusRes.ok) return;

                const alerts = await alertsRes.json();
                const status = await statusRes.json();

                // Update Telemetry Header Cards
                document.getElementById('alertsTotalCount').innerText = alerts.length;
                document.getElementById('alertsActivePill').innerText = `${status.active_alerts} Active`;
                document.getElementById('alertsTriggeredCount').innerText = status.triggered_alerts;
                document.getElementById('alertsStaleCount').innerText = status.stale_alerts;

                const activeBadge = document.getElementById('alertsActiveBadge');
                if (activeBadge) {
                    activeBadge.innerText = status.active_alerts;
                    if (status.active_alerts > 0) activeBadge.classList.remove('hidden');
                    else activeBadge.classList.add('hidden');
                }

                if (status.market_data) {
                    const md = status.market_data;
                    const ageEl = document.getElementById('feedAgeDisplay');
                    if (ageEl) ageEl.innerText = md.last_poll_age_sec !== null ? `Age: ${md.last_poll_age_sec}s` : 'Age: --s';

                    const pollsEl = document.getElementById('feedPollsDisplay');
                    if (pollsEl) pollsEl.innerText = `Polls: ${md.poll_count}`;

                    const healthEl = document.getElementById('feedHealthBadge');
                    if (healthEl) {
                        if (md.is_healthy) {
                            healthEl.className = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-950 text-emerald-400 border border-emerald-800';
                            healthEl.innerText = 'HEALTHY';
                        } else {
                            healthEl.className = 'px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-400 border border-amber-800';
                            healthEl.innerText = 'STALE FEED';
                        }
                    }
                }

                // Update Discord Telemetry
                if (status.discord) {
                    const dBadge = document.getElementById('discordAlertBadge');
                    const dWebhook = document.getElementById('discordWebhookDisplay');
                    const dBtn = document.getElementById('testDiscordBtn');

                    if (dBadge) {
                        if (status.discord.enabled) {
                            dBadge.className = 'px-1.5 py-0.5 rounded text-[9px] font-bold bg-indigo-950 text-indigo-400 border border-indigo-800';
                            dBadge.innerText = 'DISCORD: ON';
                        } else {
                            dBadge.className = 'px-1.5 py-0.5 rounded text-[9px] font-bold bg-slate-800 text-slate-400 border border-slate-700';
                            dBadge.innerText = 'DISCORD: OFF';
                        }
                    }

                    if (dWebhook) {
                        if (status.discord.webhook_masked) {
                            dWebhook.innerText = `Webhook: ${status.discord.webhook_masked}`;
                            dWebhook.title = `Masked URL: ${status.discord.webhook_masked} (Delivered: ${status.discord.delivered_count || 0}, Failed: ${status.discord.failed_count || 0})`;
                        } else {
                            dWebhook.innerText = 'Webhook: [NOT CONFIGURED]';
                            dWebhook.title = 'Configure DISCORD_PRICE_ALERT_WEBHOOK_URL to enable notifications.';
                        }
                    }

                    if (dBtn) {
                        if (status.discord.enabled) {
                            dBtn.classList.remove('hidden');
                        } else {
                            dBtn.classList.add('hidden');
                        }
                    }
                }

                // Filter alerts
                let filtered = alerts;
                if (alertsFilter === 'ACTIVE') {
                    filtered = alerts.filter(a => a.is_active);
                } else if (alertsFilter === 'TRIGGERED') {
                    filtered = alerts.filter(a => a.status === 'TRIGGERED');
                } else if (alertsFilter === 'DISABLED') {
                    filtered = alerts.filter(a => !a.is_active || a.status === 'DISABLED');
                }

                // Render Table
                const tbody = document.getElementById('alertsTableBody');
                const emptyState = document.getElementById('alertsEmptyState');

                if (filtered.length === 0) {
                    tbody.innerHTML = '';
                    emptyState.classList.remove('hidden');
                } else {
                    emptyState.classList.add('hidden');
                    tbody.innerHTML = filtered.map(a => renderAlertRow(a)).join('');
                }

                // Also refresh history
                await loadAlertHistory();

            } catch (err) {
                console.error("Error loading alerts:", err);
            }
        }

        function renderAlertRow(a) {
            const isAbove = a.direction === 'PRICE_ABOVE';
            const dirIcon = isAbove ? '↑' : '↓';
            const dirClass = isAbove ? 'text-emerald-400' : 'text-red-400';
            const dirLabel = isAbove ? 'Crosses Above' : 'Crosses Below';

            // Distance calculation
            let distHtml = '<span class="text-slate-500">--</span>';
            if (a.current_price !== null && a.current_price !== undefined) {
                const isClose = Math.abs(a.distance_pct || 0) < 1.0;
                const distClass = (a.distance_pct >= 0) ? 'text-emerald-400' : 'text-amber-400';
                distHtml = `
                    <div class="font-mono">
                        <span class="text-white font-semibold">$${formatPrice(a.current_price)}</span>
                        <span class="ml-1 text-[10px] ${distClass} font-bold">(${a.distance_pct > 0 ? '+' : ''}${a.distance_pct}%)</span>
                    </div>
                `;
            }

            // Status Badge
            let statusBadge = '';
            if (a.status === 'ACTIVE') {
                statusBadge = a.armed 
                    ? '<span class="px-2 py-0.5 rounded text-[10px] font-bold badge-green">ARMED</span>'
                    : '<span class="px-2 py-0.5 rounded text-[10px] font-bold badge-yellow">UNARMED (Awaiting Crossback)</span>';
            } else if (a.status === 'TRIGGERED') {
                statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold badge-blue">TRIGGERED</span>';
            } else if (a.status === 'DISABLED') {
                statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold badge-gray">DISABLED</span>';
            } else if (a.status === 'STALE') {
                statusBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-950 text-amber-400 border border-amber-800">STALE DATA</span>';
            } else {
                statusBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold badge-red">${a.status}</span>`;
            }

            const modeLabel = a.alert_type === 'RECURRING'
                ? '<span class="text-indigo-400 font-mono">RECURRING</span>'
                : '<span class="text-slate-400 font-mono">ONE-OFF</span>';

            const toggleBtnText = a.is_active ? 'Pause' : 'Resume';
            const toggleBtnColor = a.is_active ? 'text-amber-400 hover:text-amber-300' : 'text-emerald-400 hover:text-emerald-300';

            return `
                <tr class="hover:bg-slate-800/50 transition">
                    <td class="px-4 py-3">
                        <div class="font-bold text-white font-mono">${escapeHtml(a.symbol)}</div>
                        <div class="text-[10px] text-slate-500 font-mono">${a.price_source === 'MARK_PRICE' ? 'Mark' : 'Last'} Price</div>
                    </td>
                    <td class="px-4 py-3 font-mono">
                        <div class="text-white font-bold">$${formatPrice(a.target_price)}</div>
                        <div class="text-[10px] ${dirClass} font-semibold">${dirIcon} ${dirLabel}</div>
                    </td>
                    <td class="px-4 py-3">
                        ${distHtml}
                    </td>
                    <td class="px-4 py-3 text-[11px]">
                        <div>${modeLabel}</div>
                        <div class="text-[10px] text-slate-500">${a.trigger_count} triggers</div>
                    </td>
                    <td class="px-4 py-3">
                        ${statusBadge}
                    </td>
                    <td class="px-4 py-3 text-slate-300 text-xs max-w-xs truncate" title="${escapeHtml(a.note)}">
                        ${a.note ? escapeHtml(a.note) : '<span class="text-slate-600">--</span>'}
                    </td>
                    <td class="px-4 py-3 text-[10px] font-mono text-slate-400">
                        <div>Created: ${new Date(a.created_at).toLocaleDateString()}</div>
                        ${a.last_triggered_at ? `<div class="text-amber-400">Trig: ${new Date(a.last_triggered_at).toLocaleTimeString()}</div>` : ''}
                    </td>
                    <td class="px-4 py-3 text-right space-x-2">
                        <button onclick="toggleAlertState('${a.alert_id}')" class="text-xs font-semibold ${toggleBtnColor} transition">${toggleBtnText}</button>
                        <button onclick="editAlert('${a.alert_id}')" class="text-xs font-semibold text-indigo-400 hover:text-indigo-300 transition">Edit</button>
                        <button onclick="deleteAlert('${a.alert_id}')" class="text-xs font-semibold text-red-400 hover:text-red-300 transition">Delete</button>
                    </td>
                </tr>
            `;
        }

        async function editAlert(alertId) {
            try {
                const res = await fetch(`/api/alerts/${alertId}`);
                if (!res.ok) return;
                const a = await res.json();

                document.getElementById('editingAlertId').value = a.alert_id;
                const symInput = document.getElementById('alertSymbolInput');
                symInput.value = a.symbol;
                symInput.disabled = true; // Keep symbol locked during edit

                document.getElementById('alertTargetPrice').value = a.target_price;
                document.getElementById('alertDirection').value = a.direction;
                document.getElementById('alertPriceSource').value = a.price_source;
                document.getElementById('alertRecurrence').value = a.alert_type;
                document.getElementById('alertNote').value = a.note || '';

                document.getElementById('alertFormHeading').innerText = `Edit Alert: ${a.symbol}`;
                document.getElementById('alertSubmitBtnText').innerText = 'Save Changes';
                document.getElementById('alertCancelBtn').classList.remove('hidden');

                // Scroll to form smoothly
                document.getElementById('priceAlertForm').scrollIntoView({ behavior: 'smooth' });
            } catch (err) {
                showNotice('Failed to load alert details for editing.', 'error');
            }
        }

        async function toggleAlertState(alertId) {
            try {
                const res = await fetch(`/api/alerts/${alertId}/toggle`, { method: 'PATCH' });
                if (res.ok) {
                    await loadAlerts();
                } else {
                    const data = await res.json().catch(() => ({}));
                    showNotice(data.detail || 'Failed to toggle alert.', 'error');
                }
            } catch (err) {
                showNotice(err.message, 'error');
            }
        }

        async function deleteAlert(alertId) {
            if (!confirm('Are you sure you want to delete this price alert?')) return;
            try {
                const res = await fetch(`/api/alerts/${alertId}`, { method: 'DELETE' });
                if (res.ok) {
                    showNotice('Price alert deleted.');
                    await loadAlerts();
                } else {
                    const data = await res.json().catch(() => ({}));
                    showNotice(data.detail || 'Failed to delete alert.', 'error');
                }
            } catch (err) {
                showNotice(err.message, 'error');
            }
        }

        async function loadAlertHistory() {
            try {
                const res = await fetch('/api/alerts/history?limit=50');
                if (!res.ok) return;
                const events = await res.json();

                const unackCount = events.filter(e => !e.acknowledged).length;
                const unackBadge = document.getElementById('unackBadge');
                if (unackBadge) {
                    unackBadge.innerText = `${unackCount} Unack`;
                    if (unackCount > 0) unackBadge.classList.remove('hidden');
                    else unackBadge.classList.add('hidden');
                }

                // Check for new events to trigger toast and chime
                if (alertsInitialized) {
                    for (const ev of events) {
                        if (!seenAlertEventIds.has(ev.event_id) && !ev.acknowledged) {
                            showToastNotification(ev);
                            playAlertChime();
                            seenAlertEventIds.add(ev.event_id);
                        }
                    }
                } else {
                    events.forEach(e => seenAlertEventIds.add(e.event_id));
                    alertsInitialized = true;
                }

                const tbody = document.getElementById('alertHistoryTableBody');
                const emptyEl = document.getElementById('alertHistoryEmptyState');

                if (events.length === 0) {
                    tbody.innerHTML = '';
                    emptyEl.classList.remove('hidden');
                } else {
                    emptyEl.classList.add('hidden');
                    tbody.innerHTML = events.map(e => `
                        <tr class="hover:bg-slate-800/40 font-mono text-[11px] transition">
                            <td class="px-4 py-2 text-slate-400">${new Date(e.triggered_at).toLocaleString()}</td>
                            <td class="px-4 py-2 font-bold text-white">${escapeHtml(e.symbol)}</td>
                            <td class="px-4 py-2 ${e.direction === 'PRICE_ABOVE' ? 'text-emerald-400' : 'text-red-400'} font-semibold">
                                ${e.direction === 'PRICE_ABOVE' ? '↑ ABOVE' : '↓ BELOW'}
                            </td>
                            <td class="px-4 py-2 text-slate-300">$${formatPrice(e.target_price)}</td>
                            <td class="px-4 py-2 font-bold text-indigo-300">$${formatPrice(e.trigger_price)}</td>
                            <td class="px-4 py-2 text-slate-400 text-[10px]">${e.price_source === 'MARK_PRICE' ? 'Mark' : 'Last'}</td>
                            <td class="px-4 py-2 text-slate-400 italic font-sans max-w-xs truncate">${e.note ? escapeHtml(e.note) : '--'}</td>
                            <td class="px-4 py-2">
                                ${e.acknowledged 
                                    ? '<span class="text-slate-500 font-sans text-[10px]">Seen</span>' 
                                    : '<span class="px-1.5 py-0.2 rounded text-[9px] font-bold bg-amber-900/60 text-amber-300 border border-amber-700/60 font-sans">NEW</span>'}
                            </td>
                        </tr>
                    `).join('');
                }
            } catch (err) {
                console.error("Error loading alert history:", err);
            }
        }

        async function ackAllAlertEvents() {
            try {
                await fetch('/api/alerts/history/ack', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                await loadAlertHistory();
            } catch (err) {
                console.error("Error acknowledging events:", err);
            }
        }

        async function clearAlertHistory() {
            if (!confirm('Clear all historical alert trigger logs?')) return;
            try {
                await fetch('/api/alerts/history', { method: 'DELETE' });
                seenAlertEventIds.clear();
                await loadAlertHistory();
            } catch (err) {
                console.error("Error clearing history:", err);
            }
        }

        // Periodic polling for price alerts and trigger events (every 5 seconds)
        setInterval(() => {
            loadAlertHistory();
            const alertsTab = document.getElementById('tab-alerts');
            if (alertsTab && !alertsTab.classList.contains('hidden')) {
                loadAlerts();
            }
        }, 5000);

        // Fetch initial alerts telemetry on page load
        fetchAlertSymbols();

        // =========================================================================