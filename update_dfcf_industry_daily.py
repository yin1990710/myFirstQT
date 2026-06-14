import tushare as ts
import pandas as pd
import time

# 1. 初始化Tushare（替换为你的专属Token）
TOKEN = "228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3"
pro = ts.pro_api(TOKEN)

# 2. 配置批量查询参数
# 你需要查询的东方财富BK代码列表
BK_CODE_LIST = ["BK0420", "BK0421", "BK0422", "BK0428"]
# 板块名称映射（可选，用于文件名更清晰）
BK_NAME_MAP = {
    "BK0420": "航空机场",
    "BK0421": "铁路公路",
    "BK0422": "物流",
    "BK0428": "电力"
}
# 查询时间范围
START_DATE = "20260611"
END_DATE = "20260612"
# 复权方式
ADJ_TYPE = 1
# 免费版需加延时，避免触发频次限制（单位：秒）
DELAY = 0.5

# 3. 循环拉取所有板块数据
total_df = pd.DataFrame()  # 总表，合并所有板块数据
for bk_code in BK_CODE_LIST:
    # 转换为Tushare代码
    ts_code = bk_code.replace("BK", "DF")
    # 获取板块名称
    bk_name = BK_NAME_MAP.get(bk_code, bk_code)
    
    print(f"正在拉取：{bk_code} {bk_name}...")
    # 调用接口
    df_daily = pro.index_daily(
        ts_code=ts_code,
        start_date=START_DATE,
        end_date=END_DATE,
        adj=ADJ_TYPE
    )
    
    # 数据清洗
    df_daily['东方财富BK代码'] = bk_code
    df_daily['板块名称'] = bk_name
    df_daily = df_daily[[
        '东方财富BK代码', '板块名称', 'trade_date', 'open', 'high', 'low', 'close',
        'pct_chg', 'vol', 'amount'
    ]]
    df_daily.columns = [
        '东方财富BK代码', '板块名称', '交易日期', '开盘价', '最高价', '最低价', '收盘价',
        '涨跌幅(%)', '成交量(手)', '成交额(元)'
    ]
    df_daily = df_daily.sort_values('交易日期', ascending=True).reset_index(drop=True)
    
    # 合并到总表
    total_df = pd.concat([total_df, df_daily], ignore_index=True)
    # 单独保存每个板块的文件
    df_daily.to_excel(f'{bk_code}_{bk_name}_日线行情.xlsx', index=False)
    
    # 加延时，避免触发Tushare频次限制
    time.sleep(DELAY)

# 4. 保存总表
print("所有板块数据拉取完成！")
print(f"总数据量：{len(total_df)} 行")
total_df.to_excel('多板块行情总表.xlsx', index=False)
print("总表已保存为：多板块行情总表.xlsx")