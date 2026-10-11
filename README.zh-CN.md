# mc-schem-builder

[English](README.md) | **简体中文**

由自然语言描述生成 Minecraft Java 1.21.1 的 Litematica 建筑投影（`.schem`），带机器校验的验收闭环与可迭代的修复流程。同时它也是一个 TRAE Agent Skill：放进工作区后，在对话里描述一栋建筑即可。

## 这个项目能做什么

- 把一句话变成可直接放置的 Litematica 投影：`.schem` + 预览图，自动分片（单片 256³），目标 Java 1.21.1
- 内置 9 个风格，多数需求只需改 spec 字段，不必写几何代码
- 其余形态由声明式几何层兜底：直线、圆、椭圆、矩形、平行四边形、球面、长方体、半空间、二元函数曲面、空间曲线（螺旋线 / 贝塞尔 / 环面纽结），支持并、交、差布尔合成
- 微调用体素级 diff 把关，证明预期包围盒之外逐点不变
- 跑完整验收闭环：静态体检 + 四视图多模态识图，错误带坐标反馈，迭代直到通过

## 为什么有用

- **不会静默失败**。生成前用从游戏 jar 提取的 1062 方块注册表拦下不存在的方块名 —— 否则 Litematica 会一声不吭换成空气。
- **spec 是唯一真相源**。迭代只改字段，不重写脚本；脚本在运行时读取 spec。
- **交付物自带证据**。断言报告、对照阈值的体检数值、预览图，而不是「看起来没问题」。
- **依赖极轻**。只有三个纯 Python 包，渲染不需要 PIL 或 matplotlib。

## 工作原理

mc-schem-builder 把这套流程闭环化：

1. **描述建筑** —— 五要素：类型、尺寸、材质、关键部件、约束
2. **声明 spec** —— `spec.json` 是唯一真相源；迭代就是改字段，而不是重写脚本
3. **生成** —— 轨道 A：预设风格直出，零几何代码；轨道 B：生成骨架脚本，只填一个 `build()` 函数，用声明式几何拼装
4. **校验** —— 每片的格式 / 完整性 / 规格断言，外加体素级 diff 门禁，证明「其余部分逐点不变」
5. **验收闭环** —— 静态体检（悬空方块、连通性、组件包围盒、对称轴偏差）配合四视图多模态识图；每条错误都带坐标、类型、严重等级，迭代直到通过或触及轮次上限

## 快速开始

### 环境要求

- Python 3.10+
- `pip install mcschematic nbtlib numpy`

就这些。渲染不需要 PIL 或 matplotlib —— 预览图由纯 numpy + zlib 直接手写 PNG。

### 安装

```bash
# 把整个目录复制到 TRAE 工作区（标准 Agent Skills 布局）
# <你的项目根>/.trae/skills/mc-schem-builder/
```

方块注册表（`references/block_registry_1_21_1.json` —— 从游戏客户端 jar 提取的 1062 个方块及其合法 blockstate）已随仓库附带。这正是拦住「静默变空气」的关键。重新提取或切换 MC 版本时：

```bash
python scripts/extract_block_registry.py --version 1.21.1
```

### 验证安装

```bash
python scripts/validate_spec.py --list        # 9 个风格
python scripts/block_check.py --selfcheck     # 正例 10/10、反例 9/9
python scripts/geom_lib.py --selfcheck        # 几何层回归
python scripts/build_lib.py --selfcheck       # 基元/变换/材质断言
```

### 端到端跑一个城堡

```bash
python scripts/validate_spec.py --new castle --name demo --out spec.json
python scripts/validate_spec.py --check spec.json
python scripts/gen_schem.py --spec spec.json --out-dir ./out
python scripts/verify_schem.py --assert ./out/demo.schem
python scripts/render_preview.py --schem ./out/demo.schem
```

预期：断言全绿，并产出 `demo.schem`、三张预览图、`demo.report.json`。

### 放进游戏

1. 把 `.schem` 复制到 **`.minecraft/schematics/`**（不是 `config/litematica/schematics/`；使用版本隔离启动器时为 `.minecraft/versions/<实例名>/schematics/`）
2. 进游戏按 `M` → 原理图管理器 → 加载 → 粘贴原理图
3. Easy Place 是 Litematica 内置功能但默认关闭；全自动打印需另装第三方 Printer 模组

> ⚠️ 在公开服务器使用 Printer / 全自动放置可能违反服务器规则。本项目只生成投影文件，不连接任何服务器、不修改游戏文件。

## 两条轨道

| | **轨道 A：预设风格直出** | **轨道 B：自定义几何** |
|---|---|---|
| 何时用 | 需求能映射到已有风格 | 风格库覆盖不了的形态 |
| 入口 | `python scripts/gen_schem.py --spec spec.json` | `python scripts/scaffold.py --spec spec.json --out build_x.py` |
| 谁写几何 | 不用写，只改 spec 字段 | 只填 `build()` 函数体（语义基元 + 声明式几何）；不碰 mcschematic API |

自带 builder 的风格共 9 个：`castle`、`wall`、`tower`、`bridge`、`modern_villa`、`modern_house`、`spiral_tower`、`curved_roof`、`slant_house`。

**校验不通过就不要生成** —— 参数越界、跨字段矛盾、方块名非法都会在 `validate_spec.py --check` 被拦下。

## 验收闭环

一轮即可跑完全流程：

```bash
python scripts/loop_check.py --spec spec.json --round 1
```

| 环节 | 内容 | 产出 |
|---|---|---|
| ① 生成 + 执行 | 跑 `build_<name>.py`（轨道 A 为 `gen_schem.py`） | `.schem` + `report.json` |
| ② 产物断言 | 每片 `verify_schem.py --assert` | 格式 / 完整性 / 规格共 8 项 |
| ③ 视觉检查 | 正/侧/俯/等轴测四视图 + 识图模板 | `<out>/audit/round_N/views/` |
| ④ 结构体检 | `audit_schem.py`（静态四维） | `audit.json` 结构化错误清单 |
| ⑤ 状态机 | 连续通过计数 / 终止判定 | `<out>/.audit_state.json` |

### 静态四维

每项都输出 实测值 / 阈值 / 判定：

| 维度 | 指标定义 | 默认阈值 |
|---|---|---|
| 悬空方块 | `floating_ratio`：下方为空气且 26 邻接实体 ≤ 1 | ≤ 0.5% |
| 结构连通性 | 不属于最大 26 邻接连通分量的方块占比 | ≤ 0.5% |
| 组件包围盒 | `spec.audit.components` 声明的 bbox 与实际对比，越界数 | 0 |
| 对称轴偏差 | 关于声明对称轴中面镜像后的差异占比 | ≤ 2% |

连通性按 26 邻接判定 —— MC 里对角搭接是常规做法（45° 台阶、斜墙）。严格 6 邻接分量数只作参考输出。

### 视觉检查

四个视图交给多模态模型（`scripts/vision_review.py`），返回结构化报告；识图结论并入同一份错误清单。三种后端：

```bash
# ① 任意 OpenAI 兼容网关（OpenAI / 通义 / 豆包 / vLLM / LM Studio / one-api ...）
export VISION_BASE_URL=https://<你的网关>
export VISION_API_KEY=<密钥>
export VISION_MODEL=<多模态模型名>        # 如 gpt-4o-mini / qwen-vl-max
python scripts/loop_check.py --spec spec.json --round 2 --vision-auto

# ② 本地 Ollama
python scripts/loop_check.py --spec spec.json --round 2 --vision-auto \
    --vision-backend ollama --vision-model llava:13b

# ③ 无模型可用：导出提示词 → 人工/agent 看图回填 → 规范化 ingestion
python scripts/vision_review.py --views <views> --bbox=-200,-59,-200,199,-4,199 \
    --prompt-out <views>/review_prompt.txt
```

未配置模型时 `--vision-auto` **不会静默通过**：该轮视觉维度记 `NOT_RUN`（整体判 FAIL），并导出提示词等回填。不需要真模型即可自测链路：`python scripts/vision_link_test.py --views <views>`（本地 mock 端点，13 项断言）。

### 终止条件

- **连续 2 轮**全维度达标 → `PASS_TERMINATED`
- **5 轮**仍未通过 → `MAX_ROUNDS_TERMINATED`，输出最佳轮次 + 未解决错误清单

每条错误都带坐标区间、错误类型、严重等级。「不对，重来」式模糊反馈在设计上被拒收。

## 迭代协议

「塔楼加高 3 格」的标准流程：改 spec 字段 → 重新生成到新版本目录 → 跑 diff 门禁：

```bash
python scripts/verify_schem.py --diff v1/x.schem v2/x.schem --expect-bbox="-3,18,-3,62,24,62"
```

任何落在预期包围盒之外的变更都会判失败。两条轨道都只改 `spec.json` —— 轨道 B 脚本在运行时读取 spec，所以你写的 `build()` 几何不会被覆盖。

> 含负坐标的参数必须用等号形式（`--expect-bbox="-3,..."`），否则 argparse 会把它当成选项。

## 配置

| 变量 | 说明 |
|---|---|
| `VISION_BASE_URL` | 视觉检查用的 OpenAI 兼容端点 |
| `VISION_API_KEY` | API 密钥（Ollama 不需要） |
| `VISION_MODEL` | 多模态模型名 |

体检阈值随 spec 一起版本化：

```json
"audit": {"symmetry_axis": "x", "float_max": 0.005,
          "components": [{"name": "大厅", "bbox": [-48,0,-14,48,55,57], "tol": 2}]}
```

## 目录结构

```
├── SKILL.md                 # Agent Skill 入口（完整工作流与触发条件）
├── assets/template_spec.json
├── scripts/
│   ├── gen_schem.py         # 轨道 A：预设风格直出
│   ├── scaffold.py          # 轨道 B：build() 骨架生成
│   ├── geom_lib.py          # 声明式几何（10 类图元 + 布尔合成）
│   ├── build_lib.py         # 体素基元/变换/材质 + 分片
│   ├── validate_spec.py     # spec 校验 CLI
│   ├── block_check.py       # 方块名 / blockstate 静态检查
│   ├── verify_schem.py      # 产物断言 + 体素 diff 门禁
│   ├── render_preview.py    # 纯 numpy 渲染（等距/俯视/正视/四视图包）
│   ├── loop_check.py        # 闭环驱动（①–⑤ 全跑）
│   ├── audit_schem.py       # 结构体检（静态四维）+ 状态机
│   ├── vision_review.py     # 视觉后端（OpenAI 兼容 / Ollama / 手工回填）
│   ├── vision_link_test.py  # 视觉链路自测（mock 端点）
│   ├── palette_1_21_1.py    # 版本常量 + 朝向构造器
│   └── extract_block_registry.py
├── references/
│   ├── styles/              # 11 个风格定义（参数集 + 跨字段约束）
│   ├── pitfalls.md          # 实测踩坑清单
│   ├── spec_schema.md       # spec.json 字段定义
│   ├── api_mcschematic.md   # 实测过的 mcschematic API 签名
│   ├── block_ids_1_21_1.md  # 常用方块 + 朝向速查
│   ├── block_registry_1_21_1.json
│   └── trae_install.md      # TRAE 安装与环境说明
└── mc_builds/               # 用于端到端测试流水线的回归工程
```

## 硬性约束

违反会导致静默失败：

1. **方块名必须存在于目标版本** —— 生成前跑 `block_check.py`。
2. **楼梯/台阶/墙/活板门/门必须显式给朝向** —— 用 `palette_1_21_1.py` 里的 `p_stairs / p_slab / p_wall / p_door / p_trapdoor` 构造器。
3. **实体不能写进 `.schem`**（盔甲架、画、物品展示框、展示实体等）。
4. **分片按含空气包围盒判定**（默认单片上限 256³），不是方块数量。
5. **`.schem` 在 Litematica 里只读** —— 改内容必须回脚本重新生成。

## 换 MC 版本

版本相关内容只集中在两处：`scripts/palette_<版本>.py`（复制 1.21.1 版，改 `MC_VERSION` / `DATA_VERSION`）与 `references/block_registry_<版本>.json`（重新提取）。同时确认目标版本的 Litematica 仍走 Sponge `.schem` 读取路径。

## 文档

- [SKILL.md](SKILL.md) —— 完整 agent 工作流、触发条件、交付清单
- [references/pitfalls.md](references/pitfalls.md) —— 踩坑清单（静默失败、闭环与视觉接入实测细节）
- [references/trae_install.md](references/trae_install.md) —— 安装、环境提示、游戏内加载
- [references/spec_schema.md](references/spec_schema.md) —— spec 字段与校验规则

## 技术栈

| 层 | 技术 |
|---|---|
| 生成 | mcschematic（Sponge `.schem`） |
| 回读 | nbtlib |
| 体素运算与渲染 | numpy（zlib 手写 PNG，无需 PIL） |
| 视觉 | 任意 OpenAI 兼容多模态端点，或 Ollama |
| 目标 | Minecraft Java 1.21.1 + Litematica |
