from dotenv import load_dotenv; load_dotenv()
from src.data_service import DataService
from src.pair_selector import PairSelector
import logging
logging.basicConfig(level=logging.INFO)

ds = DataService()
tokens = ds.get_available_tokens()
print("Warming up...")
ds.warmup(tokens)
ps = PairSelector(ds)
pairs = ps.run(tokens)
print("Pairs found:", len(pairs))
for p in pairs:
    print(p)
