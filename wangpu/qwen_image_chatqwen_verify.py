import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
CONTENT_AGENT_DIR = ROOT_DIR / "content-builder-agent"
sys.path.insert(0, str(CONTENT_AGENT_DIR))

from qwen_image_tool import generate_qwen_image


PROMPT = (
    "生成一张可爱的儿童AI陪伴机器人海报，中文标题为“童芯智造”，"
    "高清、温暖、科技感、适合儿童教育场景。"
)


def main() -> None:
    out_path = ROOT_DIR / "qwen_image_verify_output" / "qwen_image_result.png"
    result = generate_qwen_image(PROMPT, out_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("图片已保存到:", out_path)


if __name__ == "__main__":
    main()
