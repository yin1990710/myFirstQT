# 导入tushare
import tushare as ts

"""从 tushare.pro网站调取日线级别的交易数据"""

# 初始化pro接口
pro = ts.pro_api('228556619d635e28811329f4ecf6c70ae9ab57cc7a4e4d9b3b540ff3')

def get_stock_code_by_name(stock_name):
    """
    通过股票名称查询股票代码

    :param stock_name: 股票名称，如 '微光股份'、'中瓷电子'
    :return: 股票代码列表，如 ['002801.SZ']，未找到返回空列表
    """
    try:
        # 查询所有在交易的股票基本信息
        df = pro.stock_basic(list_status='L', fields=['ts_code', 'name', 'area', 'industry'])
        
        # 精确匹配股票名称
        result = df[df['name'] == stock_name]
        
        if result.empty:
            # 模糊匹配
            result = df[df['name'].str.contains(stock_name, na=False)]
        
        if result.empty:
            print(f"⚠️ 未找到名为 '{stock_name}' 的股票")
            return []
        
        # 返回股票代码列表
        codes = result['ts_code'].tolist()
        print(f"✅ 找到股票 '{stock_name}' 的代码: {codes}")
        return codes
    
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return []

def get_stock_info(stock_name_or_code):
    """
    通过股票名称或代码查询股票信息

    :param stock_name_or_code: 股票名称或代码
    :return: 包含股票信息的DataFrame
    """
    try:
        # 判断是名称还是代码
        if '.' not in stock_name_or_code and not stock_name_or_code.isdigit():
            # 按名称查询
            codes = get_stock_code_by_name(stock_name_or_code)
            if not codes:
                return None
            stock_code = codes[0]
        else:
            stock_code = stock_name_or_code
            if not stock_code.endswith('.SZ') and not stock_code.endswith('.SH'):
                # 补充交易所后缀
                stock_code = stock_code + '.SZ'
        
        # 查询股票基本信息
        df = pro.stock_basic(ts_code=stock_code, fields=['ts_code', 'name', 'area', 'industry', 'list_date'])
        
        if not df.empty:
            print(f"\n📋 股票信息:")
            print(df.to_string(index=False))
        
        return df
    
    except Exception as e:
        print(f"❌ 查询失败: {e}")
        return None

def get_stock_daily(ts_code, start_date=None, end_date=None, limit=500, offset=0):
    """
    获取股票日线数据

    :param ts_code: 股票代码，格式如 '003031.SZ'
    :param start_date: 开始日期，格式 'YYYYMMDD'
    :param end_date: 结束日期，格式 'YYYYMMDD'
    :param limit: 返回数量限制
    :param offset: 偏移量
    :return: 包含日线数据的DataFrame
    """
    # 打印调用参数
    print(f"      📡 Tushare API 调用参数:")
    print(f"         ts_code={ts_code}, start_date={start_date}, end_date={end_date}, limit={limit}, offset={offset}")

    # 拉取数据
    df = pro.daily(**{
        "ts_code": ts_code,
        "trade_date": "",
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "offset": offset
    }, fields=[
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount"
    ])
    
    return df

def main():
    """
    主函数：获取股票日线数据并打印
    """
    # 示例：获取中瓷电子(003031.SZ) 2026-05-06 至 2026-05-10 的数据
    ts_code = '003031.SZ'
    start_date = '20260506'
    end_date = '20260510'
    
    print(f"📈 正在获取 {ts_code} 的日线数据 ({start_date} ~ {end_date})")
    
    # 调用pro.daily获取数据
    df = get_stock_daily(ts_code, start_date, end_date)
    
    # 打印数据
    if not df.empty:
        print("\n📋 数据预览:")
        print(df.to_string(index=False))
    else:
        print(f"⚠️ 未获取到 {ts_code} 的数据")

if __name__ == "__main__":
    main()