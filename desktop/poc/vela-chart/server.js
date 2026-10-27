const http = require('http');
const fs = require('fs');
const path = require('path');
const url = require('url');

const PORT = 8088;
const BACKEND_PORT = 8000;
const WEB_DIR = path.join(__dirname, 'web');

const MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.ico': 'image/x-icon'
};

const server = http.createServer((req, res) => {
    // Add CORS headers
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Accept');

    if (req.method === 'OPTIONS') {
        res.writeHead(204);
        res.end();
        return;
    }

    const parsedUrl = url.parse(req.url, true);
    const pathname = parsedUrl.pathname;

    // Proxy /api/* to FastAPI backend at 127.0.0.1:8000
    if (pathname.startsWith('/api/')) {
        const proxyReq = http.request({
            host: '127.0.0.1',
            port: BACKEND_PORT,
            path: req.url,
            method: 'GET',
            headers: {
                'Accept': req.headers['accept'] || 'application/json'
            }
        }, (proxyRes) => {
            res.writeHead(proxyRes.statusCode, proxyRes.headers);
            proxyRes.pipe(res);
        });

        proxyReq.on('error', (err) => {
            res.writeHead(502, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'Backend unreachable', details: err.message }));
        });

        proxyReq.end();
        return;
    }

    // Static file serving
    let safePath = path.normalize(pathname).replace(/^(\.\.[\/\\])+/, '');
    if (safePath === '/' || safePath === '\\') safePath = '/index.html';

    const filePath = path.join(WEB_DIR, safePath);

    fs.stat(filePath, (err, stats) => {
        if (err || !stats.isFile()) {
            res.writeHead(404, { 'Content-Type': 'text/plain' });
            res.end('404 Not Found');
            return;
        }

        const ext = path.extname(filePath).toLowerCase();
        const contentType = MIME_TYPES[ext] || 'application/octet-stream';

        res.writeHead(200, { 'Content-Type': contentType });
        const stream = fs.createReadStream(filePath);
        stream.pipe(res);
    });
});

server.listen(PORT, '127.0.0.1', () => {
    console.log(`[AMS PoC Server] Serving Vela Chart PoC at http://127.0.0.1:${PORT}`);
    console.log(`[AMS PoC Server] Proxying /api/* -> http://127.0.0.1:${BACKEND_PORT}/api/*`);
});
