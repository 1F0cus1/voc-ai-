ALTER TABLE `voc_tag_result`
  ADD COLUMN `warehouse_name` varchar(200) DEFAULT NULL
    COMMENT '发货仓/仓库名称，同步自宽表warehouse_name'
    AFTER `product_name`;
