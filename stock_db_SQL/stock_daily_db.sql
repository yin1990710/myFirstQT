/*
 Navicat MySQL Dump SQL

 Source Server         : qt
 Source Server Type    : MySQL
 Source Server Version : 90700 (9.7.0)
 Source Host           : localhost:3306
 Source Schema         : stock_daily_db

 Target Server Type    : MySQL
 Target Server Version : 90700 (9.7.0)
 File Encoding         : 65001

 Date: 06/06/2026 18:04:18
*/

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ----------------------------
-- Table structure for index_daily_t
-- ----------------------------
DROP TABLE IF EXISTS `index_daily_t`;
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
) ENGINE=InnoDB AUTO_INCREMENT=9508 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指数日线数据表';

-- ----------------------------
-- Table structure for rzrq_ye_t
-- ----------------------------
DROP TABLE IF EXISTS `rzrq_ye_t`;
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

-- ----------------------------
-- Table structure for stock_daily_t
-- ----------------------------
DROP TABLE IF EXISTS `stock_daily_t`;
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
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_ts_date` (`ts_code`,`trade_date`),
  KEY `idx_ts_code` (`ts_code`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=1871482 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票日数据表';

-- ----------------------------
-- Table structure for stock_index_daily_t
-- ----------------------------
DROP TABLE IF EXISTS `stock_index_daily_t`;
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

-- ----------------------------
-- Table structure for stock_index_future_daily_t
-- ----------------------------
DROP TABLE IF EXISTS `stock_index_future_daily_t`;
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

-- ----------------------------
-- Table structure for stock_info_t
-- ----------------------------
DROP TABLE IF EXISTS `stock_info_t`;
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

-- ----------------------------
-- Table structure for ths_industry_daily_t
-- ----------------------------
DROP TABLE IF EXISTS `ths_industry_daily_t`;
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
) ENGINE=InnoDB AUTO_INCREMENT=21853 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='同花顺行业板块日线数据表';

SET FOREIGN_KEY_CHECKS = 1;
