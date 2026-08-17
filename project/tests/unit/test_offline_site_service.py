from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZipInfo
import stat
import pytest
from application.services.offline_site_service import SiteLimits, UnsafeSitePackage, analyze_site, explore_site, safe_extract_zip

def make_zip(files):
    out=BytesIO()
    with ZipFile(out,"w") as zf:
        for name,value in files.items(): zf.writestr(name,value)
    return out.getvalue()

@pytest.mark.parametrize("name",["../escape.html","/absolute.html","C:/drive.html"])
def test_zip_rejects_unsafe_paths(tmp_path,name):
    with pytest.raises(UnsafeSitePackage): safe_extract_zip(make_zip({name:"x"}),tmp_path)

def test_zip_rejects_symlink_dangerous_type_and_limits(tmp_path):
    out=BytesIO()
    with ZipFile(out,"w") as zf:
        info=ZipInfo("link.html"); info.create_system=3; info.external_attr=(stat.S_IFLNK|0o777)<<16; zf.writestr(info,"target")
    with pytest.raises(UnsafeSitePackage): safe_extract_zip(out.getvalue(),tmp_path/"a")
    with pytest.raises(UnsafeSitePackage): safe_extract_zip(make_zip({"run.exe":"bad"}),tmp_path/"b")
    with pytest.raises(UnsafeSitePackage): safe_extract_zip(make_zip({"a.html":"a","b.html":"b"}),tmp_path/"c",SiteLimits(file_count=1))

def test_multi_page_graph_and_missing_resources(tmp_path):
    content=make_zip({"index.html":"<title>Home</title><a href='profile.html'>Profile</a><img src='missing.png'>","profile.html":"<title>Profile</title><form action='save.html'><input id='name'></form>","app.js":""})
    safe_extract_zip(content,tmp_path); result=analyze_site(tmp_path)
    assert len(result["pages"])==2 and result["entry_candidates"]==["index.html"]
    assert any(x["target"]=="profile.html" for x in result["navigation_relations"])
    assert {x["target"] for x in result["missing_resources"]}>={"missing.png","save.html"}

@pytest.mark.playwright
def test_playwright_bounded_requirement_driven_exploration(tmp_path):
    (tmp_path/"index.html").write_text("""<input id='name'><button id='save' onclick=\"document.querySelector('#msg').textContent='saved'\">Save</button><button id='delete'>Delete</button><div id='msg'></div><img src='https://example.com/x.png'>""",encoding="utf-8")
    result=explore_site(tmp_path,"index.html",[{"action":"input","locator":"#name","value":"Alice"},{"action":"click","locator":"#save"},{"action":"delete","locator":"#delete"}],tmp_path/"evidence")
    assert result["final_url"].startswith("http://127.0.0.1:")
    assert any(x["type"]=="blocked_action" for x in result["events"])
    assert any(x["expected_source"]=="html_observed" for x in result["events"] if x["type"]=="interaction")
    assert result["blocked_requests"] and list((tmp_path/"evidence").glob("*.png"))
