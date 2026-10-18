// =========================================================================
// TEXT & NUMERIC FORMATTERS
// =========================================================================
        function escapeHtml(str) {
            if (str === null || str === undefined) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        function formatPrice(val) {
            if (val === null || val === undefined || isNaN(val)) return '--';
            const num = Number(val);
            if (num >= 100) return num.toFixed(2);
            if (num >= 1) return num.toFixed(4);
            return num.toFixed(5);
        }


function fmtP(val) {
    if (val === null || val === undefined || isNaN(val)) return '--';
    const n = Number(val);
    if (n >= 1000) return n.toFixed(2);
    if (n >= 1) return n.toFixed(4);
    return n.toFixed(6);
}
