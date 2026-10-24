import csv
from datetime import datetime,UTC
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
root=Path(__file__).parent
fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True,gridspec_kw={'height_ratios':[2,1]},layout='constrained')
for key,label,color in [('0','Unhedged','#203b63'),('0.66','66% relative-Beta hedge','#db8a20'),('1.0','100% relative-Beta hedge','#368476')]:
    rows=list(csv.DictReader((root/f'equity_{key}.csv').open(encoding='utf8')))
    times=[datetime.fromtimestamp(int(r['timestamp']),UTC) for r in rows];eq=np.array([float(r['equity']) for r in rows])
    axes[0].plot(times,(eq/100000-1)*100,label=label,color=color,lw=1.6,drawstyle='steps-post')
    axes[1].plot(times,(eq/np.maximum.accumulate(np.r_[100000,eq])[1:]-1)*100,color=color,lw=1.3,drawstyle='steps-post')
axes[0].axhline(0,color='#8b939b',lw=.7);axes[0].set_ylabel('Net account return (%)');axes[0].legend(loc='upper left',frameon=False,ncol=3)
axes[0].set_title('DoxAgent paired-trade replay | USD 10,000 stock leg per case; USD 100,000 starting equity',loc='left',fontsize=12,pad=16)
axes[1].set_ylabel('Drawdown (%)');axes[1].xaxis.set_major_formatter(mdates.DateFormatter('%m-%d',tz=UTC));axes[1].set_xlabel('2026 (UTC); minute marks with closed-market gaps carried forward')
for ax in axes:ax.grid(axis='y',color='#dfe4e8',lw=.6);ax.spines[['right','top']].set_visible(False)
fig.savefig(root/'equity.png',dpi=160)
fig.savefig(root/'equity.svg')
print(root/'equity.png')
