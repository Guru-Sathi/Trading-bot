import requests

def run():
    print("Downloading Scrip Master...")
    response = requests.get("https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json")
    data = response.json()

    bse_stocks = [x for x in data if x['exch_seg'] == 'BSE']
    print(f"BSE Stocks count: {len(bse_stocks)}")
    if bse_stocks:
        print(f"Sample BSE Stock: {bse_stocks[0]}")

    # Check if a popular stock exists in both
    reliance_nse = [x for x in data if x['name'] == 'RELIANCE' and x['exch_seg'] == 'NSE' and x['symbol'].endswith('-EQ')]
    reliance_bse = [x for x in data if x['name'] == 'RELIANCE' and x['exch_seg'] == 'BSE']
    print(f"RELIANCE NSE: {reliance_nse}")
    print(f"RELIANCE BSE: {reliance_bse}")

run()
