# VOC AI 打标器：GitHub部署与更新

本仓库只保存代码、SQL和启动脚本，不保存数据库密码、API Key、日志或便携Python环境。

## 新电脑第一次安装

先安装 Git for Windows，然后在 PowerShell 中执行：

```powershell
git clone https://github.com/1F0cus1/voc-ai-.git D:\VOC-AI
cd D:\VOC-AI
.\setup_new_pc.bat
```

`setup_new_pc.bat`会根据仓库中的`VERSION`从对应GitHub Release下载Windows便携包，校验SHA256，只提取Python运行环境到`.runtime\python`，然后打开桌面配置工具。

第一次打开桌面端后：

1. 根据需要打开 `start_voc_business_tagger.bat` 或 `start_voc_original_statement_tagger.bat`，填写数仓、标签知识库和AI接口配置。
2. 登记月份留空，表示全部月份。
3. 先使用Dry-run和5条小批量测试。
4. 验证后取消Dry-run并保存配置。
5. 在对应打标器窗口中选择定时方式和时间，点击“应用 / 更新定时”。
6. 两个打标器需要自动运行时，分别在各自窗口应用一次定时设置。任务也会在当前Windows用户每次登录后自动补跑一次。

保存配置、克隆仓库或更新代码都不会创建 Windows 定时任务。只在实际承担定时打标的电脑上点击“应用 / 更新定时”。

每台电脑都要重新填写一次密码和API Key，因为它们由当前Windows用户加密，不能跨电脑解密。

## 后续更新代码

确保当前批次已经结束，然后双击：

```text
update_code.bat
```

更新脚本只执行安全的`git pull --ff-only`并校验Python语法，不会覆盖：

- `scripts/voc_tagger_config.json`
- `scripts/voc_business_tagger_config.json`
- `scripts/voc_original_statement_tagger_config.json`
- `.runtime\python`
- `logs`
- Windows定时任务

如果受Git管理的代码被手工修改，更新脚本会停止，避免覆盖本地改动。

代码更新不会自动覆盖本地配置或 Windows 定时任务。需要修改运行时间时，直接打开对应打标器，在窗口内更新定时设置。已有旧业务任务继续使用 `VOC AI Tagger` 任务名，业务窗口会显示“旧任务待升级”；第一次在业务窗口应用定时时会原位升级，不会创建重复任务。

## 日常使用

- `start_desktop.bat`：打开桌面配置和手工调试；首次运行会自动准备便携Python环境。
- `start_voc_ai_tagger.bat`：兼容旧入口，功能与`start_desktop.bat`相同。
- `start_voc_business_tagger.bat`：原业务宽表打标器，使用独立本地配置。
- `start_voc_original_statement_tagger.bat`：原始语句打标器，使用独立本地配置。
- `run_once.bat`：兼容无界面执行，也可通过参数选择配置。
- `install_schedule.bat`：旧单任务安装入口，仅保留兼容。
- `start_voc_scheduler.bat`：旧单任务兼容入口。
- `remove_schedule.bat`：旧业务任务删除入口；新流程直接在对应窗口停用。
- `update_code.bat`：拉取最新代码。

两个 Windows 定时任务分别读取 `scripts/voc_business_tagger_config.json` 和 `scripts/voc_original_statement_tagger_config.json`。停用或更新其中一个不会影响另一个。旧业务批次会先补齐`voc_tag_result.warehouse_name`为空的历史结果，再开始VOC打标；Dry-run只统计可补齐数量，不更新数据库。
