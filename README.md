# VOC AI 打标器

这是一个本地运行的 VOC 四级标签打标工具，用于从业务宽表读取未打标评论，结合标签知识库、规则和 AI 模型，生成单标签打标结果。

## 功能说明

- 从数仓宽表读取 VOC 原文。
- 只处理 `raw_feedback` 不为空，且宽表 `level4_category` 为空的数据。
- 支持按 `register_month` 指定月份打标。
- 从 `voc_label_taxonomy` 读取启用的标签知识库。
- 每条评论只输出一个四级标签。
- 支持无关评论识别，例如纯符号、无意义重复字符、闲聊内容等。
- 支持 Dry-run 小批量测试，不写入数据库。
- 支持失败记录重试，重试时优先更新原失败记录。
- 支持桌面版控制器和简易本地调度后台。

## 目录结构

```text
.
├─ start_voc_ai_tagger.bat                 # 桌面版启动脚本
├─ start_voc_scheduler.bat                 # 本地调度后台启动脚本
├─ scripts/
│  ├─ voc_ai_tag_controller.py             # VOC 打标主程序
│  ├─ voc_scheduler_server.py              # 简易本地调度后台
│  ├─ import_voc_labels.py                 # 标签知识库 Excel 导入脚本
│  ├─ migrate_voc_tag_result_to_voc_hash.sql
│  └─ migrate_voc_tag_result_add_warehouse_name.sql
└─ README.md
```

## 数据库表

当前工具主要依赖三类表：

```text
dwd_rpa_voc_business       # 来源业务宽表，通常在数仓库
voc_label_taxonomy         # VOC 标签知识库，通常在结果库
voc_tag_result             # VOC 打标结果表，通常在结果库
```

宽表当前以 `voc_hash` 作为来源追溯字段，但程序不会只依赖 `voc_hash` 判断是否重复打标。

## 去重逻辑

为了避免开发刷新 `voc_hash` 后导致重复打标，程序使用兼容双 hash 的方式判断历史数据：

```text
历史兼容 hash = MD5(raw_feedback)
后续业务 hash = MD5(data_source + channel + platform_order_no + sub_order_no + register_time + raw_feedback)
```

判断是否已打过时，会同时兼容旧的原文 hash 和新的业务 hash。后续新写入的 `input_hash` 使用业务 hash。

## 启动方式

### 桌面版

双击：

```text
start_voc_ai_tagger.bat
```

桌面版适合人工测试、小批量打标、调试 AI 返回结果。

### 本地调度后台

双击：

```text
start_voc_scheduler.bat
```

启动后在浏览器访问本地后台，用于手动启动、暂停或设置简单定时任务。

## 依赖

需要 Python 3，并安装：

```bash
pip install pymysql
```

桌面版启动脚本会在启动前检查 `pymysql`，缺失时自动尝试安装。

## 首次使用步骤

1. 在数据库中创建并确认以下表：

```text
voc_label_taxonomy
voc_tag_result
dwd_rpa_voc_business
```

2. 如结果表缺少新字段，先执行迁移 SQL：

```text
scripts/migrate_voc_tag_result_to_voc_hash.sql
scripts/migrate_voc_tag_result_add_warehouse_name.sql
```

3. 启动桌面版：

```text
start_voc_ai_tagger.bat
```

4. 在界面中填写：

```text
来源数据库连接
结果数据库连接
来源宽表名
标签版本
register_month
AI API Key / Base URL / 模型名
```

5. 先勾选 Dry-run，本批数量设置为 5，确认结果后再取消 Dry-run 写库。

## 配置文件安全

以下文件包含数据库密码或 API Key，禁止提交到 Git：

```text
scripts/voc_tagger_config.json
scripts/voc_scheduler_config.json
voc_tagger_config.json
voc_scheduler_config.json
.env
```

这些文件已经写入 `.gitignore`。迁移到其它电脑时，请在新电脑重新填写配置，不要直接复制旧电脑配置文件。

## 迁移到另一台电脑

需要复制以下内容：

```text
start_voc_ai_tagger.bat
start_voc_scheduler.bat
scripts/
README.md
```

新电脑需要具备：

```text
Python 3
pymysql
能访问来源库、结果库和 AI 接口
```

如果使用便携 Python，可以把 Python 环境一起放入项目目录，并调整启动脚本中的 Python 路径。

## 注意事项

- 不要直接在业务宽表上覆盖 AI 打标过程数据。
- 打标结果优先写入 `voc_tag_result`。
- 宽表中的 `level4_category` 有值时，默认不再打标。
- 已成功打过的记录不会重复打标。
- `failed` 状态的记录允许后续重试。
- 如果标签知识库更新，需要使用新的 `label_version` 或按业务需要重新打标。
