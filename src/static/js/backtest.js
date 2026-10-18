// =========================================================================
// HISTORICAL BACKTEST & EQUITY VISUALIZER
// =========================================================================
        async function loadBacktest() {
            try {
                const res = await fetch('/api/backtest');
                const data = await res.json();

                const d = data.deterministic;
                document.getElementById('det-trades').innerText = d.total_trades;
                document.getElementById('det-winrate').innerText = `${d.win_rate_pct}%`;
                document.getElementById('det-pf').innerText = d.profit_factor;
                document.getElementById('det-pnl').innerText = `$${d.total_net_pnl.toFixed(2)}`;
                document.getElementById('det-roc').innerText = `$${(d.expectancy_per_trade || 0).toFixed(2)}/trd (${d.return_on_capital_pct}%)`;
                document.getElementById('det-dd').innerText = `$${d.max_drawdown_usdt.toFixed(2)} (${d.max_drawdown_pct}%)`;

                const m = data.ml_filtered;
                document.getElementById('ml-trades').innerText = m.total_trades;
                document.getElementById('ml-winrate').innerText = `${m.win_rate_pct}%`;
                document.getElementById('ml-pf').innerText = m.profit_factor;
                document.getElementById('ml-pnl').innerText = `$${m.total_net_pnl.toFixed(2)}`;
                document.getElementById('ml-roc').innerText = `$${(m.expectancy_per_trade || 0).toFixed(2)}/trd (${m.return_on_capital_pct}%)`;
                document.getElementById('ml-dd').innerText = `$${m.max_drawdown_usdt.toFixed(2)} (${m.max_drawdown_pct}%)`;

                if (data.chronological_splits) {
                    const cs = data.chronological_splits;
                    const badge = document.getElementById('bt-oos-status-badge');
                    if (badge) badge.innerText = cs.historical_validation_status || 'UNVALIDATED';
                    const trEl = document.getElementById('bt-split-train');
                    if (trEl && cs.in_sample_train) {
                        trEl.innerText = `${cs.in_sample_train.trades} trades | ${cs.in_sample_train.win_rate_pct}% WR | $${cs.in_sample_train.net_pnl.toFixed(2)} ($${cs.in_sample_train.expectancy_per_trade.toFixed(2)}/trd)`;
                    }
                    const vlEl = document.getElementById('bt-split-val');
                    if (vlEl && cs.validation) {
                        vlEl.innerText = `${cs.validation.trades} trades | ${cs.validation.win_rate_pct}% WR | $${cs.validation.net_pnl.toFixed(2)} ($${cs.validation.expectancy_per_trade.toFixed(2)}/trd)`;
                    }
                    const oosEl = document.getElementById('bt-split-oos');
                    if (oosEl && cs.out_of_sample_test) {
                        oosEl.innerText = `${cs.out_of_sample_test.trades} trades | ${cs.out_of_sample_test.win_rate_pct}% WR | $${cs.out_of_sample_test.net_pnl.toFixed(2)} ($${cs.out_of_sample_test.expectancy_per_trade.toFixed(2)}/trd)`;
                    }
                }

                renderEquityChart(d.equity_curve, m.equity_curve);
            } catch (err) {
                console.error("Backtest error:", err);
            }
        }

        function renderEquityChart(detCurve, mlCurve) {
            const ctx = document.getElementById('equityChart').getContext('2d');
            if (equityChartInstance) {
                equityChartInstance.destroy();
            }

            const labels = detCurve.map((pt, i) => `Trade ${i}`);
            const detData = detCurve.map(pt => pt.equity);
            const mlData = mlCurve.map(pt => pt.equity);

            equityChartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'Deterministic Baseline',
                            data: detData,
                            borderColor: '#3b82f6',
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            borderWidth: 2,
                            fill: false,
                            tension: 0.1,
                        },
                        {
                            label: 'ML-Filtered Strategy',
                            data: mlData,
                            borderColor: '#a855f7',
                            backgroundColor: 'rgba(168, 85, 247, 0.1)',
                            borderWidth: 2,
                            fill: false,
                            tension: 0.1,
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { labels: { color: '#94a3b8' } } },
                    scales: {
                        x: { display: false },
                        y: {
                            grid: { color: '#1e293b' },
                            ticks: { color: '#94a3b8', callback: v => `$${v.toFixed(2)}` }
                        }
                    }
                }
            });
        }
