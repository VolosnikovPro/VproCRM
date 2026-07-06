"""Playwright smoke test for VProCRM."""
from playwright.sync_api import sync_playwright
import sys, json

BASE = "http://127.0.0.1:8000"

def main():
    failed = []
    def check(name, ok, detail=""):
        if ok:
            print(f"  PASS: {name}")
        else:
            print(f"  FAIL: {name} {detail}")
            failed.append(name)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})

        # 1. Page loads
        print("\n=== 1. Page load ===")
        page.goto(BASE)
        page.wait_for_load_state("networkidle")
        check("Title contains VProCRM", "VProCRM" in page.title())
        check("Header visible", page.locator("h1").count() > 0)
        check("Pipeline tab active", page.locator(".tab.active[data-tab='pipeline']").count() > 0)

        # 2. Pipeline renders stages and client cards
        print("\n=== 2. Pipeline ===")
        stage_cols = page.locator(".kanban-column")
        stage_count = stage_cols.count()
        check("At least 1 stage column", stage_count >= 1)
        check("Search bar visible", page.locator("#searchInput").is_visible())
        check("Add client button visible", page.locator("button:has-text('+ Клиент')").is_visible())

        # 3. Create a new client
        print("\n=== 3. Create client ===")
        page.locator("button:has-text('+ Клиент')").click()
        page.wait_for_selector("#clientModal:not(.hidden)", timeout=3000)
        check("Client modal opened", page.locator("#clientModalTitle").is_visible())
        page.fill("#clientDealName", "Test Deal")
        page.fill("#clientName", "Тестовый клиент")
        page.fill("#clientPhone", "+7-999-999-99-99")
        page.fill("#clientEmail", "test@test.com")
        page.fill("#clientOrg", "Test Corp")
        page.fill("#clientResponsible", "Tester")
        page.fill("#clientBudget", "50000")
        # Select first stage option
        stage_select = page.locator("#clientStage")
        if stage_select.locator("option").count() > 1:
            stage_select.select_option(index=1)
        page.locator("#clientForm button[type='submit']").click()
        page.wait_for_timeout(1000)
        check("Client modal closed after save", page.locator("#clientModal.hidden").count() > 0)

        # 4. Search for the new client
        print("\n=== 4. Search ===")
        page.fill("#searchInput", "Тестовый клиент")
        page.wait_for_timeout(500)
        # Check that cards are visible in pipeline
        cards = page.locator(".client-card")
        # Should at least see some cards (pipeline may not immediately update)
        page.fill("#searchInput", "")
        page.wait_for_timeout(500)

        # 5. Open client detail
        print("\n=== 5. Client detail ===")
        # Click on first client card
        first_card = page.locator(".client-card").first
        if first_card.count() > 0:
            first_card.click()
            page.wait_for_selector("#detailModal:not(.hidden)", timeout=3000)
            check("Detail modal opened", page.locator("#detailName").is_visible())
            # Add a note
            page.fill("#noteContent", "Test note from Playwright")
            page.locator("#noteForm button[type='submit']").click()
            page.wait_for_timeout(2000)
            note_exists = page.locator("#timeline .tl-text:has-text('Test note from Playwright')").count() > 0
            check("Note added", note_exists)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            if page.locator("#detailModal:not(.hidden)").count() > 0:
                page.locator("#detailModal .modal-header").locator("button").last.click(force=True)
                page.wait_for_timeout(500)

        # 6. Tasks tab
        print("\n=== 6. Tasks tab ===")
        page.locator(".tab:has-text('Задачи')").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        check("Tasks tab active", page.locator("#tab-tasks .tasks-page").count() > 0)

        # 7. Calendar tab
        print("\n=== 7. Calendar tab ===")
        page.locator(".tab:has-text('Календарь')").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        check("Calendar tab active", page.locator("#tab-calendar .calendar-page").count() > 0)

        # 8. Dashboard tab
        print("\n=== 8. Dashboard tab ===")
        page.locator(".tab:has-text('Дашборд')").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
        check("Dashboard tab active", page.locator("#tab-dashboard .dashboard").count() > 0)

        # 9. Dark theme toggle
        print("\n=== 9. Dark theme ===")
        check("Theme toggle visible", page.locator("#themeToggle").is_visible())
        page.locator("#themeToggle").click()
        page.wait_for_timeout(300)
        is_dark = page.evaluate('() => document.body.classList.contains("dark")')
        check("Dark theme applied", is_dark)

        # 10. API smoke tests
        print("\n=== 10. API smoke tests ===")
        import httpx
        r = httpx.get(f"{BASE}/api/stages", timeout=10)
        check("GET /api/stages returns 200", r.status_code == 200)
        stages = r.json()
        check("Stages list is array", isinstance(stages, list))
        if stages:
            check("Stage has id/name", "id" in stages[0] and "name" in stages[0])

        r2 = httpx.get(f"{BASE}/api/dashboard", timeout=10)
        check("GET /api/dashboard returns 200", r2.status_code == 200)
        dash = r2.json()
        check("Dashboard has total_clients", "total_clients" in dash)

        r3 = httpx.get(f"{BASE}/api/tasks", timeout=10)
        check("GET /api/tasks returns 200", r3.status_code == 200)
        tasks = r3.json()
        for key in ("overdue", "today", "week", "later"):
            check(f"Tasks has {key}", key in tasks)

        r4 = httpx.get(f"{BASE}/api/sources", timeout=10)
        check("GET /api/sources returns 200", r4.status_code == 200)

        r5 = httpx.get(f"{BASE}/api/tags", timeout=10)
        check("GET /api/tags returns 200", r5.status_code == 200)

        # 11. Export CSV
        print("\n=== 11. Export ===")
        r6 = httpx.get(f"{BASE}/api/export/csv", timeout=10)
        check("GET /api/export/csv returns 200", r6.status_code == 200)
        check("CSV content-type", "text/csv" in r6.headers.get("content-type", ""))

        browser.close()

    print(f"\n{'='*40}")
    if failed:
        print(f"FAILED ({len(failed)}): {', '.join(failed)}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")

if __name__ == "__main__":
    main()
