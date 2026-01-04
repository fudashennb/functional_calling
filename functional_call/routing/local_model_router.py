from __future__ import annotations

"""
本地路由模型（复用 agenticSeek 思路）：
- BART zero-shot 分类（facebook/bart-large-mnli）
- AdaptiveClassifier（用于意图分类 + 复杂度判定）

说明：
- 该模块依赖 transformers/torch/adaptive_classifier，且会在首次启用时下载模型权重（耗时/耗盘）。
- 为避免影响启动速度，采用延迟加载（lazy load）。
"""

import logging
import random
from dataclasses import dataclass
from typing import List, Tuple


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalRouteResult:
    agent: str  # planner/command/status/diagnostics/chat
    confidence: float | None = None
    detail: str | None = None


class LocalModelRouter:
    def __init__(self) -> None:
        self._loaded = False
        self._bart = None
        self._intent_clf = None
        self._complexity_clf = None
        self._translator = None  # 可选：中文->英文

    # ----------------- 加载 -----------------
    def _lazy_load(self) -> None:
        if self._loaded:
            return

        try:
            from transformers import pipeline  # type: ignore
        except Exception as e:
            raise RuntimeError("未安装 transformers，无法启用本地路由模型。请安装 requirements.txt 或手动 pip install transformers") from e

        try:
            from adaptive_classifier import AdaptiveClassifier  # type: ignore
        except Exception as e:
            raise RuntimeError("未安装 adaptive_classifier，无法启用本地路由模型。请 pip install adaptive_classifier") from e

        # 1) BART zero-shot
        logger.info("正在加载本地路由模型：BART zero-shot（首次会下载权重）...")
        self._bart = pipeline("zero-shot-classification", model="facebook/bart-large-mnli")

        # 2) AdaptiveClassifier（不强制使用本地safetensors，优先用库默认预训练/自动下载能力）
        logger.info("正在加载本地路由模型：AdaptiveClassifier（首次可能会下载权重）...")
        try:
            # 部分版本支持无参初始化
            self._intent_clf = AdaptiveClassifier()
            self._complexity_clf = AdaptiveClassifier()
        except Exception:
            # 兜底：尝试从 HuggingFace/本地路径加载（由用户配置）
            # 你也可以通过环境变量指定模型仓库/路径并在这里读取
            self._intent_clf = AdaptiveClassifier.from_pretrained("adaptive_classifier")  # type: ignore[arg-type]
            self._complexity_clf = AdaptiveClassifier.from_pretrained("adaptive_classifier")  # type: ignore[arg-type]

        self._learn_few_shots_intent()
        self._learn_few_shots_complexity()

        # 3) 翻译器（可选）：中文输入转英文，提升BART效果
        self._try_load_zh_en_translator()

        self._loaded = True
        logger.info("本地路由模型加载完成。")

    def _try_load_zh_en_translator(self) -> None:
        """
        可选加载 Helsinki-NLP/opus-mt-zh-en，用于中文->英文翻译（仅用于路由）。
        若缺少 sentencepiece 等依赖，自动跳过。
        """
        try:
            from transformers import MarianMTModel, MarianTokenizer  # type: ignore
        except Exception:
            logger.info("未加载中文->英文翻译模型（缺少 Marian 相关依赖或 transformers 版本不支持），将直接用原文本做路由。")
            return

        try:
            model_name = "Helsinki-NLP/opus-mt-zh-en"
            tok = MarianTokenizer.from_pretrained(model_name)
            mdl = MarianMTModel.from_pretrained(model_name)
            self._translator = (tok, mdl)
            logger.info("已加载中文->英文翻译模型（用于路由增强）。")
        except Exception as e:
            logger.info(f"中文->英文翻译模型加载失败，将跳过：{e}")

    # ----------------- few-shots -----------------
    def _learn_few_shots_intent(self) -> None:
        """
        微样本意图分类：command/status/diagnostics/planner/chat
        """
        shots = [
            # 指令 (command)
            ("导航到站点一", "command"),
            ("前往站点2", "command"),
            ("去站点3", "command"),
            ("移动到A点", "command"),
            ("顶升到50", "command"),
            ("举起货物", "command"),
            ("放下货物", "command"),
            ("降下顶杆", "command"),
            ("开始充电", "command"),
            ("去充电桩", "command"),
            ("停止充电", "command"),
            ("取消当前指令", "command"),
            ("立刻停止", "command"),
            
            # 状态 (status)
            ("查看电池状态", "status"),
            ("现在电量多少", "status"),
            ("汇报一下你的位置", "status"),
            ("你在哪", "status"),
            ("任务进度怎么样", "status"),
            ("汇报任务执行情况", "status"),
            ("还在跑吗", "status"),
            ("查看传感器数据", "status"),
            
            # 诊断 (diagnostics)
            ("连不上机器人", "diagnostics"),
            ("Modbus连接失败", "diagnostics"),
            ("502错误怎么回事", "diagnostics"),
            ("SSH隧道断了", "diagnostics"),
            ("超时了怎么办", "diagnostics"),
            ("为什么报错", "diagnostics"),
            ("任务失败的原因是什么", "diagnostics"),
            ("检查一下连接状态", "diagnostics"),
            
            # 规划 (planner)
            ("先去站点1再顶升到50", "planner"),
            ("依次导航到站点1和站点2", "planner"),
            ("去站点A接货然后送到站点B", "planner"),
            ("先充电完成后回起始点", "planner"),
            ("执行一套动作：顶升，去A点，放下", "planner"),
            
            # 闲聊 (chat)
            ("你好", "chat"),
            ("你是谁", "chat"),
            ("讲个笑话", "chat"),
            ("今天天气怎么样", "chat"),
            ("介绍一下你自己", "chat"),
            ("你会干什么", "chat"),
        ]
        random.shuffle(shots)
        texts = [t for t, _ in shots]
        labels = [l for _, l in shots]
        self._intent_clf.add_examples(texts, labels)

    def _learn_few_shots_complexity(self) -> None:
        """
        复杂度判定：HIGH/LOW（HIGH -> planner）
        """
        shots = [
            # LOW (单步任务或简单查询)
            ("导航到站点1", "LOW"),
            ("去充电", "LOW"),
            ("顶升", "LOW"),
            ("放下", "LOW"),
            ("查看电量", "LOW"),
            ("你好", "LOW"),
            ("你是谁", "LOW"),
            ("为什么失败了", "LOW"),
            ("现在到哪了", "LOW"),
            
            # HIGH (多步规划或逻辑复杂)
            ("先导航到站点1再顶升到50", "HIGH"),
            ("完成后去充电", "HIGH"),
            ("依次执行：去站点1、顶升、回站点2", "HIGH"),
            ("帮我规划一下路线：去A拿货，去B卸货", "HIGH"),
            ("先检查电池，如果电量够就去工作，不够就去充电", "HIGH"),
            ("先回原点，然后再去站点3", "HIGH"),
        ]
        random.shuffle(shots)
        texts = [t for t, _ in shots]
        labels = [l for _, l in shots]
        self._complexity_clf.add_examples(texts, labels)

    # ----------------- 辅助 -----------------
    def _maybe_translate_zh_to_en(self, text: str, lang: str) -> str:
        if lang != "zh":
            return text
        if not self._translator:
            return text
        tok, mdl = self._translator
        try:
            inputs = tok(text, return_tensors="pt", padding=True, truncation=True)
            out = mdl.generate(**inputs, max_length=256)
            return tok.decode(out[0], skip_special_tokens=True)
        except Exception:
            return text

    def _intent_predict(self, text: str) -> Tuple[str, float | None]:
        preds = self._intent_clf.predict(text)
        preds = [p for p in preds if p[0] not in ["HIGH", "LOW"]]
        preds = sorted(preds, key=lambda x: x[1], reverse=True)
        if not preds:
            return "chat", None
        return preds[0][0], float(preds[0][1])

    def _complexity_predict(self, text: str) -> Tuple[str, float | None]:
        preds = self._complexity_clf.predict(text)
        preds = sorted(preds, key=lambda x: x[1], reverse=True)
        if not preds:
            return "LOW", None
        return preds[0][0], float(preds[0][1])

    # ----------------- 对外接口 -----------------
    def warm_up(self) -> None:
        """
        预热模型：手动触发延迟加载，将模型读入内存。
        """
        self._lazy_load()

    def route(self, *, text: str, lang: str, labels: List[str]) -> LocalRouteResult:
        """
        返回 agent 选择结果。
        优化后的逻辑：
        1. 翻译预处理 (专门给 BART 用)
        2. AdaptiveClassifier 使用原始文本 (匹配中文 Few-Shot)
        3. BART 使用描述性标签
        """
        self._lazy_load()

        # 1. 准备文本
        text_en = self._maybe_translate_zh_to_en(text, lang)
        
        # 2. 极短文本回退
        if len(text) <= 2:
            return LocalRouteResult(agent="chat", confidence=1.0, detail="text_too_short")

        # 3. 复杂度：使用翻译后的文本评估 (HIGH -> planner)
        complexity, c_conf = self._complexity_predict(text_en)
        if complexity == "HIGH" and (c_conf is None or c_conf >= 0.5):
            return LocalRouteResult(agent="planner", confidence=c_conf, detail=f"complexity=HIGH(conf:{c_conf})")

        # 4. 投票：BART vs AdaptiveClassifier
        
        # --- BART 推理 (使用英文和描述性标签) ---
        # 将内部 agent 名映射为更具语义的英文描述
        semantic_map = {
            "command": "robot execution command or action",
            "status": "robot status or information query",
            "diagnostics": "system fault diagnostics or troubleshooting",
            "planner": "complex multi-step task planning",
            "chat": "casual conversation or greeting"
        }
        # 仅对传入的 labels 进行映射
        inv_semantic_map = {v: k for k, v in semantic_map.items()}
        bart_labels = [semantic_map.get(l, l) for l in labels]
        
        try:
            bart_res = self._bart(text_en, bart_labels)
            bart_label_semantic = bart_res["labels"][0]
            bart_label = inv_semantic_map.get(bart_label_semantic, bart_label_semantic)
            bart_score = float(bart_res["scores"][0])
        except Exception as e:
            logger.warning(f"BART 推理失败: {e}")
            bart_label, bart_score = "chat", 0.0

        # --- AdaptiveClassifier 推理 (使用原始中文文本匹配 Few-Shot) ---
        # 这是修复问题的核心：中文样本对中文输入
        llm_label, llm_score = self._intent_predict(text)
        if llm_label not in labels:
            llm_label = "chat"

        # 5. 归一化加权融合
        if llm_score is None:
            return LocalRouteResult(agent=bart_label, confidence=bart_score, detail="vote=bart_only")
        
        denom = bart_score + llm_score
        if denom <= 0:
            return LocalRouteResult(agent=bart_label, confidence=bart_score, detail="vote=degenerate")
            
        bart_final = bart_score / denom
        llm_final = llm_score / denom
        
        # 如果判定结果不一致，且一方具有压倒性优势，或者两者信心都不足，则需要更细致的合并
        if bart_label == llm_label:
            chosen = bart_label
        else:
            # 权重微调：由于 Adaptive 匹配的是领域 Few-Shot，在有匹配时应给予更高权重
            # 这里我们让 llm_final 权重增加 20%
            if llm_final * 1.2 > bart_final:
                chosen = llm_label
            else:
                chosen = bart_label
            
        detail = f"vote=bart({bart_label}:{bart_final:.3f}) vs adaptive({llm_label}:{llm_final:.3f})"
        return LocalRouteResult(agent=chosen, confidence=max(bart_final, llm_final), detail=detail)


