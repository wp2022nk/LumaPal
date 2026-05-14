import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit


DASHSCOPE_API_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_QWEN_IMAGE_MODEL = "qwen-image-2.0-pro"
DEFAULT_IMAGE_SIZE = "1024*1024"

# Prefer the real environment variable. The fallback keeps the existing local
# demo script working in the same way it did before.
LOCAL_DASHSCOPE_API_KEY = "sk-4022a3931c75477f95b921f7dfacea8d"

DOWNLOAD_CONNECT_TIMEOUT = 20
DOWNLOAD_READ_TIMEOUT = 180
DOWNLOAD_RETRIES_PER_URL = 2


def _response_to_dict(response) -> dict:
    if isinstance(response, dict):
        return response
    if hasattr(response, "to_dict"):
        return response.to_dict()
    if hasattr(response, "__dict__"):
        return response.__dict__
    return json.loads(json.dumps(response, default=str))


def _extract_image_url(response_dict: dict) -> str:
    try:
        content = response_dict["output"]["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            "Qwen image response did not contain output.choices[0].message.content"
        ) from exc

    for item in content:
        if isinstance(item, dict) and item.get("image"):
            return item["image"]

    raise RuntimeError("Qwen image response did not contain an image URL")


def _candidate_download_urls(image_url: str) -> list[str]:
    """Return OSS URLs to try when the accelerate endpoint is unreachable."""
    urls = [image_url]
    split_url = urlsplit(image_url)
    host = split_url.netloc

    env_hosts = [
        item.strip()
        for item in os.environ.get("QWEN_IMAGE_DOWNLOAD_HOSTS", "").split(",")
        if item.strip()
    ]

    if ".oss-accelerate.aliyuncs.com" in host:
        bucket = host.split(".oss-accelerate.aliyuncs.com", 1)[0]
        fallback_hosts = env_hosts or [
            "oss-cn-beijing.aliyuncs.com",
            "oss-cn-shanghai.aliyuncs.com",
            "oss-cn-hangzhou.aliyuncs.com",
            "oss-cn-shenzhen.aliyuncs.com",
            "oss-accelerate-overseas.aliyuncs.com",
        ]
        for fallback_host in fallback_hosts:
            new_host = f"{bucket}.{fallback_host}"
            urls.append(urlunsplit(split_url._replace(netloc=new_host)))
    elif env_hosts:
        bucket = host.split(".", 1)[0]
        for fallback_host in env_hosts:
            urls.append(urlunsplit(split_url._replace(netloc=f"{bucket}.{fallback_host}")))

    return list(dict.fromkeys(urls))


def download_image_with_fallbacks(image_url: str, output_path: Path) -> dict:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f"{output_path.name}.tmp")

    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/png,image/*,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 QwenImageDownloader/1.0",
    }
    errors = []

    for url in _candidate_download_urls(image_url):
        for attempt in range(1, DOWNLOAD_RETRIES_PER_URL + 1):
            try:
                print(
                    f"[qwen_image] downloading attempt {attempt}: {urlsplit(url).netloc}",
                    flush=True,
                )
                request = Request(url, headers=headers)
                with urlopen(request, timeout=DOWNLOAD_READ_TIMEOUT) as response:
                    status = getattr(response, "status", response.getcode())
                    if status >= 400:
                        raise RuntimeError(f"HTTP {status}")
                    total = 0
                    with open(tmp_path, "wb") as file:
                        while True:
                            chunk = response.read(1024 * 256)
                            if not chunk:
                                break
                            if chunk:
                                file.write(chunk)
                                total += len(chunk)

                if total <= 0:
                    raise RuntimeError("downloaded file is empty")

                tmp_path.replace(output_path)
                return {
                    "path": str(output_path),
                    "bytes": total,
                    "download_url": url,
                }
            except Exception as exc:
                errors.append(f"{urlsplit(url).netloc} attempt {attempt}: {exc}")
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
                if attempt < DOWNLOAD_RETRIES_PER_URL:
                    time.sleep(2 * attempt)

    raise RuntimeError("Image download failed after all fallbacks: " + " | ".join(errors))


def generate_qwen_image(
    prompt: str,
    output_path: Path,
    *,
    size: str = DEFAULT_IMAGE_SIZE,
    model: str | None = None,
    watermark: bool = False,
    prompt_extend: bool = True,
) -> dict:
    from dashscope import MultiModalConversation
    import dashscope

    api_key = os.environ.get("DASHSCOPE_API_KEY") or LOCAL_DASHSCOPE_API_KEY
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")

    dashscope.base_http_api_url = DASHSCOPE_API_URL
    model_name = model or os.environ.get("QWEN_IMAGE_MODEL", DEFAULT_QWEN_IMAGE_MODEL)
    messages = [
        {
            "role": "user",
            "content": [{"text": prompt}],
        }
    ]

    response = MultiModalConversation.call(
        api_key=api_key,
        model=model_name,
        messages=messages,
        result_format="message",
        stream=False,
        watermark=watermark,
        prompt_extend=prompt_extend,
        size=size,
    )
    response_dict = _response_to_dict(response)

    status_code = response_dict.get("status_code")
    if status_code and status_code != 200:
        raise RuntimeError(
            "Qwen image generation failed: "
            f"{response_dict.get('code', '')} {response_dict.get('message', '')}"
        )

    image_url = _extract_image_url(response_dict)
    download_result = download_image_with_fallbacks(image_url, Path(output_path))
    return {
        "model": model_name,
        "size": size,
        "image_url": image_url,
        **download_result,
    }
