"""Safe ZIP extraction, multi-page analysis, and bounded Playwright exploration."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
import re, shutil, stat, threading, time
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
        for tag,attr in patterns.items():
            for match in re.finditer(fr"<{tag}\b[^>]*\b{attr}\s*=\s*['\"]([^'\"]+)['\"]",content,re.I):
                raw=match.group(1); parsed=urlparse(raw)
                if parsed.scheme or raw.startswith("//"): refs.append({"kind":tag,"source":relative,"target":raw,"external":True}); continue
                target=((path.parent/parsed.path).resolve() if parsed.path else path); exists=target.exists() and (target==root or root in target.parents)
                item={"kind":tag,"source":relative,"target":raw,"resolved":target.relative_to(root).as_posix() if exists else "","external":False,"missing":not exists}
                refs.append(item); relations.append(item)
                if not exists: missing.append(item)
        pages.append({**page,"entry_candidate":path.name.lower() in {"index.html","index.htm"},"references":refs,"element_count":len(elements)})
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
        context.route("**/*",route_handler); page=context.new_page(); page.on("console",lambda msg:console.append({"type":msg.type,"text":msg.text})); page.on("dialog",lambda dialog:(events.append({"type":"dialog","message":dialog.message}),dialog.dismiss()))
        page.goto(f"{base}/{entry}",wait_until="domcontentloaded")
        for index,action in enumerate(plan[:limits.actions_per_page]):
            if time.monotonic()-started>limits.timeout_seconds or states>=limits.max_states: break
            kind=action.get("action"); locator=action.get("locator","")
            if kind in {"delete","logout","download","external"}: events.append({"type":"blocked_action","action":action}); continue
            before={"url":page.url,"text":page.locator("body").inner_text()[:10000],"html":page.content()[:50000]}; before_path=evidence_dir/f"{index:03d}-before.png"; page.screenshot(path=str(before_path),full_page=True); target=page.locator(locator).first
            if kind=="click": target.click()
            elif kind=="input": target.fill(str(action.get("value","")))
            elif kind=="select": target.select_option(str(action.get("value","")))
            elif kind=="check": target.check()
            elif kind=="uncheck": target.uncheck()
            else: events.append({"type":"unsupported_action","action":action}); continue
            page.wait_for_timeout(100); after={"url":page.url,"text":page.locator("body").inner_text()[:10000],"html":page.content()[:50000]}; after_path=evidence_dir/f"{index:03d}-after.png"; page.screenshot(path=str(after_path),full_page=True)
            boxes=page.locator("input,button,select,textarea,a").evaluate_all("els=>els.map(e=>{const r=e.getBoundingClientRect();return {tag:e.tagName.toLowerCase(),id:e.id,text:e.innerText||e.value||'',disabled:!!e.disabled,checked:!!e.checked,box:{x:r.x,y:r.y,width:r.width,height:r.height}}})")
            events.append({"type":"interaction","action":action,"before":before,"after":after,"changed":before!=after,"controls":boxes,"before_screenshot":str(before_path),"after_screenshot":str(after_path),"expected_source":"html_observed"}); states+=2
        result={"entry":entry,"final_url":page.url,"visible_text":page.locator("body").inner_text()[:20000],"events":events,"console_errors":[x for x in console if x["type"]=="error"],"blocked_requests":blocked,"elapsed_seconds":round(time.monotonic()-started,3),"limits":asdict(limits)}; browser.close(); return result
