# 绘本有声化 & 成长轨迹报告 优化计划（执行版）

> 上下文：本文件接续 `storybook-audio-and-growth-report-plan.md` 的设计稿，作为可执行的实现路线。前序阶段已交付：
> - `roadshow-final-products/storybook/book.html` 已重写为"单页居中 + 点击朗读 + 自动续播 + 键盘控制 + speechSynthesis 兜底"。
> - `roadshow-final-products/storybook/audio/page-00-cover.wav` ~ `page-05.wav` 共 6 段音频已合成。
> - `roadshow-final-products/growth-report/report-data.json` 已扩展 `exploration_photos / artifact_gallery / highlights`，每个 `curiosity_themes[*]` 与 `works[*]` 已附 `evidence_image / cover_image / link`。
> - `wangpu/content-builder-agent/skills/growth-trajectory-report/scripts/render_growth_report.py` 已升级（支持新字段、`file://` 与 `data:` 双模式）。
>
> 本文件专注**剩余的执行收尾**与**双 skill 同步**。

---

## 1. 当前状态核对

| 项 | 状态 | 备注 |
| --- | --- | --- |
| `roadshow-final-products/storybook/book.html` | ✅ 已重写 | 单页居中 / 点击朗读 / 键盘支持 / speechSynthesis 兜底 |
| `roadshow-final-products/storybook/audio/*.wav` (×6) | ✅ 已生成 | 通过 `_synthesize_wav` 直接合成 |
| `roadshow-final-products/growth-report/report-data.json` | ✅ 已扩展 | exploration_photos(8) / artifact_gallery(4) / highlights(5) + 图片字段 |
| `wangpu/.../growth-trajectory-report/scripts/render_growth_report.py` | ✅ 已重写 | 支持新字段、file:// 与 data: 双模式 |
| `roadshow-final-products/growth-report/index.html` | ❌ 仍为旧版 | 未执行 `render_growth_report.py` 重新生成 |
| `roadshow-final-products/growth-report/growth-report.pdf` | ❌ 仍为旧版 | 同上 |
| `output/roadshow-final-products/growth-report/*` | ❌ 未同步 | deepagent 渲染目标，仍是旧版 |
| `output/roadshow-final-products/storybook/*` | ⚠️ 缺 audio | book.html 仍为旧 6 页静态版，无 audio/ |
| `wangpu/.../storybook/SKILL.md` | ❌ 未更新 | 仍描述 PNG + PDF 流程 |
| `wangpu/.../storybook/scripts/render_storybook.py` | ❌ 未更新 | 未集成 TTS 与新 audio HTML 模板 |
| `wangpu/.../growth-trajectory-report/SKILL.md` | ❌ 未更新 | 未要求采集图片 |
| `wangpu/.../growth-trajectory-report/references/report-design.md` | ❌ 未更新 | 未涉及"图片走廊" |
| `wangpu/.../growth-trajectory-report/scripts/render_growth_report.py` | ✅ 已升级（重复写一次） | 用于固化 |

---

## 2. 关键事实与决策

### 2.1 双目录并存

- `output/roadshow-final-products/...` 是 deepagent 虚拟路径 `/output/...` 映射到磁盘的根（`render_*.py` 的 `OUTPUT_ROOT`），由 deepagent 渲染脚本直接写。
- `roadshow-final-products/...` 是最终 demo / 路演展示目录，目前手工维护。
- 现有 `roadshow-final-products/storybook/book.html` 是**手写**的优化版（含 `file:///D:/...` 绝对路径），并未通过 skill 脚本生成。这暗示：用户希望"先把 demo 调到位"，再让 skill 跟上。
- 决策：
  1. **skill 脚本走 `/output/` 契约不变**（避免破坏 deepagent 工作流）。
  2. **执行阶段**：(a) 让 skill 脚本能在 `output/roadshow-final-products/storybook/` 下重新生成产物（覆盖现有旧 book.html），并把 `audio/` 一起带过去；(b) 同时复制/同步到 `roadshow-final-products/` demo 目录，使两边一致。

### 2.2 音频路径硬编码

- 现 `roadshow-final-products/storybook/book.html` 中 `AUDIO_BASE = "file:///D:/WorkSpace/VScodeProject/2026_AIGC/output/roadshow-final-products/storybook/audio/"`，但音频真实位置在 `roadshow-final-products/storybook/audio/`。
- 浏览器双击 `roadshow-final-products/storybook/book.html` 时**找不到音频**，会自动走 `speechSynthesis` 兜底。
- 决策：
  - **立即修**：把 `AUDIO_BASE` 改为相对当前 HTML 的 `./audio/` 路径，避免硬编码绝对盘符。
  - 同时把音频复制到 `output/roadshow-final-products/storybook/audio/`（与脚本契约一致），使 skill 重新渲染后的 HTML 也能找到音频。

### 2.3 TTS 工具的二次使用方式

- `content_builder/tools/tts.py` 已经实现 `_synthesize_wav` 私有函数。
- `@tool generate_tts_audio` 装饰版要求 `ToolRuntime`，渲染脚本（CLI 调用）不能直接用。
- 决策：
  - **不修改** `tts.py`（保持单一职责）。
  - skill 脚本中**直接 import** `_synthesize_wav` 与 `_create_wav_header` 内部函数，传入 `load_main_config()` 与解析好的输出路径。失败时写 `*-error.txt` 并继续，不中断主流程。
  - **不**把 `generate_tts_audio` 注册到 `TOOL_REGISTRY`（最小改动原则；后续如 deepagent 工具调用需要再注册）。

### 2.4 路径解析对齐

- `render_storybook.py` 的 `resolve_artifact_path` 强制路径必须在 `OUTPUT_ROOT`（即 `WORKSPACE_ROOT / "output"`）下。
- 当前 TTS 工具 `resolve_audio_output_path` 也是同样限制。
- 决策：渲染时把 TTS 目标路径写成 `/output/roadshow-final-products/storybook/audio/page-XX.wav`；同步后端把 `roadshow-final-products/storybook/audio/` 复制到 `output/roadshow-final-products/storybook/audio/`。

---

## 3. 实施步骤（顺序执行）

### 3.1 阶段 D：补完 demo（roadshow-final-products 现场）

> 目标：让现有 demo HTML 在浏览器双击时**真的能播放音频**，而不是只走 speechSynthesis。

| ID | 动作 | 文件 |
| --- | --- | --- |
| D1 | 修正 `book.html` 的 `AUDIO_BASE` 为相对路径 `./audio/`，移除硬编码盘符。 | `roadshow-final-products/storybook/book.html` |
| D2 | 复制 `roadshow-final-products/storybook/audio/` 到 `output/roadshow-final-products/storybook/audio/`（保持与 skill 脚本契约一致）。 | 目录复制 |
| D3 | 浏览器双击 `roadshow-final-products/storybook/book.html` 验证：能听到音频（不再 fallback）；删除单个 WAV 后该页自动 fallback 到 speechSynthesis。 | 手动验证 |

### 3.2 阶段 E：skill 升级 — storybook 有声化

| ID | 动作 | 文件 |
| --- | --- | --- |
| E1 | 在 `render_storybook.py` 中**新增** `--audio` 标志（默认 `True`），并在 `main()` 中：<br>① 加载 `book.json` 后调用 `tts._synthesize_wav(text, audio/page-XX.wav)` 合成所有页音频；<br>② 任一页失败写 `*-error.txt` 并跳过，不中断；<br>③ 合成完生成 `book.html`（**用新"点击朗读"模板**）+ PDF。 | `wangpu/content-builder-agent/skills/storybook/scripts/render_storybook.py` |
| E2 | 在 `render_storybook.py` 中**抽出** `render_audiobook_html(book, audio_map)` 私有函数，输出与 `roadshow-final-products/storybook/book.html` 相同结构（含 `data-page` / `data-text` / `file://` 或相对路径，speechSynthesis 兜底，键盘支持）。 | 同上 |
| E3 | 更新 `SKILL.md` ：<br>① 新增 **"Web Audio Storybook"** 小节：说明 `book.html` 改为"单页居中 + 点击朗读 + 自动续播"，`audio/page-*.wav` 为必需产物（缺失则前端自动 fallback）；<br>② 在 **Required Output** 加入 `audio/page-*.wav` 目录与命名规则；<br>③ 在 **Rendering** 段说明 `--audio` 标志；<br>④ 在 **Completion Checklist** 加"音频产物齐备"检查项。 | `wangpu/content-builder-agent/skills/storybook/SKILL.md` |
| E4 | **不**注册 `generate_tts_audio` 到 `TOOL_REGISTRY`（保持最小改动；脚本直接 import 私有函数）。 | `wangpu/content-builder-agent/content_builder/tools/__init__.py`（不动） |

### 3.3 阶段 F：skill 升级 — growth-trajectory-report 图片化

| ID | 动作 | 文件 |
| --- | --- | --- |
| F1 | 更新 `SKILL.md` ：<br>① 新增 **"Image-aware Report"** 段：要求扫描 `history/<date>/uploads/images/*` 与 `history/<date>/artifacts/**/cover.png` 与 `roadshow-final-products/storybook/images/*` 与 `roadshow-final-products/game/index.html` 等产物，写入 `report-data.json`；<br>② 更新数据 schema（新增 `exploration_photos / artifact_gallery / highlights / curiosity_themes[*].evidence_image / works[*].cover_image` 等）；<br>③ **Completion Checklist** 加"每个 themes 有图、每个 works 有封面、exploration_photos ≥ 6"等检查。 | `wangpu/content-builder-agent/skills/growth-trajectory-report/SKILL.md` |
| F2 | 在 `references/report-design.md` 新增 **"Photo & Artifact Inclusion"** 段：<br>① 主题卡片必须配图（`evidence_image`），缺图时降级为图标占位；<br>② 作品卡片用 `cover_image` + `link` 形成"作品走廊"；<br>③ 探险相册使用 `aspect-ratio: 1` 缩略图，caption 在卡片底部；<br>④ 童言 / 亮点用左右交替时间线渲染。 | `wangpu/content-builder-agent/skills/growth-trajectory-report/references/report-design.md` |
| F3 | 已在 `render_growth_report.py` 中实现新字段渲染（无新改动），但需确认：<br>① `image_src()` 路径解析优先用 `WORKSPACE_ROOT` 拼相对路径，回退到绝对盘符；<br>② `--embed-images` 标志在 PDF 模式下强制走 `data:` 内嵌，规避 Chrome headless 的 `file://` 限制；<br>③ 输出文件大小阈值（`>2_000_000`）合理（≤ 2 MB 才内嵌，超出仍走 `file://`，由用户在浏览器打开 HTML）。 | `wangpu/content-builder-agent/skills/growth-trajectory-report/scripts/render_growth_report.py`（已升级） |

### 3.4 阶段 G：重新生成现场产物

| ID | 动作 | 命令 |
| --- | --- | --- |
| G1 | 重新生成绘本 HTML（覆盖 `output/roadshow-final-products/storybook/book.html` 与 `roadshow-final-products/storybook/book.html`） | `& "D:\Robort_Learn\envs\deepagents\python.exe" wangpu/content-builder-agent/skills/storybook/scripts/render_storybook.py --book /output/roadshow-final-products/storybook/book.json --output-dir /output/roadshow-final-products/storybook --audio` |
| G2 | 重新生成成长报告 HTML + PDF | `& "D:\Robort_Learn\envs\deepagents\python.exe" wangpu/content-builder-agent/skills/growth-trajectory-report/scripts/render_growth_report.py --data /output/roadshow-final-products/growth-report/report-data.json --output-dir /output/roadshow-final-products/growth-report --pdf --embed-images` |
| G3 | 同步到 demo 目录 | `Copy-Item` 把 `output/roadshow-final-products/storybook/book.html` 与 `audio/` 复制到 `roadshow-final-products/storybook/`；把 `output/roadshow-final-products/growth-report/index.html` 与 `growth-report.pdf` 复制到 `roadshow-final-products/growth-report/`。 |

### 3.5 阶段 H：独立验证两个 skill

| ID | 动作 | 命令 |
| --- | --- | --- |
| H1 | 在临时目录用最小 book.json 验证 storybook skill | 见 §5.1 |
| H2 | 在临时目录用最小 report-data.json 验证 growth-trajectory-report skill | 见 §5.2 |
| H3 | 记录 SKILL.md 与脚本 CLI 签名是否一致 | 文档对照 |

---

## 4. 文件变更清单

### 4.1 必改文件

| 路径 | 动作 | 摘要 |
| --- | --- | --- |
| `roadshow-final-products/storybook/book.html` | 编辑 | `AUDIO_BASE` 改相对路径 |
| `wangpu/content-builder-agent/skills/storybook/scripts/render_storybook.py` | 改写 | 新增 `--audio`、TTS 内联、新 HTML 模板 |
| `wangpu/content-builder-agent/skills/storybook/SKILL.md` | 编辑 | 新增 Web Audio Storybook 段、Required Output 加入 audio/、Completion Checklist 更新 |
| `wangpu/content-builder-agent/skills/growth-trajectory-report/SKILL.md` | 编辑 | 新增 Image-aware Report 段、schema 更新、Checklist 更新 |
| `wangpu/content-builder-agent/skills/growth-trajectory-report/references/report-design.md` | 编辑 | 新增 Photo & Artifact Inclusion 段 |

### 4.2 必动产物（重新生成或复制）

| 路径 | 动作 |
| --- | --- |
| `output/roadshow-final-products/storybook/book.html` | 重新生成（用升级后脚本） |
| `output/roadshow-final-products/storybook/audio/*.wav` | 脚本内联合成（与 `roadshow-final-products/.../audio/` 同步） |
| `output/roadshow-final-products/storybook/didi-cloud-adventure.pdf` | 重新生成 |
| `output/roadshow-final-products/growth-report/index.html` | 重新生成 |
| `output/roadshow-final-products/growth-report/growth-report.pdf` | 重新生成（带 `--embed-images`） |
| `roadshow-final-products/storybook/book.html` | 从 `output/` 复制（确保 demo 与 skill 输出一致） |
| `roadshow-final-products/storybook/audio/*.wav` | 从 `output/` 复制 |
| `roadshow-final-products/growth-report/index.html` | 从 `output/` 复制 |
| `roadshow-final-products/growth-report/growth-report.pdf` | 从 `output/` 复制 |

### 4.3 不动文件

| 路径 | 原因 |
| --- | --- |
| `content_builder/tools/tts.py` | 直接 import 私有函数即可 |
| `content_builder/config.py` | 只读取，不修改 |
| `content_builder/tools/__init__.py` | 不注册 TTS 到 `TOOL_REGISTRY`（最小改动） |
| `roadshow-final-products/storybook/book.json / story.md / visual-bible.md / source.md` | 内容数据稳定 |
| `history/` | 数据源，不动 |

---

## 5. 验证步骤

### 5.1 独立验证 storybook skill

```powershell
# 1. 准备最小测试 book.json
$testDir = "d:\WorkSpace\VScodeProject\2026_AIGC\output\_test_storybook"
New-Item -ItemType Directory -Force -Path "$testDir\images" | Out-Null
# 从 roadshow-final-products/storybook 复制 2 张 PNG + book.json 缩为 2 页
Copy-Item "d:\WorkSpace\VScodeProject\2026_AIGC\output\roadshow-final-products\storybook\images\page-00-cover.png" "$testDir\images\page-00-cover.png"
Copy-Item "d:\WorkSpace\VScodeProject\2026_AIGC\output\roadshow-final-products\storybook\images\page-01.png" "$testDir\images\page-01.png"
# 写一个最小 book.json（slug=test、2 页）

# 2. 跑脚本
& "D:\Robort_Learn\envs\deepagents\python.exe" wangpu/content-builder-agent/skills/storybook/scripts/render_storybook.py --book /output/_test_storybook/book.json --output-dir /output/_test_storybook --audio

# 3. 期望
# - audio/page-00-cover.wav, page-01.wav 存在
# - book.html 包含 "stage", "data-page", "AUDIO_BASE"
# - <slug>.pdf 存在
```

### 5.2 独立验证 growth-trajectory-report skill

```powershell
# 1. 准备最小 report-data.json（必填字段：title, slug, child_name, period, summary, metrics, abilities）
# 2. 跑脚本（不带 --pdf 先验证 HTML）
& "D:\Robort_Learn\envs\deepagents\python.exe" wangpu/content-builder-agent/skills/growth-trajectory-report/scripts/render_growth_report.py --data /output/_test_report/report-data.json --output-dir /output/_test_report
# 3. 期望：index.html 存在，浏览器打开有 Hero / Metrics / Radar / Timeline 等

# 4. 跑 PDF 模式
& "D:\Robort_Learn\envs\deepagents\python.exe" wangpu/content-builder-agent/skills/growth-trajectory-report/scripts/render_growth_report.py --data /output/_test_report/report-data.json --output-dir /output/_test_report --pdf --embed-images
# 5. 期望：growth-report.pdf 存在且 > 50 KB
```

### 5.3 静态 / 视觉验证

- `book.html`（`roadshow-final-products/...`）双击打开：
  - 单页居中，背景温暖渐变，封面/正文/收尾排版美观
  - **无** `<button>`、`<input type="button">`、`.control-btn`、`.nav-btn` 之类控件
  - 点击任意位置 → 控制台 `Audio play`，WAV 加载自 `./audio/`
  - 音频结束 → fade-out → 切下页 → fade-in → 继续朗读
  - 删除单个 WAV → 重新打开 → 该页自动走 `speechSynthesis`
  - ←/→ 翻页、Space 重播、Esc 停止
- `index.html`（`roadshow-final-products/growth-report/`）双击打开：
  - Hero 渐变卡片、metrics 4 列、雷达图 + 能力表格
  - 主题卡片 3 列，每张含图、insight、badge
  - 作品走廊 4 列封面墙、探险相册网格、亮点时间线左右交替
  - 移动端 `<=860px` 全部 grid 退化为单列
  - 打印/Chrome headless → PDF > 50 KB

---

## 6. 风险与回退

| 风险 | 缓解 |
| --- | --- |
| TTS API key 缺失 / DashScope 限流 | `_synthesize_wav` 抛错 → 单页 `*-error.txt` → 浏览器自动 `speechSynthesis` 兜底 |
| Chrome headless 渲染 PDF 失败 | 旧 HTML 备份（`book.html.bak`）；脚本对 PDF 失败不抛 fatal，只 print stderr |
| 图片路径在打包/部署时失效 | HTML 走 `Path.as_uri()`（`file://`）便于本地双击；PDF 走 `data:` 内嵌 |
| 音频文件体积大 | 单页 5-15 s、24kHz/16bit/单声道 PCM，约 240-720 KB；6 页总计 < 5 MB |
| skill 输出与 demo 不一致 | G3 阶段明确从 `output/` 复制到 `roadshow-final-products/` 同步两边 |
| `output/roadshow-final-products/storybook/` 下旧 book.html 未替换 | G1 用新脚本显式覆盖；G3 同步到 demo |

---

## 7. 决策默认值

| 决策点 | 默认选择 |
| --- | --- |
| 朗读声音来源 | Qwen DashScope TTS（`config.voice.tts`），缺 key 时浏览器 `speechSynthesis` 兜底 |
| 翻页行为 | 音频结束 → fade → 自动下一页；首次点击未点击时仅显示当前页 |
| 按钮 | 0 个；保留极轻 hint（`♪ 点击朗读`）与底部圆点（非按钮） |
| 单页 vs 翻书双页 | 单页（避免误点） |
| PDF | 保留（与新 HTML 共存） |
| 报告图片来源 | `history/.../uploads/images/*` + `history/.../artifacts/**/cover.png` + `roadshow-final-products/storybook/images/*` |
| 报告"童言"来源 | `report-data.json.hero_quote` + 历次对话抽取（`highlights[].type="童言"`） |
| 是否注册 TTS 到 `TOOL_REGISTRY` | 否（最小改动） |
| 音频路径 | HTML 内相对 `./audio/`（demo 友好）；脚本内 `/output/...`（契约一致） |

---

## 8. 落地后产物清单（最终态）

```
output/roadshow-final-products/
  storybook/
    audio/
      page-00-cover.wav
      page-01.wav ... page-05.wav
    images/page-*.png (×6)
    book.html             # 新有声版（由升级后脚本生成）
    book.json
    didi-cloud-adventure.pdf
  growth-report/
    report-data.json      # 含探索照片 / 作品走廊 / 亮点时间线
    index.html            # 新版（含新 section）
    growth-report.pdf     # 重新生成（data: 内嵌图片）

roadshow-final-products/    # demo 目录（与 output/ 同步）
  storybook/
    audio/*.wav (×6)
    book.html             # 从 output/ 复制
    ...
  growth-report/
    index.html
    growth-report.pdf
    report-data.json

wangpu/content-builder-agent/skills/
  storybook/
    SKILL.md              # 新增 Web Audio Storybook 段
    scripts/render_storybook.py  # 内联 TTS + 新模板
  growth-trajectory-report/
    SKILL.md              # 新增 Image-aware Report 段
    references/report-design.md  # 新增 Photo & Artifact Inclusion
    scripts/render_growth_report.py  # 渲染新字段（已升级）
```

---

## 9. 开放问题（默认回答）

| # | 问题 | 默认回答 |
| --- | --- | --- |
| 1 | 朗读音色 | 使用 `config.voice.tts.voice` 默认值 |
| 2 | 翻页行为 | 音频结束自动翻页 |
| 3 | 报告封面墙是否包含 `roadshow-final-products/game/index.html` | 是（`artifact_gallery` 第 2 条） |
| 4 | 报告字体 | 用系统字体（YaHei / Noto Sans SC），不引入新依赖 |

如用户希望偏离默认值，请在 Plan 审阅时告知；否则按默认执行。
