"""
语言检测与输出语言策略。

- 默认中文回复
- 检测到英文输入则英文回复
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageResult:
    lang: str  # "zh" | "en"
    score: float | None = None


class LanguageService:
    def __init__(self, default_lang: str = "zh") -> None:
        self.default_lang = default_lang

    def detect(self, text: str) -> LanguageResult:
        text = (text or "").strip()
        if not text:
            return LanguageResult(lang=self.default_lang, score=None)

        # 优先用 langid（轻量）
        try:
            import langid  # type: ignore

            langid.set_languages(["zh", "en"])
            lang, score = langid.classify(text)
            if lang not in {"zh", "en"}:
                return LanguageResult(lang=self.default_lang, score=float(score))
            return LanguageResult(lang=lang, score=float(score))
        except Exception:
            # 兜底：简单规则
            ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
            return LanguageResult(lang="en" if ascii_ratio > 0.8 else "zh", score=None)


