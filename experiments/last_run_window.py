"""Print epoch-ms start/end of the most recent live run (for dashboard screenshots)."""
import pandas as pd

from pipeline.common import influx

d = influx()
q = 'SELECT "round" FROM "server" WHERE time > now() - 1d ORDER BY time DESC LIMIT 2000'
df = pd.DataFrame(d.query(q).get_points())
df["time"] = pd.to_datetime(df.time)
last0 = df[df["round"] == 0].time.max()
end = df[df.time >= last0].time.max()
print(int(last0.timestamp() * 1000) - 15000, int(end.timestamp() * 1000) + 15000)
