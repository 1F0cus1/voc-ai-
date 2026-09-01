@echo off
chcp 65001 >nul
call "%~dp0start_voc_ai_tagger.bat" --config "%~dp0scripts\voc_business_tagger_config.json" --profile-name "原业务宽表" --default-source-table "dwd_rpa_voc_business" --default-result-table "voc_tag_result" --default-result-db-connection "target" --default-result-write-mode "mysql" --default-api-base-url "http://ai.xmpaohong.com:8080/v1" --default-model "gpt-5.6-sol"
