import requests

def run():
    print("Downloading Scrip Master...")
    response = requests.get("https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json")
    data = response.json()

    nifty_spot = [x for x in data if x['name'] == 'NIFTY' and x['exch_seg'] == 'NSE']
    print(f"NIFTY Spot: {nifty_spot[:5]}")

    nifty_opt = [x for x in data if x['name'] == 'NIFTY' and x['instrumenttype'] == 'OPTIDX' and x['exch_seg'] == 'NFO']
    print(f"NIFTY Options count: {len(nifty_opt)}")
    if nifty_opt:
        print(f"Sample NIFTY Option: {nifty_opt[0]}")

run()
