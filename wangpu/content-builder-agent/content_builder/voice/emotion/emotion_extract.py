"""主智能体播报文本的情绪识别。

参考项目中的 emotion_extract.py 以关键词和标点为主，这里保留同样思路，
但去掉前端角色素材相关内容，只返回控制台和后续 TTS/前端都能复用的结构化
情绪结果。
"""

from __future__ import annotations


class EmotionExtractor:
    """根据文本内容估计一个简洁情绪标签。"""

    def __init__(self) -> None:
        self.emoji_map = {
            "neutral": "😶",
            "happy": "🙂",
            "laughing": "😄",
            "sad": "😢",
            "angry": "😠",
            "surprised": "😮",
            "thinking": "🤔",
            "confident": "😎",
            "relaxed": "😌",
            "confused": "😕",
        }
        self.emotion_chinese_map = {
            "neutral": "中性",
            "happy": "开心",
            "laughing": "大笑",
            "sad": "难过",
            "angry": "生气",
            "surprised": "惊讶",
            "thinking": "思考",
            "confident": "自信",
            "relaxed": "放松",
            "confused": "困惑",
        }
        self.emotion_keywords = {
            "laughing": ["哈哈", "好笑", "有趣", "太逗", "lol", "haha"],
            "happy": ["好的", "太好了", "完成了", "顺利", "不错", "开心", "高兴", "happy", "great"],
            "sad": ["抱歉", "遗憾", "失败", "难过", "可惜", "sad", "sorry"],
            "angry": ["生气", "愤怒", "讨厌", "糟糕", "angry", "mad"],
            "surprised": ["哇", "居然", "没想到", "惊讶", "surprise", "wow"],
            "thinking": ["我想", "我会先", "接下来", "分析", "考虑", "think"],
            "confident": ["确定", "没问题", "可以做到", "放心", "definitely", "sure"],
            "relaxed": ["慢慢", "轻松", "别急", "放松", "relax"],
            "confused": ["为什么", "怎么回事", "不确定", "困惑", "what", "why", "how"],
        }
        self.priority_order = [
            "laughing",
            "angry",
            "sad",
            "surprised",
            "confident",
            "happy",
            "thinking",
            "confused",
            "relaxed",
        ]

    def analyze_emotion(self, text: str) -> str:
        """返回英文情绪标签。"""

        if not text:
            return "neutral"
        lowered = text.lower()

        if "?" in text or "？" in text:
            return "thinking"
        if "!" in text or "！" in text:
            if any(word in lowered for word in self.emotion_keywords["happy"]):
                return "laughing"
            return "surprised"
        if "..." in text or "……" in text:
            return "thinking"

        scores = {emotion: 0 for emotion in self.emoji_map}
        for emotion, keywords in self.emotion_keywords.items():
            for keyword in keywords:
                if keyword.lower() in lowered:
                    scores[emotion] += 1

        best_score = max(scores.values())
        if best_score <= 0:
            return "neutral"
        top_emotions = {emotion for emotion, score in scores.items() if score == best_score}
        for emotion in self.priority_order:
            if emotion in top_emotions:
                return emotion
        return "neutral"

    def extract(self, text: str) -> dict[str, str | float]:
        """返回情绪结构，供控制台打印或未来 UI 复用。"""

        emotion_en = self.analyze_emotion(text)
        return {
            "emotion_en": emotion_en,
            "emotion_cn": self.emotion_chinese_map.get(emotion_en, "未知"),
            "emoji": self.emoji_map.get(emotion_en, "😶"),
            "confidence": 1.0,
        }
