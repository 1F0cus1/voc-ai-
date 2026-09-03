@echo off
chcp 65001 >nul
call "%~dp0start_voc_ai_tagger.bat" --config "%~dp0scripts\voc_original_statement_tagger_config.json" --profile-name "原始语句" --profile-id "original_statement" --schedule-task-name "VOC AI Tagger - Original Statement" --default-source-table "rpa.dwd_rpa_voc_original_statement" --default-result-table "rpa.ods_rpa_voc_original_statement_tag_result" --default-result-db-connection "source" --default-result-write-mode "primary_key" --default-api-base-url "http://ai.xmpaohong.com:8080/v1" --default-model "gpt-5.6-sol"
