/**
 * SignalGenerator Universal Table Pagination Engine
 * Provides consistent client-side pagination, page size selection (10, 25, 50, 100),
 * full-dataset filter/sort preservation, and bilingual labels (EN / ID).
 */

const _paginationStates = {};
const _paginationCallbacks = {};

function getPaginationState(key, defaults = {}) {
    if (!_paginationStates[key]) {
        _paginationStates[key] = {
            currentPage: 1,
            pageSize: defaults.pageSize || 25,
            totalItems: 0,
            totalPages: 1
        };
    }
    return _paginationStates[key];
}

function resetPaginationPage(key) {
    if (_paginationStates[key]) {
        _paginationStates[key].currentPage = 1;
    }
}

function setPaginationPageSize(key, newSize) {
    if (_paginationStates[key]) {
        _paginationStates[key].pageSize = parseInt(newSize, 10) || 25;
        _paginationStates[key].currentPage = 1;
    }
}

/**
 * Returns just the sliced array of items for current page of `key`.
 */
function paginateList(key, items) {
    const res = paginateData(key, items);
    return res.pageData || [];
}

/**
 * Flexible pagination function supporting:
 * 1) paginateData(key, items) -> { pageData, state, startIndex, endIndex }
 * 2) paginateData(items, state) -> { pageData, state, startIndex, endIndex }
 */
function paginateData(keyOrItems, maybeItemsOrState) {
    let key, items, state;
    if (typeof keyOrItems === 'string') {
        key = keyOrItems;
        items = Array.isArray(maybeItemsOrState) ? maybeItemsOrState : [];
        state = getPaginationState(key);
    } else {
        items = Array.isArray(keyOrItems) ? keyOrItems : [];
        state = maybeItemsOrState || getPaginationState('default');
    }

    state.totalItems = items.length;
    state.totalPages = Math.max(1, Math.ceil(items.length / state.pageSize));
    
    // Safety clamp: if filtered results reduced total pages, clamp current page
    if (state.currentPage > state.totalPages) {
        state.currentPage = state.totalPages;
    }
    if (state.currentPage < 1) {
        state.currentPage = 1;
    }

    const startIndex = (state.currentPage - 1) * state.pageSize;
    const endIndex = Math.min(startIndex + state.pageSize, items.length);
    const pageData = items.slice(startIndex, endIndex);

    return {
        pageData,
        state,
        startIndex,
        endIndex
    };
}

// Global dispatchers called by pagination DOM buttons
window.__onPageNav = function(key, targetPage) {
    const state = getPaginationState(key);
    state.currentPage = Math.max(1, Math.min(parseInt(targetPage, 10) || 1, state.totalPages));
    if (typeof _paginationCallbacks[key] === 'function') {
        _paginationCallbacks[key](state.currentPage);
    }
};

window.__onPageSize = function(key, newSize) {
    setPaginationPageSize(key, newSize);
    if (typeof _paginationCallbacks[key] === 'function') {
        _paginationCallbacks[key](1);
    }
};

/**
 * Flexible controls renderer supporting:
 * Signature A: renderPaginationControls(key, containerId, onRerenderCallback)
 * Signature B: renderPaginationControls(containerId, state, onPageChangeName, onPageSizeChangeName)
 */
function renderPaginationControls(arg1, arg2, arg3, arg4) {
    let key, containerId, state, onRerender;
    let isLegacySignature = false;

    if (typeof arg1 === 'string' && typeof arg2 === 'string') {
        // Signature A: (key, containerId, onRerenderCallback)
        key = arg1;
        containerId = arg2;
        onRerender = arg3;
        state = getPaginationState(key);
        if (typeof onRerender === 'function') {
            _paginationCallbacks[key] = onRerender;
        }
    } else {
        // Signature B: (containerId, state, onPageChangeName, onPageSizeChangeName)
        isLegacySignature = true;
        containerId = arg1;
        state = arg2 || { currentPage: 1, pageSize: 25, totalItems: 0, totalPages: 1 };
        key = containerId;
    }

    const container = document.getElementById(containerId);
    if (!container) return;

    if (state.totalItems === 0) {
        container.innerHTML = '';
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');

    const startIndex = state.totalItems > 0 ? (state.currentPage - 1) * state.pageSize + 1 : 0;
    const endIndex = Math.min(state.currentPage * state.pageSize, state.totalItems);

    const isFirstPage = state.currentPage <= 1;
    const isLastPage = state.currentPage >= state.totalPages;

    const translate = typeof t === 'function' ? t : (k, fallback) => fallback;
    const showingText = translate('pg_showing', 'Showing');
    const toText = translate('pg_to', 'to');
    const ofText = translate('pg_of', 'of');
    const recordsText = translate('pg_records', 'records');
    const pageText = translate('pg_page', 'Page');
    const perPageText = translate('pg_per_page', 'per page');

    const onSizeAction = isLegacySignature && typeof arg4 === 'string'
        ? `window['${arg4}'](this.value)`
        : `window.__onPageSize('${key}', this.value)`;

    const onNavAction = (target) => {
        if (isLegacySignature && typeof arg3 === 'string') {
            return `window['${arg3}'](${target})`;
        }
        return `window.__onPageNav('${key}', ${target})`;
    };

    container.innerHTML = `
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-3 border-t border-slate-800/80 text-xs text-slate-400 select-none">
            <!-- Left: Record Counter & Page Size Selector -->
            <div class="flex flex-wrap items-center gap-3">
                <span class="font-mono">
                    ${showingText} <strong class="text-white">${startIndex}</strong>–<strong class="text-white">${endIndex}</strong> ${ofText} <strong class="text-white">${state.totalItems}</strong> ${recordsText}
                </span>

                <div class="flex items-center gap-1.5 pl-2 border-l border-slate-800">
                    <label class="text-[11px] text-slate-500">${perPageText}:</label>
                    <select onchange="${onSizeAction}" class="bg-slate-900 border border-slate-700 text-slate-200 text-xs rounded px-2 py-0.5 focus:outline-none focus:border-indigo-500 font-mono">
                        <option value="10" ${state.pageSize === 10 ? 'selected' : ''}>10</option>
                        <option value="25" ${state.pageSize === 25 ? 'selected' : ''}>25</option>
                        <option value="50" ${state.pageSize === 50 ? 'selected' : ''}>50</option>
                        <option value="100" ${state.pageSize === 100 ? 'selected' : ''}>100</option>
                    </select>
                </div>
            </div>

            <!-- Right: Navigation Buttons -->
            <div class="flex items-center gap-1 font-mono">
                <!-- First Page -->
                <button type="button" 
                    onclick="${onNavAction(1)}" 
                    ${isFirstPage ? 'disabled' : ''}
                    class="px-2 py-1 rounded border border-slate-800 bg-slate-900/90 hover:bg-slate-800 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed transition" 
                    title="${translate('pg_first', 'First Page')}">
                    &laquo;
                </button>

                <!-- Previous Page -->
                <button type="button" 
                    onclick="${onNavAction(state.currentPage - 1)}" 
                    ${isFirstPage ? 'disabled' : ''}
                    class="px-2.5 py-1 rounded border border-slate-800 bg-slate-900/90 hover:bg-slate-800 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed transition" 
                    title="${translate('pg_prev', 'Previous Page')}">
                    &lsaquo;
                </button>

                <!-- Current / Total Pages Indicator -->
                <span class="px-3 py-1 rounded bg-slate-950 border border-slate-800 text-slate-300 font-bold">
                    ${pageText} ${state.currentPage} / ${state.totalPages}
                </span>

                <!-- Next Page -->
                <button type="button" 
                    onclick="${onNavAction(state.currentPage + 1)}" 
                    ${isLastPage ? 'disabled' : ''}
                    class="px-2.5 py-1 rounded border border-slate-800 bg-slate-900/90 hover:bg-slate-800 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed transition" 
                    title="${translate('pg_next', 'Next Page')}">
                    &rsaquo;
                </button>

                <!-- Last Page -->
                <button type="button" 
                    onclick="${onNavAction(state.totalPages)}" 
                    ${isLastPage ? 'disabled' : ''}
                    class="px-2 py-1 rounded border border-slate-800 bg-slate-900/90 hover:bg-slate-800 text-slate-300 disabled:opacity-30 disabled:cursor-not-allowed transition" 
                    title="${translate('pg_last', 'Last Page')}">
                    &raquo;
                </button>
            </div>
        </div>
    `;
}
