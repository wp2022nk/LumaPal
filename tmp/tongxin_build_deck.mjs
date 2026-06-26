import fs from "node:fs/promises";
import path from "node:path";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const root = "D:/WorkSpace/VScodeProject/2026_AIGC";
const outputDir = path.join(root, "outputs");
const assetRoot = path.join(outputDir, "童芯智造_产品演示_assets");
const scenesDir = path.join(assetRoot, "scenes");
const avatarsDir = path.join(assetRoot, "avatars");
const qaDir = path.join(assetRoot, "qa");
const finalPptx = path.join(outputDir, "童芯智造_产品演示_水循环故事.pptx");

const W = 1280;
const H = 720;
const font = "Microsoft YaHei";

const colors = {
  bg: "#FFF2D3",
  ink: "#392719",
  sub: "#7B5B3B",
  orange: "#F47B20",
  blue: "#1673E6",
  green: "#15A96B",
  line: "#F4C777",
  panel: "#FFFDF7",
  panel2: "#FFF7E8",
  child: "#FFF6E8",
  mom: "#FFF1DE",
  ai: "#EAF8DF",
  shadow: "2px 7px 20px #A96F24/18",
};

const avatarPaths = {
  child: path.join(avatarsDir, "avatar_xiaoxing.png"),
  mom: path.join(avatarsDir, "avatar_mother.png"),
  ai: path.join(avatarsDir, "avatar_robot.png"),
};

const slides = [
  {
    no: "01",
    title: "万物问答：孩子和AI聊水循环",
    subtitle: "从生活现象出发，用多模态对话激发孩子的科学好奇心",
    tag: "白天探索时刻",
    scene: "scene01_water_chat.png",
    highlight: "白天从一杯热茶出发，AI把生活现象转成科学追问。",
    chats: [
      ["child", "小星", "好奇星伴，你看！妈妈的杯子在冒烟，是着火了吗？"],
      ["ai", "好奇星伴", "不是着火哦，那是热水变出来的水蒸气宝宝。"],
      ["child", "小星", "为什么我抓不住它？"],
      ["ai", "好奇星伴", "它特别小，遇到凉空气会变成小水珠。"],
      ["child", "小星", "那外面的雨，也是这样变出来的吗？"],
      ["ai", "好奇星伴", "对啦！太阳把水晒成水蒸气，飞到天上变成云，云装不下就变成雨。"],
      ["child", "小星", "所以云就是天上的大茶杯！"],
    ],
    features: ["AI多轮语音对话", "拍学机识别+VQA", "生活场景化学习", "引导式思维", "知识迁移", "即时激励"],
  },
  {
    no: "02",
    title: "亲子共创：把早上的探索变成睡前故事",
    subtitle: "孩子、家长和AI一起，把知识变成专属家庭故事",
    tag: "夜晚共创时刻",
    scene: "scene02_parent_story.png",
    highlight: "晚上由孩子和妈妈共同出演，把知识变成家庭故事。",
    chats: [
      ["ai", "好奇星伴", "我把早上的茶杯热气、下雨和冰箱白雾整理成故事素材啦。"],
      ["child", "小星", "我要让小水滴叫滴滴！它先从茶杯里飞出去。"],
      ["mom", "妈妈", "那妈妈可以当云朵妈妈吗？"],
      ["ai", "好奇星伴", "当然可以。小星当小水滴，妈妈当云朵，故事开始！"],
      ["child", "小星", "茶杯茶杯，谢谢你！我要去天上冒险啦！"],
      ["mom", "妈妈", "欢迎你呀小滴滴，云朵妈妈这里软软的。"],
      ["ai", "好奇星伴", "云朵越来越重了，小水滴们会变成雨，落到花园再回到天空。"],
    ],
    features: ["亲子共创剧场", "低门槛家长参与", "多端联动", "体感互动", "知识内化", "精彩片段保存"],
  },
  {
    no: "03",
    title: "AI绘本生成：故事投影到电视上",
    subtitle: "把对话、选择和手绘作品，实时生成孩子自己的绘本",
    tag: "绘本生成时刻",
    scene: "scene03_picturebook_tv.png",
    highlight: "对话、选择、手绘作品自动沉淀为可分享的亲子绘本。",
    chats: [
      ["ai", "好奇星伴", "闯关选择和刚才的故事，已经生成专属绘本啦。"],
      ["child", "小星", "我真的在绘本里！我是小星向导！"],
      ["mom", "妈妈", "最后一页还能放你画的窗户和滴滴吗？"],
      ["ai", "好奇星伴", "可以，拍学机已经识别手绘作品，正在融合进结尾页。"],
      ["child", "小星", "滴滴会对我说谢谢吗？"],
      ["ai", "好奇星伴", "会哦。旁白写好了：谢谢你带我完成水循环大冒险。"],
      ["ai", "好奇星伴", "绘本PDF、亲子朗读版、小游戏版和家长端回顾版已同步。"],
    ],
    features: ["AIGC实时绘本生成", "绘本代入", "手绘/语音融合", "电视端投屏", "家长端同步", "多版本内容"],
  },
  {
    no: "04",
    title: "AI主动生成小游戏：让知识变成闯关记忆",
    subtitle: "把绘本故事转化为任务关卡，在游戏中巩固理解",
    tag: "游戏生成时刻",
    scene: "scene04_game.png",
    highlight: "AI主动把故事变成游戏，让知识在反复闯关中被记住。",
    chats: [
      ["ai", "好奇星伴", "小星，想不想把滴滴的故事变成小游戏？"],
      ["child", "小星", "要！我要帮滴滴回家！"],
      ["mom", "妈妈", "游戏里也能复习水循环吗？"],
      ["ai", "好奇星伴", "可以。第一关找水滴，第二关云朵派对，第三关雨滴降落。"],
      ["child", "小星", "如果云朵装不下，就变成雨跳下去！"],
      ["ai", "好奇星伴", "回答正确，获得“云朵守护者”徽章。"],
      ["ai", "好奇星伴", "后续会根据答题记录自动优化关卡和提示。"],
    ],
    features: ["智能小游戏生成", "知识记忆强化", "闯关式迁移", "难度自适应", "兴趣任务推荐", "学玩创闭环"],
  },
  {
    no: "05",
    title: "家长理念注入：把单一路线扩展成系统闭环",
    subtitle: "家长提出教育目标，AI即时改善故事、绘本和游戏任务",
    tag: "理念共创时刻",
    scene: "scene05_parent_concept.png",
    highlight: "家长把教育理念输入系统，AI把它转化为可体验的支线任务。",
    chats: [
      ["mom", "妈妈", "我希望她知道，雨滴落地后不只有一种结局。"],
      ["mom", "妈妈", "它可能汇入地下水、河流，也可能被植物吸收。"],
      ["ai", "好奇星伴", "收到。我会把滴滴的旅程扩展成多条支线。"],
      ["child", "小星", "滴滴可以坐河流小船去大海吗？"],
      ["ai", "好奇星伴", "可以，还会遇到植物朋友和地下水隧道。"],
      ["mom", "妈妈", "最后要回到天空，形成完整闭环。"],
      ["ai", "好奇星伴", "已更新绘本结尾和游戏任务，强化“循环”的理解。"],
    ],
    features: ["家长理念注入", "内容二次生成", "知识图谱补全", "个性化路径", "家长端协同", "闭环认知强化"],
  },
  {
    no: "06",
    title: "成长轨迹报告：从一次好奇到可追踪成长",
    subtitle: "一周后，AI自动生成成长报告，帮助家长看见真实变化",
    tag: "一周后 · 成长报告",
    scene: "scene06_growth_report.png",
    highlight: "自进化：回答风格更贴合习惯，绘本/游戏Skill持续改进。",
    chats: [
      ["ai", "好奇星伴", "小星本周成长报告已更新，是否投屏查看？"],
      ["mom", "妈妈", "打开看看，她从水循环里学到了什么。"],
      ["ai", "好奇星伴", "本周对话28次，拍学探索3次，绘本1本，小游戏完成4关。"],
      ["child", "小星", "妈妈你看，那是我的滴滴！"],
      ["ai", "好奇星伴", "她已能用“先、然后、最后”复述水循环，并主动联想到植物和河流。"],
      ["mom", "妈妈", "下周可以继续做什么？"],
      ["ai", "好奇星伴", "推荐“光和影子”主题；回答风格和游戏Skill会根据历史反馈持续改进。"],
    ],
    features: ["成长轨迹报告", "能力雷达", "兴趣档案", "个性化建议", "家长端联动", "自进化Skill"],
    extra: "回答风格越来越贴合用户习惯 · 绘本/游戏任务基于历史持续改进",
  },
];

async function readImageBlob(imagePath) {
  const bytes = await fs.readFile(imagePath);
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

async function writeBlob(filePath, blob) {
  await fs.writeFile(filePath, new Uint8Array(await blob.arrayBuffer()));
}

function addText(slide, text, position, style = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = text;
  shape.text.style = {
    fontFamily: font,
    color: colors.ink,
    fontSize: 18,
    ...style,
  };
  return shape;
}

function addRound(slide, position, fill, lineFill = "none", radius = 18, shadow = undefined) {
  return slide.shapes.add({
    geometry: "roundRect",
    position,
    fill,
    line: { style: "solid", fill: lineFill, width: lineFill === "none" ? 0 : 1 },
    borderRadius: radius,
    ...(shadow ? { shadow } : {}),
  });
}

async function addImage(slide, imagePath, position, alt, fit = "cover", geometry = "roundRect", radius = 22) {
  const blob = await readImageBlob(imagePath);
  const config = {
    blob,
    contentType: "image/png",
    alt,
    fit,
    position,
    geometry,
  };
  if (geometry === "rect" || geometry === "roundRect") {
    config.borderRadius = radius;
  }
  return slide.images.add(config);
}

function bubbleHeight(text) {
  const len = text.length;
  return Math.max(36, 28 + Math.ceil(len / 27) * 18);
}

async function addChat(slide, chat, y, panel) {
  const [role, speaker, body] = chat;
  const isAi = role === "ai";
  const isMom = role === "mom";
  const avatarSize = 42;
  const gap = 10;
  const bubbleW = isAi ? 500 : 508;
  const text = `${speaker}：${body}`;
  const h = bubbleHeight(text);
  const avatarX = isAi ? panel.left + panel.width - 62 : panel.left + 24;
  const bubbleX = isAi ? avatarX - gap - bubbleW : avatarX + avatarSize + gap;
  const fill = isAi ? colors.ai : isMom ? colors.mom : colors.child;

  await addImage(
    slide,
    avatarPaths[role],
    { left: avatarX, top: y + 2, width: avatarSize, height: avatarSize },
    `${speaker}头像`,
    "cover",
    "ellipse",
    999,
  );
  addRound(slide, { left: bubbleX, top: y, width: bubbleW, height: h }, fill, "#F3D8A1", 16, "shadow-sm");
  addText(slide, text, { left: bubbleX + 14, top: y + 8, width: bubbleW - 28, height: h - 10 }, {
    fontSize: 14.2,
    color: role === "ai" ? "#238148" : role === "mom" ? "#A86116" : "#C35B00",
    bold: true,
  });
  return y + h + 8;
}

function addHeader(slide, spec) {
  addText(slide, "童芯智造", { left: 42, top: 28, width: 156, height: 42 }, {
    fontSize: 30,
    bold: true,
    color: colors.orange,
  });
  addText(slide, "AI好奇心成长伙伴", { left: 47, top: 66, width: 150, height: 22 }, {
    fontSize: 13,
    bold: true,
    color: colors.ink,
  });
  addText(slide, spec.no, { left: 242, top: 14, width: 100, height: 62 }, {
    fontSize: 52,
    bold: true,
    color: colors.orange,
  });
  addText(slide, spec.title, { left: 344, top: 31, width: 668, height: 48 }, {
    fontSize: 33,
    bold: true,
    color: colors.ink,
  });
  addText(slide, spec.subtitle, { left: 346, top: 80, width: 710, height: 28 }, {
    fontSize: 18,
    color: colors.sub,
  });
  addRound(slide, { left: 1036, top: 34, width: 198, height: 44 }, "#FFF7E8", "#F2C170", 24, "shadow-sm");
  addText(slide, spec.tag, { left: 1060, top: 45, width: 154, height: 24 }, {
    fontSize: 17,
    bold: true,
    color: colors.orange,
    alignment: "center",
  });
}

function addFeatureBar(slide, spec) {
  const bar = { left: 42, top: 610, width: 1196, height: 78 };
  addRound(slide, bar, "#FFF7E8", "#F0C36F", 28, colors.shadow);
  const chipW = 184;
  const gap = 12;
  const colorsByIndex = ["#36B37E", "#F26A36", "#397CF6", "#7C5BEF", "#28B8A7", "#F2A51C"];
  spec.features.forEach((feature, i) => {
    const x = bar.left + 20 + i * (chipW + gap);
    const chip = { left: x, top: bar.top + 13, width: chipW, height: 52 };
    addRound(slide, chip, "#FFFFFF", "#F1D49B", 22, "shadow-sm");
    slide.shapes.add({
      geometry: "ellipse",
      position: { left: x + 10, top: bar.top + 21, width: 36, height: 36 },
      fill: colorsByIndex[i],
      line: { style: "solid", fill: "none", width: 0 },
    });
    addText(slide, String(i + 1), { left: x + 10, top: bar.top + 28, width: 36, height: 20 }, {
      fontSize: 14,
      bold: true,
      color: "white",
      alignment: "center",
    });
    addText(slide, feature, { left: x + 52, top: bar.top + 21, width: chipW - 60, height: 38 }, {
      fontSize: feature.length > 7 ? 14.2 : 16,
      bold: true,
      color: colors.ink,
    });
  });

  if (spec.extra) {
    addText(slide, spec.extra, { left: 335, top: 690, width: 610, height: 20 }, {
      fontSize: 12,
      bold: true,
      color: "#A95C12",
      alignment: "center",
    });
  }
  addText(slide, spec.no, { left: 1195, top: 686, width: 40, height: 24 }, {
    fontSize: 16,
    bold: true,
    color: "#8C6A3B",
    alignment: "right",
  });
}

async function addSlide(presentation, spec) {
  const slide = presentation.slides.add();
  slide.background.fill = colors.bg;

  slide.shapes.add({
    geometry: "rect",
    position: { left: 0, top: 0, width: W, height: H },
    fill: "#FFF1CC",
    line: { style: "solid", fill: "none", width: 0 },
  });
  addHeader(slide, spec);

  const chatPanel = { left: 42, top: 128, width: 698, height: 458 };
  addRound(slide, chatPanel, "#FFFDF7/94", "#F0CA80", 28, colors.shadow);
  addText(slide, "微信群聊 · 小星的水循环探索", { left: chatPanel.left + 28, top: chatPanel.top + 16, width: 380, height: 24 }, {
    fontSize: 16,
    bold: true,
    color: "#5F4124",
  });

  let y = chatPanel.top + 52;
  for (const chat of spec.chats) {
    y = await addChat(slide, chat, y, chatPanel);
  }

  const sceneFrame = { left: 762, top: 128, width: 476, height: 360 };
  addRound(slide, { left: sceneFrame.left - 10, top: sceneFrame.top - 10, width: sceneFrame.width + 20, height: sceneFrame.height + 20 }, "#FFFFFF", "#F2C170", 28, colors.shadow);
  await addImage(
    slide,
    path.join(scenesDir, spec.scene),
    sceneFrame,
    `${spec.no}场景图`,
    "cover",
    "roundRect",
    24,
  );

  addRound(slide, { left: 762, top: 512, width: 476, height: 74 }, colors.panel2, "#F0CA80", 22, "shadow-sm");
  addText(slide, "场景亮点", { left: 790, top: 526, width: 92, height: 24 }, {
    fontSize: 16,
    bold: true,
    color: colors.orange,
  });
  addText(slide, spec.highlight, { left: 884, top: 525, width: 326, height: 42 }, {
    fontSize: 16,
    bold: true,
    color: colors.ink,
  });

  addFeatureBar(slide, spec);
}

async function main() {
  await fs.mkdir(outputDir, { recursive: true });
  await fs.mkdir(qaDir, { recursive: true });

  const presentation = Presentation.create({
    slideSize: { width: W, height: H },
  });

  for (const spec of slides) {
    await addSlide(presentation, spec);
  }

  for (const [index, slide] of presentation.slides.items.entries()) {
    const stem = `slide-${String(index + 1).padStart(2, "0")}`;
    await writeBlob(path.join(qaDir, `${stem}.png`), await presentation.export({ slide, format: "png", scale: 1 }));
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(path.join(qaDir, `${stem}.layout.json`), await layout.text(), "utf8");
  }

  await writeBlob(path.join(qaDir, "deck-montage.webp"), await presentation.export({ format: "webp", montage: true, scale: 1 }));
  const pptx = await PresentationFile.exportPptx(presentation);
  await pptx.save(finalPptx);
  console.log(finalPptx);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
