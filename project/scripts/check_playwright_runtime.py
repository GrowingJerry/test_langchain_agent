"""Offline-safe Chromium self-check with local interaction and network blocking."""
from pathlib import Path
import os,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from application.services.offline_site_service import explore_site,playwright_health

root=Path(__file__).resolve().parents[1]
browser_root=root/"vendor"/"playwright-browsers"
os.environ["PLAYWRIGHT_BROWSERS_PATH"]=str(browser_root)
health=playwright_health(browser_root)
if not health.get("available"): raise SystemExit(f"Playwright check failed: {health}")
with tempfile.TemporaryDirectory() as folder:
    site=Path(folder); (site/"index.html").write_text("<label for='name'>姓名</label><input id='name'><button onclick=\"document.body.dataset.ok='1'\">保存</button><img src='https://example.com/x.png'>",encoding="utf-8")
    result=explore_site(site,"index.html",[{"action":"input","target":{"role":"textbox","accessible_name":"姓名"},"value":"测试"}],site/"evidence")
    if not result["events"] or not result["blocked_requests"] or not list((site/"evidence").glob("*.png")): raise SystemExit("Playwright interaction/network/screenshot check failed")
print({**health,"external_requests_blocked":len(result["blocked_requests"])})
