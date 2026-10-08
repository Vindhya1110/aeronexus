"""Landing website + public endpoints: open to everyone, coarse, and never shadow the API."""


def test_landing_page_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200 and "Aeronex" in r.text and "Login to view stats" in r.text
    assert client.get("/style.css").status_code == 200 and client.get("/app.js").status_code == 200


def test_api_and_docs_not_shadowed_by_site(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/v1/zones").status_code == 200
    assert client.get("/docs").status_code == 200


def test_public_config_points_at_dashboard(make_client):
    c = make_client(dashboard_url="http://example.test:9000")
    assert c.get("/v1/public/config").json() == {"product": "Aeronex", "dashboard_url": "http://example.test:9000"}


def test_public_summary_is_coarse_and_open_even_when_login_enforced(make_client):
    c = make_client(require_auth=True)
    assert c.get("/v1/zones").status_code == 401                  # detail stays behind login
    s = c.get("/v1/public/summary")
    assert s.status_code == 200
    body = s.json()
    assert len(body["zones"]) == 6 and set(body["zones"][0]) == {"zone_id", "name", "aqi", "category", "pm25"}
    assert set(body["network"]) == {"zones", "devices", "online"}
    assert "source" not in s.text and "event" not in s.text      # no attribution / events / device ids


def test_dashboard_lives_under_app_on_the_same_origin(make_client):
    c = make_client(dashboard_upstream="http://127.0.0.1:9")      # nothing listens there
    r = c.get("/app", follow_redirects=False)
    assert r.status_code in (301, 302, 307) and r.headers["location"] == "/app/"
    down = c.get("/app/")
    assert down.status_code == 503 and "Dashboard is starting" in down.text    # friendly page, not a stack trace
    assert c.get("/").status_code == 200 and c.get("/healthz").status_code == 200   # the rest of the site is unaffected


def test_login_button_target_is_same_origin(client):
    assert client.get("/v1/public/config").json()["dashboard_url"] == "/app/"


def test_site_has_no_event_branding():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    text = " ".join(p.read_text(encoding="utf-8").lower() for p in (root / "site").glob("*") if p.suffix in (".html", ".js", ".css"))
    text += (root / "dashboard" / "app.py").read_text(encoding="utf-8").lower()
    assert "techfest" not in text and "iit bombay" not in text and "innovatex" not in text


def test_home_page_has_a_login_modal_and_a_signed_in_navbar_hook(client):
    html = client.get("/").text
    assert 'id="loginModal"' in html and 'id="formIn"' in html and 'id="formUp"' in html and 'id="navAuth"' in html
    js = client.get("/app.js").text
    assert "/v1/auth/me" in js and "/v1/auth/logout" in js and "Open dashboard" in js        # signed-in state, sign-out, no second login
