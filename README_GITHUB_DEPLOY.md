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

1. 填写数仓、知识库/结果库和AI接口配置。
2. 登记月份留空，表示全部月份。
3. 先使用Dry-run和5条小批量测试。
4. 验证后取消Dry-run并保存配置。
5. 手工运行一次`run_once.bat`。
6. 运行`install_schedule.bat`创建Windows定时任务。

每台电脑都要重新填写一次密码和API Key，因为它们由当前Windows用户加密，不能跨电脑解密。

## 后续更新代码

确保当前批次已经结束，然后双击：

```text
update_code.bat
```

更新脚本只执行安全的`git pull --ff-only`并校验Python语法，不会覆盖：

- `scripts/voc_tagger_config.json`
- `.runtime\python`
- `logs`
- Windows定时任务

如果受Git管理的代码被手工修改，更新脚本会停止，避免覆盖本地改动。

## 日常使用

- `start_desktop.bat`：打开桌面配置和手工调试。
- `run_once.bat`：无界面执行一个批次。
- `install_schedule.bat`：创建或修改定时任务。
- `remove_schedule.bat`：删除定时任务。
- `update_code.bat`：拉取最新代码。

每次批次会先补齐`voc_tag_result.warehouse_name`为空的历史结果，再开始VOC打标。Dry-run只统计可补齐数量，不更新数据库。
