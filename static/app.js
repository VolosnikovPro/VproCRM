let stages = [];
let sources = [];
let tags = [];
let clients = [];
let currentClientId = null;
let contextClientId = null;
let currentClientData = null;

async function api(path, method = 'GET', body = null) {
    const opts = { method, headers: {} };
    if (body && !(body instanceof FormData)) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    } else if (body instanceof FormData) {
        opts.body = body;
    }
    const res = await fetch(path, opts);
    if (!res.ok) { const err = await res.json().catch(() => ({ detail: res.statusText })); throw new Error(err.detail || 'Request failed'); }
    return res.json();
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function fmtDate(d) { return new Date(d).toLocaleString('ru-RU'); }
function fmtDateShort(d) { if (!d) return ''; const dt = new Date(d); return dt.toLocaleDateString('ru-RU'); }
function fmtMoney(v) { return Number(v).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' ₽'; }

function switchTab(name) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    document.querySelector(`.tab[data-tab="${name}"]`).classList.add('active');
    if (name === 'dashboard') renderDashboard();
    if (name === 'tasks') renderTasksPage();
    if (name === 'calendar') renderCalendar();
    if (name === 'integrations') renderIntegrations();
    if (name === 'ads') renderAds();
}
async function loadData() {
    try {
        stages = await api('/api/stages');
        sources = await api('/api/sources');
        tags = await api('/api/tags');
        clients = await api('/api/clients');
        renderPipeline();
        populateSelects();
    } catch (e) {
        console.error('loadData failed, retrying in 2s:', e);
        setTimeout(loadData, 2000);
    }
}

function populateSelects() {
    const stageSel = document.getElementById('clientStage');
    stageSel.innerHTML = stages.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
    const sourceSel = document.getElementById('clientSource');
    sourceSel.innerHTML = sources.map(s => `<option value="${s.name}">${s.name}</option>`).join('');
    const filterStage = document.getElementById('filterStage');
    filterStage.innerHTML = '<option value="">Все этапы</option>' + stages.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
    const filterSource = document.getElementById('filterSource');
    filterSource.innerHTML = '<option value="">Все источники</option>' + sources.map(s => `<option value="${s.name}">${s.name}</option>`).join('');
    const filterTag = document.getElementById('filterTag');
    filterTag.innerHTML = '<option value="">Все теги</option>' + tags.map(t => `<option value="${t.id}">${t.name}</option>`).join('');
    renderTagPicker();
}

function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

/* Pipeline */
function renderPipeline() {
    const el = document.getElementById('pipeline');
    el.innerHTML = '';
    stages.forEach(stage => {
        const sc = clients.filter(c => c.stage_id === stage.id);
        const col = document.createElement('div');
        col.className = 'kanban-column';
        col.innerHTML = `
            <div class="column-header" style="border-left: 3px solid ${stage.color}">
                <span class="stage-color" style="background:${stage.color}"></span>
                <span class="stage-info">${esc(stage.name)}</span>
                <span class="stage-count">${sc.length}</span>
                ${stage.total_budget > 0 ? `<span class="stage-budget">${fmtMoney(stage.total_budget)}</span>` : ''}
                <div class="column-header-actions">
                    <button class="btn btn-sm" onclick="event.stopPropagation(); openStageModal(${stage.id})">✎</button>
                    ${stage.name === 'Отказ' ? `<button class="btn btn-sm" onclick="event.stopPropagation(); openRejectionReasonsEditor()" title="Причины отказа">📋</button>` : ''}
                    <button class="btn btn-sm btn-danger" onclick="event.stopPropagation(); deleteStage(${stage.id})">✕</button>
                </div>
            </div>
            <div class="column-body" data-stage-id="${stage.id}">
                ${sc.length === 0 ? '<div class="empty-stage">Нет клиентов</div>' : ''}
            </div>
        `;
        const body = col.querySelector('.column-body');
        sc.forEach(c => body.appendChild(createClientCard(c)));
        el.appendChild(col);
    });
    const addBtn = document.createElement('button');
    addBtn.className = 'add-stage-btn';
    addBtn.textContent = '+ Добавить этап';
    addBtn.onclick = () => openStageModal();
    el.appendChild(addBtn);
}

function createClientCard(client) {
    const card = document.createElement('div');
    card.className = 'client-card';
    card.style.borderLeftColor = stages.find(s => s.id === client.stage_id)?.color || '#6b7280';

    let budgetHtml = client.budget > 0 ? `<div class="budget">${fmtMoney(client.budget)}</div>` : '';
    let indicator = '';
    if (client.overdue_count > 0) {
        indicator = `<span class="task-indicator task-overdue" title="${client.overdue_count} просроченных">${client.overdue_count}</span>`;
    } else if (client.today_count > 0) {
        indicator = `<span class="task-indicator task-today" title="${client.today_count} на сегодня">${client.today_count}</span>`;
    } else if (client.week_count > 0) {
        indicator = `<span class="task-indicator task-week" title="${client.week_count} на неделе">${client.week_count}</span>`;
    } else if (client.later_count > 0) {
        indicator = `<span class="task-indicator task-later" title="${client.later_count} задач">${client.later_count}</span>`;
    } else {
        indicator = `<span class="task-indicator task-none" title="Нет задач">0</span>`;
    }
    let tagsHtml = '';
    if (client.tags && client.tags.length > 0) {
        tagsHtml = '<div class="card-tags">' + client.tags.map(t => `<span class="tag-badge" style="background:${t.color}">${esc(t.name)}</span>`).join('') + '</div>';
    }
    let lastAct = '';
    if (client.last_activity) {
        lastAct = `<span style="font-size:10px;color:var(--text-muted)">${fmtDateShort(client.last_activity)}</span>`;
    }

    card.innerHTML = `
        <div class="card-top">
            <div class="name">${esc(client.deal_name || client.name)}</div>
            ${indicator}
        </div>
        <div class="contact-name">👤 ${esc(client.name)}</div>
        <div class="phone">${esc(client.phone)}</div>
        ${client.organization ? `<div class="org">${esc(client.organization)}</div>` : ''}
        ${budgetHtml}
        ${tagsHtml}
        <div class="card-footer">
            <span class="source">${esc(client.source || 'Без источника')}</span>
            <div style="display:flex;align-items:center;gap:4px">
                ${lastAct}
                <span style="font-size:11px;color:var(--text-muted)">${client.notes?.length || 0} зап.</span>
                <button class="stage-btn" data-client-id="${client.id}" onclick="event.stopPropagation(); showStageDropdown(event, ${client.id})">▼</button>
            </div>
        </div>
    `;
    card.onclick = () => openDetail(client.id);
    card.oncontextmenu = (e) => { e.preventDefault(); showContextMenu(e, client.id); };
    card.draggable = true;
    card.dataset.clientId = client.id;
    card.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', client.id); card.style.opacity = '0.4'; });
    card.addEventListener('dragend', e => { card.style.opacity = '1'; });
    return card;
}

/* Drag & Drop */
document.addEventListener('dragover', e => { const col = e.target.closest('.column-body'); if (col) { e.preventDefault(); col.classList.add('drag-over'); } });
document.addEventListener('dragleave', e => { const col = e.target.closest('.column-body'); if (col) col.classList.remove('drag-over'); });
document.addEventListener('drop', async e => {
    const col = e.target.closest('.column-body'); if (!col) return;
    col.classList.remove('drag-over');
    const clientId = Number(e.dataTransfer.getData('text/plain'));
    const stageId = Number(col.dataset.stageId);
    if (!clientId || !stageId) return;
    const client = clients.find(c => c.id === clientId);
    if (!client || client.stage_id === stageId) return;
    const targetStage = stages.find(s => s.id === stageId);
    if (targetStage && targetStage.name === 'Отказ') {
        showRejectionModal(clientId, stageId);
    } else {
        await api(`/api/clients/${clientId}`, 'PUT', { stage_id: stageId });
        await loadData();
    }
});

/* Context Menu */
function showContextMenu(e, clientId) { contextClientId = clientId; const m = document.getElementById('contextMenu'); m.style.left = e.clientX + 'px'; m.style.top = e.clientY + 'px'; m.classList.remove('hidden'); }
document.addEventListener('click', () => document.getElementById('contextMenu').classList.add('hidden'));
function contextAction(action) {
    document.getElementById('contextMenu').classList.add('hidden');
    if (!contextClientId) return;
    if (action === 'edit') openClientModal(contextClientId);
    else if (action === 'delete') deleteClientById(contextClientId);
    else if (action === 'moveFirst' && stages.length > 0) {
        api(`/api/clients/${contextClientId}`, 'PUT', { stage_id: stages[0].id }).then(() => loadData());
    }
}


/* ── Stage dropdown on cards ── */
function showStageDropdown(event, clientId) {
    event.stopPropagation();
    closeStageDropdown();
    const dd = document.getElementById('stageDropdown');
    dd.innerHTML = stages.map(s => `
        <div class="stage-dropdown-item" data-stage-id="${s.id}" data-is-reject="${s.name === 'Отказ'}" style="${s.name === 'Отказ' ? 'border-top:1px solid var(--border);margin-top:4px;padding-top:10px' : ''}">
            <span class="sd-dot" style="background:${s.color}"></span>
            ${esc(s.name)}
        </div>
    `).join('');
    dd.style.left = event.clientX + 'px';
    dd.style.top = event.clientY + 'px';
    dd.classList.remove('hidden');
    dd.onclick = (e) => {
        const item = e.target.closest('.stage-dropdown-item');
        if (!item) return;
        closeStageDropdown();
        const targetStageId = Number(item.dataset.stageId);
        if (item.dataset.isReject === 'true') {
            showRejectionModal(clientId, targetStageId);
        } else {
            moveClientToStage(clientId, targetStageId);
        }
    };
    setTimeout(() => document.addEventListener('click', closeStageDropdown, { once: true }), 0);
}

function closeStageDropdown() {
    document.getElementById('stageDropdown').classList.add('hidden');
}

async function moveClientToStage(clientId, stageId) {
    const stage = stages.find(s => s.id === stageId);
    try {
        await api(`/api/clients/${clientId}`, 'PUT', { stage_id: stageId });
        await loadData();
    } catch (e) {
        alert('Ошибка перемещения: ' + e.message);
    }
}

/* ── Rejection reason dialog ── */
let _rejectClientId = null;
let _rejectTargetStageId = null;

function showRejectionModal(clientId, targetStageId) {
    _rejectClientId = clientId;
    _rejectTargetStageId = targetStageId;
    const client = clients.find(c => c.id === clientId);
    document.getElementById('rejectionClientName').textContent = 'Клиент: ' + (client?.deal_name || client?.name || '');
    const sel = document.getElementById('rejectionReasonSelect');
    sel.innerHTML = '<option value="">— выберите причину —</option>';
    document.getElementById('rejectionError').style.display = 'none';
    api('/api/rejection-reasons').then(reasons => {
        reasons.forEach(r => {
            const opt = document.createElement('option');
            opt.value = r.id; opt.textContent = r.name;
            sel.appendChild(opt);
        });
    });
    document.getElementById('rejectionModal').classList.remove('hidden');
}

async function confirmRejection() {
    const reasonId = Number(document.getElementById('rejectionReasonSelect').value);
    const errEl = document.getElementById('rejectionError');
    if (!reasonId) {
        errEl.textContent = 'Сделка не перемещена в отказ. Выберите причину.';
        errEl.style.display = 'block';
        return;
    }
    errEl.style.display = 'none';
    closeModal('rejectionModal');
    try {
        await api(`/api/clients/${_rejectClientId}`, 'PUT', { stage_id: _rejectTargetStageId, rejection_reason_id: reasonId });
        await loadData();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

/* ── Rejection reasons editor ── */
function openRejectionReasonsEditor() {
    const modal = document.getElementById('rejectionReasonsModal');
    modal.classList.remove('hidden');
    renderRejectionReasons();
}

async function renderRejectionReasons() {
    const list = document.getElementById('rejectionReasonsList');
    try {
        const reasons = await api('/api/rejection-reasons');
        list.innerHTML = reasons.map(r => `
            <div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)">
                <span style="flex:1;font-size:14px">${esc(r.name)}</span>
                <button class="btn btn-sm" onclick="editRejectionReason(${r.id}, '${esc(r.name)}')" style="font-size:12px">✎</button>
                <button class="btn btn-sm btn-danger" onclick="deleteRejectionReason(${r.id})" style="font-size:12px">✕</button>
            </div>
        `).join('');
    } catch (e) {
        document.getElementById('rejectionReasonsList').innerHTML = '<span style="color:var(--danger)">Ошибка загрузки</span>';
    }
}

async function addRejectionReason() {
    const input = document.getElementById('newReasonInput');
    const name = input.value.trim();
    if (!name) return;
    try {
        await api('/api/rejection-reasons', 'POST', { name });
        input.value = '';
        renderRejectionReasons();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function editRejectionReason(id, currentName) {
    const newName = prompt('Новое название:', currentName);
    if (!newName || newName === currentName) return;
    try {
        await api(`/api/rejection-reasons/${id}`, 'PUT', { name: newName });
        renderRejectionReasons();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

async function deleteRejectionReason(id) {
    if (!confirm('Удалить причину?')) return;
    try {
        await api(`/api/rejection-reasons/${id}`, 'DELETE');
        renderRejectionReasons();
    } catch (e) {
        alert('Ошибка: ' + e.message);
    }
}

/* Search */
let searchTimer;
function onSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(doSearch, 300); }
async function doSearch() {
    const q = document.getElementById('searchInput').value.trim();
    const stageId = document.getElementById('filterStage').value;
    const source = document.getElementById('filterSource').value;
    const tagId = document.getElementById('filterTag').value;
    const params = new URLSearchParams();
    if (q) params.set('query', q);
    if (stageId) params.set('stage_id', stageId);
    if (source) params.set('source', source);
    if (tagId) params.set('tag_id', tagId);
    const qs = params.toString();
    clients = await api('/api/clients' + (qs ? '?' + qs : ''));
    renderPipeline();
}

/* Stage CRUD */
function openStageModal(id) {
    document.getElementById('stageModalTitle').textContent = id ? 'Редактировать этап' : 'Новый этап';
    if (id) { const s = stages.find(x => x.id === id); if (!s) return;
        document.getElementById('stageId').value = s.id; document.getElementById('stageName').value = s.name; document.getElementById('stageColor').value = s.color;
    } else { document.getElementById('stageId').value = ''; document.getElementById('stageName').value = ''; document.getElementById('stageColor').value = '#3b82f6'; }
    document.getElementById('stageModal').classList.remove('hidden');
}
async function saveStage(e) {
    e.preventDefault();
    const id = document.getElementById('stageId').value;
    const data = { name: document.getElementById('stageName').value, order: id ? (stages.find(s => s.id === Number(id))?.order ?? stages.length) : stages.length, color: document.getElementById('stageColor').value };
    if (id) await api(`/api/stages/${id}`, 'PUT', data); else await api('/api/stages', 'POST', data);
    closeModal('stageModal'); await loadData();
}
async function deleteStage(id) {
    if (!confirm(`Удалить этап?`)) return;
    await api(`/api/stages/${id}`, 'DELETE');
    await loadData();
}

/* Source CRUD */
function openSourceModal(id) {
    document.getElementById('sourceModalTitle').textContent = id ? 'Редактировать источник' : 'Новый источник';
    if (id) { const s = sources.find(x => x.id === id); if (!s) return;
        document.getElementById('sourceId').value = s.id; document.getElementById('sourceName').value = s.name;
    } else { document.getElementById('sourceId').value = ''; document.getElementById('sourceName').value = ''; }
    document.getElementById('sourceModal').classList.remove('hidden');
}
async function saveSource(e) {
    e.preventDefault();
    const id = document.getElementById('sourceId').value; const data = { name: document.getElementById('sourceName').value, order: 0 };
    try { if (id) await api(`/api/sources/${id}`, 'PUT', data); else await api('/api/sources', 'POST', data); closeModal('sourceModal'); await loadData(); } catch (err) { alert(err.message); }
}
async function deleteSource(id) { if (!confirm('Удалить источник?')) return; await api(`/api/sources/${id}`, 'DELETE'); await loadData(); }

/* Tag CRUD */
function openTagModal(id) {
    document.getElementById('tagModalTitle').textContent = id ? 'Редактировать тег' : 'Новый тег';
    if (id) { const t = tags.find(x => x.id === id); if (!t) return;
        document.getElementById('tagId').value = t.id; document.getElementById('tagName').value = t.name; document.getElementById('tagColor').value = t.color;
    } else { document.getElementById('tagId').value = ''; document.getElementById('tagName').value = ''; document.getElementById('tagColor').value = '#6b7280'; }
    document.getElementById('tagModal').classList.remove('hidden');
}
async function saveTag(e) {
    e.preventDefault();
    const id = document.getElementById('tagId').value; const data = { name: document.getElementById('tagName').value, color: document.getElementById('tagColor').value };
    try { if (id) await api(`/api/tags/${id}`, 'PUT', data); else await api('/api/tags', 'POST', data); closeModal('tagModal'); await loadData(); } catch (err) { alert(err.message); }
}
async function deleteTag(id) { if (!confirm('Удалить тег?')) return; await api(`/api/tags/${id}`, 'DELETE'); await loadData(); }

function renderTagPicker() {
    const el = document.getElementById('tagPicker');
    if (!el) return;
    el.innerHTML = tags.map(t => `<button type="button" class="tag-option" style="border-color:${t.color};color:${t.color}" data-tag-id="${t.id}" onclick="toggleTagPick(this)">${esc(t.name)}</button>`).join('');
}
function toggleTagPick(btn) { btn.classList.toggle('selected'); if (btn.classList.contains('selected')) { btn.style.background = btn.style.borderColor; btn.style.color = '#fff'; } else { btn.style.background = 'none'; btn.style.color = btn.style.borderColor; } }
function getSelectedTagIds() { return [...document.querySelectorAll('#tagPicker .tag-option.selected')].map(el => Number(el.dataset.tagId)); }

/* Client CRUD */
async function openClientModal(id) {
    const modal = document.getElementById('clientModal');
    document.getElementById('clientModalTitle').textContent = id ? 'Редактировать клиента' : 'Новый клиент';
    document.getElementById('clientId').value = id || '';
    if (id) {
        const c = await api(`/api/clients/${id}`);
        document.getElementById('clientName').value = c.name;
        document.getElementById('clientPhone').value = c.phone;
        document.getElementById('clientEmail').value = c.email;
        document.getElementById('clientOrg').value = c.organization;
        document.getElementById('clientAddress').value = c.address;
        document.getElementById('clientResponsible').value = c.responsible;
        document.getElementById('clientBudget').value = c.budget || 0;
        document.getElementById('clientSource').value = c.source;
        document.getElementById('clientStage').value = c.stage_id;
        document.querySelectorAll('#tagPicker .tag-option').forEach(el => el.classList.remove('selected'));
        (c.tags || []).forEach(t => {
            const btn = document.querySelector(`#tagPicker .tag-option[data-tag-id="${t.id}"]`);
            if (btn) { btn.classList.add('selected'); btn.style.background = btn.style.borderColor; btn.style.color = '#fff'; }
        });
    } else {
        ['clientName','clientPhone','clientEmail','clientOrg','clientAddress','clientResponsible'].forEach(id => document.getElementById(id).value = '');
        document.getElementById('clientBudget').value = '0';
        document.getElementById('clientSource').value = sources[0]?.name || '';
        if (stages.length > 0) document.getElementById('clientStage').value = stages[0].id;
        document.querySelectorAll('#tagPicker .tag-option').forEach(el => { el.classList.remove('selected'); el.style.background = 'none'; el.style.color = el.style.borderColor; });
    }
    modal.classList.remove('hidden');
}

async function saveClient(e) {
    e.preventDefault();
    const id = document.getElementById('clientId').value;
    const data = {
        deal_name: document.getElementById('clientDealName').value,
        name: document.getElementById('clientName').value, phone: document.getElementById('clientPhone').value,
        email: document.getElementById('clientEmail').value, organization: document.getElementById('clientOrg').value,
        address: document.getElementById('clientAddress').value, responsible: document.getElementById('clientResponsible').value,
        budget: Number(document.getElementById('clientBudget').value) || 0,
        source: document.getElementById('clientSource').value, stage_id: Number(document.getElementById('clientStage').value),
        custom_fields: [], tag_ids: getSelectedTagIds(),
    };
    if (id) await api(`/api/clients/${id}`, 'PUT', data);
    else await api('/api/clients', 'POST', data);
    closeModal('clientModal'); await loadData();
}

/* Import CSV */
function openImportModal() { document.getElementById('importModal').classList.remove('hidden'); document.getElementById('importResult').innerHTML = ''; }
async function doImport() {
    const fileInput = document.getElementById('importFile');
    if (!fileInput.files[0]) { alert('Выберите файл'); return; }
    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    document.getElementById('importResult').innerHTML = 'Загрузка...';
    const result = await api('/api/import/csv', 'POST', fd);
    let html = `<div style="font-weight:600;color:var(--success)">Импортировано: ${result.imported}</div>`;
    if (result.errors.length > 0) html += '<div style="margin-top:8px;font-size:12px;color:var(--danger)">' + result.errors.map(e => '<div>' + esc(e) + '</div>').join('') + '</div>';
    document.getElementById('importResult').innerHTML = html;
    if (result.imported > 0) await loadData();
}

/* Detail tabs */
/* Detail & Notes */
async function openDetail(clientId) {
    currentClientId = clientId;
    currentClientData = null;
    const client = await api(`/api/clients/${clientId}`);
    currentClientData = client;
    const stage = stages.find(s => s.id === client.stage_id);
    document.getElementById('detailName').textContent = client.deal_name || client.name;
    document.getElementById('detailDealName').textContent = client.deal_name || '—';
    document.getElementById('detailContactName').textContent = client.name;
    document.getElementById('detailPhone').textContent = client.phone || '—';
    document.getElementById('detailEmail').textContent = client.email || '—';
    document.getElementById('detailOrg').textContent = client.organization || '—';
    document.getElementById('detailAddress').textContent = client.address || '—';
    document.getElementById('detailSource').textContent = client.source || '—';
    document.getElementById('detailStage').textContent = stage ? stage.name : '—';
    document.getElementById('detailResponsible').textContent = client.responsible || '—';
    document.getElementById('detailBudget').textContent = client.budget > 0 ? fmtMoney(client.budget) : '—';
    document.getElementById('detailCreated').textContent = fmtDate(client.created_at);
    document.getElementById('detailUpdated').textContent = fmtDate(client.updated_at);
    const tagsEl = document.getElementById('detailTags');
    tagsEl.innerHTML = (client.tags || []).map(t => `<span class="tag-badge" style="background:${t.color}">${esc(t.name)}</span>`).join('');

    /* Avito link + messages */
    let attachments = [], avitoMsgs = [], avitoChat = null;
    try { attachments = await api(`/api/clients/${clientId}/attachments`); } catch {}
    try {
        avitoChat = await api(`/api/avito/client/${clientId}/chat`);
        if (avitoChat) {
            // Sync messages from Avito API
            try { await api(`/api/avito/client/${clientId}/sync-messages`, 'POST'); } catch {}
            avitoMsgs = await api(`/api/avito/client/${clientId}/messages`);
        }
    } catch {}
    renderTimeline(client, attachments, avitoMsgs, avitoChat);

    /* Show Avito info */
    const avitoSection = document.getElementById('detailAvito');
    if (avitoChat && avitoChat.chat_id) {
        avitoSection.classList.remove('hidden');
        document.getElementById('detailAvitoPartner').textContent = avitoChat.item_title ? `${avitoChat.item_title} (${avitoChat.chat_id.slice(0,8)}...)` : `Чат ${avitoChat.chat_id.slice(0,8)}...`;
        const link = document.getElementById('detailAvitoLink');
        link.href = avitoChat.item_url || '#';
        link.style.display = avitoChat.item_url ? 'inline' : 'none';
        document.getElementById('avitoChatReply').dataset.chatId = avitoChat.chat_id;
    } else {
        avitoSection.classList.add('hidden');
    }

    document.getElementById('noteContent').value = '';
    document.getElementById('noteFileName').textContent = '';
    document.getElementById('noteFile').value = '';
    document.getElementById('detailModal').classList.remove('hidden');
}

function iconNote() { return '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M1 2h14v10H5l-3 3V2z"/></svg>'; }
function iconTask() { return '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="1" width="14" height="14" rx="2"/><path d="M5 8l2 2 4-4"/></svg>'; }
function iconFile() { return '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 14H3a1 1 0 01-1-1V3a1 1 0 011-1h6l4 4v7a1 1 0 01-1 1z"/><path d="M9 2v4h4"/></svg>'; }
function iconAvito() { return '<img src="/avito.png" style="width:100%;height:100%;object-fit:cover" alt="Авито">'; }

function renderTimeline(client, attachments, avitoMsgs, avitoChat) {
    const el = document.getElementById('timeline');
    const items = [];
    const usedFileIds = new Set();

    const icons = {
        created: '+', moved: '→', note: 'N', task: 'T',
        attachment: 'F', merged: '∪', task_done: '✓',
        task_reactivated: '↻', task_deleted: '✕',
    };

    // Activity log entries
    (client.activity_log || []).forEach(a => {
        const isTaskDone = a.action === 'task' && a.description.startsWith('Задача выполнена');
        const isTaskReactivated = a.action === 'task' && a.description.startsWith('Задача возобновлена');
        const isTaskDeleted = a.action === 'task' && a.description.startsWith('Задача удалена');
        let icon = icons[a.action] || '•';
        if (isTaskDone) icon = icons.task_done;
        else if (isTaskReactivated) icon = icons.task_reactivated;
        else if (isTaskDeleted) icon = icons.task_deleted;
        items.push({
            type: 'log',
            date: new Date(a.created_at),
            html: `
                <div class="tl-icon">${icon}</div>
                <div class="tl-body">
                    <div class="tl-text">${esc(a.description)}</div>
                    <div class="tl-date">${fmtDate(a.created_at)}</div>
                </div>
            `,
        });
    });

    // Notes (with file link support)
    (client.notes || []).forEach(n => {
        let displayContent = n.content;
        const fileLinks = [];
        displayContent = displayContent.replace(/\[file:(\d+)\]([^\n]+)/g, (m, fid, fname) => {
            usedFileIds.add(Number(fid));
            fileLinks.push(`<div style="margin-top:4px"><a href="/api/attachments/${fid}/download" target="_blank" class="file-link">${esc(fname)}</a></div>`);
            return '';
        });
        items.push({
            type: 'note',
            date: new Date(n.created_at),
            html: `
                <div class="tl-icon tl-note-icon">${iconNote()}</div>
                <div class="tl-body">
                    <div class="tl-text tl-note-text">${esc(displayContent).replace(/\n/g, '<br>')}${fileLinks.join('')}</div>
                    <div class="tl-date">${fmtDate(n.created_at)}</div>
                    <div class="tl-actions">
                        <button class="btn btn-sm" onclick="editNote(${n.id},'${esc(n.content.replace(/\[file:\d+\][^\n]*/g, '').trim()).replace(/'/g, "\\'")}')">✎</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteNote(${n.id})">✕</button>
                    </div>
                </div>
            `,
        });
    });

    // Tasks
    (client.tasks || []).forEach(t => {
        const overdue = !t.completed && t.due_date && new Date(t.due_date) < new Date();
        const cls = t.completed ? 'tl-task-done' : (overdue ? 'tl-task-overdue' : '');
        items.push({
            type: 'task',
            date: new Date(t.created_at),
            html: `
                <div class="tl-icon tl-task-icon">${iconTask()}</div>
                <div class="tl-body ${cls}">
                    <label class="task-checkbox">
                        <input type="checkbox" ${t.completed ? 'checked' : ''} onchange="toggleTask(${t.id}, this.checked)">
                        <span></span>
                    </label>
                    <div class="task-body">
                        <div class="task-title">${esc(t.title)}</div>
                        ${t.due_date ? `<div class="task-due">${fmtDate(t.due_date)}</div>` : ''}
                    </div>
                    <button class="btn btn-sm btn-danger" onclick="deleteTask(${t.id})">✕</button>
                </div>
            `,
        });
    });

    // Unattached files (not linked to any note)
    (attachments || []).forEach(a => {
        if (usedFileIds.has(a.id)) return;
        const size = a.file_size > 1024 ? Math.round(a.file_size / 1024) + ' KB' : a.file_size + ' B';
        items.push({
            type: 'file',
            date: new Date(a.created_at),
            html: `
                <div class="tl-icon tl-file-icon">${iconFile()}</div>
                <div class="tl-body">
                    <div class="tl-text"><a href="/api/attachments/${a.id}/download" target="_blank">${esc(a.original_name)}</a> <span style="font-size:11px;color:var(--text-muted)">${size}</span></div>
                    <div class="tl-date">${fmtDate(a.created_at)}</div>
                    <div class="tl-actions">
                        <button class="btn btn-sm btn-danger" onclick="deleteAttachment(${a.id})">✕</button>
                    </div>
                </div>
            `,
        });
    });

    // Avito messages — chat bubbles
    if (avitoMsgs && avitoMsgs.length > 0) {
        avitoMsgs.forEach(m => {
            const isMine = m.author_name === 'Вы';
            const authorLabel = isMine ? (client.responsible || 'Вы') : (client.name || 'Клиент');
            items.push({
                type: 'avito',
                date: new Date(m.created_at),
                html: `
                    <div class="tl-icon tl-avito-icon">${iconAvito()}</div>
                    <div class="avito-bubble ${isMine ? 'avito-bubble-mine' : 'avito-bubble-theirs'}">
                        <div class="avito-bubble-text ${isMine ? '' : 'avito-msg-clickable'}" data-chat-id="${avitoChat?.chat_id || ''}" data-msg-id="${esc(m.message_id)}" ${isMine ? '' : 'onclick="toggleAvitoReply(this)"'}>${esc(m.content)}</div>
                        <div class="avito-bubble-meta">
                            <span class="avito-bubble-author">${esc(authorLabel)}</span>
                            <span class="avito-bubble-time">${fmtDate(m.created_at)}</span>
                            ${isMine ? `<span class="avito-read-status">${m.is_read ? '✓✓' : '✓'}</span>` : ''}
                        </div>
                        ${!isMine ? `<div class="avito-reply-box hidden" id="avitoReply-${esc(m.message_id)}">
                            <form onsubmit="sendAvitoReply(event, '${esc(avitoChat?.chat_id || '')}', '${esc(m.message_id)}')" style="display:flex;gap:4px;margin-top:4px">
                                <input type="text" placeholder="Ответить..." style="flex:1;font-size:13px;padding:4px 8px" required>
                                <button type="submit" class="btn btn-sm btn-primary">→</button>
                            </form>
                        </div>` : ''}
                    </div>
                `,
            });
        });
    }

    // Sort by date descending (newest first)
    items.sort((a, b) => b.date - a.date);

    if (items.length === 0) {
        el.innerHTML = '<div style="color:var(--text-muted);font-size:13px;padding:12px 0;">Нет активности</div>';
        return;
    }

    el.innerHTML = items.map(item => `<div class="tl-item">${item.html}</div>`).join('');
}

async function addNote(e) {
    e.preventDefault();
    const content = document.getElementById('noteContent').value;
    if (!content.trim()) return;
    const fileInput = document.getElementById('noteFile');
    const hasFile = fileInput.files.length > 0;
    if (hasFile) {
        const fd = new FormData();
        fd.append('file', fileInput.files[0]);
        const att = await api(`/api/clients/${currentClientId}/attachments`, 'POST', fd);
        await api(`/api/clients/${currentClientId}/notes`, 'POST', { content: `${content}\n\n[file:${att.id}]${att.original_name}` });
    } else {
        await api(`/api/clients/${currentClientId}/notes`, 'POST', { content });
    }
    document.getElementById('noteContent').value = '';
    document.getElementById('noteFileName').textContent = '';
    fileInput.value = '';
    await openDetail(currentClientId); await loadData();
}

function onNoteFileSelected() {
    const fi = document.getElementById('noteFile');
    document.getElementById('noteFileName').textContent = fi.files.length ? fi.files[0].name : '';
}
async function editNote(id, old) { const content = prompt('Редактировать запись:', old); if (content === null || !content.trim()) return; await api(`/api/notes/${id}`, 'PUT', { content }); await openDetail(currentClientId); }
async function deleteNote(id) { if (!confirm('Удалить запись?')) return; await api(`/api/notes/${id}`, 'DELETE'); await openDetail(currentClientId); }
async function deleteClient() { if (!confirm('Удалить клиента?')) return; await api(`/api/clients/${currentClientId}`, 'DELETE'); closeModal('detailModal'); await loadData(); }
async function deleteClientById(id) { if (!confirm('Удалить клиента?')) return; await api(`/api/clients/${id}`, 'DELETE'); closeModal('detailModal'); await loadData(); }
function editCurrentClient() { closeModal('detailModal'); openClientModal(currentClientId); }

/* Tasks */
async function toggleTask(taskId, completed) { await api(`/api/tasks/${taskId}`, 'PUT', { completed }); await openDetail(currentClientId); await loadData(); }
async function deleteTask(taskId) { if (!confirm('Удалить задачу?')) return; await api(`/api/tasks/${taskId}`, 'DELETE'); await openDetail(currentClientId); await loadData(); }
async function addTask(e) {
    e.preventDefault();
    const title = document.getElementById('taskTitle').value; const dueDate = document.getElementById('taskDueDate').value;
    if (!title.trim()) return;
    const data = { title };
    if (dueDate) {
        // Send as local time string (YYYY-MM-DDTHH:MM) without UTC conversion
        data.due_date = dueDate + ':00';
    }
    await api(`/api/clients/${currentClientId}/tasks`, 'POST', data);
    document.getElementById('taskTitle').value = ''; document.getElementById('taskDueDate').value = '';
    await openDetail(currentClientId); await loadData();
}

/* Attachment */
async function deleteAttachment(attId) {
    if (!confirm('Удалить файл?')) return;
    await api(`/api/attachments/${attId}`, 'DELETE');
    await openDetail(currentClientId);
}

/* Tasks Page */
async function renderTasksPage() {
    const data = await api('/api/tasks');
    const el = document.getElementById('tasksPage');
    const sections = [
        { key: 'overdue', label: 'Просрочено', cls: 'overdue-color', dot: 'var(--danger)' },
        { key: 'today', label: 'Сегодня', cls: 'today-color', dot: 'var(--success)' },
        { key: 'week', label: 'На неделе', cls: 'week-color', dot: 'var(--accent)' },
        { key: 'later', label: 'Позже', cls: '', dot: 'var(--text-muted)' },
    ];
    el.innerHTML = sections.map(s => `
        <div class="task-section">
            <h3><span class="task-section-dot" style="background:${s.dot}"></span> ${s.label} <span class="ts-count">${(data[s.key] || []).length}</span></h3>
            ${(data[s.key] || []).map(t => `
                <div class="task-card" onclick="openDetail(${t.client_id})" style="cursor:pointer">
                    <div class="task-client">${esc(t.client_name)}</div>
                    <div class="task-title">${esc(t.title)}</div>
                    ${t.due_date ? `<div class="task-date ${s.cls}">${fmtDateShort(t.due_date)}</div>` : ''}
                </div>
            `).join('') || '<div style="font-size:13px;color:var(--text-muted)">Нет задач</div>'}
        </div>
    `).join('');
}

/* Dashboard */
async function renderDashboard() {
    const data = await api('/api/dashboard');
    const el = document.getElementById('dashboard');
    const monthNames = ['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек'];
    const maxMonthly = Math.max(...data.monthly_clients.map(m => m.count), 1);
    el.innerHTML = `
        <div class="dashboard-card">
            <h3>Всего клиентов</h3>
            <div class="big-stat">${data.total_clients}</div>
            <div class="big-stat-label">За 7 дней: +${data.recent_clients}</div>
        </div>
        <div class="dashboard-card">
            <h3>Финансы</h3>
            <div class="stat-row"><span>Общий бюджет</span><span class="stat-count">${fmtMoney(data.total_budget)}</span></div>
            <div class="stat-row"><span>Средний бюджет</span><span class="stat-count">${fmtMoney(data.avg_budget)}</span></div>
            <div style="margin-top:8px;font-size:12px;color:var(--text-muted)">По этапам:</div>
            ${data.budget_by_stage.filter(s => s.budget > 0).map(s => `
                <div class="stat-row" style="font-size:13px"><span class="stat-label"><span class="stat-dot" style="background:${s.color}"></span>${esc(s.name)}</span><span class="stat-count">${fmtMoney(s.budget)}</span></div>
            `).join('')}
        </div>
        <div class="dashboard-card">
            <h3>По этапам</h3>
            ${data.stage_distribution.map(s => `
                <div class="stat-row"><span class="stat-label"><span class="stat-dot" style="background:${s.color}"></span>${esc(s.name)}</span><span class="stat-count">${s.count}</span></div>
            `).join('')}
        </div>
        <div class="dashboard-card">
            <h3>По источникам</h3>
            ${data.source_distribution.map(s => `
                <div class="stat-row"><span class="stat-label">${esc(s.name)}</span><span class="stat-count">${s.count}</span></div>
            `).join('')}
        </div>
        <div class="dashboard-card">
            <h3>Задачи</h3>
            <div style="display:flex;gap:24px;margin-bottom:12px">
                <div><div class="big-stat" style="font-size:28px">${data.total_tasks}</div><div class="big-stat-label">Всего</div></div>
                <div><div class="big-stat" style="font-size:28px;color:var(--success)">${data.completed_tasks}</div><div class="big-stat-label">Вып-но</div></div>
                <div><div class="big-stat" style="font-size:28px;color:var(--danger)">${data.overdue_tasks}</div><div class="big-stat-label">Просрочка</div></div>
            </div>
            <div class="stat-row"><span>Без задач</span><span class="stat-count">${data.no_task_clients}</span></div>
            <div style="margin-top:8px;font-size:12px;color:var(--text-muted)">По этапам:</div>
            ${data.task_completion_by_stage.filter(s => s.total > 0).map(s => {
                const pct = s.total > 0 ? Math.round(s.completed / s.total * 100) : 0;
                return `<div class="stat-row" style="font-size:13px"><span class="stat-label"><span class="stat-dot" style="background:${s.color}"></span>${esc(s.name)}</span><span class="stat-count">${s.completed}/${s.total} (${pct}%)</span></div>`;
            }).join('')}
        </div>
        <div class="dashboard-card">
            <h3>Ответственные</h3>
            ${data.by_responsible.map(r => `
                <div class="stat-row"><span>${esc(r.name)}</span><span class="stat-count">${r.count}</span></div>
            `).join('') || '<div style="font-size:13px;color:var(--text-muted)">Нет данных</div>'}
        </div>
        <div class="dashboard-card">
            <h3>Активность</h3>
            <div class="stat-row"><span>Активны за 7 дней</span><span class="stat-count" style="color:var(--success)">${data.active_7d}</span></div>
            <div class="stat-row"><span>Активны за 30 дней</span><span class="stat-count" style="color:var(--primary)">${data.active_30d}</span></div>
            <div class="stat-row"><span>Неактивны (нет записей)</span><span class="stat-count" style="color:var(--danger)">${data.inactive_clients}</span></div>
        </div>
        <div class="dashboard-card">
            <h3>Теги</h3>
            ${data.tag_distribution.map(t => `
                <div class="stat-row"><span class="stat-label"><span class="stat-dot" style="background:${t.color}"></span>${esc(t.name)}</span><span class="stat-count">${t.count}</span></div>
            `).join('') || '<div style="font-size:13px;color:var(--text-muted)">Нет тегов</div>'}
        </div>
        <div class="dashboard-card">
            <h3>Новые клиенты по месяцам</h3>
            <div style="display:flex;align-items:end;gap:6px;height:100px;padding-top:10px">
                ${data.monthly_clients.map(m => {
                    const h = Math.max(4, Math.round(m.count / maxMonthly * 80));
                    return `<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:4px">
                        <div style="font-size:11px;font-weight:600;color:var(--primary)">${m.count}</div>
                        <div style="width:100%;background:var(--primary);border-radius:4px 4px 0 0;height:${h}px;min-height:4px;transition:height 0.3s"></div>
                        <div style="font-size:10px;color:var(--text-muted)">${monthNames[m.month-1]}<br>${m.year}</div>
                    </div>`;
                }).join('')}
            </div>
        </div>
    `;
}

/* Calendar */
let calYear, calMonth;
function renderCalendar() {
    const now = new Date();
    calYear = calYear || now.getFullYear();
    calMonth = calMonth !== undefined ? calMonth : now.getMonth();
    const el = document.getElementById('calendarPage');
    el.innerHTML = `
        <div class="calendar-nav">
            <button class="btn btn-sm" onclick="calMove(-1)">←</button>
            <h2>${new Date(calYear, calMonth).toLocaleString('ru-RU', { month: 'long', year: 'numeric' })}</h2>
            <button class="btn btn-sm" onclick="calMove(1)">→</button>
            <button class="btn btn-sm" onclick="calToday()">Сегодня</button>
        </div>
        <div class="calendar-grid" id="calGrid"></div>
    `;
    loadCalendar();
}
function calMove(d) { calMonth += d; if (calMonth > 11) { calMonth = 0; calYear++; } if (calMonth < 0) { calMonth = 11; calYear--; } renderCalendar(); }
function calToday() { const n = new Date(); calYear = n.getFullYear(); calMonth = n.getMonth(); renderCalendar(); }

async function loadCalendar() {
    const data = await api(`/api/calendar/tasks?year=${calYear}&month=${calMonth + 1}`);
    const grid = document.getElementById('calGrid');
    const wd = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
    const first = new Date(calYear, calMonth, 1).getDay();
    const startOffset = first === 0 ? 6 : first - 1;
    const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
    const daysInPrev = new Date(calYear, calMonth, 0).getDate();
    const today = new Date();
    const isToday = (d, m) => d === today.getDate() && m === today.getMonth() && calYear === today.getFullYear();

    let html = wd.map(d => `<div class="calendar-weekday">${d}</div>`).join('');
    // Fill leading empty cells from previous month
    for (let i = startOffset - 1; i >= 0; i--) {
        html += `<div class="calendar-day"></div>`;
    }
    for (let d = 1; d <= daysInMonth; d++) {
        const dayTasks = data.days[d] || [];
        let tasksHtml = '';
        if (dayTasks.length > 0) {
            tasksHtml = '<div class="day-tasks">' + dayTasks.slice(0, 4).map(t => {
                const overdue = !t.completed && t.due_date && new Date(t.due_date) < new Date();
                const cls = (t.completed ? 'done' : '') + (overdue ? ' overdue' : '');
                return `<div class="day-task ${cls}" onclick="event.stopPropagation();openDetail(${t.client_id})"><span class="task-client">${esc(t.client_name)}</span> ${esc(t.title)}</div>`;
            }).join('') + '</div>';
            if (dayTasks.length > 4) tasksHtml += `<div style="font-size:10px;color:var(--text-muted);padding:1px 4px">+${dayTasks.length - 4} ещё</div>`;
        }
        const cls = isToday(d, calMonth) ? 'calendar-day today' : 'calendar-day';
        html += `<div class="${cls}"><div class="day-num">${d}</div>${tasksHtml}</div>`;
    }
    // No trailing cells – grid ends with last day of month
    grid.innerHTML = html;
}

async function showDayTasks(day) {
    const data = await api(`/api/calendar/tasks?year=${calYear}&month=${calMonth + 1}`);
    const tasks = data.days[day] || [];
    if (tasks.length === 0) return;
    const msg = tasks.map(t => `${t.client_name}: ${t.title}${t.completed ? ' ✓' : ''}`).join('\n');
    alert(msg);
}

/* Backup/Restore */
async function doRestore() {
    const fileInput = document.getElementById('restoreFile');
    if (!fileInput.files[0]) { alert('Выберите файл backup'); return; }
    if (!confirm('Восстановление перезапишет текущую БД. Продолжить?')) return;
    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    const res = await api('/api/restore', 'POST', fd);
    alert(res.message);
}

/* Merge */
async function findDuplicates() {
    const el = document.getElementById('mergeResult');
    el.innerHTML = 'Поиск...';
    try {
        const groups = await api('/api/clients/duplicates');
        if (groups.length === 0) { el.innerHTML = '<div style="color:var(--text-muted)">Дубликаты не найдены</div>'; return; }
        el.innerHTML = groups.map(g => {
            const cs = g.clients.map(c => `
                <div class="merge-client">
                    <div class="mc-info">
                        <strong>${esc(c.name)}</strong>
                        <div class="mc-meta">${esc(c.phone)}${c.organization ? ' · ' + esc(c.organization) : ''} · создан ${fmtDateShort(c.created_at)}</div>
                    </div>
                    <div class="mc-actions">
                        ${g.clients.filter(x => x.id !== c.id).map(x => `<button class="btn btn-sm btn-primary" onclick="mergeClients(${c.id},${x.id})">Оставить этого, объединить #${x.id}</button>`).join('')}
                    </div>
                </div>
            `).join('');
            return `<div class="merge-group"><h4>${esc(g.field)}</h4>${cs}</div>`;
        }).join('');
    } catch (err) {
        el.innerHTML = `<div style="color:var(--danger)">Ошибка: ${esc(err.message)}</div>`;
    }
}

async function mergeClients(keepId, mergeId) {
    if (!confirm(`Объединить клиента #${mergeId} в #${keepId}?`)) return;
    await api('/api/clients/merge', 'POST', { keep_id: keepId, merge_id: mergeId });
    await findDuplicates();
    await loadData();
}

/* Export */
function exportCSV() { window.open('/api/export/csv', '_blank'); }

/* Settings modal */
function openSettingsModal() {
    document.getElementById('settingsDarkTheme').checked = document.body.classList.contains('dark');
    document.getElementById('settingsNotifications').checked = localStorage.getItem('crm-notif') !== '0';
    document.getElementById('settingsModal').classList.remove('hidden');
}

/* Dark theme */
function updateThemeIcon() {
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = document.body.classList.contains('dark') ? '\u2600' : '\u263E';
}
function toggleDark() {
    document.body.classList.toggle('dark');
    const cb = document.getElementById('settingsDarkTheme');
    if (cb) cb.checked = document.body.classList.contains('dark');
    localStorage.setItem('crm-dark', document.body.classList.contains('dark') ? '1' : '');
    updateThemeIcon();
}
function toggleDarkQuick() {
    toggleDark();
}
if (localStorage.getItem('crm-dark')) {
    document.body.classList.add('dark');
}
updateThemeIcon();

/* Notifications */
function toggleNotifications() {
    const on = document.getElementById('settingsNotifications').checked;
    localStorage.setItem('crm-notif', on ? '1' : '0');
    if (on) {
        if (Notification.permission === 'default') Notification.requestPermission();
        checkTasks();
    }
}

let notifiedTasks = new Set(JSON.parse(localStorage.getItem('crm-notified') || '[]'));

async function checkTasks() {
    if (localStorage.getItem('crm-notif') === '0') return;
    if (Notification.permission === 'default') Notification.requestPermission();
    if (Notification.permission !== 'granted') return;
    try {
        const data = await api('/api/tasks');
        const now = new Date();
        const allTasks = [...(data.overdue || []), ...(data.today || []), ...(data.week || []), ...(data.later || [])];
        allTasks.forEach(t => {
            if (!t.due_date || t.completed) return;
            const due = new Date(t.due_date);
            const diffMs = due.getTime() - now.getTime();
            const diffMin = Math.round(diffMs / 60000);
            const taskKey = `${t.id}-${t.due_date}`;
            if (notifiedTasks.has(taskKey)) return;
            if (diffMin <= 0 && diffMin > -2) {
                // Due now (within 2 min window)
                showNotification(`Задача просрочена: ${t.title}`, t.client_name);
                notifiedTasks.add(taskKey);
            } else if (diffMin > 0 && diffMin <= 15) {
                // Due within 15 min
                showNotification(`Через ${diffMin} мин: ${t.title}`, t.client_name);
                notifiedTasks.add(taskKey);
            }
        });
        localStorage.setItem('crm-notified', JSON.stringify([...notifiedTasks]));
    } catch {}
}

function showNotification(body, title) {
    try {
        new Notification(title || 'CRM', { body });
    } catch {}
    playBeep();
}

function playBeep() {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.frequency.value = 800; gain.gain.value = 0.3;
        osc.start(); osc.stop(ctx.currentTime + 0.3);
    } catch {}
}

/* Inline edit fields in detail modal */
function inlineEdit(el) {
    if (el.tagName === 'INPUT' || el.tagName === 'SELECT') return;
    const field = el.dataset.field;
    const id = currentClientId;
    if (!id) return;
    let currentValue = el.textContent;
    if (currentValue === '—') currentValue = '';
    if (field === 'deal_name') currentValue = document.getElementById('detailDealName').textContent;
    if (field === 'name') currentValue = document.getElementById('detailContactName').textContent;

    if (field === 'stage_id') {
        const sel = document.createElement('select');
        stages.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id; opt.textContent = s.name;
            if (s.name === el.textContent || s.id === currentClientData?.stage_id) opt.selected = true;
            sel.appendChild(opt);
        });
        el.innerHTML = '';
        el.appendChild(sel);
        sel.focus();
        const doSave = async () => {
            const val = Number(sel.value);
            const stage = stages.find(s => s.id === val);
            if (stage && stage.name === 'Отказ') {
                el.innerHTML = stageName; // revert
                showRejectionModal(id, val);
                return;
            }
            el.innerHTML = stage ? stage.name : '—';
            await api(`/api/clients/${id}`, 'PUT', { stage_id: val });
            await loadData();
            openDetail(id);
        };
        sel.addEventListener('change', doSave);
        sel.addEventListener('blur', doSave);
        sel.addEventListener('keydown', e => { if (e.key === 'Escape') { el.innerHTML = stageName; } });
        const stageName = el.textContent;
        return;
    }

    if (field === 'source') {
        const sel = document.createElement('select');
        const sourceNames = sources.map(s => s.name);
        sourceNames.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s; opt.textContent = s;
            if (s === currentValue) opt.selected = true;
            sel.appendChild(opt);
        });
        el.innerHTML = '';
        el.appendChild(sel);
        sel.focus();
        const doSave = async () => {
            const val = sel.value;
            el.textContent = val || '—';
            await api(`/api/clients/${id}`, 'PUT', { source: val });
            await loadData();
        };
        sel.addEventListener('change', doSave);
        sel.addEventListener('blur', doSave);
        sel.addEventListener('keydown', e => { if (e.key === 'Escape') { el.innerHTML = currentValue || '—'; } });
        return;
    }

    if (field === 'budget') {
        const inp = document.createElement('input');
        inp.type = 'number';
        inp.value = currentValue.replace(/\s/g, '');
        inp.style.width = '120px';
        el.innerHTML = '';
        el.appendChild(inp);
        inp.focus(); inp.select();
        const doSave = async () => {
            const val = Number(inp.value) || 0;
            el.textContent = val > 0 ? fmtMoney(val) : '—';
            await api(`/api/clients/${id}`, 'PUT', { budget: val });
            await loadData();
        };
        inp.addEventListener('blur', doSave);
        inp.addEventListener('keydown', e => {
            if (e.key === 'Enter') { inp.blur(); }
            if (e.key === 'Escape') { el.textContent = currentValue || '—'; }
        });
        return;
    }

    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = currentValue;
    if (field === 'deal_name') inp.style.fontSize = '20px'; else if (field === 'name') inp.style.fontSize = '16px'; else inp.style.fontSize = '14px';
    inp.style.width = field === 'deal_name' || field === 'name' ? '300px' : '200px';
    el.innerHTML = '';
    el.appendChild(inp);
    inp.focus(); inp.select();

    const doSave = async () => {
        const val = inp.value.trim();
        el.textContent = val || '—';
        if (field === 'deal_name') { document.getElementById('detailName').textContent = val || client?.name || '—'; document.getElementById('detailDealName').textContent = val || '—'; }
        if (field === 'name') { document.getElementById('detailContactName').textContent = val || '—'; if (!document.getElementById('detailDealName').textContent || document.getElementById('detailDealName').textContent === '—') { document.getElementById('detailName').textContent = val || '—'; } }
        const data = {};
        data[field] = val;
        await api(`/api/clients/${id}`, 'PUT', data);
        await loadData();
    };
    inp.addEventListener('blur', doSave);
    inp.addEventListener('keydown', e => {
        if (e.key === 'Enter') { inp.blur(); }
        if (e.key === 'Escape') { el.textContent = currentValue || '—'; if (field === 'deal_name') document.getElementById('detailName').textContent = currentValue || currentClientData?.name || '—'; if (field === 'name') document.getElementById('detailContactName').textContent = currentValue || '—'; }
    });
}

/* ── Ads Page ── */
let adsRange = '30d';

function setAdsRange(range) {
    adsRange = range;
    document.querySelectorAll('.date-selector .btn').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector(`.date-selector .btn[data-range="${range}"]`);
    if (btn) btn.classList.add('active');
    const now = new Date();
    let from = new Date(), to = new Date();
    switch (range) {
        case 'today':
            from = now;
            break;
        case '7d':
            from = new Date(now.getTime() - 7 * 86400000);
            break;
        case '30d':
            from = new Date(now.getTime() - 30 * 86400000);
            break;
        case 'week': {
            const day = now.getDay();
            const diff = day === 0 ? 6 : day - 1;
            from = new Date(now.getTime() - diff * 86400000);
            break;
        }
        case 'month':
            from = new Date(now.getFullYear(), now.getMonth(), 1);
            break;
    }
    document.getElementById('adsDateFrom').value = fmtInputDate(from);
    document.getElementById('adsDateTo').value = fmtInputDate(to);
    refreshAds();
}

function fmtInputDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}

let adsLoading = false;
let adsData = [];
let adsSortCol = null;
let adsSortDir = 1;

async function refreshAds() {
    if (adsLoading) return;
    adsLoading = true;
    const tbody = document.getElementById('adsBody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="11" class="ads-loading">Загрузка...</td></tr>';
    try {
        const dateFrom = document.getElementById('adsDateFrom').value;
        const dateTo = document.getElementById('adsDateTo').value;
        const data = await api(`/api/avito/items?date_from=${dateFrom}&date_to=${dateTo}`);
        adsData = data;
        applyAdsSort();
    } catch (e) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="11" class="ads-empty">Ошибка загрузки: ${esc(e.message)}</td></tr>`;
    } finally {
        adsLoading = false;
    }
}

function adsSort(col) {
    if (adsSortCol === col) { adsSortDir *= -1; } else { adsSortCol = col; adsSortDir = 1; }
    applyAdsSort();
    document.querySelectorAll('.ads-table th').forEach(th => th.classList.remove('sort-asc', 'sort-desc'));
    const th = document.querySelector(`.ads-table th[data-col="${col}"]`);
    if (th) th.classList.add(adsSortDir === 1 ? 'sort-asc' : 'sort-desc');
}

function applyAdsSort() {
    const tbody = document.getElementById('adsBody');
    if (!tbody) return;
    if (!adsData || adsData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="11" class="ads-empty">Объявления не найдены. Нажмите «Синхронизировать объявления»</td></tr>';
        return;
    }
    // Stats date
    const dates = adsData.map(a => a.stats_updated_at).filter(Boolean);
    const dateEl = document.getElementById('adsStatsDate');
    if (dateEl) {
        if (dates.length) {
            const latest = dates.reduce((a, b) => new Date(a) > new Date(b) ? a : b);
            const d = new Date(latest);
            dateEl.textContent = 'Статистика обновлена: ' + d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit' });
        } else {
            dateEl.textContent = 'Статистика не загружена. Нажмите «Обновить статистику»';
        }
    }
    // Totals
    let sumImp = 0, sumViews = 0, sumFav = 0, sumCont = 0, sumSpent = 0;
    let hasImp = 0, hasViews = 0, hasFav = 0, hasCont = 0, hasSpent = 0;
    for (const a of adsData) {
        if (a.impressions != null) { sumImp += a.impressions; hasImp++; }
        if (a.views != null) { sumViews += a.views; hasViews++; }
        if (a.favorites != null) { sumFav += a.favorites; hasFav++; }
        if (a.contacts != null) { sumCont += a.contacts; hasCont++; }
        if (a.spent != null) { sumSpent += a.spent; hasSpent++; }
    }
    const totalRow = document.getElementById('adsTotalRow');
    if (totalRow) {
        const avgViewCost = hasSpent && hasViews ? sumSpent / sumViews : null;
        const avgContactCost = hasSpent && hasCont ? sumSpent / sumCont : null;
        totalRow.innerHTML = '<td colspan="3" class="ads-total-label">Итого по всем объявлениям</td>'
            + `<td class="ads-total-val">${hasImp ? sumImp.toLocaleString('ru-RU') : '—'}</td>`
            + `<td class="ads-total-val">${hasViews ? sumViews.toLocaleString('ru-RU') : '—'}</td>`
            + `<td class="ads-total-val">${hasFav ? sumFav.toLocaleString('ru-RU') : '—'}</td>`
            + `<td class="ads-total-val">${hasCont ? sumCont.toLocaleString('ru-RU') : '—'}</td>`
            + `<td class="ads-total-val">${hasSpent ? fmtMoney(sumSpent) : '—'}</td>`
            + `<td class="ads-total-val">${avgViewCost != null ? fmtMoney(avgViewCost) : '—'}</td>`
            + `<td class="ads-total-val">${avgContactCost != null ? fmtMoney(avgContactCost) : '—'}</td>`
            + '<td></td>';
    }
    let data = [...adsData];
    if (adsSortCol) {
        data.sort((a, b) => {
            let va = a[adsSortCol], vb = b[adsSortCol];
            if (va == null) va = -Infinity;
            if (vb == null) vb = -Infinity;
            if (typeof va === 'string') return adsSortDir * va.localeCompare(vb, 'ru');
            return adsSortDir * (va - vb);
        });
    }
    tbody.innerHTML = data.map(a => {
        const stages = (a.stage_stats || []).map(s =>
            `<span class="stage-dot" style="background:${esc(s.color)}"></span>${esc(s.stage_name)}: ${s.count}`
        ).join(' ');
        return `<tr>
            <td><a href="${esc(a.url)}" target="_blank">${esc(a.title)}</a></td>
            <td>${esc(a.address)}</td>
            <td>${a.placed_at ? fmtDateShort(a.placed_at) : '—'}</td>
            <td>${a.impressions != null ? a.impressions.toLocaleString('ru-RU') : '—'}</td>
            <td>${a.views != null ? a.views.toLocaleString('ru-RU') : '—'}</td>
            <td>${a.favorites != null ? a.favorites.toLocaleString('ru-RU') : '—'}</td>
            <td>${a.contacts != null ? a.contacts.toLocaleString('ru-RU') : '—'}</td>
            <td>${a.spent != null ? fmtMoney(a.spent) : '—'}</td>
            <td>${a.price_per_view != null ? fmtMoney(a.price_per_view) : '—'}</td>
            <td>${a.price_per_contact != null ? fmtMoney(a.price_per_contact) : '—'}</td>
            <td>${stages || '—'}</td>
        </tr>`;
    }).join('');
}

async function renderAds() {
    const el = document.getElementById('adsPage');
    if (!el) return;
    let status, statsInfo;
    try { status = await api('/api/avito/status'); } catch { status = { connected: false }; }
    if (!status.connected) {
        el.innerHTML = '<div class="ads-empty">Подключите Avito в разделе <a href="#" onclick="switchTab(\'integrations\');return false">Интеграции</a></div>';
        return;
    }
    try { statsInfo = await api('/api/avito/stats-info'); } catch { statsInfo = { has_data: false }; }
    const statsBtn = statsInfo.has_data
        ? '<button class="btn" onclick="refreshAvitoStats()">Обновить статистику</button>'
        : '<button class="btn" onclick="showSyncStatsModal()">Загрузить статистику</button>';
    el.innerHTML = `
        <div class="ads-toolbar">
            <button class="btn" onclick="syncAvitoItems()">Синхронизировать объявления</button>
            ${statsBtn}
            <span id="adsSyncStatus"></span>
        </div>
        <div class="date-selector">
            <button class="btn btn-sm" data-range="today" onclick="setAdsRange('today')">День</button>
            <button class="btn btn-sm" data-range="7d" onclick="setAdsRange('7d')">7 дней</button>
            <button class="btn btn-sm active" data-range="30d" onclick="setAdsRange('30d')">30 дней</button>
            <button class="btn btn-sm" data-range="week" onclick="setAdsRange('week')">Эта неделя</button>
            <button class="btn btn-sm" data-range="month" onclick="setAdsRange('month')">Этот месяц</button>
            <span class="date-custom">
                <input type="date" id="adsDateFrom">
                <input type="date" id="adsDateTo">
                <button class="btn btn-sm" onclick="refreshAds()">OK</button>
            </span>
        </div>
        <div class="ads-stats-date" id="adsStatsDate"></div>
        <table class="ads-table">
            <thead>
                <tr>
                    <th data-col="title" onclick="adsSort('title')">Название</th>
                    <th data-col="address" onclick="adsSort('address')">Город</th>
                    <th data-col="placed_at" onclick="adsSort('placed_at')">Дата размещения</th>
                    <th data-col="impressions" onclick="adsSort('impressions')">Показы</th>
                    <th data-col="views" onclick="adsSort('views')">Просмотры</th>
                    <th data-col="favorites" onclick="adsSort('favorites')">Избранное</th>
                    <th data-col="contacts" onclick="adsSort('contacts')">Контакты</th>
                    <th data-col="spent" onclick="adsSort('spent')">Потрачено</th>
                    <th data-col="price_per_view" onclick="adsSort('price_per_view')">Цена просмотра</th>
                    <th data-col="price_per_contact" onclick="adsSort('price_per_contact')">Цена контакта</th>
                    <th>Этапы воронки</th>
                </tr>
                <tr id="adsTotalRow" class="ads-total-row"></tr>
            </thead>
            <tbody id="adsBody"><tr><td colspan="11" class="ads-loading">Загрузка...</td></tr></tbody>
        </table>`;
    setAdsRange(adsRange);
}

async function syncAvitoItems() {
    const statusEl = document.getElementById('adsSyncStatus');
    if (!statusEl) return;
    statusEl.textContent = 'Синхронизация...';
    try {
        const res = await api('/api/avito/sync-items', 'POST');
        statusEl.textContent = `Синхронизировано: ${res.synced} объявлений`;
        refreshAds();
    } catch (e) {
        statusEl.textContent = `Ошибка: ${e.message}`;
    }
}

async function refreshAvitoStats() {
    const statusEl = document.getElementById('adsSyncStatus');
    if (!statusEl) return;
    statusEl.textContent = 'Обновление статистики...';
    try {
        const res = await api('/api/avito/refresh-stats', 'POST');
        if (res.need_choice) {
            statusEl.textContent = '';
            showRefreshChoiceModal(res.last_date);
            return;
        }
        if (res.status === 'started') {
            statusEl.textContent = `Синхронизация запущена (${res.total_days} дней)...`;
            _pollSyncProgress(statusEl);
            return;
        }
        statusEl.textContent = `Статистика обновлена. Добавлено дней: ${res.synced_days || 0}`;
        refreshAds();
    } catch (e) {
        statusEl.textContent = `Ошибка: ${e.message}`;
    }
}

function showSyncStatsModal() {
    document.getElementById('statsSyncBody').innerHTML = `
        <p style="margin-bottom:16px">Выберите период для первичной загрузки статистики:</p>
        <p style="font-size:13px;color:var(--text-muted);margin-bottom:12px">Загрузка займёт 1–2 минуты на каждые 30 дней (ограничение API Avito — 1 запрос в минуту).</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn" onclick="startSyncStats(30)" style="flex:1">30 дней</button>
            <button class="btn" onclick="startSyncStats(90)" style="flex:1">90 дней</button>
        </div>
        <div style="display:flex;gap:8px;margin-top:16px;justify-content:flex-end">
            <button class="btn btn-secondary" onclick="closeModal('statsSyncModal')">Отмена</button>
        </div>`;
    document.getElementById('statsSyncModal').classList.remove('hidden');
}

let _syncPollTimer = null;

async function startSyncStats(days) {
    closeModal('statsSyncModal');
    const statusEl = document.getElementById('adsSyncStatus');
    if (statusEl) statusEl.textContent = 'Загрузка статистики...';
    try {
        const res = await api(`/api/avito/sync-stats?days=${days}`, 'POST');
        if (res.status === 'started') {
            if (statusEl) statusEl.textContent = `Синхронизация запущена (${res.total_days} дней)...`;
            _pollSyncProgress(statusEl);
        }
    } catch (e) {
        if (statusEl) statusEl.textContent = `Ошибка: ${e.message}`;
    }
}

function showRefreshChoiceModal(lastDate) {
    const dateInfo = lastDate ? `Последние данные от ${esc(lastDate)}.` : 'Данные отсутствуют.';
    document.getElementById('statsSyncBody').innerHTML = `
        <p style="margin-bottom:12px">${dateInfo} Период больше 30 дней. Выберите период для обновления:</p>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn" onclick="refreshWithMode('month_begin')" style="flex:1">С 1 числа текущего месяца</button>
            <button class="btn" onclick="refreshWithMode('30d')" style="flex:1">30 дней</button>
            <button class="btn" onclick="refreshWithMode('90d')" style="flex:1">90 дней</button>
        </div>
        <div style="display:flex;gap:8px;margin-top:16px;justify-content:flex-end">
            <button class="btn btn-secondary" onclick="closeModal('statsSyncModal')">Отмена</button>
        </div>`;
    document.getElementById('statsSyncModal').classList.remove('hidden');
}

async function refreshWithMode(mode) {
    closeModal('statsSyncModal');
    const statusEl = document.getElementById('adsSyncStatus');
    if (statusEl) statusEl.textContent = 'Обновление статистики...';
    try {
        const res = await api(`/api/avito/refresh-stats?mode=${mode}`, 'POST');
        if (res.status === 'started') {
            if (statusEl) statusEl.textContent = `Синхронизация запущена (${res.total_days} дней)...`;
            _pollSyncProgress(statusEl);
        }
    } catch (e) {
        if (statusEl) statusEl.textContent = `Ошибка: ${e.message}`;
    }
}

async function _pollSyncProgress(statusEl) {
    if (_syncPollTimer) clearInterval(_syncPollTimer);
    _syncPollTimer = setInterval(async () => {
        try {
            const info = await api('/api/avito/stats-info');
            if (info.sync_error) {
                statusEl.textContent = `Ошибка: ${info.sync_error}`;
                clearInterval(_syncPollTimer);
                _syncPollTimer = null;
                return;
            }
            if (info.sync_running) {
                statusEl.textContent = `Загрузка... ${info.sync_synced}/${info.sync_total} дней`;
            } else {
                statusEl.textContent = `Синхронизация завершена. Загружено дней: ${info.sync_synced}`;
                clearInterval(_syncPollTimer);
                _syncPollTimer = null;
                refreshAds();
            }
        } catch (e) {
            // ignore polling errors
        }
    }, 5000);
}

/* ── Integrations Page ── */
async function renderIntegrations() {
    const el = document.getElementById('integrationsPage');
    if (!el) { console.error('integrationsPage not found'); return; }
    let status;
    try {
        status = await api('/api/avito/status');
    } catch (e) {
        el.innerHTML = '<div style="padding:24px;color:var(--danger)">Ошибка загрузки статуса: ' + esc(e.message) + '</div>';
        return;
    }
    el.innerHTML = `
        <div class="integrations-container">
            <h2 style="margin-bottom:20px">Интеграции</h2>

            <div class="integration-card">
                <div class="int-header">
                    <span class="int-icon">🛒</span>
                    <span class="int-title">Авито</span>
                    <span class="int-badge" id="avitoBadge">${status.connected ? '✅ подключено' : '❌ не подключено'}</span>
                </div>
                <div class="int-body">
                    <div class="form-grid">
                        <div>
                            <label>Client ID</label>
                            <input type="text" id="avitoClientId" value="${esc(status.avito_client_id || '')}" placeholder="Введите Client ID">
                        </div>
                        <div>
                            <label>Client Secret</label>
                            <input type="password" id="avitoClientSecret" value="${esc(status.avito_client_secret || '')}" placeholder="Введите Client Secret">
                        </div>
                    </div>
                    <button class="btn btn-primary" onclick="saveAvitoConfig()" style="margin-top:12px">Сохранить</button>
                    <div id="avitoConfigStatus" style="margin-top:8px;font-size:13px"></div>
                    <div class="int-help" style="margin-top:16px;font-size:13px;color:var(--text-muted);line-height:1.6">
                        <strong>Как настроить:</strong><br>
                        1. Зайдите на <a href="https://www.avito.ru/professionals/api" target="_blank">avito.ru/professionals/api</a> → «Получить ключи»<br>
                        2. Скопируйте Client ID и Client Secret в поля выше и нажмите «Сохранить»<br>
                        3. Нажмите «Подключить Авито» ниже
                    </div>
                    <div style="margin-top:16px;display:flex;align-items:center;gap:12px">
                        <button class="btn btn-primary" id="intAvitoConnectBtn" onclick="connectAvito()" ${status.connected ? 'style="display:none"' : ''}>Подключить Авито</button>
                        <button class="btn btn-danger" id="intAvitoDisconnectBtn" onclick="disconnectAvito()" ${status.connected ? '' : 'style="display:none"'}>Отключить Авито</button>
                        <span style="font-size:13px;color:var(--text-muted)" id="intAvitoStatus">${status.connected ? 'user_id: ' + status.user_id : ''}</span>
                    </div>
                    ${status.connected ? '<button class="btn btn-sm" onclick="syncAvito()" style="margin-top:12px;font-size:13px">🔄 Синхронизировать сейчас</button>' : ''}
                    <div id="avitoSyncStatus" style="margin-top:8px;font-size:13px;color:var(--text-muted)"></div>
                </div>
            </div>
        </div>
    `;
}

async function saveAvitoConfig() {
    const clientId = document.getElementById('avitoClientId').value.trim();
    const clientSecret = document.getElementById('avitoClientSecret').value.trim();
    const statusEl = document.getElementById('avitoConfigStatus');
    try {
        await api('/api/config', 'PUT', { avito_client_id: clientId, avito_client_secret: clientSecret });
        statusEl.innerHTML = '<span style="color:var(--success)">✅ Настройки сохранены</span>';
    } catch (err) {
        statusEl.innerHTML = '<span style="color:var(--danger)">❌ ' + esc(err.message) + '</span>';
    }
}

/* ── Avito background sync ── */
let avitoPollTimer = null;

function startAvitoSyncTimer() {
    if (avitoPollTimer) clearInterval(avitoPollTimer);
    avitoPollTimer = setInterval(async () => {
        try {
            await api('/api/avito/sync', 'POST');
            renderPipeline();
        } catch (e) {
            // silently ignore
        }
    }, 60000);
}

function stopAvitoSyncTimer() {
    if (avitoPollTimer) {
        clearInterval(avitoPollTimer);
        avitoPollTimer = null;
    }
}

async function syncAvito() {
    const el = document.getElementById('avitoSyncStatus');
    if (el) el.textContent = '⏳ Синхронизация...';
    try {
        await api('/api/avito/sync', 'POST');
        if (el) el.innerHTML = '<span style="color:var(--success)">✅ Синхронизация выполнена</span>';
    } catch (err) {
        if (el) el.innerHTML = '<span style="color:var(--danger)">❌ ' + esc(err.message) + '</span>';
    }
}

/* Avito reply from deal card */
async function sendAvitoReplyFromCard(e) {
    e.preventDefault();
    const inp = document.getElementById('avitoReplyInput');
    if (!inp || !inp.value.trim()) return;
    const chatId = document.getElementById('avitoChatReply').dataset.chatId;
    if (!chatId) { alert('Чат Авито не найден'); return; }
    const content = inp.value.trim();
    inp.value = '';
    try {
        await api('/api/avito/chats/' + chatId + '/messages', 'POST', { content });
        // Refresh
        const clientId = currentClientId;
        if (clientId) {
            const avitoMsgs = await api('/api/avito/client/' + clientId + '/messages');
            const avitoChat = await api('/api/avito/client/' + clientId + '/chat');
            const client = await api('/api/clients/' + clientId);
            let attachments = [];
            try { attachments = await api('/api/clients/' + clientId + '/attachments'); } catch {}
            renderTimeline(client, attachments, avitoMsgs, avitoChat);
        }
    } catch (err) {
        alert('Ошибка отправки: ' + err.message);
        inp.value = content;
    }
}

/* Avito timeline reply helpers */
function toggleAvitoReply(el) {
    const msgId = el.dataset.msgId;
    const box = document.getElementById('avitoReply-' + msgId);
    if (!box) return;
    box.classList.toggle('hidden');
    if (!box.classList.contains('hidden')) {
        const inp = box.querySelector('input');
        if (inp) setTimeout(() => inp.focus(), 100);
    }
}

async function sendAvitoReply(e, chatId, msgId) {
    e.preventDefault();
    const inp = e.target.querySelector('input');
    if (!inp || !inp.value.trim()) return;
    const content = inp.value.trim();
    inp.value = '';
    if (!chatId) { alert('Чат Авито не найден'); return; }
    await api('/api/avito/chats/' + chatId + '/messages', 'POST', { content });
    // Refresh timeline messages
    const clientId = currentClientId;
    if (clientId) {
        const avitoMsgs = await api('/api/avito/client/' + clientId + '/messages');
        const avitoChat = await api('/api/avito/client/' + clientId + '/chat');
        const client = await api('/api/clients/' + clientId);
        let attachments = [];
        try { attachments = await api('/api/clients/' + clientId + '/attachments'); } catch {}
        renderTimeline(client, attachments, avitoMsgs, avitoChat);
    }
}

/* Avito settings modal helpers */
async function connectAvito() {
    try {
        await api('/api/avito/connect', 'POST');
        startAvitoSyncTimer();
        renderIntegrations();
    } catch (err) {
        alert('Ошибка подключения: ' + err.message);
    }
}

async function disconnectAvito() {
    try {
        await api('/api/avito/disconnect', 'POST');
        stopAvitoSyncTimer();
        renderIntegrations();
    } catch (err) {
        alert('Ошибка: ' + err.message);
    }
}

if (localStorage.getItem('crm-notif') !== '0' && Notification.permission === 'default') {
    Notification.requestPermission();
}
setInterval(checkTasks, 30000);
setTimeout(checkTasks, 5000);

/* Init */
loadData();

/* Start Avito background sync if already connected */
api('/api/avito/status').then(s => { if (s.connected) startAvitoSyncTimer(); }).catch(() => {});
