---
name: "mc-schem-builder"
description: "由自然语言描述生成 Minecraft Java 1.21.1 的 Litematica 建筑投影(.schem)，含预设风格直出与自定义几何两条轨道、体素级 diff 迭代门禁、多视角预览。当用户要求生成/修改建筑投影、.schem、投影文件，或提到城堡/围墙/塔楼/桥梁/现代别墅等建筑外壳时触发。"
---

# MC 建筑投影生成器（Generate Minecraft .schem Blueprints）

把「用自然语言描述一栋建筑」变成「一个能直接拖进 Litematica 用的 `.schem` 文件 + 预览图 + 验证报告」，
并支持「塔楼加高 3 格」这类**微调迭代**——且用机器断言保证「其余部分逐点不变」。

## 何时使用

- 用户要求生成建筑投影 / `.schem` / 投影文件 / 原理图
- 用户描述一栋建筑（城堡、围墙、塔楼、桥梁、别墅…）并想要可放置的成果
- 用户已有投影，要求修改（加高、加装饰、换材质）并希望**只改指定部分**

不适用：精细内饰、红石电路、地形生成、非 1.21.1 版本（见「换 MC 版本」）。

---

## 第 0 步：环境预检（每次都要做）

```bash
python scripts/validate_spec.py --list      # 确认脚本可运行、风格库可加载
python scripts/block_check.py --selfcheck   # 确认方块注册表存在且校验器正常
```

若报「未找到方块注册表」，运行一次提取（需本机已安装对应版本 MC 客户端）：

```bash
python scripts/extract_block_registry.py --version 1.21.1
```

> 注册表从**游戏本体的客户端 jar** 提取，得到 1062 个方块及其合法 blockstate 属性。
> 这是本 skill 能拦住「静默变空气」的根基 —— 不要手工维护方块表。

---

## 第 1 步：选轨道（二选一，不要混用）

| | **轨道 A：预设风格直出** | **轨道 B：自定义几何** |
|---|---|---|
| 何时用 | 需求能映射到已有风格（castle / wall / tower / bridge / modern_villa / modern_house / spiral_tower / curved_roof / slant_house；另 `gangxia_north` / `fuzhou_railway_station` **只声明参数、没有 builder，须走轨道 B**） | 风格库里没有的形态（哥特飞扶壁、螺旋塔、异形穹顶）；或先照 `references/styles/gangxia_north.json` / `fuzhou_railway_station.json` 的样子加一个「只有参数、没有 builder」的风格声明，把参数集与约束固定下来 |
| 入口 | `gen_schem.py --spec spec.json` | `scaffold.py --spec spec.json --out build_x.py` |
| 谁写几何 | 不用写，只改 spec 字段 | **只填 `build()` 函数体**。用 `build_lib` 语义基元拼装，或用 `geom_lib` **声明式几何体**（直线/圆/椭圆/矩形/平行四边形/球面/长方体/半空间/二元函数曲面/空间曲线，支持并 `\|`、交 `&`、差 `-` 合成）自建任意形态，其余由骨架负责 |
| 模型职责 | 改参数 | 写几何逻辑，不碰 mcschematic API |

先用 `validate_spec.py --show <style>` 看该风格有哪些参数，再决定能否落到风格库。

---

## 第 2 步：需求问清 5 要素

如果用户描述不完整，用 `mcp__ask-user__ask_user` 一次问清（不要逐条追问）：

1. **建筑类型**（决定轨道与风格）
2. **尺寸**（长/宽/高，或塔半径/高度）
3. **材质偏好**（墙体/屋顶/装饰用什么方块）
4. **关键部件**（要不要城门、城垛、塔楼、窗户、栏杆）
5. **影响版本/兼容的约束**（MC 版本、是否公开服、是否需要分片）

内部可映射到 MC 版本与只生成外壳这两条默认，无需问用户。

---

## 第 3 步：写 spec.json（唯一真相源）

spec 决定一切；迭代就是改其中几个字段。

```bash
python scripts/validate_spec.py --new castle --name my_castle --out spec.json
python scripts/validate_spec.py --check spec.json          # 必须通过才能生成
```

字段含义见 `references/spec_schema.md`，可直接用的模板见 `assets/template_spec.json`。

**校验不通过就不要生成** —— 参数越界、跨字段矛盾、方块名非法都会在这里被拦下。

---

## 第 4 步：生成

**轨道 A**
```bash
python scripts/gen_schem.py --spec spec.json --out-dir <输出目录>
```

**轨道 B**
```bash
python scripts/scaffold.py --spec spec.json --out build_x.py --check
# 然后编辑 build() 函数体（骨架里已列出全部可用基元与硬性提醒）
python scripts/block_check.py build_x.py     # 写完先自查方块名
python build_x.py
```
> 生成的脚本**在运行时读取 spec.json**（不内嵌参数），改参数只需改 spec 再重跑该脚本，
> 不必重新 scaffold，也不会覆盖你写的几何。

生成前必做的方块自检（对轨道 B 尤其重要）：
```bash
python scripts/block_check.py spec.json
```

---

## 第 5 步：闭环验收（生成→执行→检查→反馈→修复）

一次「一轮检查」把生成、产物断言、四视图、结构体检、状态机全跑完：

```bash
python scripts/loop_check.py --spec spec.json --round 1
```

| # | 环节 | 内部命令 | 产出 |
|---|---|---|---|
| ① | 生成+执行 | 跑 `build_<name>.py`（轨道 A 为 `gen_schem.py`） | `.schem` + `report.json` |
| ② | 产物断言 | `verify_schem.py --assert`（每片） | 格式层/完整性层/规格层 8 项 |
| ③ | 视觉检查 | 四视图（正/侧/俯/等轴测）+ 识图模板 | `<out>/audit/round_N/views/` |
| ④ | 结构体检 | `audit_schem.py`（静态四维） | `audit.json`（结构化错误清单） |
| ⑤ | 状态机 | 连续通过计数 / 终止判定 | `<out>/.audit_state.json` |

### 静态四维（每项都输出 实测值 / 阈值 / 判定）

| 维度 | 指标定义 | 默认阈值 |
|---|---|---|
| 悬空方块 | `floating_ratio`：下方为空气 **且** 26 邻接实体 ≤ 1（真正孤立的漂浮块） | ≤ 0.5% |
| 结构连通性 | `disconnected_ratio`：不属于最大连通分量（26 邻接）的方块占比；分量个数 | ≤ 0.5% / 1 个 |
| 组件包围盒 | 按 `spec.audit.components` 逐项比对「区间内实际 bbox vs 声明 bbox」，越界数 | 0 |
| 对称轴偏差 | `symmetry_dev`：关于包围盒中面镜像后的差异占比（需 `spec.audit.symmetry_axis`） | ≤ 2% |

> 连通性按 **26 邻接**判定 —— MC 里对角搭接是常规做法（45° 台阶、斜墙、斜屋脊），
> 纯 6 邻接会把一条完整台阶判成 N 个独立结构。严格 6 邻接分量数只作为参考值输出。

### 视觉维度：必须落成结构化报告

③ 只负责把图准备好；**识图要由多模态模型完成**（读 `views/view_{front,side,top,iso}.png`，
按模板回填 `vision_report.json`），再用 `--vision` 传回，视觉错误会并进同一份结构化清单：

```bash
python scripts/loop_check.py --spec spec.json --round 2 \
    --vision <out>/audit/round_2/views/vision_report.json
```

```json
{"schema":"mc-vision/1","reviewer":"<模型名>","verdict":"PASS",
 "findings":[{"type":"屋顶塌陷","severity":"error","view":"iso",
              "region":[0.4,0.1,0.6,0.3],"note":"中部下陷约 3 格"}]}
```

* **每条必须能定位**：给 `bbox`（世界坐标）或 `region`（归一化矩形）+ `view`。
  正/侧/俯三个正交视图的 `region` 会被自动换算成世界坐标区间（深度方向取全深度，
  属剖面式定位）；等轴测是透视，只能靠 `region` + 文字描述。
* `verdict=FAIL` 却没有任何 error 级条目 → 判为**模糊反馈**，直接打回。

#### 接多模态模型（`vision_review.py`，三种后端）

```bash
# ① OpenAI 兼容接口（推荐：OpenAI / 通义 / 豆包 / DeepSeek-VL / vLLM / LM Studio / one-api 等）
export VISION_BASE_URL=https://<你的网关>    # 或 --endpoint
export VISION_API_KEY=<密钥>                # ollama 不需要
export VISION_MODEL=<多模态模型名>           # 如 gpt-4o-mini / qwen-vl-max
python scripts/loop_check.py --spec spec.json --round 2 --vision-auto

# ② Ollama 原生（本地模型）
python scripts/loop_check.py --spec spec.json --round 2 --vision-auto \
    --vision-backend ollama --vision-model llava:13b

# ③ 无模型可调时的正规做法：导出提示词 → agent/人工逐张看图回填 → 规范化 ingestion
python scripts/vision_review.py --views <views> --bbox=-200,-59,-200,199,-4,199 \
    --prompt-out <views>/review_prompt.txt
python scripts/vision_review.py --ingest <views>/agent_reply.json --out <views>/vision_report.json
```

* 未配置模型时 `--vision-auto` **不会静默通过**：本轮视觉维度记 `NOT_RUN`（整体判 FAIL），
  并导出 `review_prompt.txt` 等回填；退出码 2 明确提示需要哪些环境变量。
* 链路自测（不需要真模型）：`python scripts/vision_link_test.py --views <views>` ——
  用本地 mock 端点验「请求形状 / 回复解析 / 规范化 / region→世界坐标」13 项。
* `--bbox` 含负数时必须用**等号形式**（`--bbox=-200,...`），空格形式会被 argparse 当成选项。

### 终止条件

* **连续 2 轮**所有维度达标（含视觉）→ `PASS_TERMINATED`，交付当前结果。
  被 `--skip-vision` 跳过的轮次**不计入**连续通过。
* 到 **5 轮**上限仍未通过 → `MAX_ROUNDS_TERMINATED`，输出最佳轮次 + 未解决错误清单。
* 两条路径都会打印**下一步动作**：每条 error 带 bbox、错误类型、修复提示 —— 不允许"不对，重来"。

阈值可以放进 spec（推荐，随产物一起版本化）：

```json
"audit": {"symmetry_axis": "x", "float_max": 0.005,
          "components": [{"name": "中央大厅", "bbox": [-48,0,-14,48,55,57], "tol": 2}]}
```

---

## 迭代协议（微调不跑偏的关键）

用户说「塔楼加高 3 格」时，**必须**按这个顺序做：

1. **先读现有 spec**，明确回一句「只改哪几个字段」（例：`tower_height: 18 -> 21`）
2. 改 spec（不要重写整个脚本、不要重新描述需求）
3. 重新生成到**新版本目录**（`v2/`），不要覆盖旧产物
4. **跑 diff 门禁**：

```bash
python scripts/verify_schem.py --diff v1/x.schem v2/x.schem --expect-bbox="x0,y0,z0,x1,y1,z1"
```

```
变更集
  新增 296   删除 56   替换 96
  变更包围盒 x -3..62 | y 18..24 | z -3..62
  预期区域   x -3..62 | y 18..24 | z -3..62
  越界变更   0          ← 必须为 0 才算通过
```

**越界变更数 ≠ 0 一律判失败**，回报给用户后再修。这把「其余部分逐点不变」从人眼检查变成机器断言。

> **两条轨道都只改 spec.json。** 轨道 B 的 `build_*.py` 在运行时读取 spec.json（参数不内嵌），
> 所以「加高 3 格」同样是：改 spec → 重跑该脚本，**不会覆盖 `build()` 里的几何**；
> 只有改几何才去编辑 `build()`。

> 坐标含负数时 `--expect-bbox` 要用等号形式：`--expect-bbox="-3,18,-3,62,24,62"`，
> 否则 argparse 会把 `-3,...` 当成选项。

---

## 硬性约束（违反会导致静默失败）

1. **方块名必须存在于目标版本**。Litematica 遇到不存在的方块名会**静默替换成空气、不报错** —— 事后无法从文件里察觉。所以 `block_check.py` 必须在生成前跑。
2. **楼梯 / 台阶 / 墙 / 活板门 / 门必须显式给朝向**，否则会全部朝向默认方向（屋顶会很难看）。用 `palette_1_21_1.py` 的构造器：
   `p_stairs(base, facing, half)` / `p_slab(base, type_)` / `p_wall(base, north=...)` / `p_door(...)` / `p_trapdoor(...)`
3. **实体不能写进 .schem**（`armor_stand` / `painting` / `item_frame` / `display` 等）。校验器会专门提示。
4. **分片阈值按含空气包围盒判定**，不是实体方块数 —— Litematica 的加载压力来自包围盒，空心塔与实心塔同样卡。默认单片上限 `256³`。
5. **`.schem` 在 Litematica 里是只读的**，改内容必须回脚本重新生成。

---

## 输出尾部固定提示（每次交付都要带上）

> **放置位置**：把 `.schem` 复制到 `.minecraft/schematics/`（**不是** `config/litematica/schematics/`），
> 进游戏按 `M` → **原理图管理器** → 加载 → **粘贴原理图**。
> **自动放置**：Easy Place 是 Litematica 内置功能但**默认关闭**；全自动打印需另装第三方 Printer 模组。
> ⚠️ **服务器使用**：在公开服务器上使用 Printer / 全自动放置**可能违反服务器规则**，请仅在自己的服务器或已获许可的环境中使用。本 skill 只生成投影文件，不连接任何服务器。

---

## 换 MC 版本

版本相关的东西只集中在两处，换版本时一起替换：

1. `scripts/palette_<版本>.py` —— 复制 `palette_1_21_1.py`，改 `MC_VERSION` / `DATA_VERSION`
2. `references/block_registry_<版本>.json` —— 用 `extract_block_registry.py` 重新提取

同时确认目标版本的 Litematica 可用，并复核它仍走 Sponge `.schem` 读取路径。

---

## 参考文件

| 文件 | 内容 |
|---|---|
| `references/pitfalls.md` | **实测踩过的坑清单**（含 API 位置、目录、静默失败机制） |
| `scripts/loop_check.py` | **闭环驱动**：一轮 = 生成→执行→四视图→结构体检→状态机判定，并打印可定位的下一步 |
| `scripts/audit_schem.py` | **结构体检**：悬空占比 / 连通性 / 组件包围盒 / 对称轴偏差 四维 + 结构化错误清单 + 迭代状态机 |
| `scripts/vision_review.py` | **视觉维度接入**：四视图 → 多模态模型（OpenAI 兼容 / Ollama）→ 结构化识图报告；也可导出提示词供回填 |
| `scripts/vision_link_test.py` | 视觉接入链路自测（本地 mock 端点，13 项断言，不需要真模型） |
| `scripts/geom_lib.py` | **声明式几何合成层**（`build_lib` 的地基）：直线(参数方程)/圆/椭圆/矩形/平行四边形/球面/长方体/半空间/二元函数曲面/空间(闭合)曲线（螺旋线·贝塞尔·环面纽结）+ 布尔合成 |
| `references/api_mcschematic.md` | 实测过的 mcschematic API 签名与版本枚举 |
| `references/block_ids_1_21_1.md` | 建筑常用方块 + 朝向速查 + 1.21 新增 / 1.21.1 不存在的方块 |
| `references/spec_schema.md` | spec.json 字段定义与校验规则 |
| `references/trae_install.md` | 在 TRAE 中安装、注册与依赖说明 |
| `assets/template_spec.json` | 可直接改用的 spec 模板 |
