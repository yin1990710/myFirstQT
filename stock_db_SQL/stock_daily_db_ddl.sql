-- MySQL dump 10.13  Distrib 9.7.1, for macos14.8 (x86_64)
--
-- Host: 127.0.0.1    Database: stock_daily_db
-- ------------------------------------------------------
-- Server version	9.7.0

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

--
-- GTID state at the beginning of the backup 
--

SET @@GLOBAL.GTID_PURGED=/*!80000 '+'*/ '000fdc94-52ad-11f1-be74-0329c35a7641:1-397666';

--
-- Table structure for table `index_daily_t`
--

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
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ts_date` (`ts_code`,`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=10709 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指数日线数据表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `rzrq_ye_t`
--

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
  PRIMARY KEY (`trade_date`,`exchange_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `sse_market_summary_t`
--

DROP TABLE IF EXISTS `sse_market_summary_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `sse_market_summary_t` (
  `trade_date` varchar(8) NOT NULL COMMENT '交易日期',
  `board_type` varchar(20) NOT NULL COMMENT '板块类型（主板、科创板等）',
  `total_mv` decimal(15,2) DEFAULT NULL COMMENT '总市值（亿元）',
  `float_mv` decimal(15,2) DEFAULT NULL COMMENT '流通市值（亿元）',
  `company_count` int DEFAULT NULL COMMENT '上市公司数',
  `stock_count` int DEFAULT NULL COMMENT '上市股票数',
  `total_shares` decimal(15,2) DEFAULT NULL COMMENT '总股本（亿股）',
  `float_shares` decimal(15,2) DEFAULT NULL COMMENT '流通股本（亿股）',
  `avg_pe_ratio` decimal(10,2) DEFAULT NULL COMMENT '平均市盈率',
  `update_time` datetime DEFAULT NULL COMMENT '更新时间',
  PRIMARY KEY (`trade_date`,`board_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='上交所市场总貌数据表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `stock_daily_basic_info_t`
--

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
  PRIMARY KEY (`ts_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票每日基本交易指标';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `stock_daily_factor_t`
--

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

--
-- Table structure for table `stock_daily_qfq_t`
--

DROP TABLE IF EXISTS `stock_daily_qfq_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `stock_daily_qfq_t` (
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
  `qfq_adj_factor` float(4,2) DEFAULT NULL COMMENT '当日前复权因子',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ts_date` (`ts_code`,`trade_date`),
  KEY `idx_ts_code` (`ts_code`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=1871482 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票前复权日交易数据表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `stock_daily_t`
--

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
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ts_date` (`ts_code`,`trade_date`),
  KEY `idx_ts_code` (`ts_code`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=41080892 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票日数据表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `stock_index_daily_t`
--

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
  PRIMARY KEY (`ts_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `stock_index_future_daily_t`
--

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
  PRIMARY KEY (`ts_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `stock_index_future_delta_t`
--

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

--
-- Table structure for table `stock_info_t`
--

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

--
-- Table structure for table `strategy_selected_stock_daily_t`
--

DROP TABLE IF EXISTS `strategy_selected_stock_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `strategy_selected_stock_daily_t` (
  `ts_code` varchar(12) NOT NULL COMMENT '股票代码',
  `stock_name` varchar(50) DEFAULT NULL COMMENT '股票名称(来自stock_info_t，可空)',
  `trade_date` varchar(8) NOT NULL COMMENT '交易日(选股目标日)',
  `strategy` varchar(128) NOT NULL COMMENT '选股策略，多个用逗号分隔(如2wave_daily,2wave_w23)',
  `selected` tinyint NOT NULL DEFAULT '1' COMMENT '是否被选中(0:否,1:是)，任一策略选中即为1',
  `max_gain_10d` float DEFAULT NULL COMMENT '10个交易日中最大涨幅(%,T+1~T+10最高close/T日close)',
  `max_down_10d` float DEFAULT NULL COMMENT '10个交易日中最大跌幅(%,T+1~T+10最低close/T日close)',
  `max_down_20d` float DEFAULT NULL COMMENT '20个交易日中最大跌幅(%,T+1~T+20最低close/T日close)',
  `max_gain_20d` float DEFAULT NULL COMMENT '20个交易日中最大涨幅(%,T+1~T+20最高close/T日close)',
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`ts_code`,`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='每日选股结果记录表(便于回测)';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `szse_market_summary_t`
--

DROP TABLE IF EXISTS `szse_market_summary_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `szse_market_summary_t` (
  `trade_date` varchar(8) NOT NULL COMMENT '交易日期',
  `board_type` varchar(20) NOT NULL COMMENT '板块类型（主板A股、创业板A股等）',
  `total_mv` decimal(15,2) DEFAULT NULL COMMENT '总市值（亿元）',
  `float_mv` decimal(15,2) DEFAULT NULL COMMENT '流通市值（亿元）',
  `company_count` int DEFAULT NULL COMMENT '上市公司数',
  `total_amount` decimal(15,2) DEFAULT NULL COMMENT '总成交金额（亿元）',
  `avg_pe_ratio` decimal(10,2) DEFAULT NULL COMMENT '平均市盈率',
  `update_time` datetime DEFAULT NULL COMMENT '更新时间',
  PRIMARY KEY (`trade_date`,`board_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='深交所市场总貌数据表';
/*!40101 SET character_set_client = @saved_cs_client */;

--
-- Table structure for table `ths_industry_daily_t`
--

DROP TABLE IF EXISTS `ths_industry_daily_t`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `ths_industry_daily_t` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `trade_date` varchar(8) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '交易日期',
  `ts_code` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT '板块代码',
  `name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '板块名称',
  `lead_stock` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT '领涨股票名称',
  `close_price` decimal(10,2) DEFAULT NULL COMMENT '最新价',
  `pct_change` decimal(10,2) DEFAULT NULL COMMENT '行业涨跌幅',
  `industry_index` decimal(12,2) DEFAULT NULL COMMENT '行业指数',
  `company_num` int DEFAULT NULL COMMENT '公司数量',
  `pct_change_stock` decimal(10,2) DEFAULT NULL COMMENT '领涨股涨跌幅',
  `net_buy_amount` decimal(20,2) DEFAULT NULL COMMENT '流入资金(元)',
  `net_sell_amount` decimal(20,2) DEFAULT NULL COMMENT '流出资金(元)',
  `net_amount` decimal(20,2) DEFAULT NULL COMMENT '净额(元)',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ts_date` (`ts_code`,`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=35564 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='同花顺行业板块日线数据表';
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

-- Dump completed on 2026-09-14 23:13:44
