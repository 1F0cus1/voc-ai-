ALTER TABLE `voc_tag_result`
  MODIFY COLUMN `source_id` bigint(20) NULL
    COMMENT '旧版来源业务宽表ID，使用voc_hash的新数据留空',
  ADD COLUMN `source_key` varchar(64) NULL AFTER `source_id`
    COMMENT '来源业务主键，对应dwd_rpa_voc_business.voc_hash',
  ADD UNIQUE KEY `uk_source_key_version` (`source_table`, `source_key`, `label_version`);
