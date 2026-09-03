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
- 支持桌面版控制器和 Windows 定时批处理。

## 目录结构

```text
.
├─ start_voc_business_tagger.bat           # 原业务宽表打标器
├─ start_voc_original_statement_tagger.bat # 原始语句打标器
├─ start_desktop.bat                       # 两个入口共用的便携桌面启动脚本
├─ start_voc_ai_tagger.bat                 # 兼容旧桌面入口
├─ run_once.bat                            # 可指定配置的无界面批处理
├─ start_voc_scheduler.bat                 # 旧单任务兼容入口
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
voc_tag_result             # 默认 VOC 打标结果表，通常在结果库
```

来源宽表和结果表均可填写 `表名` 或 `库名.表名`。例如原始语句打标链路：

```text
rpa.dwd_rpa_voc_original_statement
  -> rpa.ods_rpa_voc_original_statement_tag_result
```

标签知识库始终从目标连接读取。结果表可按配置写入目标连接或来源连接：

| 打标器 | 来源表 | 结果表 | 结果连接 | 写入模式 |
| --- | --- | --- | --- | --- |
| 原业务宽表 | `dwd_rpa_voc_business` | `voc_tag_result` | `target` | `mysql` |
| 原始语句 | `rpa.dwd_rpa_voc_original_statement` | `rpa.ods_rpa_voc_original_statement_tag_result` | `source` | `primary_key` |

`mysql` 模式保留原来的 MySQL 重试更新和 `ON DUPLICATE KEY UPDATE`。`primary_key` 模式会生成稳定的正数 `BIGINT id`，使用普通 `INSERT` 交给主键表执行 upsert。

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

两个打标器使用同一套分类代码，但分别保存配置。按需要双击：

```text
start_voc_business_tagger.bat             # dwd_rpa_voc_business -> voc_tag_result
start_voc_original_statement_tagger.bat   # rpa.dwd_rpa_voc_original_statement -> rpa.ods_rpa_voc_original_statement_tag_result
```

两个窗口可同时打开，窗口标题会显示“原业务宽表”或“原始语句”。数据库、Key、模型、月份、批量数、结果连接、写入模式和提示词保存在各自的本地配置文件中，互不覆盖。

### Windows 定时任务

定时设置已经放进两个打标器窗口。打开对应窗口后，在“定时设置”中选择“每天固定时间”或“每隔 N 分钟”，然后点击“应用 / 更新定时”。两个任务分别读取各自的完整配置：

```text
原业务宽表：VOC AI Tagger
原始语句：VOC AI Tagger - Original Statement
```

业务任务沿用旧任务名 `VOC AI Tagger`。已有旧宽表任务不会因代码更新自动改变，业务窗口会显示“旧任务待升级”；点击“应用 / 更新定时”后，会原位更新旧任务，不会额外创建一个重复的宽表任务。`start_voc_scheduler.bat`、`install_schedule.bat` 和 `remove_schedule.bat` 只保留给旧单任务流程兼容使用，新配置不需要再打开这些入口。

## 依赖

首次使用先运行 `setup_new_pc.bat`。脚本会准备 `.runtime\python` 便携运行环境并校验 `pymysql`、`tkinter`，具体流程见 `README_GITHUB_DEPLOY.md`。

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

3. 启动对应的桌面版：

```text
start_voc_business_tagger.bat
start_voc_original_statement_tagger.bat
```

4. 在界面中填写：

```text
来源数据库连接
目标数据库连接（读取标签知识库）
来源宽表名
结果表表名
结果表连接和写入模式
标签版本
register_month
AI API Key / Base URL / 模型名
定时方式和执行时间
```

5. 先勾选 Dry-run，本批数量设置为 5，确认结果后再取消 Dry-run 写库。

6. 需要自动运行时，在当前打标器窗口点击“应用 / 更新定时”；需要停止自动运行时点击“停用定时”。停用其中一个任务不会影响另一个。

保存配置或更新代码不会创建 Windows 定时任务。只在实际承担定时打标的电脑上点击“应用 / 更新定时”。

## 配置文件安全

以下文件包含数据库密码或 API Key，禁止提交到 Git：

```text
scripts/voc_tagger_config.json
scripts/voc_business_tagger_config.json
scripts/voc_original_statement_tagger_config.json
scripts/voc_scheduler_config.json
voc_tagger_config.json
voc_scheduler_config.json
.env
```

这些文件已经写入 `.gitignore`。配置中的密码和 Key 使用 Windows DPAPI 加密，不能通过 Git 或直接复制到另一台电脑使用。新电脑拉取代码后，分别启动两个打标器、重新填写数据库密码和 API Key，再各自保存一次。启动脚本会预填正确的表名、结果连接、写入模式、内部 API `/v1` 地址和模型。

## 迁移和更新另一台电脑

首次安装按 `README_GITHUB_DEPLOY.md` 克隆仓库并运行 `setup_new_pc.bat`。已有安装在本次改动合并到 `master` 后双击 `update_code.bat`，即可通过 `git pull --ff-only` 拉取最新代码。

Git 只同步共享代码、启动入口、测试和文档，不同步上述本地配置文件或 Windows 定时任务。更新后分别运行两个桌面入口，在该电脑重新填写数据库密码和 API Key 并保存，再按需要应用各自的定时设置；该电脑还需要能访问来源库、标签知识库和 AI 接口。

## 注意事项

- 不要直接在业务宽表上覆盖 AI 打标过程数据。
- 打标结果写入界面中配置的结果表，旧配置默认使用 `voc_tag_result`。
- 原始语句链路需要来源数据库账号对 `rpa.ods_rpa_voc_original_statement_tag_result` 具备写权限。
- 宽表中的 `level4_category` 有值时，默认不再打标。
- 已成功打过的记录不会重复打标。
- `failed` 状态的记录允许后续重试。
- 如果标签知识库更新，需要使用新的 `label_version` 或按业务需要重新打标。
