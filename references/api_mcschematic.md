# mcschematic 实测 API 速查

> 全部由本机 `inspect` 与实跑得出，**不是**从文档或网上示例转述。
> 环境：Python 3.13 + `pip install mcschematic`

---

## 1. 导入与版本枚举

```python
import mcschematic
from mcschematic import Version        # 枚举在顶层模块
```

### ⚠️ 枚举的正确位置

```python
# ✗ 实测报 AttributeError
mcschematic.MCSchematic.Version.JE_1_21_1

# ✓ 正确
mcschematic.Version.JE_1_21_1
```

### 常用版本枚举值（实测导出）

| 枚举名 | 值（= DataVersion） |
|---|---|
| `Version.JE_1_20_4` | 3700 |
| `Version.JE_1_20_5` | 3837 |
| `Version.JE_1_21` | 3953 |
| **`Version.JE_1_21_1`** | **3955** |
| `Version.JE_1_21_2` | 4080 |
| `Version.JE_1_21_3` | 4082 |
| `Version.JE_1_21_4` | 4189 |
| `Version.JE_1_21_5` | 4325 |

枚举成员共 590 个（含全部预发布版）。命名规则：
- 正式版 `JE_<major>_<minor>[_<patch>]`
- 预发布 `JE_1_21_PRE1`、候选 `JE_1_21_RC1`

---

## 2. 核心方法签名（`inspect.signature` 实测）

```python
MCSchematic.setBlock(self, position: tuple[int, int, int], blockData: str) -> None
MCSchematic.save(self, outputFolderPath: str, schemName: str,
                 version: 'Version', fastSaving: bool = False) -> None
```

`MCSchematic` 的全部公开方法（实测枚举）：

```
getBlockDataAt, getBlockStateAt, getStructure, getSubSchematic,
makeCopy, placeSchematic, placeStructure, save, setBlock
```

---

## 3. 最小可用片段

```python
import mcschematic

schem = mcschematic.MCSchematic()
schem.setBlock((0, 0, 0),  "minecraft:stone_bricks")
schem.setBlock((-5, 0, -5), "minecraft:cobblestone")     # 负坐标可用
schem.setBlock((3, 4, 3), "minecraft:dark_oak_stairs[facing=north,half=bottom]")

# 签名：save(输出目录, 文件名, 版本) —— 自动补 .schem 后缀
schem.save(r"C:\...\.minecraft\schematics", "castle_wall", mcschematic.Version.JE_1_21_1)
```

---

## 4. 实测行为（容易踩的点）

| 行为 | 实测结果 |
|---|---|
| 负坐标 | **可用**，库自动把整体平移到非负区，包围盒随之变化 |
| 文件名后缀 | 自动补 `.schem`，`schemName` 别自己带后缀 |
| 方块名校验 | **完全不校验** —— 任何字符串照写。故必须自建白名单（见 `block_check.py`） |
| blockstate 属性 | 方括号写法可用：`"minecraft:oak_stairs[facing=north,half=bottom]"` |
| 输出格式 | Sponge Schematic **v2**（`Version=2`），gzip 压缩 |
| 与游戏版本对齐 | 由 `version` 参数决定 `DataVersion`；`JE_1_21_1` → 3955 ✓ |

---

## 5. 生成文件的内部结构（nbtlib 回读实测）

```python
import nbtlib
f = nbtlib.load("castle_wall.schem")
# 根字段（实测）
#   Version       = Int(2)          Sponge 格式版本
#   DataVersion   = Int(3955)       即 MC 1.21.1
#   Width/Height/Length = Short
#   Palette       = {blockstate_str: Int(index)}
#   BlockData     = ByteArray       varint 流，索引顺序 x + z*W + y*W*L
#   Offset        = IntArray
```

### 解码 varint（`verify_schem.py` / `render_preview.py` 已内置）

```python
def read_varint(data, pos):
    val = shift = 0
    while True:
        b = data[pos] & 0xFF; pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            return val, pos
        shift += 7
```

**快速路径**：若调色板很小（<128 项），绝大多数 varint 是单字节，
可判断 `raw.size == W*H*L and not (raw >= 0x80).any()` 后直接 `astype(np.uint16)`，省掉逐字节循环。

---

## 6. 依赖

```
mcschematic   # 生成 .schem（必需）
nbtlib        # 回读 .schem 做断言/diff/渲染（必需）
numpy         # 渲染与体素运算（必需）
```

不需要 PIL / matplotlib —— 预览渲染用纯 numpy + zlib 手写 PNG。
