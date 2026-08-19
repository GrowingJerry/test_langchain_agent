"""Safe ZIP extraction, multi-page analysis, and bounded Playwright exploration."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
import os, re, shutil, stat, threading, time
from typing import Any, Iterator
from urllib.parse import urlparse
from zipfile import BadZipFile, ZipFile
from application.services.traceability_service import parse_offline_html

ALLOWED_SUFFIXES={".html",".htm",".css",".js",".json",".png",".jpg",".jpeg",".gif",".svg",".webp",".ico",".woff",".woff2",".ttf"}

@dataclass(frozen=True)
class SiteLimits:
    upload_bytes:int=25*1024*1024; extracted_bytes:int=100*1024*1024; file_count:int=500
    compression_ratio:int=100; max_pages:int=20; max_depth:int=3; actions_per_page:int=8
    max_states:int=60; timeout_seconds:int=60

class UnsafeSitePackage(ValueError): pass

def playwright_health(browser_path:Path|None=None)->dict[str,Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError: return {"status":"python_package_missing","available":False,"repair":"离线安装 requirements-playwright.txt"}
    if browser_path is not None: os.environ["PLAYWRIGHT_BROWSERS_PATH"]=str(browser_path.resolve())
    try:
        with sync_playwright() as pw:
            executable=Path(pw.chromium.executable_path)
            if not executable.exists(): return {"status":"chromium_missing","available":False,"executable":str(executable),"repair":"运行 scripts/install-playwright-offline.ps1"}
            browser=pw.chromium.launch(headless=True); version=browser.version; browser.close()
        return {"status":"available","available":True,"executable":str(executable),"chromium_version":version}
    except Exception as exc: return {"status":"launch_failed","available":False,"error":f"{type(exc).__name__}: {exc}"}

def safe_extract_zip(content:bytes,target:Path,limits:SiteLimits=SiteLimits())->list[Path]:
    if len(content)>limits.upload_bytes: raise UnsafeSitePackage("ZIP超过上传大小限制")
    target=target.resolve(); target.mkdir(parents=True,exist_ok=True); archive=target/".upload.zip"; archive.write_bytes(content)
    extracted=[]; total=0
    try:
        with ZipFile(archive) as zf:
            infos=[x for x in zf.infolist() if not x.is_dir()]
            if len(infos)>limits.file_count: raise UnsafeSitePackage("ZIP文件数量超过限制")
            for info in infos:
                name=info.filename.replace("\\","/"); posix=PurePosixPath(name)
                if posix.is_absolute() or ".." in posix.parts or re.match(r"^[A-Za-z]:",name): raise UnsafeSitePackage("ZIP包含路径穿越或绝对路径")
                if stat.S_ISLNK((info.external_attr>>16)&0xFFFF): raise UnsafeSitePackage("ZIP不允许符号链接")
                suffix=Path(posix.name).suffix.lower()
                if suffix not in ALLOWED_SUFFIXES: raise UnsafeSitePackage(f"不允许的资源类型：{suffix or posix.name}")
                total+=info.file_size
                if total>limits.extracted_bytes: raise UnsafeSitePackage("ZIP解压大小超过限制")
                if (info.compress_size==0 and info.file_size) or (info.compress_size and info.file_size/info.compress_size>limits.compression_ratio): raise UnsafeSitePackage("ZIP疑似压缩炸弹")
                destination=(target/Path(*posix.parts)).resolve()
                if destination!=target and target not in destination.parents: raise UnsafeSitePackage("ZIP目标越界")
                destination.parent.mkdir(parents=True,exist_ok=True)
                with zf.open(info) as source,destination.open("wb") as output: shutil.copyfileobj(source,output)
                extracted.append(destination)
    except BadZipFile as exc: raise UnsafeSitePackage("无效ZIP文件") from exc
    finally: archive.unlink(missing_ok=True)
    return extracted

def analyze_site(root:Path)->dict[str,Any]:
    root=root.resolve(); html_files=sorted({*root.rglob("*.html"),*root.rglob("*.htm")}); pages=[]; relations=[]; missing=[]
    patterns={"a":"href","form":"action","iframe":"src","script":"src","link":"href","img":"src"}
    for path in html_files:
        relative=path.relative_to(root).as_posix(); content=path.read_text("utf-8",errors="replace"); page,elements=parse_offline_html(content,relative); refs=[]
        compressed=re.sub(r"<!--.*?-->"," ",content,flags=re.S); compressed=re.sub(r"<(script|style)\b[^>]*>.*?</\1>"," ",compressed,flags=re.I|re.S); compressed=re.sub(r"<[^>]+>"," ",compressed); compressed=re.sub(r"\s+"," ",compressed).strip()[:8000]
        for tag,attr in patterns.items():
            for match in re.finditer(fr"<{tag}\b[^>]*\b{attr}\s*=\s*['\"]([^'\"]+)['\"]",content,re.I):
                raw=match.group(1)
                if raw.lower().startswith("data:"): refs.append({"kind":tag,"source":relative,"target":"[inline resource omitted]","external":False,"inline_omitted":True}); continue
                parsed=urlparse(raw)
                if parsed.scheme or raw.startswith("//"): refs.append({"kind":tag,"source":relative,"target":raw,"external":True}); continue
                target=((path.parent/parsed.path).resolve() if parsed.path else path); exists=target.exists() and (target==root or root in target.parents)
                item={"kind":tag,"source":relative,"target":raw,"resolved":target.relative_to(root).as_posix() if exists else "","external":False,"missing":not exists}
                refs.append(item); relations.append(item)
                if not exists: missing.append(item)
        pages.append({**page,"entry_candidate":path.name.lower() in {"index.html","index.htm"},"references":refs,"element_count":len(elements),"visible_text_summary":compressed,"source_bytes":path.stat().st_size,"compressed_chars":len(compressed)})
    candidates=[x["path"] for x in pages if x["entry_candidate"]] or ([pages[0]["path"]] if pages else [])
    return {"pages":pages,"navigation_relations":[x for x in relations if x["kind"] in {"a","form","iframe"}],"missing_resources":missing,"entry_candidates":candidates}

class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self,*_): pass

@contextmanager
def local_site_server(root:Path)->Iterator[str]:
    server=ThreadingHTTPServer(("127.0.0.1",0),partial(_QuietHandler,directory=str(root))); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try: yield f"http://127.0.0.1:{server.server_port}"
    finally: server.shutdown(); server.server_close(); thread.join(timeout=2)

def explore_site(root:Path,entry:str,plan:list[dict[str,Any]],evidence_dir:Path,limits:SiteLimits=SiteLimits())->dict[str,Any]:
    """Execute only explicit requirement-driven actions; dangerous actions are blocked."""
    from playwright.sync_api import sync_playwright
    started=time.monotonic(); evidence_dir.mkdir(parents=True,exist_ok=True); events=[]; console=[]; blocked=[]; states=0
    with local_site_server(root) as base,sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True); context=browser.new_context(accept_downloads=False)
        def route_handler(route):
            host=(urlparse(route.request.url).hostname or "").lower()
            if host not in {"127.0.0.1","localhost"}: blocked.append(route.request.url); route.abort("blockedbyclient")
            else: route.continue_()
        context.route("**/*",route_handler); page=context.new_page(); popups=[]; dialogs=[]
        page.on("console",lambda msg:console.append({"type":msg.type,"text":msg.text})); page.on("popup",lambda popup:popups.append(popup.url)); page.on("dialog",lambda dialog:(dialogs.append({"type":dialog.type,"message":dialog.message}),dialog.dismiss()))
        page.goto(f"{base}/{entry}",wait_until="domcontentloaded")
        for index,action in enumerate(plan[:limits.actions_per_page]):
            if time.monotonic()-started>limits.timeout_seconds or states>=limits.max_states: break
            kind=action.get("action"); semantic=action.get("target") or {}; locator=action.get("locator",""); reason="explicit_debug_locator"
            if kind in {"delete","logout","download","external"}: events.append({"type":"blocked_action","action":action}); continue
            before={"url":page.url,"text":page.locator("body").inner_text()[:10000],"html":page.content()[:50000]}; before_path=evidence_dir/f"{index:03d}-before.png"; page.screenshot(path=str(before_path),full_page=True)
            if semantic:
                candidates=[]; accessible=str(semantic.get("accessible_name") or ""); role=str(semantic.get("role") or "")
                if role and accessible: candidates.append((page.get_by_role(role,name=accessible,exact=True),"get_by_role"))
                if accessible: candidates.extend([(page.get_by_label(accessible,exact=True),"get_by_label"),(page.get_by_placeholder(accessible,exact=True),"get_by_placeholder"),(page.get_by_text(accessible,exact=True),"get_by_text")])
                resolved=next(((candidate,why) for candidate,why in candidates if candidate.count()==1),None)
                if not resolved: events.append({"action_id":f"ACT-{index+1}","type":"ambiguous_target","semantic_target":semantic,"candidate_count":max([x.count() for x,_ in candidates] or [0]),"success":False,"failure_reason":"语义定位结果不是唯一元素","expected_source":"html_observed"}); continue
                target,reason=resolved; locator=reason+":"+accessible
            else: target=page.locator(locator)
            if target.count()!=1: events.append({"action_id":f"ACT-{index+1}","type":"ambiguous_target","semantic_target":semantic,"candidate_count":target.count(),"success":False,"failure_reason":"定位结果不是唯一元素","expected_source":"html_observed"}); continue
            if kind=="click": target.click()
            elif kind=="input": target.fill(str(action.get("value","")))
            elif kind=="select": target.select_option(str(action.get("value","")))
            elif kind=="check": target.check()
            elif kind=="uncheck": target.uncheck()
            else: events.append({"type":"unsupported_action","action":action}); continue
            page.wait_for_timeout(100); after={"url":page.url,"text":page.locator("body").inner_text()[:10000],"html":page.content()[:50000]}; after_path=evidence_dir/f"{index:03d}-after.png"; page.screenshot(path=str(after_path),full_page=True)
            boxes=page.locator("input,button,select,textarea,a").evaluate_all("els=>els.map(e=>{const r=e.getBoundingClientRect();return {tag:e.tagName.toLowerCase(),id:e.id,text:e.innerText||e.value||'',disabled:!!e.disabled,checked:!!e.checked,box:{x:r.x,y:r.y,width:r.width,height:r.height}}})")
            events.append({"action_id":f"ACT-{index+1}","page_id":entry,"type":"interaction","action":kind,"semantic_target":semantic,"final_locator":locator,"locator_reason":reason,"purpose":action.get("purpose",""),"before_dom_summary":before["html"],"after_dom_summary":after["html"],"url_before":before["url"],"url_after":after["url"],"visible_text_before":before["text"],"visible_text_after":after["text"],"dialogs":list(dialogs),"new_windows":list(popups),"changed":before!=after,"controls":boxes,"before_screenshot":str(before_path),"after_screenshot":str(after_path),"success":True,"failure_reason":"","expected_source":"html_observed"}); dialogs.clear(); popups.clear(); states+=2
        result={"entry":entry,"final_url":page.url,"visible_text":page.locator("body").inner_text()[:20000],"events":events,"console_errors":[x for x in console if x["type"]=="error"],"blocked_requests":blocked,"elapsed_seconds":round(time.monotonic()-started,3),"limits":asdict(limits)}; browser.close(); return result

def automatic_safe_plan(root:Path,entry:str,limits:SiteLimits=SiteLimits())->list[dict[str,Any]]:
    """Create a conservative semantic plan; users never provide locators or actions."""
    page_path=(root/entry).resolve(); _,elements=parse_offline_html(page_path.read_bytes(),entry); plan=[]
    dangerous=("删除","发布","退出","下载","清空","提交")
    for element in elements:
        name=element.label or element.attributes.get("aria-label") or element.attributes.get("placeholder") or element.text
        if not name or any(word in name for word in dangerous) or not element.visible or not element.enabled: continue
        role=element.attributes.get("role") or ({"button":"button","a":"link","select":"combobox","textarea":"textbox"}.get(element.tag) or ("textbox" if element.tag=="input" else ""))
        if element.tag in {"input","textarea"}: action={"action":"input","value":"自动探索测试数据"}
        elif element.tag=="select" and element.options: action={"action":"select","value":element.options[0]}
        elif element.tag in {"button","a"}: action={"action":"click"}
        else: continue
        action.update({"target":{"role":role,"accessible_name":name,"region":element.semantic_position},"purpose":"获取与需求相关的本地页面观测"}); plan.append(action)
        if len(plan)>=limits.actions_per_page: break
    return plan
