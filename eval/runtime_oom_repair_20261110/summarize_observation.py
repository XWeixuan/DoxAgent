"""Summarize bounded release observations; input contains no credentials."""
import json
import sys
from pathlib import Path

def psi(text, field="full"):
    line=next(s for s in text.splitlines() if s.startswith(field+" "))
    return {k:float(v) for k,v in (x.split("=") for x in line.split()[1:])}

def events(text):
    return {k:int(v) for k,v in (x.split() for x in text.splitlines())}

def summarize(path):
    all_rows=[json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines()]
    rows=[x for x in all_rows if "mem_kib" in x]
    a,b=rows[0],rows[-1]
    duration=b["elapsed_s"]-a["elapsed_s"]
    swap=lambda x:(x["mem_kib"]["SwapTotal"]-x["mem_kib"]["SwapFree"])/1024
    result=dict(start=a["at"],end=b["at"],duration_s=duration,samples=len(rows),
                sample_errors=len(all_rows)-len(rows),
                available_gib=dict(start=a["mem_kib"]["MemAvailable"]/1024**2,
                    end=b["mem_kib"]["MemAvailable"]/1024**2,
                    minimum=min(x["mem_kib"]["MemAvailable"] for x in rows)/1024**2),
                swap_mib=dict(start=swap(a),end=swap(b),peak=max(map(swap,rows))),containers={})
    for role in a["containers"]:
        samples=[x["containers"][role] for x in rows]
        first,last=samples[0],samples[-1]
        result["containers"][role]=dict(
            max_memory_gib=max(int(x.get("memory.current",0)) for x in samples)/1024**3,
            end_memory_gib=int(last.get("memory.current",0))/1024**3,
            max_pids=max(int(x.get("pids.current",0)) for x in samples),
            end_pids=int(last.get("pids.current",0)),
            restart_delta=last["restarts"]-first["restarts"],
            oom_kill_delta=events(last.get("memory.events","oom_kill 0"))["oom_kill"]-events(first.get("memory.events","oom_kill 0"))["oom_kill"],
            end_status=last["status"],
            average_cpu_cores=(events(last.get("cpu.stat","usage_usec 0"))["usage_usec"]-events(first.get("cpu.stat","usage_usec 0"))["usage_usec"])/duration/1000000)
    for kind in ["io","memory"]:
        result[kind+"_psi_full"]=dict(
            max_avg10=max(psi(x["psi"][kind])["avg10"] for x in rows),
            max_avg60=max(psi(x["psi"][kind])["avg60"] for x in rows),
            window_stall_percent=(psi(b["psi"][kind])["total"]-psi(a["psi"][kind])["total"])/duration/10000)
    da,db=a["disk"][0],b["disk"][0]
    result["disk"]=dict(read_mib_per_s=(int(db[5])-int(da[5]))*512/1024**2/duration,
                        write_mib_per_s=(int(db[9])-int(da[9]))*512/1024**2/duration)
    result["active_jobs"]=dict(start=a.get("jobs"),end=b.get("jobs"))
    return result

if __name__=="__main__":
    print(json.dumps(summarize(sys.argv[1]),ensure_ascii=False,indent=2))
