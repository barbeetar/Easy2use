# Grafana Dashboard 跨 Cluster／跨廠區複製 SOP

## 1. 目的

本 SOP 用於將「複製端 Grafana」中的 Dashboard，透過 Grafana API 複製至「被複製端 Grafana」。

適用情境：

- 不同 Kubernetes Cluster
- 不同廠區
- Grafana 無法直接互相存取
- 需透過 K8s 跳板機操作 Grafana
- 可直接 SSH 至被複製端跳板機
- 或被複製端必須經過中間機器才能連線

---

## 2. 名詞定義

| 名稱 | 說明 |
|---|---|
| 複製端 | 原本存放 Dashboard 的 Grafana |
| 被複製端 | Dashboard 要匯入的 Grafana |
| 中間機器 | 跨廠區時，用來轉接 SSH Tunnel 的主機 |
| 複製端跳板機 | 執行 Python 程式及操作複製端 K8s 的主機 |
| 被複製端跳板機 | 可以操作被複製端 K8s Cluster 的主機 |

Grafana Service Account 權限：

| 位置 | Service Account 權限 |
|---|---|
| 複製端 Grafana | Viewer |
| 被複製端 Grafana | Editor |

---

## 3. 架構判斷

操作前先確認：

複製端跳板機是否可以直接 SSH 至被複製端 K8s 跳板機？

如果可以：

使用「情境 A：直接連線」

如果不可以，需要先進入另一台機器才能 SSH 至被複製端：

使用「情境 B：經過中間機器」

---

## 4. 情境 A：可以直接連被複製端跳板機

架構：

```text
【複製端跳板機】
Python
│
├── localhost:3000
│        │
│        └── kubectl port-forward
│                  ↓
│             複製端 Grafana
│
└── localhost:3001
         │
         └── SSH Tunnel
                  ↓
【被複製端 K8s 跳板機】
localhost:3001
         │
         └── kubectl port-forward
                  ↓
            被複製端 Grafana
```

---

## 5. 情境 B：需要經過中間機器

架構：

```text
【複製端跳板機】
Python
│
├── localhost:3000
│        │
│        └── kubectl port-forward
│                  ↓
│             複製端 Grafana
│
└── localhost:3001
         │
         └── SSH Tunnel
                  ↓
【中間可連線機器】
localhost:13001
         │
         └── SSH Tunnel
                  ↓
【被複製端 K8s 跳板機】
localhost:3001
         │
         └── kubectl port-forward
                  ↓
            被複製端 Grafana
```

---

## 6. 事前工具確認

複製端跳板機至少需要：

- kubectl
- python3
- curl
- ssh
- nc（非必要，但建議）

確認：

```bash
kubectl version --client
python3 --version
curl --version
ssh -V
```

確認是否有 nc：

```bash
which nc
```

---

## 7. Kubernetes 權限確認

### 7.1 複製端

在複製端跳板機：

```bash
kubectl get nodes
```

正常情況必須能取得 Node 資訊。

確認 Grafana Pod：

```bash
kubectl get pod -n monitoring | grep grafana
```

正常應看到：

```text
grafana-xxxxxxxxxx-xxxxx   1/1   Running
```

確認 Grafana Service：

```bash
kubectl get svc -n monitoring | grep grafana
```

例如：

```text
grafana   ClusterIP   10.x.x.x   <none>   80/TCP
```

---

### 7.2 被複製端

在被複製端 K8s 跳板機：

```bash
kubectl get nodes
```

確認 Grafana Pod：

```bash
kubectl get pod -n monitoring | grep grafana
```

確認 Grafana Service：

```bash
kubectl get svc -n monitoring | grep grafana
```

---

## 8. 防火牆需求

### 8.1 情境 A：直接連線

需要允許：

| Source | Destination | Protocol | Port | 用途 |
|---|---|---|---|---|
| 複製端跳板機 | 被複製端跳板機 | TCP | 22 | SSH / SSH Tunnel |

架構：

```text
複製端跳板機
      │
      │ TCP/22
      ▼
被複製端跳板機
```

---

### 8.2 情境 B：經過中間機器

需要允許：

| Source | Destination | Protocol | Port | 用途 |
|---|---|---|---|---|
| 複製端跳板機 | 中間機器 | TCP | 22 | SSH / SSH Tunnel |
| 中間機器 | 被複製端跳板機 | TCP | 22 | SSH / SSH Tunnel |

架構：

```text
複製端跳板機
      │
      │ TCP/22
      ▼
中間機器
      │
      │ TCP/22
      ▼
被複製端跳板機
```

---

## 9. Kubernetes API 防火牆

執行 kubectl 的跳板機必須可以連到該 Cluster 的 Kubernetes API Server。

常見為：

```text
TCP/6443
```

驗證方式：

```bash
kubectl get nodes
```

如果原本就能正常執行，不需要為本次 Dashboard 複製額外開放。

---

## 10. Local Port 不需要開 Firewall

本 SOP 使用：

```text
3000
3001
13001
```

這些 Port 都綁定：

```text
127.0.0.1
```

因此：

- 3000 不需要跨廠區 Firewall
- 3001 不需要跨廠區 Firewall
- 13001 不需要跨廠區 Firewall

跨主機真正需要的是：

```text
TCP/22
```

---

## 11. Firewall 事前驗證

### 11.1 使用 nc

測試目標 SSH Port：

```bash
nc -vz <目標IP> 22
```

正常應看到類似：

```text
Connection to <目標IP> 22 port [tcp/ssh] succeeded!
```

---

### 11.2 沒有 nc 時

可以使用：

```bash
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/<目標IP>/22'
echo $?
```

正常：

```text
0
```

代表 TCP/22 可達。

---

## 12. SSH 驗證

Firewall 通不代表帳號一定可以登入。

因此還需要：

```bash
ssh kubeadmin@<目標IP>
```

如果可以正常登入：

```text
SSH 正常
```

如果：

```text
Permission denied
```

通常檢查：

- Username
- Password
- SSH Private Key
- SSH Agent
- sshd 設定
- 公司 MobaXterm 是否使用不同認證方式

---

## 13. SSH Tunnel 功能驗證

如果一般 SSH 可以，但是 ssh -L 不行，需要確認 SSH Server 是否允許 TCP Forwarding。

Server 端常見設定：

```text
AllowTcpForwarding yes
```

測試時可以增加 Debug：

```bash
ssh -vvv \
  -N \
  -o ExitOnForwardFailure=yes \
  -L 3001:127.0.0.1:3001 \
  kubeadmin@<目標IP>
```

---

## 14. Grafana Service Account 建立

### 14.1 複製端 Grafana

建立 Service Account：

名稱建議：

```text
dashboard-export
```

Role：

```text
Viewer
```

用途：

讀取及匯出 Dashboard

取得：

```text
SOURCE_TOKEN
```

---

### 14.2 被複製端 Grafana

建立 Service Account：

名稱建議：

```text
dashboard-import
```

Role：

```text
Editor
```

用途：

建立 / 更新 Dashboard

取得：

```text
TARGET_TOKEN
```

---

## 15. Token 安全注意事項

禁止：

- 將 Token Commit 至 GitLab
- 將 Token 放在 SOP
- 將 Token 放在截圖
- 將 Token 貼在公開聊天室
- 將正式 Token 寫死在共用 Python 程式

建議使用 Shell Environment Variable：

```bash
export SOURCE_TOKEN='xxxxxxxxxxxxxxxx'
export TARGET_TOKEN='xxxxxxxxxxxxxxxx'
```

可以只確認 Token 前幾碼：

```bash
echo ${SOURCE_TOKEN:0:5}
echo ${TARGET_TOKEN:0:5}
```

避免直接：

```bash
echo $SOURCE_TOKEN
```

以免完整 Token 顯示在 Terminal。

---

## 16. 共通步驟：被複製端建立 Grafana Port Forward

不論情境 A 或 B，都先做這一步。

操作位置：

被複製端 K8s 跳板機

執行：

```bash
kubectl -n monitoring port-forward svc/grafana 3001:80
```

正常應看到：

```text
Forwarding from 127.0.0.1:3001 -> 3000
Forwarding from [::1]:3001 -> 3000
```

此 Terminal：

不可關閉

---

## 17. 被複製端 Grafana 驗證

在「被複製端 K8s 跳板機」另外開一個 Terminal。

確認 Port：

```bash
ss -lntp | grep 3001
```

應看到：

```text
127.0.0.1:3001
```

確認 Grafana：

```bash
curl -s http://127.0.0.1:3001/api/health
```

正常應包含：

```text
"database":"ok"
```

如果失敗，不要繼續 SSH Tunnel。

檢查：

```bash
kubectl get pod -n monitoring | grep grafana
kubectl get svc -n monitoring | grep grafana
kubectl get endpoints -n monitoring grafana
```

---

## 18. 情境 A：複製端可以直接連被複製端

以下只有「可以直接 SSH 被複製端」時需要執行。

---

### 18.1 在複製端測 TCP/22

操作位置：

複製端跳板機

執行：

```bash
nc -vz <被複製端跳板機IP> 22
```

---

### 18.2 在複製端測普通 SSH

```bash
ssh kubeadmin@<被複製端跳板機IP>
```

登入後，在被複製端測：

```bash
curl http://127.0.0.1:3001/api/health
```

必須：

```text
database = ok
```

離開：

```bash
exit
```

---

### 18.3 建立 SSH Tunnel

操作位置：

複製端跳板機

執行：

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -L 3001:127.0.0.1:3001 \
  kubeadmin@<被複製端跳板機IP>
```

此 Terminal 不要關閉。

---

### 18.4 在複製端驗證

另外開一個 Terminal：

```bash
ss -lntp | grep 3001
```

再測：

```bash
curl -s http://127.0.0.1:3001/api/health
```

必須看到：

```text
database = ok
```

此時路徑為：

```text
複製端 localhost:3001
        ↓
SSH Tunnel
        ↓
被複製端 localhost:3001
        ↓
kubectl port-forward
        ↓
被複製端 Grafana
```

完成後直接跳到「複製端 Grafana Port Forward」。

---

## 19. 情境 B：需要經過中間機器

如果複製端不能直接連被複製端，使用以下流程。

---

### 19.1 中間機器測被複製端 Firewall

操作位置：

中間可連線機器

執行：

```bash
nc -vz <被複製端跳板機IP> 22
```

---

### 19.2 中間機器測普通 SSH

```bash
ssh kubeadmin@<被複製端跳板機IP>
```

登入後：

```bash
curl http://127.0.0.1:3001/api/health
```

必須：

```text
database = ok
```

離開：

```bash
exit
```

---

## 20. 情境 B：建立第一段 SSH Tunnel

操作位置：

中間可連線機器

執行：

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -L 13001:127.0.0.1:3001 \
  kubeadmin@<被複製端跳板機IP>
```

此 Terminal 不要關閉。

意義：

```text
中間機器 localhost:13001
           ↓
SSH Tunnel
           ↓
被複製端 localhost:3001
```

---

## 21. 情境 B：驗證第一段 Tunnel

在中間機器另外開 Terminal：

```bash
ss -lntp | grep 13001
```

再：

```bash
curl -s http://127.0.0.1:13001/api/health
```

必須看到：

```text
database = ok
```

如果這一步失敗：

先不要進行第二段 SSH Tunnel

---

## 22. 情境 B：複製端測中間機器 Firewall

操作位置：

複製端跳板機

執行：

```bash
nc -vz <中間機器IP> 22
```

正常應顯示 TCP/22 成功。

---

## 23. 情境 B：複製端測中間機器 SSH

```bash
ssh kubeadmin@<中間機器IP>
```

成功登入後：

```bash
curl http://127.0.0.1:13001/api/health
```

必須：

```text
database = ok
```

離開：

```bash
exit
```

---

## 24. 情境 B：建立第二段 SSH Tunnel

操作位置：

複製端跳板機

執行：

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -L 3001:127.0.0.1:13001 \
  kubeadmin@<中間機器IP>
```

此 Terminal 不要關閉。

---

## 25. 情境 B：驗證完整 SSH Tunnel

在複製端另外開 Terminal：

```bash
ss -lntp | grep 3001
```

再：

```bash
curl -s http://127.0.0.1:3001/api/health
```

必須看到：

```text
database = ok
```

此時完整路徑：

```text
複製端 localhost:3001
        ↓
SSH Tunnel
        ↓
中間機器 localhost:13001
        ↓
SSH Tunnel
        ↓
被複製端 localhost:3001
        ↓
kubectl port-forward
        ↓
被複製端 Grafana
```

---

## 26. 複製端 Grafana Port Forward

接下來不論情境 A 或 B 都相同。

操作位置：

複製端跳板機

執行：

```bash
kubectl -n monitoring port-forward svc/grafana 3000:80
```

此 Terminal 不要關閉。

---

## 27. 驗證複製端 Grafana

在複製端另外開 Terminal：

```bash
ss -lntp | grep 3000
```

測試：

```bash
curl -s http://127.0.0.1:3000/api/health
```

必須：

```text
database = ok
```

---

## 28. 最終網路驗證

以下全部在：

複製端跳板機

執行。

複製端：

```bash
curl -s http://127.0.0.1:3000/api/health
```

必須：

```text
database = ok
```

被複製端：

```bash
curl -s http://127.0.0.1:3001/api/health
```

必須：

```text
database = ok
```

最終關係：

```text
http://127.0.0.1:3000
→ 複製端 Grafana

http://127.0.0.1:3001
→ 被複製端 Grafana
```

兩個都成功後，才進行 Token 驗證。

---

## 29. 複製端 Token 驗證

操作位置：

複製端跳板機

執行：

```bash
curl -s \
  -H "Authorization: Bearer ${SOURCE_TOKEN}" \
  http://127.0.0.1:3000/api/search
```

正常應看到 Dashboard，例如：

```json
[
  {
    "uid": "xxxxx",
    "title": "Node Exporter",
    "type": "dash-db"
  }
]
```

如果：

```json
[]
```

需要確認：

- 複製端是否真的有 Dashboard
- Service Account 是否為 Viewer
- Folder Permission 是否允許此 SA 查看

---

## 30. 被複製端 Token 驗證

仍然在：

複製端跳板機

執行：

```bash
curl -s \
  -H "Authorization: Bearer ${TARGET_TOKEN}" \
  http://127.0.0.1:3001/api/search
```

如果被複製端本來沒有 Dashboard：

```json
[]
```

屬於正常現象。

這代表：

- Grafana API 可以連線
- Token 可以正常認證
- 目前沒有 Dashboard

---

## 31. API 錯誤判斷

### 401 Unauthorized

如果出現：

```text
401 Unauthorized
```

檢查：

- Token 是否輸入錯誤
- Token 是否已失效
- Token 是否已刪除
- SOURCE_TOKEN / TARGET_TOKEN 是否弄反
- 是否使用錯誤 Grafana 的 Token

---

### 403 Forbidden

如果出現：

```text
403 Forbidden
```

檢查：

- Service Account Role
- Folder Permission
- Dashboard Permission
- 被複製端是否允許此 SA 建立 Dashboard

---

### Connection refused

如果：

```text
Connection refused
```

檢查：

```bash
ss -lntp | grep -E '3000|3001|13001'
```

通常代表：

- kubectl port-forward 已停止
- SSH Tunnel 已停止
- Terminal 被關閉
- Port 使用錯誤

---

### Connection timed out

如果：

```text
Connection timed out
```

優先檢查：

- Firewall
- Route
- TCP/22
- SSH Destination IP

---

## 32. Python 程式設定

Python 必須在：

複製端跳板機

執行。

程式內 URL：

```python
SOURCE_URL = "http://127.0.0.1:3000"
TARGET_URL = "http://127.0.0.1:3001"
```

Token：

```python
SOURCE_TOKEN = "複製端 Viewer Token"
TARGET_TOKEN = "被複製端 Editor Token"
```

如果 Python 程式支援環境變數，優先使用：

```bash
export SOURCE_TOKEN='xxxxxxxx'
export TARGET_TOKEN='xxxxxxxx'
```

---

## 33. 執行 Python 前最後確認

操作位置：

複製端跳板機

執行：

```bash
echo "===== SOURCE Grafana ====="
curl -s http://127.0.0.1:3000/api/health
echo
echo "===== TARGET Grafana ====="
curl -s http://127.0.0.1:3001/api/health
```

兩邊都必須：

```text
database = ok
```

再確認來源 Dashboard：

```bash
curl -s \
  -H "Authorization: Bearer ${SOURCE_TOKEN}" \
  http://127.0.0.1:3000/api/search
```

確認確實有要搬移的 Dashboard。

---

## 34. 執行 Dashboard 複製

操作位置：

複製端跳板機

執行：

```bash
python3 copy_grafana_dashboards.py
```

---

## 35. 複製完成後驗證

在複製端執行：

```bash
curl -s \
  -H "Authorization: Bearer ${TARGET_TOKEN}" \
  http://127.0.0.1:3001/api/search
```

如果原本：

```json
[]
```

複製成功後應開始看到：

```json
[
  {
    "uid": "xxxxx",
    "title": "xxxx",
    "type": "dash-db"
  }
]
```

最後登入被複製端 Grafana：

```text
Dashboards
→ Browse
```

確認 Dashboard 是否已建立。

---

## 36. 情境 A 快速操作版

### Step 1：被複製端

```bash
kubectl -n monitoring port-forward svc/grafana 3001:80
```

另外測：

```bash
curl http://127.0.0.1:3001/api/health
```

---

### Step 2：複製端

建立 Tunnel：

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -L 3001:127.0.0.1:3001 \
  kubeadmin@<被複製端跳板機IP>
```

另外開複製端 Grafana：

```bash
kubectl -n monitoring port-forward svc/grafana 3000:80
```

測試：

```bash
curl http://127.0.0.1:3000/api/health
curl http://127.0.0.1:3001/api/health
```

成功後：

```bash
python3 copy_grafana_dashboards.py
```

---

## 37. 情境 B 快速操作版

### Step 1：被複製端

```bash
kubectl -n monitoring port-forward svc/grafana 3001:80
```

測：

```bash
curl http://127.0.0.1:3001/api/health
```

---

### Step 2：中間機器

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -L 13001:127.0.0.1:3001 \
  kubeadmin@<被複製端跳板機IP>
```

另外測：

```bash
curl http://127.0.0.1:13001/api/health
```

---

### Step 3：複製端

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -L 3001:127.0.0.1:13001 \
  kubeadmin@<中間機器IP>
```

再開複製端 Grafana：

```bash
kubectl -n monitoring port-forward svc/grafana 3000:80
```

最後測：

```bash
curl http://127.0.0.1:3000/api/health
curl http://127.0.0.1:3001/api/health
```

成功後：

```bash
python3 copy_grafana_dashboards.py
```

---

## 38. 快速故障定位

| 測試位置 | 測試內容 | 失敗原因 |
|---|---|---|
| 被複製端 | 127.0.0.1:3001/api/health | Grafana / kubectl port-forward |
| 中間機器 | 127.0.0.1:13001/api/health | 第一段 SSH Tunnel |
| 複製端 | 127.0.0.1:3001/api/health | SSH Tunnel |
| 複製端 | 127.0.0.1:3000/api/health | 複製端 Grafana port-forward |
| 複製端 | SOURCE /api/search | Viewer Token / Folder 權限 |
| 複製端 | TARGET /api/search | Editor Token / Grafana API 權限 |

---

## 39. 查看 Port Forward 狀態

查看 Port：

```bash
ss -lntp | grep -E '3000|3001|13001'
```

---

## 40. 查看 SSH Tunnel

```bash
ps -ef | grep '[s]sh.*-L'
```

---

## 41. 查看 kubectl port-forward

```bash
ps -ef | grep '[k]ubectl.*port-forward'
```

---

## 42. SSH Tunnel Debug

情境 A：

```bash
ssh -vvv \
  -N \
  -o ExitOnForwardFailure=yes \
  -L 3001:127.0.0.1:3001 \
  kubeadmin@<被複製端跳板機IP>
```

情境 B 第一段：

```bash
ssh -vvv \
  -N \
  -o ExitOnForwardFailure=yes \
  -L 13001:127.0.0.1:3001 \
  kubeadmin@<被複製端跳板機IP>
```

情境 B 第二段：

```bash
ssh -vvv \
  -N \
  -o ExitOnForwardFailure=yes \
  -L 3001:127.0.0.1:13001 \
  kubeadmin@<中間機器IP>
```

---

## 43. 完成後關閉連線

Dashboard 複製完成後，建立 Tunnel / Port Forward 的 Terminal 使用：

```text
Ctrl+C
```

依序停止：

1. 複製端 kubectl port-forward
2. 複製端 SSH Tunnel
3. 中間機器 SSH Tunnel（情境 B）
4. 被複製端 kubectl port-forward

最後確認：

```bash
ss -lntp | grep -E '3000|3001|13001'
```

沒有輸出代表 Temporary Tunnel 已全部關閉。

---

## 44. 執行前 Checklist

- [ ] 複製端可以正常執行 kubectl get nodes
- [ ] 被複製端可以正常執行 kubectl get nodes
- [ ] 複製端 Grafana Pod 為 Running
- [ ] 被複製端 Grafana Pod 為 Running
- [ ] 複製端至下一層主機 TCP/22 可通
- [ ] 情境 B 中間機器至被複製端 TCP/22 可通
- [ ] SSH 可以正常登入
- [ ] SSH Tunnel 可以建立
- [ ] 被複製端 localhost:3001/api/health 正常
- [ ] 情境 B 中間機器 localhost:13001/api/health 正常
- [ ] 複製端 localhost:3000/api/health 正常
- [ ] 複製端 localhost:3001/api/health 正常
- [ ] SOURCE_TOKEN 可以讀取 Dashboard
- [ ] TARGET_TOKEN 可以存取被複製端 Grafana API
- [ ] 複製端 Service Account 為 Viewer
- [ ] 被複製端 Service Account 為 Editor
- [ ] Token 未寫入 Git Repository
- [ ] Python 程式在複製端執行

---

## 45. 最終操作邏輯

先判斷：

複製端是否可以直接 SSH 被複製端？

可以：

```text
情境 A
複製端
   ↓ SSH Tunnel
被複製端
```

不可以：

```text
情境 B
複製端
   ↓ SSH Tunnel
中間機器
   ↓ SSH Tunnel
被複製端
```

不論使用哪種方式，最後在「複製端」都必須達成：

```text
127.0.0.1:3000
→ 複製端 Grafana

127.0.0.1:3001
→ 被複製端 Grafana
```

Python 統一使用：

```python
SOURCE_URL = "http://127.0.0.1:3000"
TARGET_URL = "http://127.0.0.1:3001"
```

最後執行：

```bash
python3 copy_grafana_dashboards.py
```

即可開始 Dashboard 複製。
