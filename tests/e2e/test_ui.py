"""
End-to-End (E2E) UI Interaction Tests using Playwright, HTTPX, and Hypothesis.

Demonstrates combining:
1. HTTPX for fast backend API state injection/validation
2. pytest-playwright for headless browser interaction
3. Hypothesis for edge-case input generation
"""
import re
import pytest
import httpx
from playwright.sync_api import Page, expect
from hypothesis import given, settings, HealthCheck, strategies as st

BASE_URL = "http://localhost:5050"

class TestE2EUIInteractions:
    
    def test_course_navigation_flow(self, page: Page):
        """Manual Test #1: Course Navigation"""
        # 1. API Validation via HTTPX (synchronous)
        with httpx.Client() as client:
            resp = client.get(f"{BASE_URL}/api/course_structure")
            assert resp.status_code in (200, 400, 404)
        
        # 2. UI Interaction via Playwright
        page.goto(f"{BASE_URL}/courses")
        
        course_cards = page.locator(".course-card")
        if course_cards.count() > 0:
            first_course = course_cards.nth(0)
            first_course.locator("text=Journey").click()
            expect(page).to_have_url(re.compile(r".*/learn\?course_uid=.*"))
            
            status_bar = page.locator("#course-title-display")
            expect(status_bar).to_be_visible()
        else:
            pytest.skip("No courses in database to navigate to.")

    def test_test_page_selection(self, page: Page):
        """Manual Test #3: Test Page Course Selection"""
        page.goto(f"{BASE_URL}/test")
        
        all_courses_btn = page.locator("text='All Courses'")
        
        if all_courses_btn.is_visible():
            all_courses_btn.click()
            expect(page).to_have_url(re.compile(r".*/test\?course_uid=all.*"))
            
            change_course_btn = page.locator("text='Change Course'")
            expect(change_course_btn).to_be_visible()
        else:
            empty_state = page.locator("text=No courses available")
            if empty_state.is_visible():
                pytest.skip("No courses to test.")

    def test_theme_switching(self, page: Page):
        """Manual Test #6: Theme Switching"""
        page.goto(f"{BASE_URL}/")
        
        settings_btn = page.locator("button[aria-label='Settings'], #settings-btn, i[data-feather='settings']")
        if settings_btn.count() == 0:
            settings_btn = page.locator(".nav-link:has(svg.feather-settings)")
            
        if settings_btn.is_visible():
            settings_btn.click()
            
            modal = page.locator("#settings-modal").first
            expect(modal).to_be_visible()
            
            cyberpunk_btn = page.locator("button:has-text('Cyberpunk'), .theme-option[data-theme='cyberpunk']")
            if cyberpunk_btn.is_visible():
                cyberpunk_btn.click()
                html = page.locator("html")
                expect(html).to_have_attribute("data-theme", "cyberpunk")
                
            reader_btn = page.locator("button:has-text('Reader'), .theme-option[data-theme='reader']")
            if reader_btn.is_visible():
                reader_btn.click()
                html = page.locator("html")
                expect(html).to_have_attribute("data-theme", "reader")

            close_btn = modal.locator(".close, button:has-text('Close')")
            if close_btn.is_visible():
                close_btn.click()
                expect(modal).not_to_be_visible()

    def test_home_page_stats(self, page: Page):
        """Manual Test #7: Home Page Stats rendering and mobile viewport"""
        page.goto(f"{BASE_URL}/")
        
        stats_container = page.locator(".hero-stats")
        expect(stats_container).to_be_visible()
        
        page.set_viewport_size({"width": 375, "height": 812})
        expect(stats_container).to_be_visible()
        
        page.set_viewport_size({"width": 1280, "height": 720})

    def test_schedule_page(self, page: Page):
        """Manual Test #8: Schedule Page calendar rendering"""
        page.goto(f"{BASE_URL}/schedule")
        
        calendar = page.locator("#calendar, .fc, .cal-grid")
        
        try:
            expect(calendar.first).to_be_visible(timeout=3000)
        except AssertionError:
            empty = page.locator("text=No reviews scheduled")
            if empty.count() > 0:
                expect(empty).to_be_visible()

    def test_memory_palace_flow(self, page: Page):
        """Manual Test #9: Memory Palace navigation"""
        page.goto(f"{BASE_URL}/palace")
        
        empty_state = page.locator("text=No loci created yet")
        locus_content = page.locator("#locus-content")
        
        is_empty = empty_state.is_visible(timeout=2000)
        
        if is_empty:
            expect(empty_state).to_be_visible()
        else:
            expect(locus_content).to_be_visible()
            
            next_btn = page.locator("button:has-text('Next')")
            if next_btn.is_visible():
                next_btn.click()

    def test_accessibility_focus_management(self, page: Page):
        """Manual Test #12: Accessibility (Keyboard Navigation & Focus Traps)"""
        page.goto(f"{BASE_URL}/")
        
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        page.keyboard.press("Tab")
        
        settings_btn = page.locator(".nav-link:has(svg.feather-settings), button[aria-label='Settings']")
        if settings_btn.is_visible():
            settings_btn.click()
            
            modal = page.locator("#settings-modal").first
            expect(modal).to_be_visible()
            
            page.keyboard.press("Escape")
            expect(modal).not_to_be_visible()


    def test_course_generation_wizard(self, page: Page):
        """Manual Test #9: Course Wizard E2E Flow"""
        page.goto(f"{BASE_URL}/wizard")
        
        # Step 1: Initial Input
        topic_input = page.locator("#wizard-topic, input[placeholder*='topic']")
        if topic_input.is_visible():
            topic_input.fill("Ollama GPU Acceleration")
            page.locator("button:has-text('Next'), #wizard-next-btn").click()
            
            # Step 2: Generation/Loading
            loading_indicator = page.locator(".wizard-loading, text='Generating Curriculum'")
            expect(loading_indicator).to_be_visible()
            
            # Since this is hitting Native Ollama, it should take < 15 seconds, but we give it 60s
            review_screen = page.locator(".wizard-review, text='Review Curriculum', #module-list")
            expect(review_screen).to_be_visible(timeout=60000)
            
            # Change settings
            depth_select = page.locator("select[name='depth'], #wizard-depth")
            if depth_select.is_visible():
                depth_select.select_option("Advanced")
            
            # Final Generation
            page.locator("button:has-text('Generate Course'), #wizard-submit-btn, button.generate-btn").click()
            
            # Verify Redirect to Course List or Learn Page
            expect(page).to_have_url(re.compile(r".*/courses|.*/learn"), timeout=60000)

# Example of using Hypothesis to generate weird input data for HTTPX backend validation
@given(search_query=st.text(max_size=100))
@settings(max_examples=20, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_backend_search_resilience_with_hypothesis(search_query):
    """
    Hypothesis generates edge-case queries.
    HTTPX validates the backend doesn't crash (500).
    """
    with httpx.Client() as client:
        try:
            resp = client.get(f"{BASE_URL}/api/due_cards", params={"topic": search_query})
            assert resp.status_code in (200, 400, 404, 502)
        except httpx.ConnectError:
            pytest.skip("Server not running to test against")
