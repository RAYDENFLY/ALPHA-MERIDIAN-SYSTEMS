import { spawn } from 'child_process';
import http from 'http';

async function wait(ms) {
    return new Promise(r => setTimeout(r, ms));
}

async function getJson(url) {
    return new Promise((resolve, reject) => {
        http.get(url, res => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    resolve(JSON.parse(data));
                } catch (e) {
                    reject(e);
                }
            });
        }).on('error', reject);
    });
}

async function main() {
    const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
    const chrome = spawn(chromePath, [
        '--headless=new',
        '--disable-gpu',
        '--remote-debugging-port=9222',
        '--user-data-dir=' + process.env.TEMP + '\\chrome_diag_' + Date.now(),
        'http://localhost:8000/?tab=journal'
    ]);

    chrome.stderr.on('data', d => {});

    await wait(2000);

    let list = [];
    for (let i = 0; i < 10; i++) {
        try {
            list = await getJson('http://localhost:9222/json');
            if (list.length > 0) break;
        } catch (e) {}
        await wait(500);
    }

    if (!list || list.length === 0) {
        console.error('Could not connect to Chrome debugging endpoint');
        chrome.kill();
        return;
    }

    const page = list.find(p => p.type === 'page') || list[0];
    const ws = new WebSocket(page.webSocketDebuggerUrl);

    let id = 1;
    const callbacks = new Map();

    function send(method, params = {}) {
        return new Promise(resolve => {
            const msgId = id++;
            callbacks.set(msgId, resolve);
            ws.send(JSON.stringify({ id: msgId, method, params }));
        });
    }

    ws.onmessage = event => {
        const msg = JSON.parse(event.data);
        if (msg.id && callbacks.has(msg.id)) {
            const cb = callbacks.get(msg.id);
            callbacks.delete(msg.id);
            cb(msg.result);
        } else if (msg.method === 'Console.messageAdded') {
            console.log('[CONSOLE]', msg.params.message.level, msg.params.message.text);
        } else if (msg.method === 'Runtime.consoleAPICalled') {
            const args = (msg.params.args || []).map(a => a.value || a.description || JSON.stringify(a)).join(' ');
            console.log('[CONSOLE_API]', msg.params.type, args);
        } else if (msg.method === 'Runtime.exceptionThrown') {
            console.log('[EXCEPTION]', msg.params.exceptionDetails.text, msg.params.exceptionDetails.exception?.description);
        } else if (msg.method === 'Network.responseReceived') {
            if (msg.params.response.status >= 400) {
                console.log('[NETWORK_ERROR]', msg.params.response.status, msg.params.response.url);
            }
        }
    };

    await new Promise(r => ws.onopen = r);

    await send('Runtime.enable');
    await send('Console.enable');
    await send('Network.enable');

    console.log('--- Page loaded, waiting 3s for network & scripts ---');
    await wait(3000);

    // Evaluate state of journal table and controls
    const evalRes = await send('Runtime.evaluate', {
        expression: `
            ({
                activeTab: Array.from(document.querySelectorAll('.tab-content, [id^="tab-"]')).filter(el => !el.classList.contains('hidden')).map(el => el.id),
                journalTableRecordCount: document.getElementById('journalTableRecordCount')?.innerText,
                journalTableBodyRows: document.getElementById('journalTableBody')?.querySelectorAll('tr')?.length,
                journalEmptyStateHidden: document.getElementById('journalEmptyState')?.classList.contains('hidden'),
                lastFetchedTradesCount: window.lastFetchedTrades ? window.lastFetchedTrades.length : 'undefined',
                hasLoadJournal: typeof window.loadJournal,
                hasSetJournalMode: typeof window.setJournalMode,
                reconcileBtn: !!document.getElementById('reconcileBtn'),
                reconcileBtnDisabled: document.getElementById('reconcileBtn')?.disabled,
                exportUnifiedCsvBtn: !!document.getElementById('exportUnifiedCsvBtn'),
                refreshJournalBtn: !!document.getElementById('refreshJournalBtn'),
                allRefreshBtnsCount: document.querySelectorAll('#refreshJournalBtn').length
            })
        `,
        returnByValue: true
    });

    console.log('--- DOM State Evaluation ---');
    console.log(evalRes?.result?.value);

    // Now test clicking Reconcile button or Refresh button!
    console.log('--- Testing click on refreshJournalBtn ---');
    const clickRes = await send('Runtime.evaluate', {
        expression: `
            try {
                const btn = document.getElementById('refreshJournalBtn');
                btn.click();
                'CLICKED OK';
            } catch(e) {
                e.toString();
            }
        `,
        returnByValue: true
    });
    console.log('Click result:', clickRes?.result?.value);

    await wait(2000);

    const postClickRes = await send('Runtime.evaluate', {
        expression: `
            ({
                journalTableRecordCount: document.getElementById('journalTableRecordCount')?.innerText,
                journalTableBodyRows: document.getElementById('journalTableBody')?.querySelectorAll('tr')?.length,
                lastFetchedTradesCount: window.lastFetchedTrades ? window.lastFetchedTrades.length : 'undefined',
            })
        `,
        returnByValue: true
    });
    console.log('--- Post-Click State ---');
    console.log(postClickRes?.result?.value);

    ws.close();
    chrome.kill();
}

main().catch(console.error);
