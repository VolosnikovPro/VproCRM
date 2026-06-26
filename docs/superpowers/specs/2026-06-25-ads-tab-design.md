# Avito Ads Tab — Design Spec v3

## Overview

New **"Объявления"** tab showing Avito ad listings with stats (impressions, views, contacts) and pipeline stage distribution for clients. Date-range selector.

## Backend

### 1. New Model: `AvitoItem`

File: `models.py`

| Column | Type | Notes |
|--------|------|-------|
| `id` | Integer PK | auto |
| `avito_item_id` | Integer, unique, nullable | Avito item ID |
| `title` | String(500) | Ad title |
| `address` | String(500) | City/location |
| `url` | String(1000) | Direct link to ad |
| `price` | Integer, nullable | Price ₽ |
| `status` | String(50) | `active`, `removed`, etc |
| `category` | String(200) | Category name |
| `placed_at` | DateTime, nullable | Placement date from Avito (raw API) or null |
| `created_at` | DateTime | First sync |
| `updated_at` | DateTime | Last sync |

### 2. `AvitoChat.avito_item_id`

`avito_item_id = Column(Integer, nullable=True, index=True)` — FK-like reference to AvitoItem (no constraint to allow nulls).

### 3. OAuth

- `POST /api/avito/connect`: add `"stats:read"` to scope
- On startup: always re-acquire token if `AvitoToken` exists

### 4. Sync Items: `POST /api/avito/sync-items`

Uses raw `httpx` (NOT SDK) to call Avito API directly:
```
GET https://api.avito.ru/core/v1/items?page=1&per_page=100
```
With stored token in `Authorization: Bearer` header. Paginate through all pages.

**Why raw httpx?** The SDK's `Item` model drops useful fields like `start_time`, `images`, etc. Raw response gives full data.

Parse JSON → extract for each item:
- `id` → `avito_item_id`
- `title`, `address`, `url` (Str), `price` (int), `status`, `category` (name)
- `start_time` / `publishedAt` / `createdAt` → `placed_at` (any date field found)

Upsert into `AvitoItem` (merge on `avito_item_id`).

**Backfill `avito_item_id` on chats**: For each `AvitoChat` with `avito_item_id IS NULL`:
- Try to extract item ID from `item_url` via regex: `avito\.ru/\w+/(\d+)$` or `/item/(\d+)`
- If found, look up matching `AvitoItem` and set the FK

**Lock**: module-level `_syncing_items = False` to prevent double-sync.

### 5. Get Items with Stats: `GET /api/avito/items?date_from=...&date_to=...`

**Step 1**: Query all `AvitoItem` from local DB.

**Step 2**: Fetch stats from Avito API (raw httpx):
```
POST https://api.avito.ru/core/v1/accounts/{user_id}/stats/items
Body: {"itemIds": [1,2,3,...], "dateFrom": "YYYY-MM-DD", "dateTo": "YYYY-MM-DD", "fields": ["views","contacts","favorites","impressions"]}
```
Parse response — expected shape:
```json
{"result": [{"itemId": 1, "views": 100, "contacts": 50, "favorites": 20, "impressions": 500}, ...]}
```
or
```json
[{"itemId": 1, "views": 100, "contacts": 50}, ...]
```

Flexible parsing: try `result` then fallback to root array. Try field names: `itemId`, `item_id`, `id` for item ID; `views`, `impressions` for views; `contacts`, `contactsTotal` for contacts.

If the endpoint 404s or returns an error, return items with all stats as `null`.

**Step 3**: Pipeline stages (local SQL):
```sql
SELECT ps.name, ps.color, COUNT(*) as count
FROM avito_chats ac
JOIN clients c ON ac.client_id = c.id
JOIN pipeline_stages ps ON c.stage_id = ps.id
WHERE ac.avito_item_id = ?
GROUP BY ps.id
```

**Step 4**: Merge and return.

### 6. Sync function update

`_sync_avito_chats()`: add `existing.avito_item_id = item.id if item else None`

### 7. DB Migration

```python
try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE avito_chats ADD COLUMN avito_item_id INTEGER DEFAULT NULL"))
        conn.commit()
except Exception:
    pass
```

### 8. Response Schema

```json
[
  {
    "avito_item_id": 12345,
    "title": "iPhone 15 Pro",
    "address": "Москва",
    "url": "https://avito.ru/...",
    "price": 75000,
    "status": "active",
    "category": "Телефоны",
    "placed_at": "2026-05-01T10:00:00",
    "impressions": 1500,
    "views": 230,
    "contacts": 45,
    "stage_stats": [
      {"stage_name": "Новые", "color": "#3b82f6", "count": 12},
      {"stage_name": "Переговоры", "color": "#f59e0b", "count": 8}
    ]
  }
]
```

If stats API fails, all stats fields are `null` (rendered as `—`).

## Frontend

### 1. Tab Button + Content

```html
<button class="tab" data-tab="ads" onclick="switchTab('ads')">Объявления</button>
<div class="tab-content" id="tab-ads"><div id="adsPage"></div></div>
```

### 2. Date Selector

```html
<div class="date-selector">
  <button class="btn btn-sm active" data-range="today">День</button>
  <button class="btn btn-sm" data-range="7d">7 дней</button>
  <button class="btn btn-sm" data-range="30d">30 дней</button>
  <button class="btn btn-sm" data-range="week">Эта неделя</button>
  <button class="btn btn-sm" data-range="month">Этот месяц</button>
  <span class="date-custom">
    <input type="date" id="adsDateFrom">
    <input type="date" id="adsDateTo">
    <button class="btn btn-sm" onclick="refreshAds()">OK</button>
  </span>
</div>
```

Quick-select buttons compute range, set the date inputs, and call `refreshAds()`.

### 3. Sync Button + Table

```html
<div class="ads-toolbar">
  <button class="btn" onclick="syncAvitoItems()">Синхронизировать объявления</button>
  <span id="adsSyncStatus"></span>
</div>
<table class="ads-table">
  <thead>
    <tr>
      <th>Название</th>
      <th>Город</th>
      <th>Дата размещения</th>
      <th>Показы</th>
      <th>Просмотры</th>
      <th>Контакты</th>
      <th>Этапы воронки</th>
    </tr>
  </thead>
  <tbody id="adsBody"></tbody>
</table>
```

Stage column: `<span class="stage-dot" style="background:COLOR"></span>Название: N`

### 4. renderAds()

Default range: "30 дней". Calls `refreshAds()`.

`refreshAds()`: reads `adsDateFrom`/`adsDateTo` → `GET /api/avito/items?date_from=X&date_to=Y`

If no items synced yet: show "Объявления не найдены. Нажмите «Синхронизировать объявления»"

If Avito not connected: show message + link to Integrations tab.

Shows loading indicator during fetch.

`syncAvitoItems()`: `POST /api/avito/sync-items`, shows "Синхронизация..." spinner, on complete refreshes.

### 5. switchTab() Update

```javascript
if (name === 'ads') renderAds();
```

## Data Flow

1. User opens tab → default "30 дней" → `GET /api/avito/items`
2. If empty → "синхронизируйте объявления"
3. User clicks sync → fetches items from Avito → stored locally
4. User changes range → re-fetches stats only
5. Stats cached server-side in memory for 30s per range key

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Avito not connected | Message + link to Integrations |
| Stats API fails/404 | Items shown, stats = `—` |
| Stats scope missing | Auto-refresh on startup, else `—` |
| Double sync click | Lock prevents concurrent sync |
| Sync fails partially | Already-synced items kept, error toast |

## Files Modified

| File | Changes |
|------|---------|
| `models.py` | New `AvitoItem` model, `AvitoChat.avito_item_id` |
| `database.py` | ALTER TABLE for `avito_chats.avito_item_id` |
| `schemas.py` | Output schema for ads response |
| `server.py` | Sync-items (raw httpx), get-items+stats, scope, startup token, sync fn update |
| `static/index.html` | Tab button + content div |
| `static/app.js` | `renderAds()`, date selector, sync handler |
| `static/style.css` | Ads table + date selector styles |
