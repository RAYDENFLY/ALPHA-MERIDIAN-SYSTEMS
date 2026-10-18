// =========================================================================
// BANNER NOTIFICATIONS & NOTICES
// =========================================================================
        function showNotice(msg, type = 'info') {
            const banner = document.getElementById('noticeBanner');
            const text = document.getElementById('noticeBannerText');
            banner.className = `p-3 rounded-lg text-xs font-medium flex items-center justify-between ${
                type === 'warn' ? 'bg-amber-900/50 text-amber-200 border border-amber-700' :
                type === 'error' ? 'bg-red-900/50 text-red-200 border border-red-700' :
                'bg-indigo-900/50 text-indigo-200 border border-indigo-700'
            }`;
            text.innerText = msg;
            banner.classList.remove('hidden');
        }

        function dismissNotice() {
            document.getElementById('noticeBanner').classList.add('hidden');
        }
