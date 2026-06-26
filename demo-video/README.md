# 产品演示视频执行包

本目录用于 3 分钟产品演示视频录制。核心流程是：

孩子发问 -> AI 连续追问 -> AI 主动生成绘本 -> AI 主动生成游戏 -> App 同步与任务规划流 -> 历史产物展示 -> 闲聊问答收尾。

## 文件

- `demo_script.md`：完整台词、录制计划、剪辑方案、分镜设计。
- `demo_prompt.md`：可注入主模型的临时演示提示词。

## 推荐启动方式

在 `wangpu/content-builder-agent` 目录运行：

```powershell
python content_writer.py --config main_agent.demo.yaml
```

如果要让服务端、App 或小智固件链路也临时使用演示提示词，先设置环境变量再启动服务：

```powershell
$env:CONTENT_BUILDER_CONFIG = "main_agent.demo.yaml"
python -m content_builder.server
```

演示结束后关闭该终端，或执行：

```powershell
Remove-Item Env:CONTENT_BUILDER_CONFIG
```

单轮试演也可以运行：

```powershell
python content_writer.py --config main_agent.demo.yaml "好奇星伴，杯子是不是着火了？"
```

默认 `main_agent.yaml` 不会被修改；演示结束后，继续使用原配置即可恢复日常模式。
