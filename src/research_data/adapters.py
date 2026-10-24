import csv
from io import StringIO
from datetime import datetime
from src.research_data.types import ResearchObservation
def parse_treasury_csv(text,dataset_id,url):
    r=csv.DictReader(StringIO(text)); out=[]; cols={'2 Yr':'2Y','5 Yr':'5Y','10 Yr':'10Y','30 Yr':'30Y'}
    if not r.fieldnames or 'Date' not in r.fieldnames: raise ValueError('Treasury CSV missing Date')
    for row in r:
        d=datetime.strptime(row['Date'].strip(),'%m/%d/%Y').date().isoformat()
        for col,sym in cols.items():
            if row.get(col) in (None,'','N/A'): continue
            try: v=float(row[col])
            except ValueError: continue
            out.append(ResearchObservation('treasury',dataset_id,'US_TREASURY',sym,d,None,d,v,'percent',url,'DELAYED','VALID',f'{dataset_id}:{d}:{sym}'))
    return out
def parse_cot_csv(text,url,dataset_id='cftc_cot_disaggregated'):
    r=csv.DictReader(StringIO(text)); out=[]
    if not r.fieldnames or not {'Market_and_Exchange_Names','Report_Date_as_YYYY-MM-DD','Open_Interest_All'}.issubset(r.fieldnames): raise ValueError('COT CSV missing required columns')
    pairs={'Dealer':('Dealer_Positions_Long_All','Dealer_Positions_Short_All'),'Asset Manager':('Asset_Mgr_Positions_Long_All','Asset_Mgr_Positions_Short_All'),'Leveraged Funds':('Lev_Money_Positions_Long_All','Lev_Money_Positions_Short_All')}
    for row in r:
        if not any(x in row['Market_and_Exchange_Names'].lower() for x in ('bitcoin','ether')): continue
        for cat,(lk,sk) in pairs.items():
            try: net=float(row[lk])-float(row[sk])
            except (ValueError,TypeError,KeyError): continue
            d=row['Report_Date_as_YYYY-MM-DD']; out.append(ResearchObservation('cftc',dataset_id,row['Market_and_Exchange_Names'],cat,d,None,d,net,'contracts',url,'DELAYED','VALID',f'{dataset_id}:{d}:{cat}'))
    return out
