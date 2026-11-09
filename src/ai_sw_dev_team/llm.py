"""OpenAI 兼容 Chat Completions 客户端（标准库 urllib 实现，无第三方依赖）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import ProviderSpec


class LLMError(RuntimeError):
    """模型调用失败。"""


class OpenAICompatibleClient:
    def __init__(self, provider: ProviderSpec):
        self.provider = provider

    @property
    def available(self) -> bool:
        return self.provider.available

    def chat(self, system: str, user: str) -> str:
        if not self.available:
            raise LLMError("未配置 API Key")

        url = self.provider.base_url
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        payload = {
            "model": self.provider.model,
            "temperature": self.provider.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.provider.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.provider.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise LLMError(f"模型接口返回 HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"无法连接模型接口 {url}：{exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMError(f"模型接口调用超时（{self.provider.timeout}s）") from exc

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LLMError(f"模型接口返回非 JSON 内容：{body[:500]}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"模型接口未返回 choices：{body[:500]}")
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
            )
        if not isinstance(content, str) or not content.strip():
            raise LLMError("模型返回内容为空")
        return content
