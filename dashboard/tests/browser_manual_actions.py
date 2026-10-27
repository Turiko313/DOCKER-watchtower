"""Optional UI regression checks; uses simulated Docker/Watchtower, never real containers.

Run: python dashboard/tests/browser_manual_actions.py [--browser-executable PATH]
Requires Playwright and its Chromium browser, separately from runtime dependencies.
"""
import argparse
import copy
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from test_manual_updates import container, dashboard_app


def run(browser_executable=None):
    inventory = [container("app.one"), container("app-two"),
                 container("excluded", enabled=False),
                 container("solo", "example/solo:latest")]
    states = {c["name"]: dict(name=c["name"], id="id-" + c["name"],
                             started_at="before", status="running") for c in inventory}
    update_calls, restart_calls = [], []
    mode = {"update_failure": False}

    def update(*_args, **kwargs):
        update_calls.append(kwargs["params"])
        time.sleep(.15)
        response = Mock(status_code=200)
        response.json.return_value = {"summary": dict(scanned=1, updated=0,
                                                      failed=int(mode["update_failure"]), skipped=0)}
        return response

    def restart(names):
        restart_calls.append(names)
        time.sleep(.15)
        for name in names:
            states[name]["started_at"] += "-restarted"
        return [dict(name=name, status="success") for name in names]

    patches = [patch.object(dashboard_app, "list_containers", return_value=inventory),
               patch.object(dashboard_app, "get_watchtower_metrics", return_value={}),
               patch.object(dashboard_app, "get_update_statuses", return_value={}),
               patch.object(dashboard_app.http_requests, "post", side_effect=update),
               patch.object(dashboard_app, "restart_containers", side_effect=restart),
               patch.object(dashboard_app, "container_states", side_effect=lambda names: [
                   copy.deepcopy(states[name]) for name in names])]
    for active_patch in patches:
        active_patch.start()
    server = make_server("127.0.0.1", 0, dashboard_app.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            options = dict(headless=True)
            if browser_executable:
                options["executable_path"] = browser_executable
            browser = playwright.chromium.launch(**options)
            page = browser.new_page(http_credentials=dict(username="test-user", password="test-password"),
                                    viewport=dict(width=1540, height=1100))
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            page.wait_for_load_state("networkidle")

            def button(action, target):
                return page.locator(f'form[data-action="{action}"]').filter(
                    has=page.locator(f'input[value="{target}"]')).locator("button")

            def settled():
                page.wait_for_function("() => !window.actionInProgress")
                page.wait_for_load_state("networkidle")

            group_update = button("update", "example/app:latest")
            group_restart = button("restart", "example/app:latest")
            expect(page.locator(".btn-update")).to_have_count(5)
            expect(page.locator(".btn-restart")).to_have_count(5)
            expect(group_update).to_have_css("background-color", "rgb(31, 111, 235)")
            expect(group_restart).to_have_css("background-color", "rgb(31, 111, 235)")
            expect(button("update", "excluded")).to_be_disabled()
            expect(button("restart", "excluded")).to_be_enabled()

            group_update.click()
            expect(group_restart).to_be_disabled()
            settled()
            assert update_calls == [{"container": r"^app\.one$,^app\-two$"}], update_calls
            expect(page.locator(".group-header")).to_have_attribute("data-open", "0")
            expect(group_update).to_have_attribute("data-result", "success")
            expect(group_update).to_have_css("background-color", "rgb(35, 134, 54)")
            expect(group_restart).not_to_have_attribute("data-result", "success")
            page.evaluate("() => window.refreshDashboard()")
            settled()
            expect(group_update).to_have_attribute("data-result", "success")
            page.reload()
            page.wait_for_load_state("networkidle")
            expect(group_update).to_have_attribute("data-result", "success")

            # Advancing the browser clock avoids a two-minute test delay and
            # verifies the real expiration logic across periodic table refreshes.
            page.clock.install()
            page.clock.fast_forward(119000)
            expect(group_update).to_have_attribute("data-result", "success")
            page.clock.fast_forward(2000)
            expect(group_update).not_to_have_attribute("data-result", "success")
            expect(group_update).to_have_css("background-color", "rgb(31, 111, 235)")

            page.locator(".group-header strong").click()
            mode["update_failure"] = True
            button("update", "app.one").click()
            settled()
            expect(button("update", "app.one")).to_have_attribute("data-result", "error")
            expect(button("update", "app.one")).to_have_css("background-color", "rgb(182, 35, 36)")
            page.clock.fast_forward(121000)
            expect(button("update", "app.one")).to_have_attribute("data-result", "error")
            mode["update_failure"] = False
            button("update", "app.one").click()
            settled()
            expect(button("update", "app.one")).to_have_attribute("data-result", "success")

            button("restart", "excluded").click()
            settled()
            assert restart_calls[-1] == ["excluded"], restart_calls
            expect(button("restart", "excluded")).to_have_attribute("data-result", "success")
            expect(button("update", "excluded")).to_be_disabled()
            group_restart.click()
            settled()
            assert restart_calls[-1] == ["app.one", "app-two", "excluded"], restart_calls
            expect(group_restart).to_have_attribute("data-result", "success")

            # Emulate a dashboard restart: the POST connection closes but Docker
            # reports a new StartedAt when the status endpoint becomes reachable.
            interrupted_posts = []

            def interrupt_restart(route):
                interrupted_posts.append(route.request.post_data)
                states["solo"]["started_at"] += "-reconnected"
                route.abort("connectionreset")

            page.route("**/restart", interrupt_restart)
            button("restart", "solo").click()
            expect(button("restart", "solo")).to_have_attribute("aria-busy", "true")
            page.clock.fast_forward(3000)
            settled()
            expect(button("restart", "solo")).to_have_attribute("data-result", "success")
            assert len(interrupted_posts) == 1, "Restart mutation was retried"
            page.unroute("**/restart", interrupt_restart)

            # A non-success HTTP code must never paint green even with a
            # misleading success property in the response body.
            page.route("**/update", lambda route: route.fulfill(
                status=500, json=dict(status="success", message="Invalid success")))
            button("update", "solo").click()
            settled()
            expect(button("update", "solo")).to_have_attribute("data-result", "error")
            page.unroute("**/update")

            screenshot = Path(tempfile.gettempdir()) / "watchtower-container-actions.png"
            page.screenshot(path=str(screenshot), full_page=True)
            page.set_viewport_size(dict(width=390, height=844))
            assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
            # A user can still access both first-column actions on a small screen.
            expect(group_update).to_be_visible()
            expect(group_restart).to_be_visible()
            assert not errors, errors
            print("Browser checks passed: scoped actions, colors, 120-second expiry, persistent errors, "
                  "refresh/reload, retry, reconnect without duplicate restart, HTTP errors, mobile.")
            print(f"Screenshot: {screenshot}")
            browser.close()
    finally:
        server.shutdown()
        for active_patch in reversed(patches):
            active_patch.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable")
    run(parser.parse_args().browser_executable)
