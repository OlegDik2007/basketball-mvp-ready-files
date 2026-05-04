import time
from collector import collect_odds
from predictor import run_predictions

while True:
    try:
        print("Collecting odds...")
        collect_odds()

        print("Running predictions...")
        run_predictions()

        print("Sleeping 300 sec...")
        time.sleep(300)
    except Exception as e:
        print("Error:", e)
        time.sleep(60)
