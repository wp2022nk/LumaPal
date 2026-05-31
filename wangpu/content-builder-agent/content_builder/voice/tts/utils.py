"""TTS工具函数"""
import re


def get_string_no_punctuation_or_emoji(s):
    """去除字符串中的表情符号和特殊字符，只保留中文、英文、数字和基本标点符号
    
    采用白名单策略，确保TTS引擎不会收到非法字符
    
    注意：此函数会先过滤括号及其内容，然后再进行字符白名单过滤
    特别注意：为了支持英文流式输出，保留空格不做trim处理，避免单词粘连
    """
    if not s:
        return ""
    
    # 🔥 第一步：先过滤括号及其内容（旁白、动作描写等）
    # 过滤英文括号及其内容
    s = re.sub(r'\([^)]*\)', '', s)
    # 过滤中文括号及其内容
    s = re.sub(r'（[^）]*）', '', s)
    
    # 第二步：允许的字符白名单过滤
    allowed_chars = []
    for char in s:
        code_point = ord(char)
        
        # 基本拉丁字母和数字 (A-Z, a-z, 0-9)
        if (0x0030 <= code_point <= 0x0039 or  # 0-9
            0x0041 <= code_point <= 0x005A or  # A-Z
            0x0061 <= code_point <= 0x007A):   # a-z
            allowed_chars.append(char)
        # CJK统一汉字 (常用中文)
        elif (0x4E00 <= code_point <= 0x9FFF or     # CJK统一汉字
              0x3400 <= code_point <= 0x4DBF or     # CJK扩展A
              0x20000 <= code_point <= 0x2A6DF or   # CJK扩展B
              0x2A700 <= code_point <= 0x2B73F or   # CJK扩展C
              0x2B740 <= code_point <= 0x2B81F or   # CJK扩展D
              0x2B820 <= code_point <= 0x2CEAF or   # CJK扩展E
              0x2CEB0 <= code_point <= 0x2EBEF):    # CJK扩展F
            allowed_chars.append(char)
        # 常用标点符号（注意：这里不再包含括号，因为已经在第一步过滤了）
        # 包含英文单引号/撇号(')、反引号(`)用于英文缩写如you're, don't等
        elif char in "，。！？、；：""''《》,.:;!?'\"[]{}—-~…` ’":
            allowed_chars.append(char)
        # 空格和基本空白字符（保留空格以支持英文单词分隔）
        elif char in ' \t\n':
            allowed_chars.append(char)
    
    result = "".join(allowed_chars)
    
    # 不对空格做任何处理，完全保留原始空格
    # 这样可以确保流式输出时单词之间的空格不会丢失
    
    # 如果结果只包含标点符号和空白，返回空字符串
    if result:
        # 检查是否只有标点符号和空白
        has_content = any(
            char not in "，。！？、；：""''《》,.:;!?'\"[]{}—-~…` ’" and not char.isspace()
            for char in result
        )
        if not has_content:
            return ""
    
    return result


def is_space_or_emoji(char):
    """检查字符是否为空格或表情符号（保留所有标点符号）
    
    注意：此函数已不再被 get_string_no_punctuation_or_emoji 使用，
    保留是为了向后兼容
    """
    # 只检查空格
    if char.isspace():
        return True
    # 检查表情符号
    code_point = ord(char)
    emoji_ranges = [
        (0x1F600, 0x1F64F),  # 表情符号
        (0x1F300, 0x1F5FF),  # 杂项符号和象形文字
        (0x1F680, 0x1F6FF),  # 交通和地图符号
        (0x1F900, 0x1F9FF),  # 补充符号和象形文字
        (0x1FA70, 0x1FAFF),  # 扩展A
        (0x2600, 0x26FF),    # 杂项符号
        (0x2700, 0x27BF),    # 装饰符号
        (0x0E00, 0x0E7F),    # 泰文字符
        (0x2000, 0x206F),    # 常规标点
        (0x3000, 0x303F),    # CJK符号和标点（排除基本标点）
    ]
    return any(start <= code_point <= end for start, end in emoji_ranges)


def is_punctuation_or_emoji(char):
    """检查字符是否为空格、指定标点或表情符号（保持向后兼容）"""
    # 为了向后兼容，保留这个函数，但现在只检查空格和表情符号
    return is_space_or_emoji(char)


class MarkdownCleaner:
    """Markdown文本清理器"""
    # 公式字符
    NORMAL_FORMULA_CHARS = re.compile(r'[a-zA-Z\\^_{}\+\-\(\)\[\]=]')

    @staticmethod
    def _replace_inline_dollar(m: re.Match) -> str:
        """处理行内公式"""
        content = m.group(1)
        if MarkdownCleaner.NORMAL_FORMULA_CHARS.search(content):
            return content
        else:
            return m.group(0)

    @staticmethod
    def _replace_table_block(match: re.Match) -> str:
        """处理表格块"""
        block_text = match.group('table_block')
        lines = block_text.strip('\n').split('\n')

        parsed_table = []
        for line in lines:
            line_stripped = line.strip()
            if re.match(r'^\|\s*[-:]+\s*(\|\s*[-:]+\s*)+\|?$', line_stripped):
                continue
            columns = [col.strip() for col in line_stripped.split('|') if col.strip() != '']
            if columns:
                parsed_table.append(columns)

        if not parsed_table:
            return ""

        headers = parsed_table[0]
        data_rows = parsed_table[1:] if len(parsed_table) > 1 else []

        lines_for_tts = []
        if len(parsed_table) == 1:
            only_line_str = ", ".join(parsed_table[0])
            lines_for_tts.append(f"单行表格：{only_line_str}")
        else:
            lines_for_tts.append(f"表头是：{', '.join(headers)}")
            for i, row in enumerate(data_rows, start=1):
                row_str_list = []
                for col_index, cell_val in enumerate(row):
                    if col_index < len(headers):
                        row_str_list.append(f"{headers[col_index]} = {cell_val}")
                    else:
                        row_str_list.append(cell_val)
                lines_for_tts.append(f"第 {i} 行：{', '.join(row_str_list)}")

        return "\n".join(lines_for_tts) + "\n"

    # 预编译所有正则表达式
    REGEXES = [
        (re.compile(r'```.*?```', re.DOTALL), ''),  # 代码块
        (re.compile(r'^#+\s*', re.MULTILINE), ''),  # 标题
        (re.compile(r'(\*\*|__)(.*?)\1'), r'\2'),  # 粗体
        (re.compile(r'(\*|_)(?=\S)(.*?)(?<=\S)\1'), r'\2'),  # 斜体
        (re.compile(r'!\[.*?\]\(.*?\)'), ''),  # 图片
        (re.compile(r'\[(.*?)\]\(.*?\)'), r'\1'),  # 链接
        (re.compile(r'^\s*>+\s*', re.MULTILINE), ''),  # 引用
        (
            re.compile(r'(?P<table_block>(?:^[^\n]*\|[^\n]*\n)+)', re.MULTILINE),
            _replace_table_block
        ),
        (re.compile(r'^\s*[*+-]\s*', re.MULTILINE), '- '),  # 列表
        (re.compile(r'\$\$.*?\$\$', re.DOTALL), ''),  # 块级公式
        (
            re.compile(r'(?<![A-Za-z0-9])\$([^\n$]+)\$(?![A-Za-z0-9])'),
            _replace_inline_dollar
        ),
        (re.compile(r'\n{2,}'), '\n'),  # 多余空行
    ]

    @staticmethod
    def clean_markdown(text: str) -> str:
        """清理Markdown文本"""
        for regex, replacement in MarkdownCleaner.REGEXES:
            text = regex.sub(replacement, text)
        return text.strip()


def filter_parentheses_content(text: str) -> str:
    """过滤括号及其包含的内容
    
    去除文本中的括号及其内容，包括：
    - 英文括号：(...)
    - 中文括号：（...）
    
    这样可以避免对话中掺杂旁白或动作描写，保持对话的纯洁性。
    
    注意：为了支持英文流式输出，不处理空格，避免单词粘连
    
    Args:
        text: 待处理的文本
        
    Returns:
        过滤后的文本
    
    Examples:
        >>> filter_parentheses_content("你好(微笑)，今天天气真好")
        "你好，今天天气真好"
        >>> filter_parentheses_content("我在想（思考中）这个问题")
        "我在想这个问题"
    """
    if not text:
        return ""
    
    # 过滤英文括号及其内容
    filtered_text = re.sub(r'\([^)]*\)', '', text)
    
    # 过滤中文括号及其内容
    filtered_text = re.sub(r'（[^）]*）', '', filtered_text)
    
    # 不再清理空格，完全保留原始空格以支持英文流式输出
    return filtered_text


def filter_tool_call_info(text: str) -> str:
    """过滤工具调用信息和括号内容
    
    去除文本中：
    1. $符号包裹的工具调用信息，如：
       $🔧 正在调用工具: tool_name$
       $✅ 工具结果: result$
       $❌ 错误信息$
    2. 括号及其包含的内容（旁白、动作描写等）
    
    注意：为了支持英文流式输出，不处理空格，避免单词粘连
    
    Args:
        text: 待处理的文本
        
    Returns:
        过滤后的文本
    """
    # 使用正则表达式去除$符号包裹的内容
    # 匹配 $...$ 模式，其中...可以包含任何字符（除了$），包括换行符
    filtered_text = re.sub(r'\$[^$]*\$', '', text)
    
    # 过滤括号及其内容
    filtered_text = filter_parentheses_content(filtered_text)
    
    # 清理多余的空行（但保留空格）
    filtered_text = re.sub(r'\n\s*\n', '\n', filtered_text)  # 去除多余空行
    
    # 不再去除行首行尾空格，不再strip，以支持英文流式输出
    return filtered_text


def analyze_emotion(text: str) -> str:
    """分析文本情绪（简化版）"""
    # 简单的情绪分析，可以根据需要扩展
    emotion_keywords = {
        "happy": ["开心", "快乐", "高兴", "愉快", "哈哈"],
        "sad": ["难过", "伤心", "悲伤", "难受"],
        "angry": ["生气", "愤怒", "恼火"],
        "surprised": ["惊讶", "吃惊", "震惊"],
    }
    
    for emotion, keywords in emotion_keywords.items():
        for keyword in keywords:
            if keyword in text:
                return emotion
    
    return "neutral"