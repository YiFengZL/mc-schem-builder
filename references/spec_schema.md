# spec.json 字段定义

> **spec.json 是唯一真相源。** 迭代 = 改其中几个字段重跑，而不是重写脚本。

---

## 顶层结构

```json
{
  "schema_version": 1,
  "name": "my_castle",
  "style": "castle",
  "mc_version": "1.21.1",
  "intent": "（轨道 B 用）用自然语言写清几何需求",
  "params": { "…风格参数…": "…" },
  "output": {
    "dir": "mc_builds/my_castle",
    "basename": "my_castle",
    "slice_max_dim": 256
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 否 | 默认 1；与工具版本不一致时仅告警 |
| `name` | **是** | 输出名。仅允许字母/数字/下划线/连字符，长度 1–64 |
| `style` | **是** | 风格名，决定用哪个 builder 与哪套参数默认值 |
| `mc_version` | 否 | 默认 `1.21.1`；**不一致会直接报错**（防止版本错配产生静默空气块） |
| `intent` | 否 | 轨道 B 的需求描述（自然语言），会嵌入生成的骨架注释 |
| `params` | 是（轨道 A） | 覆盖风格默认值；未声明的键会告警 |
| `output` | 否 | 输出目录 / 文件名 / 分片阈值 |

## output 字段

| 键 | 默认 | 说明 |
|---|---|---|
| `dir` | `mc_builds/<name>` | 输出目录（相对路径按当前工作目录解析） |
| `basename` | `<name>` | 输出文件主名（自动补 `.schem`） |
| `slice_max_dim` | `256` | 单片含空气包围盒边长上限；超限自动分片，片间留 1 格重叠 |

---

## 可用风格

```bash
python scripts/validate_spec.py --list      # 列出
python scripts/validate_spec.py --show castle   # 看某风格全部参数
```

| 风格 | 说明 | 参数量 |
|---|---|---|
| `castle` | 中世纪城堡外墙：四面墙 + 城垛 + 四角圆塔（锥顶）+ 拱形城门 | 21 |
| `wall` | 围墙/院墙：墙基 + 墙体 + 压顶 + 可选城垛、扶壁柱、门洞 | 15 |
| `tower` | 独立塔楼：基座 + 塔身 + 腰线 + 窗 + 门 + 顶（锥/平/雉堞） | 18 |
| `bridge` | 拱桥：桥面 + 桥墩 + 椭圆拱券 + 栏杆 | 14 |
| `modern_villa` | 现代别墅外壳：体块 + 横向窗带 + 层间装饰 + 挑檐平顶/露台 | 14 |

## 参数类型

| type | 校验规则 |
|---|---|
| `int` | 转 int；受 `min` / `max` 约束 |
| `float` | 转 float；受 `min` / `max` 约束 |
| `bool` | 接受 true/false、1/0、yes/no、on/off |
| `str` | 原样 |
| `choice` | 必须在 `choices` 列表内 |
| `block` | **走方块白名单校验**（`block_check` 的同一套逻辑） |

## 跨字段约束

风格文件里用 `constraints` 声明，在参数类型/范围校验**之后**执行：

```json
"constraints": [
  {"expr": "base_height < wall_height", "msg": "墙基高必须小于墙体总高"},
  {"expr": "gate_width <= size - 8",   "msg": "城门宽度过大，墙长至少要比门宽多 8 格"}
]
```

- 表达式只能访问 `params` 的键
- 可用的安全内置：`min / max / abs / round / int / float / len / sum / any / all / sorted`
- 求值失败会作为错误报出（不会静默通过）

---

## 校验输出示例

```bash
$ python scripts/validate_spec.py --check bad.json
警告 1 条：
  ! 参数 'wall_hieght' 不在风格 castle 的声明中（可能是笔误，已原样传入）

[FAIL] 错误 7 条：
  X 参数 size=10 小于下限 16
  X 参数 wall_block 的方块 'minecraft:stone_brick' 非法：目标版本 1.21.1 中不存在方块
      minecraft:stone_brick，是否想写 minecraft:stone_bricks？
  X 参数 battlement_height=40 大于上限 8
  X 墙基高必须小于墙体总高（约束：base_height < wall_height）
  …
```

退出码：`0` = 通过；`1` = 有问题 → **不应继续生成**。

---

## 轨道 B 的 spec

轨道 B 也用 spec，但几何写在一个由 `scaffold.py` 生成的 Python 文件里：

```json
{
  "name": "spiral_tower",
  "style": "tower",
  "intent": "在圆塔外侧做一条螺旋上升的外凸楼梯带（环绕约 4 圈），并带一圈扶手",
  "params": { "radius": 5, "height": 30, "window_enable": false },
  "output": { "dir": "mc_builds/spiral_tower", "basename": "spiral_tower" }
}
```

```bash
python scripts/scaffold.py --spec spiral.json --out build_spiral_tower.py --check
# 编辑 build() 函数体后：
python scripts/block_check.py build_spiral_tower.py
python build_spiral_tower.py
```

骨架里已经封装好：依赖导入、spec 读取、前置校验、分片、导出、报告落盘。
模型**只填 `build(g, S)` 函数体**，不接触 mcschematic API —— 因此不会重犯 API 层的坑。

生成的脚本在**运行时读取 spec.json**（不再把参数内嵌进脚本），并跑一遍 `validate_spec`：

- **参数迭代** = 改 spec.json → 重跑 `build_<name>.py`（不会覆盖 `build()` 里的几何）
- **几何迭代** = 编辑 `build()` 函数体

spec.json 始终是唯一真相源。
