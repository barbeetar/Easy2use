# Kubescape + Headlamp Plugin 部署 SOP

## 1. 環境資訊

### Kubernetes

- Kubernetes Version：`v1.36.1`
- Kubescape Namespace：`kubescape`
- Headlamp Namespace：`headlamp`
- Cluster Name：`k8sstag`

### Kubescape

- Helm Chart：`kubescape/kubescape-operator`
- Chart Version：`1.40.4`
- Kubescape Image：`v4.0.13`

### Headlamp

- Helm Chart：`headlamp/headlamp`
- Chart Version：`0.43.0`
- Headlamp App Version：`0.43.0`

### Private Registry

```text
utcsyquay.unimicron.com
```

Kubescape image path：

```text
utcsyquay.unimicron.com/helm-charts/kubescape/
```

Headlamp image path：

```text
utcsyquay.unimicron.com/registryk8s/headlamp
```

Kubescape Headlamp Plugin：

```text
utcsyquay.unimicron.com/helm-charts/headlamp/kubescape-headlamp-plugin:v0.11.2
```

---

# 2. Kubescape Operator 使用 Image

Kubescape Operator `1.40.4` 使用到以下 image：

```text
quay.io/kubescape/http-request:v0.2.23
quay.io/kubescape/kubescape:v4.0.13
quay.io/kubescape/kubevuln:v0.3.430
quay.io/kubescape/node-agent:v0.3.219
quay.io/kubescape/operator:v0.2.169
quay.io/kubescape/storage:v0.0.331
```

## 2.1 搬到私有 Registry

範例：

```bash
skopeo copy \
  docker://quay.io/kubescape/kubescape:v4.0.13 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/kubescape:v4.0.13
```

其他 image 依相同方式搬移：

```bash
skopeo copy \
  docker://quay.io/kubescape/http-request:v0.2.23 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/http-request:v0.2.23

skopeo copy \
  docker://quay.io/kubescape/kubevuln:v0.3.430 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/kubevuln:v0.3.430

skopeo copy \
  docker://quay.io/kubescape/node-agent:v0.3.219 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/node-agent:v0.3.219

skopeo copy \
  docker://quay.io/kubescape/operator:v0.2.169 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/operator:v0.2.169

skopeo copy \
  docker://quay.io/kubescape/storage:v0.0.331 \
  docker://utcsyquay.unimicron.com/helm-charts/kubescape/storage:v0.0.331
```

---

# 3. Offline Artifacts

Kubescape 在無法直接連外抓 GitHub policy 時，可以先把 artifacts 下載到 bastion。

## 3.1 下載 artifacts

```bash
mkdir -p ~/kubescape
cd ~/kubescape

kubescape download artifacts \
  --output ./kubescape-artifacts
```

確認：

```bash
ls -lh ./kubescape-artifacts
```

目前會看到類似：

```text
agentruntimehardening.json
allcontrols.json
armobest.json
attack-tracks.json
cis-aks-t1.2.0.json
cis-aks-t1.8.0.json
cis-eks-t1.7.0.json
cis-eks-t1.8.0.json
cis-gke-v1.9.0.json
cis-v1.10.0.json
cis-v1.12.0.json
controls-inputs.json
devopsbest.json
exceptions.json
mitre.json
nsa.json
soc2.json
```

---

# 4. 建立 Offline Artifact PVC

由於 artifacts 約 5 MiB，直接做成 ConfigMap 會超過 Kubernetes request size limit，因此改用 PVC。

## 4.1 建立 PVC

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: kubescape-artifacts
  namespace: kubescape
spec:
  accessModes:
    - ReadWriteMany
  storageClassName: rook-cephfs
  resources:
    requests:
      storage: 1Gi
```

套用：

```bash
kubectl apply -f kubescape-artifacts-pvc.yaml
```

確認：

```bash
kubectl get pvc -n kubescape
```

---

# 5. 建立 Loader Pod 上傳 Artifacts

建立暫時 Pod 掛載 PVC。

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: kubescape-artifacts-loader
  namespace: kubescape
spec:
  containers:
    - name: loader
      image: busybox:1.36
      command:
        - sh
        - -c
        - sleep 86400
      volumeMounts:
        - name: artifacts
          mountPath: /artifacts
  volumes:
    - name: artifacts
      persistentVolumeClaim:
        claimName: kubescape-artifacts
```

套用：

```bash
kubectl apply -f kubescape-artifacts-loader.yaml
```

確認：

```bash
kubectl get pod -n kubescape kubescape-artifacts-loader
```

上傳：

```bash
kubectl cp \
  ./kubescape-artifacts/. \
  kubescape/kubescape-artifacts-loader:/artifacts
```

確認：

```bash
kubectl exec -n kubescape kubescape-artifacts-loader -- \
  ls -lh /artifacts
```

---

# 6. Kubescape Operator Values

## 6.1 核心設定

建議先使用保守設定：

```yaml
capabilities:
  configurationScan: enable
  continuousScan: disable
  nodeScan: disable
  nodeSbomGeneration: enable
  scanEmbeddedSBOMs: disable
  vulnerabilityScan: enable
  relevancy: disable
  vexGeneration: disable
  runtimeObservability: disable
  networkPolicyService: disable
  networkEventsStreaming: disable
  runtimeDetection: disable
  malwareDetection: disable
  nodeProfileService: disable
  admissionController: disable
  httpDetection: disable
  seccompProfileService: disable
  manageWorkloads: disable
  syncSBOM: disable
  agentRuntimePosture: disable
  riskAcceptance: disable
  remediation: disable
  autoUpgrading: disable
  kubescapeOffline: disable
  prometheusExporter: disable
```

---

## 6.2 Kubescape Offline Artifact 設定

Kubescape container：

```yaml
kubescape:
  env:
    - name: KS_DOWNLOAD_ARTIFACTS
      value: "false"

    - name: KS_CACHE_DIR
      value: /opt/kubescape-artifacts
```

掛載：

```yaml
extraVolumes:
  - name: kubescape-artifacts
    persistentVolumeClaim:
      claimName: kubescape-artifacts
```

```yaml
extraVolumeMounts:
  - name: kubescape-artifacts
    mountPath: /opt/kubescape-artifacts
```

注意：

不要掛到：

```text
/home/nonroot/.kubescape
```

因為 Chart 本身已經使用這個路徑：

```text
/home/nonroot/.kubescape
/home/nonroot/.kubescape/host-scanner.yaml
```

會發生：

```text
duplicate entries for key mountPath
```

所以改使用：

```text
/opt/kubescape-artifacts
```

---

# 7. 安裝 Kubescape

先 render：

```bash
helm template kubescape \
  kubescape/kubescape-operator \
  --version 1.40.4 \
  -n kubescape \
  -f values-utf8.yaml \
  > /tmp/kubescape.yaml
```

確認 image：

```bash
grep 'image:' /tmp/kubescape.yaml | sort -u
```

正式安裝：

```bash
helm upgrade --install kubescape \
  kubescape/kubescape-operator \
  --version 1.40.4 \
  -n kubescape \
  --create-namespace \
  -f values-utf8.yaml
```

---

# 8. 確認 Kubescape Pods

```bash
kubectl get pod -n kubescape -o wide
```

主要元件：

```text
kubescape
operator
storage
kubevuln
grype-offline-db
```

---

# 9. Grype Offline DB

如果 kubevuln 無法連到：

```text
https://grype.anchore.io/databases
```

可能看到：

```text
database does not exist
```

此時啟用：

```text
grype-offline-db
```

確認：

```bash
kubectl get pod -n kubescape | grep grype
```

正常：

```text
grype-offline-db   1/1 Running
```

再確認 kubevuln：

```bash
kubectl get pod -n kubescape | grep kubevuln
```

---

# 10. 驗證 Vulnerability Scan

```bash
kubectl get vulnerabilitymanifestsummaries -A
```

有資料代表：

```text
kubevuln
→ grype
→ storage
→ CRD
```

這條流程正常。

---

# 11. 驗證 Configuration Scan

查：

```bash
kubectl get workloadconfigurationscansummaries -A
```

例如：

```text
rook-ceph
argocd
alloy
...
```

有大量 resource，代表 Configuration Scan 已開始產生結果。

---

# 12. 手動 Framework Scan

Kubescape Operator API：

```text
operator:4002
```

## 12.1 Port Forward

Terminal 1：

```bash
kubectl port-forward \
  -n kubescape \
  svc/operator \
  4002:4002
```

正常：

```text
Forwarding from 127.0.0.1:4002 -> 4002
Forwarding from [::1]:4002 -> 4002
```

注意：

如果看到：

```text
error: lost connection to pod
```

重新啟動 port-forward 即可。

---

# 13. 手動掃 NSA

Terminal 2：

```bash
curl -X POST \
  http://127.0.0.1:4002/v1/triggerAction \
  -H 'Content-Type: application/json' \
  -d '{
    "commands":[{
      "CommandName":"kubescapeScan",
      "args":{
        "scanV1":{
          "targetType":"framework",
          "targetNames":["nsa"]
        }
      }
    }]
  }'
```

正常：

```text
ok
```

注意：

`ok` 只代表 Operator 收到 request。

真正成功要看 Kubescape log。

```bash
kubectl logs -n kubescape deploy/kubescape -f
```

成功會看到：

```text
Framework scanned: NSA
Done scanning
Scan results saved
Overall compliance-score ...
```

實際測試：

```text
NSA
Overall compliance-score = 56
```

---

# 14. 手動掃 MITRE

```bash
curl -X POST \
  http://127.0.0.1:4002/v1/triggerAction \
  -H 'Content-Type: application/json' \
  -d '{
    "commands":[{
      "CommandName":"kubescapeScan",
      "args":{
        "scanV1":{
          "targetType":"framework",
          "targetNames":["mitre"]
        }
      }
    }]
  }'
```

成功：

```text
Framework scanned: MITRE
Done scanning
Scan results saved
```

目前實際結果：

```text
MITRE
Overall compliance-score = 55
```

---

# 15. 手動掃 CIS

```bash
curl -X POST \
  http://127.0.0.1:4002/v1/triggerAction \
  -H 'Content-Type: application/json' \
  -d '{
    "commands":[{
      "CommandName":"kubescapeScan",
      "args":{
        "scanV1":{
          "targetType":"framework",
          "targetNames":["cis-v1.10.0"]
        }
      }
    }]
  }'
```

---

# 16. 快速確認 Framework Scan

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --since=30m \
  | grep -E \
  'Framework scanned|Done scanning|Overall compliance-score|Scan results saved'
```

---

# 17. Scan Coverage Warning

目前 NSA / MITRE Scan 會看到：

```text
Scan coverage score: 83% (24/26 controls evaluated)
```

缺少：

```text
C-0069
C-0070
```

原因：

```text
hostdata.kubescape.cloud/v1beta0/KubeletInfo
```

目前 Node Agent / Host Scanner 未完整啟用。

因此：

```text
83%
```

不是 Scan Failure。

---

# 18. Cloud Provider Warning

可能出現：

```text
failed to get cloud provider
```

例如：

```text
container.googleapis.com/v1/ClusterDescribe
eks.amazonaws.com/v1/ClusterDescribe
management.azure.com/v1/ClusterDescribe
```

如果不是：

```text
GKE
EKS
AKS
```

可暫時忽略。

---

# 19. Event RBAC Warning

Kubescape 可能出現：

```text
events is forbidden
```

例如：

```text
User "system:serviceaccount:kubescape:kubescape"
cannot create resource "events"
```

這表示 Kubescape 無權建立 Kubernetes Event。

不影響主要 Scan 完成。

---

# 20. Storage Persist 問題

目前已遇到：

```text
failed to store WorkloadConfigurationScanSummary manifest in storage
```

及：

```text
failed to persist scan results to storage
```

例如：

```text
the server is currently unable to handle the request
```

或：

```text
resource /argo/Deployment/argo-server//v1/argo/Service/argo-server
not found in report
```

所以可能出現：

```text
Kubescape Scanner Scan 成功
       ↓
Scan Results JSON 成功
       ↓
Storage Persist 失敗
       ↓
CRD 資料不完整
       ↓
Headlamp 顯示資料與 CLI 不一致
```

---

# 21. 確認 Storage

```bash
kubectl get pod -n kubescape | grep storage
```

查看 log：

```bash
kubectl logs \
  -n kubescape \
  deploy/storage \
  --since=30m
```

查看 Kubescape persist error：

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --since=30m \
  | grep -iE \
  'persist|storage|error|failed'
```

---

# 22. Headlamp Kubescape Plugin

既有 Headlamp：

```text
Namespace: headlamp
Release: headlamp
Chart: 0.43.0
App: 0.43.0
```

確認：

```bash
helm list -A | grep -i headlamp
```

```bash
kubectl get deploy -A | grep -i headlamp
```

---

# 23. Kubescape Headlamp Plugin Image

使用：

```text
quay.io/kubescape/headlamp-plugin:v0.11.2
```

搬到私有 Registry：

```bash
skopeo copy \
  docker://quay.io/kubescape/headlamp-plugin:v0.11.2 \
  docker://utcsyquay.unimicron.com/helm-charts/headlamp/kubescape-headlamp-plugin:v0.11.2
```

---

# 24. Headlamp Values

完整 values：

```yaml
automountServiceAccountToken: false

clusterRoleBinding:
  create: false

config:
  inCluster: false

  pluginsDir: /headlamp/plugins

  extraArgs:
    - -kubeconfig=/home/headlamp/.config/Headlamp/kubeconfigs/config
    - -oidc-ca-file=/etc/headlamp-oidc-ca/ca.crt
    - -oidc-use-access-token=true

  oidc:
    externalSecret:
      enabled: false
    secret:
      create: false

image:
  registry: utcsyquay.unimicron.com
  repository: registryk8s/headlamp
  tag: v0.43.0
  pullPolicy: IfNotPresent

podSecurityContext:
  fsGroup: 101
  fsGroupChangePolicy: OnRootMismatch

securityContext:
  allowPrivilegeEscalation: false
  privileged: false
  runAsGroup: 101
  runAsNonRoot: true
  runAsUser: 100

initContainers:
  - name: kubescape-plugin
    image: utcsyquay.unimicron.com/helm-charts/headlamp/kubescape-headlamp-plugin:v0.11.2
    imagePullPolicy: IfNotPresent
    command:
      - /bin/sh
      - -c
    args:
      - |
        echo "Copying Kubescape Headlamp plugin..."
        mkdir -p /headlamp/plugins
        cp -r /plugins/* /headlamp/plugins/
        echo "Installed plugins:"
        ls -lah /headlamp/plugins
    volumeMounts:
      - name: headlamp-plugins
        mountPath: /headlamp/plugins

volumeMounts:
  - name: headlamp-kubeconfig
    mountPath: /home/headlamp/.config/Headlamp/kubeconfigs/config
    readOnly: true
    subPath: config

  - name: headlamp-oidc-ca
    mountPath: /etc/headlamp-oidc-ca
    readOnly: true

  - name: headlamp-plugins
    mountPath: /headlamp/plugins

volumes:
  - name: headlamp-kubeconfig
    secret:
      defaultMode: 288
      secretName: headlamp-kubeconfig

  - name: headlamp-oidc-ca
    configMap:
      defaultMode: 292
      name: unimicron-ca

  - name: headlamp-plugins
    emptyDir: {}
```

---

# 25. Headlamp Helm Repo

如果：

```text
Error: repo headlamp not found
```

加入：

```bash
helm repo add \
  headlamp \
  https://kubernetes-sigs.github.io/headlamp/
```

更新：

```bash
helm repo update
```

確認：

```bash
helm search repo \
  headlamp/headlamp \
  --versions | head
```

---

# 26. Headlamp Upgrade

先 render：

```bash
helm template headlamp \
  headlamp/headlamp \
  --version 0.43.0 \
  -n headlamp \
  -f values.yaml \
  > /tmp/headlamp.yaml
```

確認 plugin：

```bash
grep -n \
  -A20 \
  -B5 \
  'kubescape-plugin' \
  /tmp/headlamp.yaml
```

正式 upgrade：

```bash
helm upgrade headlamp \
  headlamp/headlamp \
  --version 0.43.0 \
  -n headlamp \
  -f values.yaml
```

---

# 27. 確認 Headlamp Plugin

```bash
kubectl get pod -n headlamp
```

確認 initContainer：

```bash
kubectl logs \
  -n headlamp \
  deploy/headlamp \
  -c kubescape-plugin
```

確認 plugin files：

```bash
kubectl exec \
  -n headlamp \
  deploy/headlamp -- \
  sh -c \
  'find /headlamp/plugins -maxdepth 3 -type f | head -50'
```

正常會看到：

```text
/headlamp/plugins/kubescape-plugin/main.wasm
/headlamp/plugins/kubescape-plugin/frameworks.json
/headlamp/plugins/kubescape-plugin/controls.json
/headlamp/plugins/kubescape-plugin/main.js
/headlamp/plugins/kubescape-plugin/package.json
/headlamp/plugins/kubescape-plugin/rego-rules.json
```

---

# 28. Headlamp Kubescape UI

Headlamp 左側會出現：

```text
Kubescape
├── Compliance
├── Vulnerabilities
├── Network Policies
├── Policy Playground
├── Runtime Detection
├── Exceptions
└── Frameworks
```

目前主要使用：

```text
Compliance
Vulnerabilities
```

---

# 29. Headlamp Framework 資料來源

Headlamp plugin 內建：

```text
frameworks.json
controls.json
```

確認：

```bash
kubectl exec \
  -n headlamp \
  deploy/headlamp -- \
  sh -c \
  'grep -n -iE "NSA|MITRE|CIS" /headlamp/plugins/kubescape-plugin/frameworks.json | head -50'
```

目前可看到：

```text
NSA
MITRE
cis-v1.10.0
cis-v1.12.0
...
```

---

# 30. Headlamp Compliance Score 計算方式

Headlamp 上方：

```text
CIS
MITRE
NSA
SOC2
```

Framework score 是 plugin 自己利用：

```text
frameworks.json
+
WorkloadConfigurationScanSummary
```

重新計算。

概念：

```text
Framework
 ↓
framework.controls
 ↓
每個 Control 對應 CRD 中的 controlID
 ↓
算 Control Compliance
 ↓
全部 Control 平均
 ↓
Framework %
```

---

# 31. Headlamp 與 CLI 分數不同

目前實際看到：

```text
Kubescape Scanner

NSA   ≈ 56%
MITRE ≈ 55%
```

Headlamp：

```text
NSA   ≈ 76%
MITRE ≈ 90%
```

原因可能是：

```text
Scanner
 ↓
完整掃描結果
 ↓
Storage Persist
 ↓
部分失敗
 ↓
CRD 不完整
 ↓
Headlamp 用 CRD 重算
```

另外 Headlamp plugin 的邏輯：

```text
如果某 Control 完全沒有 workload result
→ Compliance = 100%
```

因此 CRD 缺資料時可能造成 Framework Score 偏高。

---

# 32. Namespace Compliance 特別注意

Headlamp：

```text
Compliance
→ Namespaces
```

下面的：

```text
alloy
apisix
argo
argocd
...
```

Compliance %

不是：

```text
NSA %
MITRE %
CIS %
```

它的計算是：

```text
該 Namespace
↓
所有 WorkloadConfigurationScanSummary
↓
所有 spec.controls
↓
passed / total
```

所以切：

```text
NSA
MITRE
CIS
SOC2
```

Namespace %

目前不會跟著變。

這是目前 Headlamp Kubescape Plugin 的程式邏輯。

---

# 33. Framework Button 與 Namespace View

Headlamp Framework button：

```text
NSA
MITRE
CIS
SOC2
```

主要控制 Framework 顯示及 Framework Score。

但：

```text
Namespaces
```

使用的是全部 controls：

```text
spec.controls
```

沒有依 `activeFrameworks` filter。

所以：

```text
切 NSA
→ Namespace % 不變

切 MITRE
→ Namespace % 不變
```

是目前 Plugin 設計行為。

---

# 34. 建議判斷依據

## Framework 真實 Scan 結果

以 Kubescape Scanner Log 為準：

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --since=30m \
  | grep -E \
  'Framework scanned|Overall compliance-score'
```

例如：

```text
Framework scanned: NSA
Overall compliance-score: 56
```

---

## Headlamp Dashboard

Headlamp 適合：

```text
快速瀏覽
Namespace
Resources
Failed Controls
Vulnerabilities
```

但在 Storage Persist 問題修好前：

```text
Headlamp Framework %
```

不建議直接當成最終 Compliance Score。

---

# 35. 常用檢查指令

## Pods

```bash
kubectl get pod -n kubescape -o wide
```

---

## Configuration Scan

```bash
kubectl get workloadconfigurationscansummaries -A
```

```bash
kubectl get workloadconfigurationscans -A
```

---

## Vulnerability

```bash
kubectl get vulnerabilitymanifestsummaries -A
```

---

## Cluster Scan Summary

```bash
kubectl get configurationscansummaries
```

---

## Kubescape Log

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --tail=200
```

---

## Storage Log

```bash
kubectl logs \
  -n kubescape \
  deploy/storage \
  --tail=200
```

---

## Framework Scan 結果

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --since=30m \
  | grep -E \
  'Framework scanned|Done scanning|Scan results saved|Overall compliance-score'
```

---

## Persist Error

```bash
kubectl logs \
  -n kubescape \
  deploy/kubescape \
  --since=30m \
  | grep -iE \
  'persist|storage|error|failed'
```

---

# 36. 目前已知問題

## 問題 1：C-0240

```text
framework: C-0240: framework from file not matching
```

目前研判為自動 / Continuous Scan 路徑產生。

建議測試階段：

```yaml
capabilities:
  continuousScan: disable
```

---

## 問題 2：Storage Persist

```text
failed to persist scan results to storage
```

可能造成：

```text
CRD 不完整
↓
Headlamp 分數不準
```

---

## 問題 3：Argo Resource

```text
resource /argo/Deployment/argo-server//v1/argo/Service/argo-server
not found in report
```

目前仍需進一步確認。

---

## 問題 4：Host Scanner

目前有：

```text
Failed to list OsReleaseFile CRDs
```

以及：

```text
KubeletInfo missing
```

造成：

```text
C-0069
C-0070
```

無法評估。

---

# 37. 架構流程

```text
                    Kubescape Artifacts PVC
                           │
                           │
                           ▼
                    Kubescape Scanner
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
      Configuration Scan        Vulnerability Scan
              │                         │
              ▼                         ▼
          Kubescape                 Kubevuln
              │                         │
              │                     Grype DB
              │                         │
              └────────────┬────────────┘
                           │
                           ▼
                        Storage
                           │
                           ▼
        spdx.softwarecomposition.kubescape.io
                           │
            ┌──────────────┼──────────────┐
            │              │              │
            ▼              ▼              ▼
 WorkloadConfiguration  Vulnerability   Summary
       Scan CRDs           CRDs            CRDs
            │
            ▼
     Headlamp Plugin
            │
            ▼
      Kubescape Web UI
```

---

# 38. 最終狀態

目前已完成：

```text
Kubescape Operator           OK
Offline Artifacts            OK
NSA Framework Scan           OK
MITRE Framework Scan         OK
Vulnerability Scan           OK
Grype Offline DB             OK
Storage                      可運作，但有 Persist Error
Headlamp                     OK
Kubescape Headlamp Plugin    OK
Compliance UI                OK
Vulnerability UI             OK
```

目前待處理：

```text
Storage Persist Error
C-0240 Continuous Scan Error
Headlamp Framework Score 與 Scanner Score 差異
Host Scanner / KubeletInfo
```
