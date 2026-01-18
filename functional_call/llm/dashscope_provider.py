"""
DashScope（通义千问）LLM Provider（OpenAI 兼容接口）。

说明：
- 只实现最小可用的 chat completion（messages -> assistant_text）
- 使用 DashScope OpenAI-compatible endpoint：/v1/chat/completions
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import requests
import time
from functools import wraps

def retry_on_network_error(max_retries=3, delay=1):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_err = None
            for i in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (requests.RequestException, Exception) as e:
                    last_err = e
                    if i < max_retries - 1:
                        logger.warning(f"网络请求失败，正在进行第 {i+1} 次重试: {e}")
                        time.sleep(delay * (i + 1))
                    else:
                        break
            raise last_err
        return wrapper
    return decorator

logger = logging.getLogger(__name__)


class DashScopeError(RuntimeError):
    pass


class DashScopeLLMProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "qwen-plus",
        timeout_s: int = 30,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ) -> None:
        if not api_key:
            raise ValueError("DashScope API Key 不能为空，请设置环境变量 DASHSCOPE_API_KEY")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._base_url = base_url

    @property
    def model(self) -> str:
        return self._model

    def chat(
        self,
        *,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        top_p: float = 0.8,
        max_tokens: int = 1024,
    ) -> str:
        """
        调用 DashScope OpenAI-compatible Chat Completions。
        """
        result = self.call_with_tools(
            messages=messages,
            tools=None,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens
        )
        return result.get("content", "")

    async def ask(self, prompt: str, temperature: float = 0.2) -> str:
        """
        简单的问答模式（异步封装）。
        """
        import asyncio
        loop = asyncio.get_event_loop()
        messages = [{"role": "user", "content": prompt}]
        # 直接在线程池运行同步的 chat
        return await loop.run_in_executor(
            None, 
            lambda: self.chat(messages=messages, temperature=temperature)
        )

    @retry_on_network_error(max_retries=3, delay=1)
    def call_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        top_p: float = 0.8,
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """
        调用 DashScope，支持 tools 参数。
        返回 Dict: {"content": str, "tool_calls": list | None}
        """
        url = self._base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stream": False,
        }
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=self._timeout_s)
        except requests.RequestException as e:
            raise DashScopeError(f"DashScope 请求失败：{e}") from e

        if resp.status_code != 200:
            snippet = resp.text[:500] if resp.text else ""
            raise DashScopeError(f"DashScope 返回异常状态码：{resp.status_code}，内容：{snippet}")

        try:
            data = resp.json()
        except Exception as e:
            raise DashScopeError(f"DashScope 响应不是合法JSON：{resp.text[:500]}") from e

        try:
            if "error" in data:
                err = data.get("error") or {}
                raise DashScopeError(f"DashScope错误：{err.get('message') or err}")

            choices = data["choices"]
            if not choices:
                raise KeyError("choices为空")
            
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            tool_calls = message.get("tool_calls")
            
            return {
                "content": content,
                "tool_calls": tool_calls
            }
        except Exception as e:
            raise DashScopeError(f"解析DashScope响应失败：{json.dumps(data, ensure_ascii=False)[:800]}") from e


