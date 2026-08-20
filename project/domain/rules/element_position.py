"""Evidence-derived relative position phrases."""
from __future__ import annotations
from typing import Any

def relative_position(box: dict[str, float], container: dict[str, float]) -> str:
    cx=box["x"]+box["width"]/2-container.get("x",0); cy=box["y"]+box["height"]/2-container.get("y",0)
    width=max(container["width"],1); height=max(container["height"],1)
    col=0 if cx<width/3 else (2 if cx>width*2/3 else 1)
    row=0 if cy<height/3 else (2 if cy>height*2/3 else 1)
    return (("左上角","顶部中间","右上角"),("左侧","中央","右侧"),("左下角","底部中间","右下角"))[row][col]

def position_evidence(box: dict[str,float] | None, viewport: dict[str,float], region_name: str="") -> dict[str,Any]:
    if not box:
        return {"region_name":region_name,"relative_position":"方位待确认","position_phrase":region_name or "方位待确认","position_source":"dom_landmark" if region_name else "unconfirmed","position_confidence":.65 if region_name else 0,"viewport":f"{int(viewport['width'])}x{int(viewport['height'])}"}
    position=relative_position(box,{"x":0,"y":0,**viewport})
    return {"region_name":region_name,"relative_position":position,"position_phrase":f"{region_name}{position}" if region_name else position,"position_source":"playwright_bbox+dom_landmark" if region_name else "playwright_bbox","position_confidence":.94 if region_name else .85,"viewport":f"{int(viewport['width'])}x{int(viewport['height'])}"}
