# Ceph Node 資源異常與自動重啟監控 SOP

## 1. 目的

本 SOP 用於排查 Ceph Node 發生以下現象：

- Node Memory 使用率逐漸升高
- `kubectl top node` 顯示 Memory 接近或超過 100%
- Ceph OSD / MGR / MON 服務開始異常
- Ceph Container 自動 Restart
- Restart 後服務恢復正常
- Restart 後 Node Memory 使用量明顯下降

目前實際觀察到的節點：

```text
utcsyk8scepht03
```

曾出現：

```text
MEMORY(bytes)   MEMORY(%)
26383Mi         110%
```

並且同時間 Ceph OSD / MGR 發生 Restart。

---

# 2. 已確認的現象

## 2.1 Kubernetes Node 狀態

確認：

```bash
kubectl get node utcsyk8scepht03 \
  -o jsonpath='{range .status.conditions[*]}{.type}{"\t"}{.status}{"\t"}{.reason}{"\t"}{.message}{"\n"}{end}'
```

正常狀態：

```text
NetworkUnavailable  False
MemoryPressure      False
DiskPressure        False
PIDPressure         False
Ready               True
```

目前沒有：

- MemoryPressure
- DiskPressure
- PIDPressure

---

## 2.2 沒有發現 OOM

在 Ceph Node：

```bash
sudo journalctl -k --since "1 hours ago" \
| grep -Ei 'oom|out of memory|killed process|memory'
```

目前沒有發現：

```text
OOMKilled
Out of memory
Killed process
```

因此目前不屬於典型 Linux OOM 問題。

---

# 3. Ceph Restart 原因

查詢 Ceph Pod Restart 狀態：

```bash
kubectl get pod -n rook-ceph \
  -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,RESTART:.status.containerStatuses[*].restartCount,LAST_REASON:.status.containerStatuses[*].lastState.terminated.reason'
```

曾觀察到：

```text
rook-ceph-mgr-b      Restart 37
rook-ceph-osd-11     Restart 34
rook-ceph-mon-c      Restart 26
rook-ceph-osd-9      Restart 24
```

其中：

```text
rook-ceph-osd-11
Reason=Completed
Exit=0
```

代表：

```text
不是 Ceph Crash
不是 OOMKilled
而是 Container 被正常停止
```

---

# 4. Liveness Probe 設定

## OSD

查詢：

```bash
kubectl get pod \
rook-ceph-osd-11-64cc869cd5-p72j7 \
-n rook-ceph \
-o jsonpath='{.spec.containers[?(@.name=="osd")].livenessProbe}'
echo
```

結果：

```json
{
  "failureThreshold": 3,
  "initialDelaySeconds": 10,
  "periodSeconds": 10,
  "successThreshold": 1,
  "timeoutSeconds": 5
}
```

---

## MGR

查詢：

```bash
kubectl get pod \
rook-ceph-mgr-b-655c5dbb5c-d77lr \
-n rook-ceph \
-o jsonpath='{.spec.containers[?(@.name=="mgr")].livenessProbe}'
echo
```

設定同樣為：

```text
每 10 秒檢查一次
每次最多等待 5 秒
連續失敗 3 次
```

也就是：

```text
Health Check
    ↓
超過 5 秒
    ↓
Probe Failed
    ↓
10 秒後再檢查
    ↓
連續失敗 3 次
    ↓
Kubelet Restart Container
```

---

# 5. 已確認的 Restart 時間線

Kubelet 曾出現：

```text
ExecSync cmd from runtime service failed

timeout 5s exceeded

context deadline exceeded
```

例如：

```text
09:14:56 MGR B timeout
09:14:56 OSD 11 timeout
09:14:57 Calico timeout

09:15:01 MGR B timeout
09:15:01 OSD 11 timeout

09:15:06 MGR B timeout

09:15:11 OSD 11 timeout
```

接著 Containerd：

```text
StopContainer
```

最後：

```text
OSD / MGR Restart
```

目前判斷流程：

```text
Node 發生短暫資源 / 執行延遲
            ↓
Containerd ExecSync 超過 5 秒
            ↓
Ceph Liveness Probe Failed
            ↓
連續失敗 3 次
            ↓
Kubelet 要求 Containerd StopContainer
            ↓
Ceph 正常 Shutdown
            ↓
Exit Code = 0
            ↓
Container Restart
            ↓
服務恢復
```

---

# 6. 目前尚未確認的根因

目前可以確認：

```text
Ceph Restart 是因為 Liveness Probe Timeout
```

但尚未確認：

```text
為什麼 utcsyk8scepht03 會突然變慢？
```

可能方向包含：

- Ceph OSD Memory 異常成長
- Ceph MGR Memory 異常成長
- Kubelet Memory 異常
- Containerd 延遲
- Linux Kernel Memory
- Page Cache
- Slab Memory
- I/O Wait
- Disk latency
- CPU / Load 過高
- Host 上其他 Process 佔用資源

因此建立以下自動監控。

---

# 7. 建立監控目錄

> 以下操作皆在 `utcsyk8scepht03` 執行。

建立：

```bash
mkdir -p /home/kubeadmin/ceph-monitor/logs
```

進入：

```bash
cd /home/kubeadmin/ceph-monitor
```

目錄結構：

```text
/home/kubeadmin/ceph-monitor/
├── ceph-node-monitor.sh
├── monitor.pid
├── nohup.out
└── logs/
    ├── ceph-monitor-20260916.log
    └── ceph-monitor-20260917.log
```

---

# 8. 建立監控腳本

建立：

```bash
vi /home/kubeadmin/ceph-monitor/ceph-node-monitor.sh
```

內容：

```bash
#!/bin/bash

# =========================================================
# 可修改設定
# =========================================================

BASE_DIR="/home/kubeadmin/ceph-monitor"

LOG_DIR="${BASE_DIR}/logs"

# 每幾秒記錄一次
INTERVAL=30

# Log 保留時間
# 2880 分鐘 = 48 小時
RETENTION_MINUTES=2880


# =========================================================
# 初始化
# =========================================================

mkdir -p "${LOG_DIR}"


# =========================================================
# 持續監控
# =========================================================

while true; do

    DATE=$(date '+%Y%m%d')
    NOW=$(date '+%Y-%m-%d %H:%M:%S')

    LOG_FILE="${LOG_DIR}/ceph-monitor-${DATE}.log"


    {
        echo
        echo "======================================================================"
        echo " TIME : ${NOW}"
        echo "======================================================================"


        # =====================================================
        # Memory
        # =====================================================

        echo
        echo "[ MEMORY SUMMARY ]"

        free -m


        MEM_TOTAL=$(awk '/MemTotal/ {print $2}' /proc/meminfo)

        MEM_AVAILABLE=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)

        MEM_USED=$((MEM_TOTAL - MEM_AVAILABLE))


        awk \
        -v used="${MEM_USED}" \
        -v total="${MEM_TOTAL}" \
        'BEGIN {
            printf "Calculated Used Memory : %.2f %%\n",
            (used / total) * 100
        }'


        # =====================================================
        # Kernel Memory
        # =====================================================

        echo
        echo "[ KERNEL MEMORY DETAIL ]"

        grep -E \
        'MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapCached|Active|Inactive|Slab|SReclaimable|SUnreclaim|PageTables|KernelStack' \
        /proc/meminfo


        # =====================================================
        # Top Memory Process
        # =====================================================

        echo
        echo "[ TOP 20 MEMORY PROCESSES ]"

        printf "%-8s %-12s %8s %8s %12s %-25s\n" \
            "PID" \
            "USER" \
            "CPU%" \
            "MEM%" \
            "RSS(MB)" \
            "COMMAND"


        ps -eo pid,user,%cpu,%mem,rss,comm --sort=-rss \
        | awk '
        NR>1 && NR<=21 {

            printf "%-8s %-12s %8s %8s %12.1f %-25s\n",
            $1,
            $2,
            $3,
            $4,
            $5/1024,
            $6

        }'


        # =====================================================
        # Ceph Process
        # =====================================================

        echo
        echo "[ CEPH PROCESSES ]"

        ps -eo pid,user,%cpu,%mem,rss,etime,comm --sort=-rss \
        | grep -E 'ceph-osd|ceph-mgr|ceph-mon|ceph-mds|ceph-rgw' \
        | awk '
        {

            printf "%-8s %-10s CPU=%-6s MEM=%-6s RSS=%8.1f MB ETIME=%-12s %s\n",
            $1,
            $2,
            $3,
            $4,
            $5/1024,
            $6,
            $7

        }'


        # =====================================================
        # System Load
        # =====================================================

        echo
        echo "[ SYSTEM LOAD ]"

        uptime


        # =====================================================
        # VMSTAT
        # =====================================================

        echo
        echo "[ VMSTAT ]"

        vmstat 1 3


        # =====================================================
        # CPU
        # =====================================================

        echo
        echo "[ CPU SNAPSHOT ]"

        top -bn1 \
        | grep -E '^%Cpu|^Cpu' \
        | head -1


        # =====================================================
        # Disk Usage
        # =====================================================

        echo
        echo "[ DISK USAGE ]"

        df -h /


        # =====================================================
        # Disk I/O
        # =====================================================

        echo
        echo "[ BLOCK DEVICE I/O ]"

        if command -v iostat >/dev/null 2>&1; then

            iostat -xz 1 2

        else

            echo "iostat not installed"

        fi


        # =====================================================
        # PSI Memory
        # =====================================================

        echo
        echo "[ PSI - MEMORY ]"

        if [ -f /proc/pressure/memory ]; then

            cat /proc/pressure/memory

        else

            echo "PSI memory not supported"

        fi


        # =====================================================
        # PSI CPU
        # =====================================================

        echo
        echo "[ PSI - CPU ]"

        if [ -f /proc/pressure/cpu ]; then

            cat /proc/pressure/cpu

        else

            echo "PSI cpu not supported"

        fi


        # =====================================================
        # PSI I/O
        # =====================================================

        echo
        echo "[ PSI - IO ]"

        if [ -f /proc/pressure/io ]; then

            cat /proc/pressure/io

        else

            echo "PSI io not supported"

        fi


        # =====================================================
        # Kubelet Error
        # =====================================================

        echo
        echo "[ KUBELET ERRORS - LAST 2 MINUTES ]"

        journalctl \
            -u kubelet \
            --since "2 minutes ago" \
            --no-pager \
        | grep -Ei \
        'ExecSync|probe|unhealthy|deadline|timeout|kill|killing|error|failed' \
        | tail -30


        # =====================================================
        # Containerd Error
        # =====================================================

        echo
        echo "[ CONTAINERD ERRORS - LAST 2 MINUTES ]"

        journalctl \
            -u containerd \
            --since "2 minutes ago" \
            --no-pager \
        | grep -Ei \
        'ExecSync|deadline|timeout|error|failed|kill|StopContainer|shim|ttrpc' \
        | tail -30


        # =====================================================
        # Kernel Warning
        # =====================================================

        echo
        echo "[ KERNEL WARNINGS - LAST 2 MINUTES ]"

        journalctl \
            -k \
            --since "2 minutes ago" \
            --no-pager \
        | grep -Ei \
        'oom|out of memory|killed process|blocked|hung|stall|timeout|reset|error|fail|nvme|scsi|I/O|watchdog|lockup' \
        | tail -30


        echo
        echo "======================================================================"


    } >> "${LOG_FILE}" 2>&1


    # =========================================================
    # 清除超過 48 小時的 Log
    # =========================================================

    find "${LOG_DIR}" \
        -type f \
        -name 'ceph-monitor-*.log' \
        -mmin +"${RETENTION_MINUTES}" \
        -delete


    # =========================================================
    # 等待下一次檢查
    # =========================================================

    sleep "${INTERVAL}"

done
```

---

# 9. 給予執行權限

```bash
chmod +x /home/kubeadmin/ceph-monitor/ceph-node-monitor.sh
```

確認：

```bash
ls -l /home/kubeadmin/ceph-monitor/ceph-node-monitor.sh
```

---

# 10. 背景啟動監控

進入：

```bash
cd /home/kubeadmin/ceph-monitor
```

啟動：

```bash
nohup ./ceph-node-monitor.sh \
  >nohup.out 2>&1 &

echo $! > monitor.pid
```

---

# 11. 確認監控是否正常執行

查看 PID：

```bash
cat /home/kubeadmin/ceph-monitor/monitor.pid
```

確認 Process：

```bash
ps -fp $(cat /home/kubeadmin/ceph-monitor/monitor.pid)
```

如果看到：

```text
/bin/bash ./ceph-node-monitor.sh
```

代表正常。

---

# 12. 查看 Log

查看目前有哪些 Log：

```bash
ls -lh /home/kubeadmin/ceph-monitor/logs/
```

今天的 Log：

```bash
ls -lh \
/home/kubeadmin/ceph-monitor/logs/ceph-monitor-$(date +%Y%m%d).log
```

查看最後 200 行：

```bash
tail -200 \
/home/kubeadmin/ceph-monitor/logs/ceph-monitor-$(date +%Y%m%d).log
```

即時查看：

```bash
tail -f \
/home/kubeadmin/ceph-monitor/logs/ceph-monitor-$(date +%Y%m%d).log
```

離開：

```text
Ctrl + C
```

只會停止 `tail -f`。

不會停止背景監控。

---

# 13. 停止背景監控

執行：

```bash
kill $(cat /home/kubeadmin/ceph-monitor/monitor.pid)
```

確認：

```bash
ps -fp $(cat /home/kubeadmin/ceph-monitor/monitor.pid)
```

沒有 Process 代表已停止。

刪除 PID File：

```bash
rm -f /home/kubeadmin/ceph-monitor/monitor.pid
```

---

# 14. 如果無法正常停止

先查 PID：

```bash
cat /home/kubeadmin/ceph-monitor/monitor.pid
```

強制停止：

```bash
kill -9 $(cat /home/kubeadmin/ceph-monitor/monitor.pid)
```

再刪除：

```bash
rm -f /home/kubeadmin/ceph-monitor/monitor.pid
```

> `kill -9` 僅建議在一般 `kill` 無法停止時使用。

---

# 15. 重新啟動監控

如果腳本有修改，建議：

## Step 1：停止舊監控

```bash
kill $(cat /home/kubeadmin/ceph-monitor/monitor.pid) 2>/dev/null
```

```bash
rm -f /home/kubeadmin/ceph-monitor/monitor.pid
```

---

## Step 2：重新啟動

```bash
cd /home/kubeadmin/ceph-monitor
```

```bash
nohup ./ceph-node-monitor.sh \
  >nohup.out 2>&1 &

echo $! > monitor.pid
```

---

## Step 3：確認

```bash
ps -fp $(cat monitor.pid)
```

---

# 16. Log 保存策略

目前設定：

```bash
RETENTION_MINUTES=2880
```

代表：

```text
2880 分鐘
=
48 小時
=
2 天
```

超過 48 小時的：

```text
ceph-monitor-*.log
```

會自動刪除。

---

# 17. 修改保存時間

例如改成保留 3 天：

```bash
RETENTION_MINUTES=4320
```

計算：

```text
60 × 24 × 3
=
4320
```

---

## 保留 7 天

```bash
RETENTION_MINUTES=10080
```

---

# 18. 修改監控頻率

目前：

```bash
INTERVAL=30
```

代表：

```text
每 30 秒記錄一次
```

如果希望每 1 分鐘：

```bash
INTERVAL=60
```

目前因為問題可能在短時間內發生，建議暫時維持：

```bash
INTERVAL=30
```

---

# 19. 異常發生後怎麼查

假設發現：

```bash
kubectl top node
```

看到：

```text
utcsyk8scepht03
Memory 100%+
```

或：

```bash
kubectl get pod -n rook-ceph
```

看到：

```text
OSD / MGR RESTARTS 增加
```

先不要手動重啟 Ceph。

---

## Step 1：確認 Restart 時間

```bash
kubectl get pod -n rook-ceph -o wide
```

或：

```bash
kubectl get pod -n rook-ceph \
  -o custom-columns='NAME:.metadata.name,NODE:.spec.nodeName,RESTART:.status.containerStatuses[*].restartCount,LAST_REASON:.status.containerStatuses[*].lastState.terminated.reason'
```

---

## Step 2：查看當天監控 Log

```bash
tail -500 \
/home/kubeadmin/ceph-monitor/logs/ceph-monitor-$(date +%Y%m%d).log
```

---

## Step 3：依時間搜尋

假設問題發生在：

```text
09:15
```

可執行：

```bash
grep -A80 -B20 \
"09:15" \
/home/kubeadmin/ceph-monitor/logs/ceph-monitor-$(date +%Y%m%d).log
```

也可以查：

```bash
grep -n \
"09:15" \
/home/kubeadmin/ceph-monitor/logs/ceph-monitor-$(date +%Y%m%d).log
```

---

# 20. Log 重點判讀

## 20.1 MEMORY SUMMARY

例如：

```text
Mem:
total 32000
used 13000
available 17000
```

主要看：

```text
used
available
```

如果：

```text
available
```

一路下降，要繼續找是哪一類 Memory 增加。

---

# 21. TOP MEMORY PROCESS

主要觀察：

```text
ceph-osd
ceph-mgr
ceph-mon
kubelet
containerd
s1-agent
alloy
```

例如：

```text
正常：

ceph-osd
RSS 2200 MB

異常前：

RSS 4000 MB
RSS 7000 MB
RSS 10000 MB
```

這代表：

```text
該 Process Memory 持續異常增加
```

---

# 22. Kernel Memory 判讀

主要看：

```text
Cached
Slab
SReclaimable
SUnreclaim
```

如果：

```text
Node Memory 很高
```

但是所有 Process RSS 都沒明顯增加，

而：

```text
Cached
```

大量增加，

可能偏向：

```text
Page Cache
```

如果：

```text
Slab
SUnreclaim
```

大量增加，

則可能偏向：

```text
Kernel Memory
```

---

# 23. VMSTAT 判讀

輸出：

```text
r
b
si
so
bi
bo
in
cs
us
sy
id
wa
```

主要看：

```text
r
b
wa
```

---

## r

代表等待 CPU 的 Process。

如果長時間非常高：

```text
CPU / Load 壓力
```

---

## b

代表 blocked process。

如果持續很高：

```text
可能有 I/O Block
```

---

## wa

代表 I/O Wait。

例如：

```text
wa = 30
wa = 50
wa = 80
```

就需要特別注意 Disk / Storage latency。

---

# 24. PSI 判讀

腳本會記錄：

```text
PSI - MEMORY
PSI - CPU
PSI - IO
```

例如：

```text
some avg10=30.00
```

代表最近 10 秒：

```text
有明顯資源 Stall
```

如果：

```text
IO PSI
```

在出問題前突然很高，

可能偏向：

```text
Disk / Storage latency
```

如果：

```text
Memory PSI
```

變高，

則可能是：

```text
Memory Pressure / reclaim stall
```

---

# 25. IOSTAT 判讀

如果系統有：

```bash
iostat
```

會自動紀錄：

```text
await
%util
r/s
w/s
rkB/s
wkB/s
```

主要看：

```text
await
%util
```

---

## await

代表 I/O 平均等待時間。

如果突然：

```text
幾百 ms
幾千 ms
```

需要注意 Storage latency。

---

## %util

如果長時間：

```text
100%
```

代表該 Block Device 非常忙碌。

---

# 26. Kubelet Log 判讀

目前已經觀察到：

```text
ExecSync cmd from runtime service failed

rpc error:

DeadlineExceeded

timeout 5s exceeded

context deadline exceeded
```

如果監控 Log 再次大量出現：

```text
ExecSync
timeout
DeadlineExceeded
```

代表：

```text
Kubelet 執行 Container Health Check 發生延遲
```

---

# 27. Containerd Log 判讀

目前曾出現：

```text
ExecSync failed
```

接著：

```text
StopContainer
```

例如：

```text
ExecSync timeout
        ↓
StopContainer
        ↓
Ceph Restart
```

如果之後再次發生相同模式，

就可以確認：

```text
Liveness Probe Timeout
```

再次觸發 Restart。

---

# 28. Kernel Warning 判讀

主要檢查：

```text
OOM
Out of memory
Killed process
hung
blocked
stall
timeout
NVMe
SCSI
I/O error
watchdog
lockup
```

如果沒有任何輸出：

```text
目前沒有看到明顯 Kernel / Disk Hardware Error
```

---

# 29. 問題判斷方式

## Case 1：Ceph Process Memory 一直增加

例如：

```text
OSD 11

2 GB
↓
4 GB
↓
8 GB
↓
12 GB
```

可能方向：

```text
Ceph OSD Memory
BlueStore Cache
RocksDB
OSD Memory Target
```

下一步可查：

```bash
ceph config get osd osd_memory_target
```

---

# 30. Case 2：Memory 增加，但 Process RSS 沒增加

例如：

```text
Node Used Memory：

13GB
↓
20GB
↓
26GB
```

但：

```text
OSD / MGR / kubelet
```

都沒變。

則檢查：

```text
Cached
Slab
SReclaimable
SUnreclaim
```

可能方向：

```text
Page Cache
Kernel Memory
Slab
Cgroup
```

---

# 31. Case 3：I/O Wait / PSI IO 很高

如果看到：

```text
vmstat wa 很高
```

以及：

```text
PSI IO 很高
```

或：

```text
iostat await 很高
```

可能方向：

```text
Disk I/O latency
Ceph OSD Device latency
Storage Device Busy
```

---

# 32. Case 4：CPU / Load 很高

如果：

```text
load average
```

突然非常高，

或：

```text
VMSTAT r
```

大量增加，

可能方向：

```text
CPU Saturation
Process Scheduling Delay
```

這也可能導致：

```text
ExecSync 超過 5 秒
```

---

# 33. Case 5：所有資源看起來正常，但 ExecSync Timeout

如果：

```text
Memory 正常
CPU 正常
Disk 正常
IO 正常
```

但：

```text
Ceph
Calico
```

同時 ExecSync timeout，

則需往：

```text
containerd
containerd-shim
kubelet
CRI Runtime
Host OS
```

方向繼續查。

---

# 34. 目前已知結論

目前已確認：

```text
Ceph Container 不是因為 OOMKilled
```

也不是：

```text
Ceph Process Crash
```

而是：

```text
Node 發生短暫延遲
        ↓
Containerd ExecSync Timeout
        ↓
Ceph Liveness Probe Failed
        ↓
FailureThreshold = 3
        ↓
Kubelet StopContainer
        ↓
Ceph 正常退出
        ↓
Exit Code 0
        ↓
Container Restart
        ↓
服務恢復
```

目前真正需要確認的是：

```text
造成 Node 短暫延遲的底層原因
```

因此使用本 SOP 的監控腳本，

持續紀錄：

```text
Memory
Kernel Memory
Process RSS
Ceph Process
CPU
Load
VMSTAT
Disk
I/O
PSI
Kubelet
Containerd
Kernel Error
```

等下一次異常發生後，

比對：

```text
異常發生前 5～10 分鐘
```

即可進一步定位真正原因。

---

# 35. Quick Start

## 建立目錄

```bash
mkdir -p /home/kubeadmin/ceph-monitor/logs
```

---

## 執行權限

```bash
chmod +x /home/kubeadmin/ceph-monitor/ceph-node-monitor.sh
```

---

## 啟動

```bash
cd /home/kubeadmin/ceph-monitor

nohup ./ceph-node-monitor.sh \
  >nohup.out 2>&1 &

echo $! > monitor.pid
```

---

## 確認

```bash
ps -fp $(cat monitor.pid)
```

---

## 看 Log

```bash
tail -200 \
/home/kubeadmin/ceph-monitor/logs/ceph-monitor-$(date +%Y%m%d).log
```

---

## 停止

```bash
kill $(cat /home/kubeadmin/ceph-monitor/monitor.pid)
```

```bash
rm -f /home/kubeadmin/ceph-monitor/monitor.pid
```

---

## 重啟

```bash
kill $(cat /home/kubeadmin/ceph-monitor/monitor.pid) 2>/dev/null

rm -f /home/kubeadmin/ceph-monitor/monitor.pid

cd /home/kubeadmin/ceph-monitor

nohup ./ceph-node-monitor.sh \
  >nohup.out 2>&1 &

echo $! > monitor.pid
```

---

# 36. 建議

目前暫時不建議直接修改：

```text
timeoutSeconds
failureThreshold
periodSeconds
```

原因是：

```text
Ceph 與 Calico 曾在同一時間發生 ExecSync Timeout
```

表示問題可能不是單純 Ceph Probe 太敏感，

而是：

```text
Node / Container Runtime
```

本身發生短暫延遲。

應先透過本監控腳本確認底層原因，

再決定是否需要調整 Ceph Liveness Probe。
