"""
-- Dependencies to be installed --

pip install openpyxl
pip install pandas
pip install lxml beautifulsoup4
"""

import os
import time
import pandas as pd
import logging

def configure_logging():
    # Configure Application Logging
    log_filepath = "ToolsLog.log"
    format = "%(asctime)s: - %(levelname)s - %(message)s"
    logging.basicConfig(
            filename=log_filepath,
            format=format,
            level=logging.INFO,
            datefmt="%Y-%m-%d %H:%M:%S")

def fetch_screener_data(link):
    cache_index = None
    data = pd.DataFrame()
    current_page = 1
    page_limit = 25
    while current_page < page_limit:

        if current_page == 1:
            url=link
        else:
            url = f'{link}?page={current_page}'

        all_tables = pd.read_html(url, flavor='bs4')
        combined_df = pd.concat(all_tables)

        combined_df = combined_df.drop(
            combined_df[combined_df['S.No.'].isnull()].index)

        # print(combined_df)
        # if cache_index == combined_df.iloc[-2]['S.No.']:
        if len(combined_df.index) < 26:
            data = pd.concat([data, combined_df], axis=0)
            break
        # cache_index = combined_df.iloc[-2]['S.No.']
        # print(cache_index)
        data = pd.concat([data, combined_df], axis=0)
        current_page += 1
        time.sleep(1)
    data = data.iloc[0:].drop(data[data['S.No.'] == 'S.No.'].index)

    return data

pd.set_option("display.max_rows", None, "display.max_columns", None)

configure_logging()

logging.info("Tools : GARP extract commenced")
# Fetch PE/PB/Dividend Yield Value Ratio
pbv_link = 'https://www.screener.in/screens/2112737/trendvalue_pricebookvalue/'
pbv_df = fetch_screener_data(pbv_link)
pbv_df = pbv_df[['Name','P/E', 'Div Yld  %', 'CMP / BV']]
pbv_df['P/E'] = pd.to_numeric(pbv_df['P/E'], errors='coerce')
pbv_df['Div Yld  %'] = pd.to_numeric(pbv_df['Div Yld  %'], errors='coerce')
pbv_df['CMP / BV'] = pd.to_numeric(pbv_df['CMP / BV'], errors='coerce')
pbv_df['P/E'] = pbv_df['P/E'].fillna(100000)
pbv_df['CMP / BV'] = pbv_df['CMP / BV'].fillna(100000)
pbv_df['Div Yld  %'] = pbv_df['Div Yld  %'].fillna(0)
# pbv_df.to_excel('D:/pbv.xlsx', index=False)
pbv_df.dropna(inplace=True)
pbv_df = pbv_df[pbv_df['P/E'] > 0]
pbv_df = pbv_df[pbv_df['Div Yld  %'] > 0]
merged_df = pbv_df

# Fetch Price to Free Cash Flow
cashflow_link = 'https://www.screener.in/screens/2112756/trendvalue_cashflow/'
cf_df = fetch_screener_data(cashflow_link)
cf_df = cf_df[['Name','CMP / OCF']]
cf_df['CMP / OCF'] = pd.to_numeric(cf_df['CMP / OCF'], errors='coerce')
cf_df['CMP / OCF'] = cf_df['CMP / OCF'].fillna(100000)
# cf_df.to_excel('D:/cashflow.xlsx', index=False)
cf_df.dropna(inplace=True)
cf_df = cf_df[cf_df['CMP / OCF'] > 0]
merged_df = pd.merge(merged_df, cf_df, on='Name', how='inner')
# print("Free Cash Flow data extraction complete")

# Fetch EV to EBITDA
ev_link = 'https://www.screener.in/screens/2112767/trendvalue_ev/'
ev_df = fetch_screener_data(ev_link)
ev_df = ev_df[['Name','EV / EBITDA']]
ev_df['EV / EBITDA'] = pd.to_numeric(ev_df['EV / EBITDA'], errors='coerce')
ev_df['EV / EBITDA'] = ev_df['EV / EBITDA'].fillna(100000)
# ev_df.to_excel('D:/ev.xlsx', index=False)
ev_df.dropna(inplace=True)
ev_df = ev_df[ev_df['EV / EBITDA'] > 0]
merged_df = pd.merge(merged_df, ev_df, on='Name', how='inner')
# print("EV to EBDITA data extraction complete")

# Fetch Price to Sales ratio
sales_link = 'https://www.screener.in/screens/2112772/trendvalue_pricesales/'
sales_df = fetch_screener_data(sales_link)
sales_df = sales_df[['Name','CMP / Sales']]
sales_df['CMP / Sales'] = pd.to_numeric(sales_df['CMP / Sales'], errors='coerce')
sales_df['CMP / Sales'] = sales_df['CMP / Sales'].fillna(100000)
# sales_df.to_excel('D:/sales.xlsx', index=False)
sales_df.dropna(inplace=True)
sales_df = sales_df[sales_df['CMP / Sales'] > 0]
# print("Price to Sales Ratio data extraction complete")

# Fetch Last 6 months return (Momentum)
momentum_link = 'https://www.screener.in/screens/2112742/trendvalue_momentum/'
mo_df = fetch_screener_data(momentum_link)
mo_df = mo_df[['Name','6mth return  %']]
mo_df['6mth return  %'] = pd.to_numeric(mo_df['6mth return  %'], errors='coerce')
mo_df['6mth return  %'] = mo_df['6mth return  %'].fillna(-100000)
# mo_df.to_excel('D:/mo.xlsx', index=False)
mo_df.dropna(inplace=True)
mo_df = mo_df[mo_df['6mth return  %'] > 0]
merged_df = pd.merge(merged_df, mo_df, on='Name', how='inner')
# print("Momentum / 6 month Returns data extraction complete")

# Final Merged dataset
merged_df = pd.merge(merged_df, sales_df, on='Name', how='inner')
merged_df = merged_df.rename(columns={
    'Name': 'Stock',
    'P/E': 'PE',
    'Div Yld  %': 'Div',
    'CMP / BV': 'BV',
    'CMP / OCF': 'Cashflow',
    'EV / EBITDA': 'EV',
    'CMP / Sales': 'Sales',
    '6mth return  %': '6mo Return'
})
merged_df['PE'] = merged_df['PE'].map(lambda x: float(x))
merged_df['Div'] = merged_df['Div'].map(lambda x: float(x))
merged_df['BV'] = merged_df['BV'].map(lambda x: float(x))
merged_df['6mo Return'] = merged_df['6mo Return'].map(lambda x: float(x))
# merged_df['3mo Return'] = merged_df['3mo Return'].map(lambda x: float(x))
merged_df['Cashflow'] = merged_df['Cashflow'].map(lambda x: float(x))
merged_df['EV'] = merged_df['EV'].map(lambda x: float(x))
merged_df['Sales'] = merged_df['Sales'].map(lambda x: float(x))

# Apply Decile for PE
merged_df['PE_Rank'] = merged_df['PE'].rank()
merged_df['PE_Decile'] = pd.qcut(merged_df['PE_Rank'], q=10, labels=False, duplicates='drop') + 1

# Apply Decile for Div
merged_df['Div_Rank'] = merged_df['Div'].rank(ascending=False)
merged_df['Div_Decile'] = pd.qcut(merged_df['Div_Rank'], q=10, labels=False, duplicates='drop') + 1

# Apply Decile for BV
merged_df['BV_Rank'] = merged_df['BV'].rank()
merged_df['BV_Decile'] = pd.qcut(merged_df['BV_Rank'], q=10, labels=False, duplicates='drop') + 1

# Apply Decile for Cashflow
merged_df['Cashflow_Rank'] = merged_df['Cashflow'].rank()
merged_df['Cashflow_Decile'] = pd.qcut(merged_df['Cashflow_Rank'], q=10, labels=False, duplicates='drop') + 1

# Apply Decile for EV
merged_df['EV_Rank'] = merged_df['EV'].rank()
merged_df['EV_Decile'] = pd.qcut(merged_df['EV_Rank'], q=10, labels=False, duplicates='drop') + 1

# Apply Decile for Sales
merged_df['Sales_Rank'] = merged_df['Sales'].rank()
merged_df['Sales_Decile'] = pd.qcut(merged_df['Sales_Rank'], q=10, labels=False, duplicates='drop') + 1

# Consolidated_Rank column
merged_df['Consolidated_Rank'] = merged_df['PE_Decile'] + merged_df['Div_Decile'] + merged_df['BV_Decile'] + merged_df['Cashflow_Decile'] + merged_df['EV_Decile'] + merged_df['Sales_Decile']

# Retain only stocks that has given a postive return in the last 6 months
merged_df = merged_df[merged_df['6mo Return'] > 0]

# Decile on consolidated rank
merged_df['Consolidated_Decile'] = pd.qcut(merged_df['Consolidated_Rank'], q=10, labels=False, duplicates='drop') + 1
merged_df = merged_df.sort_values(by=['Consolidated_Decile', '6mo Return'], ascending=[True, False])
os.makedirs('data', exist_ok=True)
merged_df.to_excel('data/merged.xlsx', index=False)

# Retain only rows in the first decile of Consolidated_Decile
df_final = merged_df[merged_df['Consolidated_Decile'] == 1]
# df_final = df_final.head(25)
df_final.to_excel('data/final.xlsx', index=False)
print("Final Dataset")
print(df_final)

logging.info("Tools : GARP extract completed")
