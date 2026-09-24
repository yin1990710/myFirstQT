
/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;
SET @MYSQLDUMP_TEMP_LOG_BIN = @@SESSION.SQL_LOG_BIN;
SET @@SESSION.SQL_LOG_BIN= 0;
SET @@GLOBAL.GTID_PURGED=/*!80000 '+'*/ '000fdc94-52ad-11f1-be74-0329c35a7641:1-406877';
DROP TABLE IF EXISTS `backtest_task_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `backtest_task_t` (
  `task_id` varchar(40) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '任务ID',
  `account` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '提交账号',
  `task_name` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '任务名称 账号-策略_日期范围',
  `strategy` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '策略名称',
  `strategy_file` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '对应策略py文件名',
  `start_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回测起始日期YYYYMMDD',
  `end_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回测结束日期YYYYMMDD',
  `status` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'running' COMMENT 'running/done/failed',
  `stage` varchar(16) COLLATE utf8mb4_unicode_ci DEFAULT 'queued' COMMENT 'queued/scanning/computing/done/failed',
  `progress_done` int DEFAULT NULL COMMENT '已扫描交易日数',
  `progress_total` int DEFAULT NULL COMMENT '总交易日数',
  `progress_current_date` varchar(8) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '当前扫描交易日',
  `error` text COLLATE utf8mb4_unicode_ci COMMENT '失败原因',
  `saved` tinyint NOT NULL DEFAULT '0' COMMENT '结果是否已写入结果表',
  `start_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '任务开始时间',
  `end_time` datetime DEFAULT NULL COMMENT '任务结束时间',
  `duration` decimal(10,1) DEFAULT NULL COMMENT '耗时秒',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`task_id`),
  KEY `idx_account` (`account`,`start_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='策略回测任务注册表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `em_board_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `em_board_daily_t` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `board_code` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '东财板块代码(BK开头)',
  `board_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '板块名称',
  `board_type` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '板块类型: 行业/概念',
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期(YYYYMMDD)',
  `pct_chg` decimal(6,2) DEFAULT NULL COMMENT '涨跌幅(%)',
  `turnover_rate` decimal(10,4) DEFAULT NULL COMMENT '换手率(%)',
  `main_net_inflow` decimal(20,2) DEFAULT NULL COMMENT '主力净流入(元)',
  `up_count` int DEFAULT NULL COMMENT '上涨家数',
  `down_count` int DEFAULT NULL COMMENT '下跌家数',
  `leading_stock` varchar(32) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '领涨股名称',
  `total_mv` decimal(20,2) DEFAULT NULL COMMENT '总市值(元)',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_board_date` (`board_code`,`trade_date`),
  KEY `idx_date_type` (`trade_date`,`board_type`)
) ENGINE=InnoDB AUTO_INCREMENT=44 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='东方财富行业/概念板块当日快照表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `etf_basic_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `etf_basic_t` (
  `ts_code` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'ETF代码',
  `csname` varchar(60) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'ETF简称',
  `extname` varchar(60) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'ETF扩展简称',
  `cname` varchar(120) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'ETF全称',
  `index_code` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '跟踪指数代码',
  `index_name` varchar(60) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '跟踪指数名称',
  `setup_date` varchar(8) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '成立日期',
  `list_date` varchar(8) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '上市日期',
  `list_status` varchar(4) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '上市状态：L上市 D退市 P暂停',
  `exchange` varchar(4) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '交易所：SH上交所 SZ深交所',
  `mgr_name` varchar(40) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '管理人',
  `custod_name` varchar(80) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '托管人',
  `mgt_fee` decimal(6,3) DEFAULT NULL COMMENT '管理费(%)',
  `etf_type` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'ETF类型',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据更新时间',
  PRIMARY KEY (`ts_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='ETF基础信息表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `etf_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `etf_daily_t` (
  `ts_code` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'ETF代码',
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期',
  `pre_close` decimal(10,4) DEFAULT NULL COMMENT '前收盘价',
  `open` decimal(10,4) DEFAULT NULL COMMENT '开盘价',
  `high` decimal(10,4) DEFAULT NULL COMMENT '最高价',
  `low` decimal(10,4) DEFAULT NULL COMMENT '最低价',
  `close` decimal(10,4) DEFAULT NULL COMMENT '收盘价',
  `change` decimal(10,4) DEFAULT NULL COMMENT '涨跌额',
  `pct_chg` decimal(8,3) DEFAULT NULL COMMENT '涨跌幅(%)',
  `vol` decimal(20,4) DEFAULT NULL COMMENT '成交量(手)',
  `amount` decimal(20,4) DEFAULT NULL COMMENT '成交额(千元)',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据更新时间',
  PRIMARY KEY (`ts_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='ETF日线行情表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `exchange_market_overview_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `exchange_market_overview_t` (
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期YYYYMMDD',
  `exchange` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易所：SSE上交所/SZSE深交所',
  `board_type` varchar(12) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '板块：全市场/主板/科创板/创业板',
  `company_count` int DEFAULT NULL COMMENT '上市公司数（家）',
  `listed_count` int DEFAULT NULL COMMENT '上市证券/股票数（只）',
  `total_mv` decimal(18,2) DEFAULT NULL COMMENT '总市值（亿元）',
  `float_mv` decimal(18,2) DEFAULT NULL COMMENT '流通市值（亿元）',
  `total_amount` decimal(18,2) DEFAULT NULL COMMENT '成交金额（亿元）',
  `total_volume` decimal(20,2) DEFAULT NULL COMMENT '成交量（万股，仅深交所）',
  `total_shares` decimal(18,2) DEFAULT NULL COMMENT '总股本（亿股，仅上交所）',
  `float_shares` decimal(18,2) DEFAULT NULL COMMENT '流通股本（亿股，仅上交所）',
  `avg_pe` decimal(12,4) DEFAULT NULL COMMENT '平均市盈率（倍）',
  `data_source` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '数据来源URL',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`trade_date`,`exchange`,`board_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='沪深交易所市场总貌分板块数据表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `index_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `index_daily_t` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `ts_code` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '指数代码',
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期',
  `open` decimal(10,2) DEFAULT NULL COMMENT '开盘价',
  `high` decimal(10,2) DEFAULT NULL COMMENT '最高价',
  `low` decimal(10,2) DEFAULT NULL COMMENT '最低价',
  `close` decimal(10,2) DEFAULT NULL COMMENT '收盘价',
  `pre_close` decimal(10,2) DEFAULT NULL COMMENT '前收盘价',
  `change` decimal(10,2) DEFAULT NULL COMMENT '涨跌额',
  `pct_chg` decimal(6,2) DEFAULT NULL COMMENT '涨跌幅(%)',
  `vol` bigint DEFAULT NULL COMMENT '成交量(手)',
  `amount` decimal(18,2) DEFAULT NULL COMMENT '成交额(千元)',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ts_date` (`ts_code`,`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=11899 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指数日线数据表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `industry_heat_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `industry_heat_daily_t` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期(YYYYMMDD)',
  `board_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '板块名称',
  `stock_count` int NOT NULL COMMENT '行业股票家数',
  `turnover_ratio` decimal(12,6) DEFAULT NULL COMMENT '总成交额/总市值',
  `up_down_ratio` decimal(12,4) DEFAULT NULL COMMENT '涨/跌比例',
  `up_over_8pct` int DEFAULT NULL COMMENT '涨幅>8%只数',
  `down_over_5pct` int DEFAULT NULL COMMENT '跌幅>5%只数',
  `pct_median` decimal(10,4) DEFAULT NULL COMMENT '涨幅中位数(%)',
  `up_ratio` decimal(8,4) DEFAULT NULL COMMENT '上涨只数/总家数',
  `avg_short_strength` decimal(10,4) DEFAULT NULL COMMENT '平均短线强弱得分',
  `heat_score` decimal(14,4) DEFAULT NULL COMMENT '综合打分',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_date_board` (`trade_date`,`board_name`),
  KEY `idx_date_score` (`trade_date`,`heat_score`)
) ENGINE=InnoDB AUTO_INCREMENT=3231 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='行业短线热度分析表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `market_overview_metric_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `market_overview_metric_daily_t` (
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期YYYYMMDD',
  `metric_key` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '指标编号(A1-A4,B1-B7,C1-C4)',
  `metric_group` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '指标分类(短线技术/资金面/估值)',
  `metric_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '指标名称',
  `metric_value` decimal(20,4) DEFAULT NULL COMMENT '指标值',
  `metric_unit` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '单位(%,亿元,倍,家)',
  `metric_source` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '数据来源',
  `metric_status` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT 'ok' COMMENT '状态(ok/approx/missing)',
  `metric_note` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`trade_date`,`metric_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='大盘指标每日快照表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `my_stock_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `my_stock_t` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `account` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '账号',
  `ts_code` varchar(12) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '股票代码',
  `stock_name` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '股票名称',
  `selected_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '入选日期YYYYMMDD',
  `buy_price` decimal(10,3) DEFAULT NULL COMMENT '买入价格',
  `strategy_name` varchar(128) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '入选策略名称',
  `note` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '备注',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_account_stock` (`account`,`ts_code`)
) ENGINE=InnoDB AUTO_INCREMENT=98 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户股票池表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `rzrq_ye_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `rzrq_ye_t` (
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL,
  `exchange_id` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `rzye` decimal(20,2) DEFAULT NULL,
  `rzmre` decimal(20,2) DEFAULT NULL,
  `rzche` decimal(20,2) DEFAULT NULL,
  `rqye` decimal(20,2) DEFAULT NULL,
  `rqmcl` decimal(20,2) DEFAULT NULL,
  `rzrqye` decimal(20,2) DEFAULT NULL,
  `rqyl` decimal(20,2) DEFAULT NULL,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据更新时间',
  PRIMARY KEY (`trade_date`,`exchange_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `stock_daily_basic_info_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `stock_daily_basic_info_t` (
  `ts_code` varchar(16) NOT NULL COMMENT '股票代码',
  `trade_date` varchar(8) NOT NULL COMMENT '交易日期',
  `close` double DEFAULT NULL COMMENT '当日收盘价',
  `turnover_rate` double DEFAULT NULL COMMENT '换手率(%)',
  `turnover_rate_f` double DEFAULT NULL COMMENT '换手率(自由流通股)',
  `volume_ratio` double DEFAULT NULL COMMENT '量比',
  `pe` double DEFAULT NULL COMMENT '市盈率(总市值/净利润)',
  `pe_ttm` double DEFAULT NULL COMMENT '市盈率TTM',
  `pb` double DEFAULT NULL COMMENT '市净率',
  `ps` double DEFAULT NULL COMMENT '市销率',
  `ps_ttm` double DEFAULT NULL COMMENT '市销率TTM',
  `dv_ratio` double DEFAULT NULL COMMENT '股息率(%)',
  `dv_ttm` double DEFAULT NULL COMMENT '股息率TTM(%)',
  `total_share` double DEFAULT NULL COMMENT '总股本(万股)',
  `float_share` double DEFAULT NULL COMMENT '流通股本(万股)',
  `free_share` double DEFAULT NULL COMMENT '自由流通股本(万股)',
  `total_mv` double DEFAULT NULL COMMENT '总市值(万元)',
  `circ_mv` double DEFAULT NULL COMMENT '流通市值(万元)',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据更新时间',
  PRIMARY KEY (`ts_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票每日基本交易指标';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `stock_daily_factor_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `stock_daily_factor_t` (
  `ts_code` varchar(16) NOT NULL COMMENT '股票代码',
  `trade_date` varchar(8) NOT NULL COMMENT '交易日期',
  `close` double DEFAULT NULL COMMENT '当日收盘价',
  `open` double DEFAULT NULL COMMENT '当日开盘价',
  `high` double DEFAULT NULL COMMENT '当日最高价',
  `low` double DEFAULT NULL COMMENT '当日最低价',
  `pre_close` double DEFAULT NULL COMMENT '前一日收盘价',
  `change` double DEFAULT NULL COMMENT '当日涨跌额',
  `pct_change` double DEFAULT NULL COMMENT '当日涨跌幅(%)',
  `vol` double DEFAULT NULL COMMENT '成交量(手)',
  `amount` double DEFAULT NULL COMMENT '成交额(千元)',
  `adj_factor` double DEFAULT NULL COMMENT '复权因子',
  `open_hfq` double DEFAULT NULL COMMENT '后复权开盘价',
  `open_qfq` double DEFAULT NULL COMMENT '前复权开盘价',
  `close_hfq` double DEFAULT NULL COMMENT '后复权收盘价',
  `close_qfq` double DEFAULT NULL COMMENT '前复权收盘价',
  `high_hfq` double DEFAULT NULL COMMENT '后复权最高价',
  `high_qfq` double DEFAULT NULL COMMENT '前复权最高价',
  `low_hfq` double DEFAULT NULL COMMENT '后复权最低价',
  `low_qfq` double DEFAULT NULL COMMENT '前复权最低价',
  `pre_close_hfq` double DEFAULT NULL COMMENT '后复权前收盘价',
  `pre_close_qfq` double DEFAULT NULL COMMENT '前复权前收盘价',
  `macd_dif` double DEFAULT NULL COMMENT 'MACD-DIF',
  `macd_dea` double DEFAULT NULL COMMENT 'MACD-DEA',
  `macd` double DEFAULT NULL COMMENT 'MACD',
  `kdj_k` double DEFAULT NULL COMMENT 'KDJ-K',
  `kdj_d` double DEFAULT NULL COMMENT 'KDJ-D',
  `kdj_j` double DEFAULT NULL COMMENT 'KDJ-J',
  `rsi_6` double DEFAULT NULL COMMENT 'RSI-6',
  `rsi_12` double DEFAULT NULL COMMENT 'RSI-12',
  `rsi_24` double DEFAULT NULL COMMENT 'RSI-24',
  `boll_upper` double DEFAULT NULL COMMENT 'BOLL上轨',
  `boll_mid` double DEFAULT NULL COMMENT 'BOLL中轨',
  `boll_lower` double DEFAULT NULL COMMENT 'BOLL下轨',
  `cci` double DEFAULT NULL COMMENT 'CCI顺势指标',
  PRIMARY KEY (`ts_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票每日技术面因子';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `stock_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `stock_daily_t` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `ts_code` varchar(20) NOT NULL COMMENT '股票代码',
  `trade_date` varchar(8) NOT NULL COMMENT '交易日期',
  `open` float DEFAULT NULL COMMENT '开盘价',
  `high` float DEFAULT NULL COMMENT '最高价',
  `low` float DEFAULT NULL COMMENT '最低价',
  `close` float DEFAULT NULL COMMENT '收盘价',
  `pre_close` float DEFAULT NULL COMMENT '昨收价',
  `change` float DEFAULT NULL COMMENT '涨跌额',
  `pct_chg` float DEFAULT NULL COMMENT '涨跌幅',
  `vol` float DEFAULT NULL COMMENT '成交量',
  `amount` float DEFAULT NULL COMMENT '成交额',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  `turning_point` varchar(10) DEFAULT NULL COMMENT '波峰、波谷、波中',
  `is_buy` varchar(20) DEFAULT '未买' COMMENT '是否已买 未买/已买入',
  `ma5` float DEFAULT NULL,
  `ma30` float DEFAULT NULL,
  `qfq_adj_factor` float DEFAULT NULL,
  `short_strength_score` decimal(5,1) DEFAULT NULL COMMENT '短线强弱得分SSS(0-100,综合五维加权)',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ts_date` (`ts_code`,`trade_date`),
  KEY `idx_ts_code` (`ts_code`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=42099466 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票日数据表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `stock_dfcf_industry_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `stock_dfcf_industry_t` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `ts_code` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '股票代码(带后缀,如688213.SH)',
  `board_code` varchar(16) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '东财板块代码(BK开头)',
  `board_name` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '板块名称',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_code_board` (`ts_code`,`board_code`),
  KEY `idx_board` (`board_code`)
) ENGINE=InnoDB AUTO_INCREMENT=90205 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='股票东财行业/板块分类表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `stock_index_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `stock_index_daily_t` (
  `ts_code` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL,
  `close` float DEFAULT NULL,
  `open` float DEFAULT NULL,
  `high` float DEFAULT NULL,
  `low` float DEFAULT NULL,
  `pre_close` float DEFAULT NULL,
  `change` float DEFAULT NULL,
  `pct_chg` float DEFAULT NULL,
  `vol` float DEFAULT NULL,
  `amount` float DEFAULT NULL,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据更新时间',
  PRIMARY KEY (`ts_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `stock_index_future_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `stock_index_future_daily_t` (
  `ts_code` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL,
  `pre_close` float DEFAULT NULL,
  `pre_settle` float DEFAULT NULL,
  `open` float DEFAULT NULL,
  `high` float DEFAULT NULL,
  `low` float DEFAULT NULL,
  `close` float DEFAULT NULL,
  `settle` float DEFAULT NULL,
  `vol` float DEFAULT NULL,
  `amount` float DEFAULT NULL,
  `oi` float DEFAULT NULL,
  `oi_chg` float DEFAULT NULL,
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据更新时间',
  PRIMARY KEY (`ts_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `stock_index_future_delta_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `stock_index_future_delta_t` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `trade_date` varchar(10) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期',
  `idx_fu_avg_before_fu_diff` decimal(10,2) DEFAULT NULL COMMENT '三大指数期前一日现差算术均值',
  `idx_fu_avg_fu_diff` decimal(10,2) DEFAULT NULL COMMENT '三大指数期现差当日算术均值',
  `idx_fu_avg_diff_delta` decimal(10,2) DEFAULT NULL COMMENT '三大指数期现差边际变化算术均值',
  `idx_nextdate_diff` decimal(10,2) DEFAULT NULL COMMENT '中证全指次日涨跌幅',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ts_date` (`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=431 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指数期现差变化数据表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `stock_info_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `stock_info_t` (
  `ts_code` varchar(15) COLLATE utf8mb4_unicode_ci NOT NULL,
  `stock_name` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL,
  `industry` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `area` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `total_share` decimal(20,6) DEFAULT NULL,
  `float_share` decimal(20,6) DEFAULT NULL,
  `total_mv` decimal(20,2) DEFAULT NULL,
  `circ_mv` decimal(20,2) DEFAULT NULL,
  `list_date` varchar(8) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `exchange` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `market` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `is_hs` varchar(5) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`ts_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `strategy_backtest_result_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `strategy_backtest_result_t` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `strategy_name` varchar(128) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '策略名称',
  `strategy_file` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '对应策略py文件名',
  `start_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回测起始日期YYYYMMDD',
  `end_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '回测结束日期YYYYMMDD',
  `ts_code` varchar(12) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '股票代码',
  `stock_name` varchar(50) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '股票名称',
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '选股日期YYYYMMDD',
  `max_gain_10d` decimal(8,2) DEFAULT NULL COMMENT '10日最大涨幅(%)',
  `max_down_10d` decimal(8,2) DEFAULT NULL COMMENT '10日最大跌幅(%)',
  `max_gain_20d` decimal(8,2) DEFAULT NULL COMMENT '20日最大涨幅(%)',
  `max_down_20d` decimal(8,2) DEFAULT NULL COMMENT '20日最大跌幅(%)',
  `avg_gain_10d` decimal(8,2) DEFAULT NULL COMMENT '平均10日最大涨幅(%)',
  `avg_gain_20d` decimal(8,2) DEFAULT NULL COMMENT '平均20日最大涨幅(%)',
  `avg_down_10d` decimal(8,2) DEFAULT NULL COMMENT '平均10日最大跌幅(%)',
  `avg_down_20d` decimal(8,2) DEFAULT NULL COMMENT '平均20日最大跌幅(%)',
  `excess_sh` decimal(8,2) DEFAULT NULL COMMENT '相对上证指数超额收益(%)',
  `excess_sz` decimal(8,2) DEFAULT NULL COMMENT '相对创业板指超额收益(%)',
  `sharpe` decimal(10,2) DEFAULT NULL COMMENT '夏普比率(年化)',
  `sh_index_10d` decimal(8,2) DEFAULT NULL COMMENT '上证指数10日收益(%)',
  `sz_index_10d` decimal(8,2) DEFAULT NULL COMMENT '创业板指10日收益(%)',
  `total_stocks` int DEFAULT NULL COMMENT '选股总数',
  `valid_stocks` int DEFAULT NULL COMMENT '有效回测数',
  `total_dates` int DEFAULT NULL COMMENT '涉及交易日数',
  `run_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '回测执行时间',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_run_stock` (`strategy_name`,`start_date`,`end_date`,`ts_code`,`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=6177 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='策略回测结果表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `strategy_result_analysis_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `strategy_result_analysis_t` (
  `id` int NOT NULL AUTO_INCREMENT,
  `strategy` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '策略名称',
  `avg_gain_10d` decimal(10,4) DEFAULT NULL COMMENT '10日算术平均涨幅(%)',
  `stock_count` int NOT NULL DEFAULT '0' COMMENT '样本股票数',
  `latest_trade_date` varchar(8) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '最新选入交易日',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_strategy` (`strategy`)
) ENGINE=InnoDB AUTO_INCREMENT=33 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='策略结果分析统计表';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `strategy_selected_stock_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `strategy_selected_stock_daily_t` (
  `ts_code` varchar(12) NOT NULL COMMENT '股票代码',
  `stock_name` varchar(50) DEFAULT NULL COMMENT '股票名称(来自stock_info_t，可空)',
  `selected_date` varchar(8) NOT NULL COMMENT '入选日期(股票被策略选中并写入表的日期)',
  `compute_date` varchar(8) DEFAULT NULL COMMENT '结果回填日期(回填10日涨跌幅和同期指数涨幅指标的日期)',
  `strategy` varchar(128) NOT NULL COMMENT '选股策略，多个用逗号分隔(如2wave_daily,2wave_w23)',
  `selected` tinyint NOT NULL DEFAULT '1' COMMENT '是否被选中(0:否,1:是)，任一策略选中即为1',
  `max_gain_10d` float DEFAULT NULL COMMENT '10个交易日中最大涨幅(%,T+1~T+10最高close/T日close)',
  `max_down_10d` float DEFAULT NULL COMMENT '10个交易日中最大跌幅(%,T+1~T+10最低close/T日close)',
  `max_down_20d` float DEFAULT NULL COMMENT '20个交易日中最大跌幅(%,T+1~T+20最低close/T日close)',
  `max_gain_20d` float DEFAULT NULL COMMENT '20个交易日中最大涨幅(%,T+1~T+20最高close/T日close)',
  `sse_index_same_inc` decimal(8,2) DEFAULT NULL COMMENT '同期上证指数涨幅',
  `chinext_index_same_inc` decimal(8,2) DEFAULT NULL COMMENT '同期创业板指数涨幅',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`ts_code`,`selected_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='每日选股结果记录表(便于回测)';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `task_run_log_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `task_run_log_t` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `source` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'cron' COMMENT '任务来源(cron例行定时/manual例行页手动/strategy选股策略手动)',
  `biz_date` varchar(8) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '业务目标日YYYYMMDD(选股策略手动提交的目标交易日)',
  `run_id` varchar(60) COLLATE utf8mb4_unicode_ci NOT NULL,
  `run_date` date NOT NULL COMMENT '运行日期',
  `batch_phase` varchar(30) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '批次阶段(数据更新/选股分析与报告)',
  `step` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '步骤序号(如 2/18)',
  `script_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '任务脚本名(如 update_stock_daily.py)',
  `task_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '任务名称(如 指数日交易数据)',
  `status` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'pending' COMMENT '任务状态(success/failed/running/pending)',
  `start_time` datetime DEFAULT NULL COMMENT '任务开始时间',
  `end_time` datetime DEFAULT NULL COMMENT '任务结束时间',
  `duration_sec` decimal(10,1) DEFAULT NULL COMMENT '运行耗时(秒)',
  `detail_log` varchar(200) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '明细日志文件名(cron_logs内)',
  `batch_status` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '批次整体状态(success/failed/running)',
  `batch_start` datetime DEFAULT NULL COMMENT '批次开始时间',
  `batch_end` datetime DEFAULT NULL COMMENT '批次结束时间',
  `batch_duration_sec` decimal(10,1) DEFAULT NULL COMMENT '批次总耗时(秒)',
  `total_tasks` int DEFAULT NULL COMMENT '批次任务总数',
  `success_tasks` int DEFAULT NULL COMMENT '批次成功数',
  `failed_tasks` int DEFAULT NULL COMMENT '批次失败数',
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_run_script` (`run_id`,`script_name`),
  KEY `idx_run_date` (`run_date`),
  KEY `idx_script` (`script_name`),
  KEY `idx_source` (`source`)
) ENGINE=InnoDB AUTO_INCREMENT=9755 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='任务运行监控表(每日定时任务执行记录)';
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `user_login_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_login_t` (
  `id` int NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `account` varchar(50) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '账号',
  `password` varchar(64) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '密码(SHA256)',
  `login_status` tinyint NOT NULL DEFAULT '0' COMMENT '登录状态: 0=离线 1=在线',
  `login_time` datetime DEFAULT NULL COMMENT '最近登录时间',
  `logout_time` datetime DEFAULT NULL COMMENT '最近登出时间',
  `valid_from_date` date DEFAULT NULL COMMENT '账号有效期开始日期',
  `create_time` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_account` (`account`)
) ENGINE=InnoDB AUTO_INCREMENT=3 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户登录信息表';
/*!40101 SET character_set_client = @saved_cs_client */;
SET @@SESSION.SQL_LOG_BIN = @MYSQLDUMP_TEMP_LOG_BIN;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

