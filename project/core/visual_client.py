# -*- coding: utf-8 -*-
"""Ollama vision model client for image-aware chat and JSON extraction."""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import requests

from config.settings import (
    OLLAMA_BASE_URL,
    OLLAMA_VISION_IMAGE_MAX_SIDE,
    OLLAMA_VISION_MODEL,
    OLLAMA_VISION_TIMEOUT,
)
from core.ollama_client import OllamaClient


VISION_DIMENSION_MULTIPLE = 28


class OllamaVisualClient:
    """Client for Ollama /api/chat requests with image inputs."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
        self.model = model or OLLAMA_VISION_MODEL
        self.timeout = timeout if timeout is not None else OLLAMA_VISION_TIMEOUT
        self._json_parser = OllamaClient(
            base_url=self.base_url, model=self.model, timeout=self.timeout
        )

    def _normalized_image_bytes(self, path: Path) -> Dict[str, Any]:
        """Normalize screenshots before sending them to Ollama vision models."""
        try:
            from PIL import Image
        except ImportError:
            return {"ok": True, "content": path.read_bytes(), "error": "", "size": None}

        try:
            with Image.open(path) as image:
                image = image.convert("RGBA")
                width, height = image.size
                max_side = max(224, int(OLLAMA_VISION_IMAGE_MAX_SIDE or 1344))
                max_side = max(
                    VISION_DIMENSION_MULTIPLE,
                    (max_side // VISION_DIMENSION_MULTIPLE) * VISION_DIMENSION_MULTIPLE,
                )
                scale = min(1.0, max_side / max(width, height))
                target_width = max(VISION_DIMENSION_MULTIPLE, int(width * scale))
                target_height = max(VISION_DIMENSION_MULTIPLE, int(height * scale))
                target_width = max(
                    VISION_DIMENSION_MULTIPLE,
                    (target_width // VISION_DIMENSION_MULTIPLE)
                    * VISION_DIMENSION_MULTIPLE,
                )
                target_height = max(
                    VISION_DIMENSION_MULTIPLE,
                    (target_height // VISION_DIMENSION_MULTIPLE)
                    * VISION_DIMENSION_MULTIPLE,
                )
                if (target_width, target_height) != image.size:
                    image = image.resize((target_width, target_height), Image.LANCZOS)
                canvas = Image.new("RGBA", (max_side, max_side), (255, 255, 255, 255))
                offset = (
                    (max_side - target_width) // 2,
                    (max_side - target_height) // 2,
                )
                canvas.alpha_composite(image, dest=offset)
                image = canvas.convert("RGB")
                buffer = BytesIO()
                image.save(buffer, format="JPEG", quality=90, optimize=True)
                return {
                    "ok": True,
                    "content": buffer.getvalue(),
                    "error": "",
                    "size": (max_side, max_side),
                    "content_size": (target_width, target_height),
                }
        except Exception as exc:
            return {
                "ok": False,
                "content": b"",
                "error": f"图片预处理失败：{path}：{exc}",
                "size": None,
            }

    def encode_image_to_base64(self, image_path: Union[str, Path]) -> Dict[str, Any]:
        """Encode a local image file to base64 without raising UI-breaking errors."""
        path = Path(image_path)
        if not path.is_file():
            return {"ok": False, "image_base64": "", "error": f"图片文件不存在：{path}"}
        try:
            normalized = self._normalized_image_bytes(path)
            if not normalized["ok"]:
                return {"ok": False, "image_base64": "", "error": normalized["error"]}
            return {
                "ok": True,
                "image_base64": base64.b64encode(normalized["content"]).decode("ascii"),
                "error": "",
                "normalized_size": normalized.get("size"),
            }
        except OSError as exc:
            return {
                "ok": False,
                "image_base64": "",
                "error": f"图片读取失败：{path}：{exc}",
            }

    def _list_models(self) -> Dict[str, Any]:
        try:
            resp = requests.get(
                f"{self.base_url}/api/tags", timeout=min(5, self.timeout)
            )
            resp.raise_for_status()
            data = resp.json()
            models = [
                m.get("name", "") for m in data.get("models", []) if m.get("name")
            ]
            return {"ok": True, "models": models, "error": ""}
        except requests.RequestException as exc:
            return {
                "ok": False,
                "models": [],
                "error": f"Ollama 服务不可用：{self.base_url}：{exc}",
            }
        except ValueError as exc:
            return {
                "ok": False,
                "models": [],
                "error": f"Ollama 模型列表响应不是合法 JSON：{exc}",
            }

    def _prepare_images(
        self,
        image_paths: Optional[List[Union[str, Path]]] = None,
        image_base64_list: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        images: List[str] = []
        for path in image_paths or []:
            encoded = self.encode_image_to_base64(path)
            if not encoded["ok"]:
                return {"ok": False, "images": [], "error": encoded["error"]}
            images.append(encoded["image_base64"])
        for item in image_base64_list or []:
            value = str(item or "").strip()
            if value:
                images.append(value)
        if not images:
            return {
                "ok": False,
                "images": [],
                "error": "未提供图片：请传入 image_paths 或 image_base64_list。",
            }
        return {"ok": True, "images": images, "error": ""}

    def chat_with_images(
        self,
        prompt: str,
        image_paths: Optional[List[Union[str, Path]]] = None,
        image_base64_list: Optional[List[str]] = None,
        system_prompt: str = "",
    ) -> Dict[str, Any]:
        """Call Ollama /api/chat with images and return structured success/error data."""
        models = self._list_models()
        if not models["ok"]:
            return {"ok": False, "content": "", "error": models["error"]}
        if self.model not in models["models"]:
            return {
                "ok": False,
                "content": "",
                "error": f"视觉模型不存在：{self.model}。请先执行 ollama pull {self.model}",
            }

        prepared = self._prepare_images(
            image_paths=image_paths, image_base64_list=image_base64_list
        )
        if not prepared["ok"]:
            return {"ok": False, "content": "", "error": prepared["error"]}

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append(
            {"role": "user", "content": prompt or "", "images": prepared["images"]}
        )
        payload = {"model": self.model, "messages": messages, "stream": False}
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            )
            if not resp.ok:
                detail = (resp.text or "").strip()
                if len(detail) > 1200:
                    detail = detail[:1200] + "..."
                return {
                    "ok": False,
                    "content": "",
                    "error": (
                        f"Ollama 视觉调用失败：HTTP {resp.status_code} {resp.reason}。"
                        f"{' 服务端返回：' + detail if detail else ''}"
                    ),
                }
            data = resp.json()
            message = data.get("message") or {}
            return {
                "ok": True,
                "content": (message.get("content") or "").strip(),
                "error": "",
            }
        except requests.RequestException as exc:
            return {"ok": False, "content": "", "error": f"Ollama 视觉调用失败：{exc}"}
        except ValueError as exc:
            return {
                "ok": False,
                "content": "",
                "error": f"Ollama 视觉响应不是合法 JSON：{exc}",
            }

    def generate_json_with_images(
        self,
        prompt: str,
        image_paths: Optional[List[Union[str, Path]]] = None,
        image_base64_list: Optional[List[str]] = None,
        system_prompt: str = "",
    ) -> Dict[str, Any]:
        """Ask the vision model to return strict JSON and parse it safely."""
        sys_p = (system_prompt or "").strip()
        sys_p += "\n你必须只输出合法 JSON，不要 Markdown 代码块，不要额外说明文字。"
        result = self.chat_with_images(
            prompt=prompt,
            image_paths=image_paths,
            image_base64_list=image_base64_list,
            system_prompt=sys_p,
        )
        if not result["ok"]:
            return {"ok": False, "data": None, "raw": "", "error": result["error"]}
        parsed = self._json_parser.extract_json(result["content"])
        if parsed is None:
            return {
                "ok": False,
                "data": None,
                "raw": result["content"],
                "error": "模型未返回可解析的合法 JSON。",
            }
        return {"ok": True, "data": parsed, "raw": result["content"], "error": ""}
