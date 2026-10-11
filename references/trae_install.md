# 在 TRAE CN 中安装与注册

## 1. 目录规范（实测依据）

TRAE 官方内置的 `skill-creator` 明确写了 skill 的目录约定。本机实测该文件位于：

```
C:\Users\<你>\.trae-cn\builtin\global\skills\skill-creator\SKILL.md
```

其中原文：

> **SKILL Structure**
> 1. **Directory**: `.trae/skills/<skill-name>/`
> 2. **File**: `SKILL.md` inside the directory

所以：

| 层级 | 路径 |
|---|---|
| **工作区级（推荐）** | `<你的项目根>/.trae/skills/mc-schem-builder/` |
| 用户级参考位置 | `~/.trae-cn/builtin_skills/`（内置 skill 所在处，不建议手工写入） |

本 skill 已按该规范产出：`SKILL.md` + `scripts/` + `references/` + `assets/`，属标准 Agent Skills 布局，直接放入即可被识别。

## 2. 安装步骤

```bash
# 1) 把整个 mc-schem-builder 目录复制到 TRAE 工作区
mkdir -p <你的项目根>/.trae/skills
cp -r mc-schem-builder <你的项目根>/.trae/skills/

# 2) 安装 Python 依赖
pip install mcschematic nbtlib numpy
```

> 依赖只有三个，且都不需要编译：
> - `mcschematic` —— 生成 `.schem`
> - `nbtlib` —— 回读 `.schem`（断言 / diff / 渲染都需要）
> - `numpy` —— 体素运算与渲染
>
> 渲染**不需要** PIL / matplotlib（预览图用纯 numpy + zlib 手写 PNG）。

```bash
# 3) 生成方块注册表（一次性；需本机已装对应版本 MC 客户端）
cd <你的项目根>/.trae/skills/mc-schem-builder/scripts
python extract_block_registry.py --version 1.21.1
```

若仓库已附带 `references/block_registry_1_21_1.json`，这一步可跳过。
换 MC 版本时**必须**重跑，并同步替换 `palette_<版本>.py`。

## 3. 验证安装

```bash
cd <你的项目根>/.trae/skills/mc-schem-builder/scripts

python validate_spec.py --list        # 应列出 11 个风格（其中 gangxia_north / fuzhou_railway_station 只声明参数、无 builder，须走轨道 B）
python block_check.py --selfcheck     # 正例 10/10、反例 9/9
python palette_1_21_1.py --selfcheck  # 常用集 0 非法
python geom_lib.py --selfcheck        # 几何合成层：含与既有基元逐格等价的回归
python build_lib.py --selfcheck       # 几何/变换/细节/材质断言全绿（含分片覆盖、屋顶朝向、镜像朝向等回归）
```

全部通过后，在 TRAE 对话里直接描述建筑即可触发本 skill（description 里已含触发词）。

### 闭环验收（生成→执行→检查→反馈→修复）

```bash
# 一轮 = 跑生成 + 产物断言 + 四视图 + 结构体检 + 状态机判定（需要一份 spec.json）
python loop_check.py --spec <你的 spec.json> --round 1

# 第 2 轮起，把多模态识图结果（读 views/view_*.png 后回填）传回来
python loop_check.py --spec <你的 spec.json> --round 2 \
    --vision <输出目录>/audit/round_2/views/vision_report.json

# 只跑静态四维 / 只体检已导出的 .schem
python audit_schem.py --spec <你的 spec.json> --skip-vision
python audit_schem.py --schem out/x.schem
```

终止条件：连续 2 轮所有维度达标 → `PASS_TERMINATED`；5 轮仍未过 → `MAX_ROUNDS_TERMINATED`
（输出最佳轮次 + 未解决错误清单）。状态落在 `<输出目录>/.audit_state.json`。

### 视觉维度接多模态模型（可选，不配也能跑）

```bash
# OpenAI 兼容网关（OpenAI / 通义 / 豆包 / vLLM / LM Studio / one-api ...）
export VISION_BASE_URL=https://<你的网关>
export VISION_API_KEY=<密钥>          # Ollama 不需要
export VISION_MODEL=<多模态模型名>     # 如 gpt-4o-mini / qwen-vl-max
python loop_check.py --spec <spec.json> --round 1 --vision-auto

# 本地 Ollama
python loop_check.py --spec <spec.json> --round 1 --vision-auto \
    --vision-backend ollama --vision-model llava:13b

# 不配置模型时的链路自测（不需要真模型）
python vision_link_test.py --views <输出目录>/audit/round_1/views
```

> 未配置时 `--vision-auto` 不会静默通过：该轮视觉维度记 `NOT_RUN`（整体 FAIL），
> 并导出 `views/review_prompt.txt` 供 agent/人工逐张看图回填（再用 `--ingest` 规范化）。
> 跑脚本前建议 `export PYTHONDONTWRITEBYTECODE=1`（避免写工作区外的 `__pycache__` 触发沙箱限制）。

## 4. 端到端自测（建议首次执行一次）

```bash
cd <你的项目根>/.trae/skills/mc-schem-builder/scripts
mkdir -p /tmp/mc_demo && cd /tmp/mc_demo

# 生成默认 spec → 校验 → 生成 → 断言 → 渲染
python <skill>/scripts/validate_spec.py --new castle --name demo --out spec.json
python <skill>/scripts/validate_spec.py --check spec.json
python <skill>/scripts/gen_schem.py --spec spec.json --out-dir ./out
python <skill>/scripts/verify_schem.py --assert ./out/demo.schem
python <skill>/scripts/render_preview.py --schem ./out/demo.schem
```

预期：断言全绿、`out/demo.schem` + 三张预览图 + `demo.report.json` 全部生成。

---

## 5. 本机环境实测提示（重要）

在本机 `.minecraft` 下实测得到，直接影响「投影能不能用」：

| 发现 | 说明 |
|---|---|
| 整合包为 **NeoForge 1.21.1**（`versions/一个人的世界`、`versions/联机`，各 13 个 mod） | 与目标版本 1.21.1 一致 ✓ |
| 这些整合包中**尚未安装 Litematica** | 需要先装投影模组才能加载 `.schem` |
| **Litematica 官方只发布 Fabric 版** | 在 NeoForge 整合包上需第三方移植版，或另建 Fabric 实例 |
| 已有的 `1.20.1-Fabric` 实例装了 Fabric API，但也没有 Litematica | 同上 |

**结论**：生成的 `.schem` 版本正确（DataVersion=3955 对齐 1.21.1），
但要在游戏里看到投影，需要先在对应实例安装可用的 Litematica（或其 NeoForge 移植版）。

### 放置与加载

1. 把 `.schem` 复制到 **`.minecraft/schematics/`**
   （注意：**不是** `config/litematica/schematics/`；使用版本隔离的启动器时，
   路径形如 `.minecraft/versions/<实例名>/schematics/`）
2. 进游戏按 `M` → **原理图管理器（Schematic Manager）** → 选中文件 → **加载（Load）**
3. 再按 `M` → **粘贴原理图（Paste Schematic）** 放置投影
4. 自动填充：**Easy Place** 是 Litematica **内置**功能，但**默认关闭**，需在配置里打开；
   全自动打印要另装第三方 Printer 模组

### ⚠️ 服务器使用提示

在**公开服务器**上使用 Printer / 全自动放置**可能违反服务器规则**，请仅在自己的服务器
或已获许可的环境中使用。本 skill 只生成投影文件，**不连接任何服务器、不修改游戏文件**。
